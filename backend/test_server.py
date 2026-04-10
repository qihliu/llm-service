"""
backend/test_server.py

Unit tests for the server's HTTP endpoints.

Now that USE_VLLM defaults to "true", the server tries to import vllm at
module load time. vllm is not installed in the test environment (it requires
a GPU and is very large), so we must mock it out before importing server.py.

The mocking strategy is the same as before but targeting the vllm package:
  1. Set USE_VLLM=true in the environment (matches the new default)
  2. Inject fake vllm objects into sys.modules before importing server
  3. Import server — it finds our fakes instead of the real library
  4. Tests run against the real HTTP routing logic with a fake model
"""

import os
import sys
from unittest.mock import MagicMock, AsyncMock
import pytest


# ── Step 1: Configure environment before importing server ─────────────────────
# server.py reads USE_VLLM at module load time (the top-level if/else).
# These must be set before `import server` or the wrong branch will run.

os.environ["USE_VLLM"] = "true"
os.environ["MODEL_NAME"] = "test-model"


# ── Step 2: Build a fake vllm module ─────────────────────────────────────────
# vllm exposes three names that server.py imports:
#   AsyncLLMEngine  — the engine class
#   AsyncEngineArgs — dataclass holding engine config
#   SamplingParams  — dataclass holding per-request generation settings
#
# server.py calls:
#   AsyncEngineArgs(model=..., max_model_len=...)
#   AsyncLLMEngine.from_engine_args(engine_args)
#   engine.generate(prompt, sampling_params, request_id)  ← async generator
#
# We need engine.generate() to be an async generator that yields one fake
# output object with the structure: output.outputs[0].text = "some text"

def _make_fake_output(text: str):
    """
    Builds an object that looks like a vLLM RequestOutput.
    The real structure is: output.outputs[0].text
    MagicMock lets us set nested attributes freely.
    """
    output = MagicMock()
    output.outputs[0].text = text
    return output

async def _fake_generate(prompt, sampling_params, request_id):
    """
    Mimics AsyncLLMEngine.generate(), which is an async generator.
    Yields one fake output — server.py keeps the last one, so one is enough.
    """
    yield _make_fake_output("[mock vllm response]")

# Build the fake engine instance
fake_engine = MagicMock()
fake_engine.generate = _fake_generate   # attach our async generator

# Build the fake vllm module
mock_vllm = MagicMock()
mock_vllm.AsyncEngineArgs = MagicMock(return_value=MagicMock())
mock_vllm.AsyncLLMEngine.from_engine_args = MagicMock(return_value=fake_engine)
mock_vllm.SamplingParams = MagicMock(return_value=MagicMock())

# Inject into sys.modules — Python checks this dict before looking on disk.
# When server.py runs `from vllm import AsyncLLMEngine, ...`, it finds our fake.
sys.modules["vllm"] = mock_vllm


# ── Step 3: Now it is safe to import server ───────────────────────────────────
from fastapi.testclient import TestClient  # noqa: E402
import server                              # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    """
    TestClient sends HTTP requests to the FastAPI app in memory —
    no real network socket, no real port opened.
    """
    return TestClient(server.app)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_health_returns_ok(client):
    """GET /health should return 200 with status=ok and backend=vllm."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # Verify we're actually running the vllm path
    assert body["backend"] == "vllm"


def test_models_lists_the_configured_model(client):
    """
    GET /v1/models should return the model name set in MODEL_NAME env var.
    OpenWebUI calls this on startup to populate the model dropdown.
    """
    response = client.get("/v1/models")
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert body["data"][0]["id"] == "test-model"


def test_chat_returns_assistant_message(client):
    """POST /v1/chat/completions should return an OpenAI-format response."""
    response = client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "Say hello"}],
        "max_tokens": 50,
    })
    assert response.status_code == 200
    body = response.json()
    assert "choices" in body
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert len(body["choices"][0]["message"]["content"]) > 0


def test_chat_requires_messages(client):
    """
    A request with no 'messages' field should return HTTP 422.
    FastAPI validates this automatically from the Pydantic schema.
    """
    response = client.post("/v1/chat/completions", json={})
    assert response.status_code == 422
