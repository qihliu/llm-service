# LLM Service

A production-style LLM inference system with a chat frontend, Kubernetes deployment, and observability.

## Architecture

```
Browser
  └─► OpenWebUI (chat UI)          :30300 / :3000
        └─► Backend API (FastAPI)  :30800 / :8000
              └─► HuggingFace model (facebook/opt-125m)

Prometheus (scrapes /metrics)      :30090 / :9090
  └─► Grafana (dashboard)          :30030 / :3001
```

All components run as independent containers. Kubernetes (kind) manages restarts and routing. Prometheus and Grafana provide observability.

## Components

| Component | Technology | Purpose |
|---|---|---|
| Backend | FastAPI + HuggingFace transformers | LLM inference, OpenAI-compatible API |
| Frontend | OpenWebUI | Chat interface for users |
| Observability | Prometheus + Grafana | Metrics collection and dashboards |
| Orchestration | Kubernetes (kind) | Container scheduling, restarts, networking |
| CI/CD | GitHub Actions | Lint, test, build and push Docker image |

## Local Development (Docker Compose)

The fastest way to run everything on your laptop — no Kubernetes needed.

**Prerequisites:** Docker Desktop

```bash
docker compose up --build
```

| Service | URL |
|---|---|
| Chat UI | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3001 (admin/admin) |

Stop everything:

```bash
docker compose down
```

## Kubernetes Deployment (kind)

Runs a full Kubernetes cluster locally inside Docker.

**Prerequisites:** Docker Desktop, [kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation), [kubectl](https://kubernetes.io/docs/tasks/tools/)

```bash
# Install kind and kubectl (macOS)
brew install kind kubectl
```

**First-time setup — create the cluster:**

```bash
make cluster-up
```

**Deploy (run this every time you change code):**

```bash
make dev-up
```

This runs three steps in order:
1. `docker build` — builds the backend image locally
2. `kind load` — copies the image into the kind cluster's cache
3. `kubectl apply` — tells Kubernetes to run the updated containers

| Service | URL |
|---|---|
| Chat UI | http://localhost:30300 |
| Backend API | http://localhost:30800 |
| Prometheus | http://localhost:30090 |
| Grafana | http://localhost:30030 (admin/admin) |

**Useful commands:**

```bash
make status   # show pod health (Running / Pending / CrashLoopBackOff)
make logs     # stream backend logs
make dev-down # tear down everything and delete the cluster
```

## CI/CD (GitHub Actions)

On every push or pull request to `main`, the pipeline runs automatically:

```
lint → test → build-and-push (on push to main only)
```

| Job | What it does |
|---|---|
| `lint` | Runs `ruff check` — catches syntax errors and style issues |
| `test` | Runs `pytest` — verifies the API endpoints behave correctly |
| `build-and-push` | Builds the Docker image and pushes to GHCR |

The built image is published at:
```
ghcr.io/hoshinanoriko/llm-backend:latest
ghcr.io/hoshinanoriko/llm-backend:<commit-sha>
```

Each commit SHA tag lets you trace exactly which code produced which image.

## Running Tests Locally

```bash
# Install test dependencies into the venv (first time only)
backend/.venv/bin/pip install pytest httpx anyio

# Run tests using the venv's Python directly.
# This avoids Anaconda or system Python intercepting the command.
backend/.venv/bin/python -m pytest backend/test_server.py -v
```

The tests mock both `torch` and `transformers` at the Python import level —
neither package needs to be installed, and no model is downloaded.
vLLM is also not required; the tests always run against the CPU/transformers path.
Total runtime is under 1 second.

## Configuration

Both Docker Compose and Kubernetes read configuration from environment variables:

| Variable | Default | Description |
|---|---|---|
| `MODEL_NAME` | `facebook/opt-125m` | HuggingFace model to load |
| `USE_VLLM` | `false` | Set to `true` to use vLLM (requires GPU) |

To use a different model, change `MODEL_NAME` in `docker-compose.yaml` (under `backend.environment`) or `k8s/backend.yaml` (under `env`). No code changes needed.

## Project Structure

```
llm-service/
├── backend/
│   ├── server.py          # FastAPI server: /health, /v1/models, /v1/chat/completions
│   ├── test_server.py     # Unit tests (mocked — no GPU or model download needed)
│   ├── requirements.txt   # Python dependencies
│   └── Dockerfile         # Container recipe (CPU by default, GPU via build arg)
├── k8s/
│   ├── backend.yaml       # Kubernetes Deployment + Service for the backend
│   ├── frontend.yaml      # Kubernetes Deployment + Service for OpenWebUI
│   ├── prometheus.yaml    # Kubernetes Deployment + ConfigMap for Prometheus + Grafana
│   └── kind-config.yaml   # kind cluster config with host port mappings
├── .github/workflows/
│   └── ci.yaml            # GitHub Actions pipeline
├── docker-compose.yaml    # Local multi-container dev setup
├── prometheus.yaml        # Prometheus scrape config for docker-compose
├── Makefile               # Shortcuts: make dev-up, make logs, make dev-down
└── README.md              # This file
```

## How a Request Flows Through the System

1. User types a message in OpenWebUI (browser)
2. OpenWebUI sends `POST /v1/chat/completions` to the backend Service
3. Inside the cluster, Kubernetes DNS resolves `backend` to the backend Pod's IP
4. The FastAPI server extracts the prompt and runs it through the HuggingFace pipeline
5. The response is formatted in OpenAI JSON format and returned to OpenWebUI
6. At the same time, Prometheus scrapes `GET /metrics` every 15 seconds
7. Grafana queries Prometheus to display request counts, latency, and error rates
