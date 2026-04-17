# Makefile
#
# A Makefile gives you short named commands (called "targets") for common tasks.
# Run any target with: make <target>  e.g.  make cluster-up
#
# .PHONY tells make these are not filenames — they're always commands.
# Without it, make would skip a target if a file with the same name existed.

.PHONY: cluster-up cluster-down build load deploy dev-up dev-down status logs canary-deploy canary-rollback canary-forward port-forward-prometheus port-forward-grafana build-router load-router

# ── Cluster lifecycle ─────────────────────────────────────────────────────────

# Create a local kind cluster using the port-mapping config we wrote.
# This spins up a full Kubernetes cluster inside Docker on your laptop.
# Takes ~30 seconds. Only needs to be done once.
cluster-up:
	kind create cluster --name llm --config k8s/kind-config.yaml

# Delete the local cluster and free all its resources.
cluster-down:
	kind delete cluster --name llm

# ── Image workflow ────────────────────────────────────────────────────────────

# Build the backend Docker image locally (CPU mode, no GPU required).
# The tag "llm-backend:local" matches what k8s/backend.yaml references.
build:
	docker build -t llm-backend:local ./backend

build-router:
	docker build -t llm-router:local ./router

# Load the backend image into kind from Docker's local cache.
# Only llm-backend:local is loaded this way because it is a single-platform
# image we built ourselves — kind load works reliably for it.
#
# The public images (OpenWebUI, Prometheus, Grafana) are pulled directly by
# Kubernetes on first use. On Apple Silicon Macs, multi-platform images stored
# locally cannot be exported into kind reliably, so we let K8s pull them.
# After the first cluster creation they are cached inside kind and subsequent
# `make dev-up` runs are instant.
load:
	kind load docker-image llm-backend:local --name llm

load-router:
	kind load docker-image llm-router:local --name llm

# Apply all Kubernetes manifests to the cluster.
# We list files explicitly to skip kind-config.yaml, which is a kind-specific
# file that kubectl does not understand.
deploy:
	kubectl apply -f k8s/backend.yaml -f k8s/frontend.yaml -f k8s/prometheus.yaml

# ── Convenience targets ───────────────────────────────────────────────────────

# Full local dev setup in one command:
#   1. Build the image
#   2. Load it into kind
#   3. Deploy all manifests
#   The dependencies after the colon run first, in order.
dev-up: build load deploy
	@echo ""
	@echo "Cluster is up. Access points:"
	@echo "  Backend API : http://localhost:30800"
	@echo "  OpenWebUI   : http://localhost:30300"
	@echo "  Prometheus  : make port-forward-prometheus  →  http://localhost:9090"
	@echo "  Grafana     : make port-forward-grafana     →  http://localhost:3000"

# Tear down everything: delete K8s resources and the cluster itself.
dev-down:
	# Delete only the actual K8s manifests — not kind-config.yaml, which is
	# a kind-specific file that kubectl doesn't understand.
	kubectl delete -f k8s/backend.yaml -f k8s/frontend.yaml -f k8s/prometheus.yaml --ignore-not-found
	kind delete cluster --name llm

# ── Debugging helpers ─────────────────────────────────────────────────────────

# Show the state of all pods. Use this to check if pods are Running or crashing.
# Typical pod states:
#   Pending      — waiting to be scheduled (usually: image not found yet)
#   Running      — container started (but may still be loading the model)
#   CrashLoopBackOff — container is crashing repeatedly (check logs)
status:
	kubectl get pods -o wide

# Tail the logs of the backend pod.
# "kubectl logs -l app=backend" selects pods by label.
# "--follow" streams new log lines as they appear (like tail -f).
logs:
	kubectl logs -l app=backend --follow

# ── Observability access (dev only) ──────────────────────────────────────────
# Prometheus and Grafana use ClusterIP Services — not reachable from outside
# the cluster. Use these targets to open a tunnel from your laptop into the
# cluster for development access. Press Ctrl+C to close the tunnel.

port-forward-prometheus:
	kubectl port-forward svc/prometheus 9090:9090

port-forward-grafana:
	kubectl port-forward svc/grafana 3000:3000

# ── Canary deployment ─────────────────────────────────────────────────────────

# Start canary: build and load the router, then deploy router + v1 + v2.
# The router replaces the single backend Deployment behind the same Service.
# Users select a model in OpenWebUI; the router dispatches to the right backend.
#   facebook/opt-125m  → backend-v1 (stable)
#   facebook/opt-350m  → backend-v2 (canary)
canary-deploy: build-router load-router
	kubectl delete deployment backend --ignore-not-found
	kubectl apply -f k8s/canary/backend-v1.yaml -f k8s/canary/backend-v2.yaml -f k8s/canary/router.yaml
	@echo ""
	@echo "Canary deployed (model-routing mode):"
	@echo "  facebook/opt-125m  → backend-v1 (stable)"
	@echo "  facebook/opt-350m  → backend-v2 (canary)"
	@echo "Select a model in OpenWebUI to route to a specific backend."

# Roll BACK: remove v2 from the routing table, then scale it down.
# The router returns 404 for facebook/opt-125m-v2; all chat goes to v1.
canary-rollback:
	kubectl set env deployment/router ROUTES="facebook/opt-125m=http://backend-v1:8000"
	kubectl scale deployment backend-v2 --replicas=0
	@echo "Rolled back — only facebook/opt-125m (v1) available"

# Roll FORWARD: v2 becomes the sole stable model under its own name.
# facebook/opt-125m is retired; facebook/opt-350m is the new standard.
# Users must select the new model name in OpenWebUI.
canary-forward:
	kubectl set env deployment/router ROUTES="facebook/opt-350m=http://backend-v2:8000"
	kubectl scale deployment backend-v1 --replicas=0
	@echo "Rolled forward — facebook/opt-350m is now the only available model"
