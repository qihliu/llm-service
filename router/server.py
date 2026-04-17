"""
router/server.py

Lightweight request router that sits in front of all LLM backends.
It reads the "model" field from incoming chat requests and forwards them
to the correct backend — this is the OpenAI-style routing pattern.

How it works:
  1. A user picks a model in OpenWebUI (e.g. "facebook/opt-125m-v2")
  2. OpenWebUI sends POST /v1/chat/completions with {"model": "facebook/opt-125m", ...}
  3. This router reads the "model" field and looks it up in ROUTES
  4. It proxies the entire request to the matching backend Service
  5. The backend response is returned unchanged

Configuration (env vars):
  ROUTES  — comma-separated "model-name=http://service:port" entries
            e.g. "facebook/opt-125m=http://backend-v1:8000,facebook/opt-350m=http://backend-v2:8000"
  METRICS_PORT         — port for Prometheus scraping (default: 9090)
  START_METRICS_SERVER — set to "false" to skip binding the metrics port (used in tests)

Roll-forward / roll-back:
  To roll back (remove v2, only v1 available):
    kubectl set env deployment/router ROUTES="facebook/opt-125m=http://backend-v1:8000"
  To roll forward (v2 becomes the only model):
    kubectl set env deployment/router ROUTES="facebook/opt-350m=http://backend-v2:8000"
"""

import os

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from prometheus_client import Counter, start_http_server

app = FastAPI(title="LLM Router")

# ── Routing table ──────────────────────────────────────────────────────────────
# Parse ROUTES env var into a dict: {model_name: backend_url}
# Format: "model-a=http://backend-v1:8000,model-b=http://backend-v2:8000"

ROUTES: dict[str, str] = {}
for _entry in os.getenv("ROUTES", "").split(","):
    _entry = _entry.strip()
    if "=" in _entry:
        _model, _url = _entry.split("=", 1)
        ROUTES[_model.strip()] = _url.strip()

print(f"[router] Routing table: {ROUTES}")

# ── Prometheus metrics ─────────────────────────────────────────────────────────
# Tracks how many requests each model received and whether they succeeded.
# This makes per-model traffic visible in Grafana — useful during canary rollouts.

ROUTE_COUNT = Counter(
    "router_requests_total",
    "Requests routed by model and status",
    ["model", "status"],
)

METRICS_PORT = int(os.getenv("METRICS_PORT", "9090"))
if os.getenv("START_METRICS_SERVER", "true").lower() == "true":
    start_http_server(METRICS_PORT)
    print(f"[router] Prometheus metrics server started on port {METRICS_PORT}")

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check. Also shows the active routing table."""
    return {"status": "ok", "routes": ROUTES}


@app.get("/v1/models")
async def list_models():
    """
    Aggregate the model list from all backends.

    Queries every backend's GET /v1/models and merges the results.
    OpenWebUI calls this on startup to populate the model selector dropdown.
    Users will see one entry per backend (e.g. both v1 and v2 models).
    """
    models = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for backend_url in ROUTES.values():
            try:
                resp = await client.get(f"{backend_url}/v1/models")
                models.extend(resp.json().get("data", []))
            except Exception as e:
                print(f"[router] Could not reach {backend_url}: {e}")
    return {"object": "list", "data": models}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """
    Route a chat request to the backend that serves the requested model.

    If the model is not in the routing table, returns HTTP 404 with
    the list of valid model names so the client knows what to use.
    """
    body = await request.json()
    model = body.get("model", "")
    backend_url = ROUTES.get(model)

    if not backend_url:
        ROUTE_COUNT.labels(model=model, status="not_found").inc()
        raise HTTPException(
            status_code=404,
            detail=f"Unknown model '{model}'. Available: {list(ROUTES.keys())}",
        )

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{backend_url}/v1/chat/completions",
                json=body,
            )
        ROUTE_COUNT.labels(model=model, status="success").inc()
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except Exception as e:
        ROUTE_COUNT.labels(model=model, status="error").inc()
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {e}")
