# backend/Dockerfile
#
# A Dockerfile is a recipe for building a Docker image.
# Each instruction creates a "layer". Docker caches layers, so unchanged
# layers are reused on rebuilds — this makes subsequent builds much faster.
#
# ── Two build modes ───────────────────────────────────────────────────────────
#
# LOCAL DEVELOPMENT (CPU, no GPU required):
#   docker build -t llm-backend:local .
#   Uses python:3.11-slim as the base image (~150MB).
#   Sets USE_VLLM=false so server.py uses the transformers backend.
#
# PRODUCTION (GPU, requires NVIDIA drivers on the host):
#   docker build --build-arg BASE_IMAGE=vllm/vllm-openai:latest \
#                --build-arg USE_VLLM=true \
#                -t llm-backend:prod .
#   Uses the official vLLM image which bundles CUDA and vLLM.
#
# ARG lets callers override this value at build time with --build-arg.
# If not overridden, it defaults to the CPU-friendly python image.

ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

# Set the working directory inside the container.
# All subsequent COPY / RUN commands are relative to /app.
WORKDIR /app

# Copy requirements.txt first — before the application code.
# WHY: Docker caches each layer. If requirements.txt hasn't changed,
# the expensive "pip install" layer is reused from cache even when
# server.py changes. This makes rebuilds much faster during development.
COPY requirements.txt .

# Install Python dependencies.
# --no-cache-dir: don't store the pip download cache inside the image layer
#                 (keeps the image smaller).
# For CPU-only torch (smaller download), you can add:
#   --index-url https://download.pytorch.org/whl/cpu
# but we omit it here to keep the Dockerfile simple.
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code.
COPY server.py .

# ── Environment variables ──────────────────────────────────────────────────────
# ENV sets defaults that can be overridden at runtime with:
#   docker run -e USE_VLLM=true ...   (Docker)
#   env: [...] in a K8s manifest       (Kubernetes)

# Use the transformers (CPU) backend by default.
# Set to "true" to use vLLM (requires GPU and vllm installed).
ARG USE_VLLM=false
ENV USE_VLLM=${USE_VLLM}

ENV MODEL_NAME=facebook/opt-125m

# Document which port the server listens on.
# EXPOSE is documentation only — it doesn't actually open a port.
EXPOSE 8000

# Start the FastAPI server with uvicorn.
#   server:app    → module "server", variable "app" (the FastAPI instance)
#   --host 0.0.0.0 → listen on all interfaces (required inside Docker/K8s;
#                    default 127.0.0.1 would only be reachable within the container)
#   --port 8000   → listen on port 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
