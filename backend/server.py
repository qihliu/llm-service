"""
backend/server.py

This is the LLM inference server. It does three things:
  1. Loads a HuggingFace model — using vLLM (GPU) or transformers (CPU)
  2. Exposes an OpenAI-compatible HTTP API (/v1/chat/completions)
     so that OpenWebUI can talk to it out of the box
  3. Exposes a /metrics endpoint for Prometheus to scrape

Backend selection is controlled by the USE_VLLM environment variable:
  USE_VLLM=false  (default) → uses HuggingFace transformers, runs on CPU
                             → good for local development on your laptop
  USE_VLLM=true             → uses vLLM AsyncLLMEngine, requires a GPU
                             → use this in production

Key concepts:
  - FastAPI: a Python web framework (like Flask but faster and with auto validation)
  - transformers.pipeline: HuggingFace's high-level inference API, CPU-compatible
  - vLLM AsyncLLMEngine: handles batching and concurrent inference efficiently (GPU)
  - asyncio.to_thread: runs a blocking (synchronous) function in a thread pool
                       so it doesn't block the async event loop
  - prometheus_client: records metrics in a format Prometheus understands
"""

import asyncio
import os
import time
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, make_asgi_app

# ── Backend selection ─────────────────────────────────────────────────────────
# Read env var at startup to decide which inference backend to use.
# os.getenv returns the value as a string, so we compare with "true".

USE_VLLM = os.getenv("USE_VLLM", "false").lower() == "true"
MODEL_NAME = os.getenv("MODEL_NAME", "facebook/opt-125m")

if USE_VLLM:
    # GPU path: import vLLM's async engine.
    # AsyncLLMEngine can handle many requests at once without blocking —
    # it uses continuous batching internally, which vLLM is famous for.
    from vllm import AsyncLLMEngine, AsyncEngineArgs, SamplingParams
    engine_args = AsyncEngineArgs(model=MODEL_NAME, max_model_len=512)
    engine = AsyncLLMEngine.from_engine_args(engine_args)
    print(f"[backend] Using vLLM engine with model: {MODEL_NAME}")
else:
    # CPU path: use HuggingFace transformers pipeline.
    # pipeline("text-generation") is a high-level wrapper around the model —
    # you pass a string in and get a string out.
    # device="cpu" explicitly runs on CPU (no GPU required).
    from transformers import pipeline
    pipe = pipeline("text-generation", model=MODEL_NAME, device="cpu")
    print(f"[backend] Using transformers pipeline (CPU) with model: {MODEL_NAME}")

# ── App setup ────────────────────────────────────────────────────────────────

app = FastAPI(title="LLM Inference Server")

# Mount the Prometheus metrics endpoint at /metrics.
# make_asgi_app() creates a tiny ASGI app that serializes metrics to text.
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# ── Prometheus metrics ────────────────────────────────────────────────────────
# Counter: a number that only goes up (total requests)
# Histogram: records the distribution of values (latency buckets)
# The "status" label lets us split the counter into success vs error.

REQUEST_COUNT = Counter(
    "llm_requests_total",
    "Total number of inference requests",
    ["status"],
)
REQUEST_LATENCY = Histogram(
    "llm_request_duration_seconds",
    "End-to-end request latency in seconds",
)

# ── Request / Response schemas ────────────────────────────────────────────────
# Pydantic models validate incoming JSON automatically.
# If a field is missing or has the wrong type, FastAPI returns HTTP 422.

class Message(BaseModel):
    role: str      # "user", "assistant", or "system"
    content: str   # the actual text

class ChatRequest(BaseModel):
    model: str = MODEL_NAME
    messages: list[Message]
    max_tokens: int = 200

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/v1/models")
async def list_models():
    """
    OpenAI-compatible model listing endpoint.

    OpenWebUI calls GET /v1/models on startup to discover what models are
    available. If this endpoint is missing (returns 404), the UI shows
    "No models available" and refuses to let users chat.

    The OpenAI spec requires this exact JSON shape:
      { "object": "list", "data": [ { "id": "...", "object": "model", ... } ] }

    We return a single entry for whichever model this server is running.
    """
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_NAME,        # e.g. "facebook/opt-125m"
                "object": "model",
                "created": 0,
                "owned_by": "local",
            }
        ],
    }


@app.get("/health")
async def health():
    """
    Health check endpoint. Kubernetes liveness probes call this.
    Returns HTTP 200 with the active backend type so you can verify which
    mode the server started in.
    """
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "backend": "vllm" if USE_VLLM else "transformers",
    }


async def _generate_vllm(prompt: str, max_tokens: int) -> str:
    """
    Generate text using the vLLM async engine.

    engine.generate() is an async generator — it yields partial results
    as tokens are produced (streaming). We discard all but the last one
    because we want the fully completed output.
    """
    sampling_params = SamplingParams(max_tokens=max_tokens, temperature=0.7)
    request_id = str(uuid.uuid4())
    final = None
    async for output in engine.generate(prompt, sampling_params, request_id):
        final = output
    if final is None:
        raise RuntimeError("vLLM returned no output")
    return final.outputs[0].text


async def _generate_transformers(prompt: str, max_tokens: int) -> str:
    """
    Generate text using HuggingFace transformers (CPU).

    The transformers pipeline is synchronous (blocking). Calling it directly
    inside an async function would freeze the entire server while it runs —
    no other requests could be handled.

    asyncio.to_thread() solves this: it runs the blocking function in a
    separate thread from the thread pool, so the event loop stays free to
    handle other requests concurrently.
    """
    def _run():
        result = pipe(prompt, max_new_tokens=max_tokens, do_sample=True, temperature=0.7)
        # The pipeline returns a list of dicts. result[0]["generated_text"]
        # contains the full string including the original prompt, so we
        # strip the prompt from the beginning.
        full_text = result[0]["generated_text"]
        return full_text[len(prompt):]

    return await asyncio.to_thread(_run)


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatRequest):
    """
    OpenAI-compatible chat endpoint. OpenWebUI sends requests here.

    Request body:
        {"messages": [{"role": "user", "content": "Hello!"}], "max_tokens": 100}

    Response format follows the OpenAI spec so any OpenAI-compatible client works.
    """
    start_time = time.time()
    try:
        prompt = request.messages[-1].content

        if USE_VLLM:
            generated_text = await _generate_vllm(prompt, request.max_tokens)
        else:
            generated_text = await _generate_transformers(prompt, request.max_tokens)

        REQUEST_COUNT.labels(status="success").inc()
        REQUEST_LATENCY.observe(time.time() - start_time)

        # OpenAI response format — OpenWebUI requires choices[0].message.content
        return {
            "id": f"chatcmpl-{uuid.uuid4()}",
            "object": "chat.completion",
            "created": int(start_time),
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": generated_text},
                    "finish_reason": "stop",
                }
            ],
        }

    except Exception as e:
        REQUEST_COUNT.labels(status="error").inc()
        raise HTTPException(status_code=500, detail=str(e))
