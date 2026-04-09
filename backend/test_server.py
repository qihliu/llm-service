"""
backend/test_server.py

Unit tests for the server's HTTP endpoints.

The key challenge: server.py loads a real ML model on import, which takes
minutes and requires heavy packages. Tests must be fast and package-light.

Solution: before importing server.py, we replace the 'transformers' module
in Python's import cache (sys.modules) with a fake object. When server.py
runs 'from transformers import pipeline', Python finds our fake instead of
the real library — so no model is ever downloaded or loaded.
"""

import os
import sys
from unittest.mock import MagicMock
import pytest


# ── Step 1: Configure environment before importing server ────────────────────
# These must be set BEFORE 'import server' because server.py reads them
# at module load time (the top-level if/else block).

os.environ["USE_VLLM"] = "false"          # use the transformers (CPU) path
os.environ["MODEL_NAME"] = "test-model"   # a fake model name for tests


# ── Step 2: Mock the 'transformers' package ───────────────────────────────────
# sys.modules is a dict Python checks before looking on disk for a package.
# By putting a fake object here under "transformers", we prevent Python from
# ever loading the real library — so torch and transformers don't need to be
# installed for tests to run.

def _fake_pipe(prompt, **kwargs):
    """
    Mimics what the real transformers pipeline returns.
    The real pipeline returns the prompt + generated text concatenated,
    e.g. input "Say hello" → output [{"generated_text": "Say hello world"}]
    server.py strips the prompt prefix, leaving just " world".
    """
    return [{"generated_text": prompt + " [mock response]"}]

mock_transformers = MagicMock()
mock_transformers.pipeline.return_value = MagicMock(side_effect=_fake_pipe)
sys.modules["transformers"] = mock_transformers
sys.modules["accelerate"] = MagicMock()   # transformers sometimes imports this


# ── Step 3: Now it is safe to import server ───────────────────────────────────
from fastapi.testclient import TestClient   # noqa: E402
import server                               # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    """
    TestClient wraps the FastAPI app and lets us send HTTP requests to it
    in memory — no real network socket, no real port opened.
    """
    return TestClient(server.app)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_health_returns_ok(client):
    """GET /health should return 200 with status=ok."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_models_lists_the_configured_model(client):
    """
    GET /v1/models should return the model name from the env var.
    OpenWebUI calls this on startup — if it returns 404 or empty,
    the UI shows 'No models available'.
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
    FastAPI validates this automatically from the Pydantic schema —
    our code never even runs.
    """
    response = client.post("/v1/chat/completions", json={})
    assert response.status_code == 422
