# Makefile
#
# A Makefile gives you short named commands (called "targets") for common tasks.
# Run any target with: make <target>  e.g.  make cluster-up
#
# .PHONY tells make these are not filenames — they're always commands.
# Without it, make would skip a target if a file with the same name existed.

.PHONY: cluster-up cluster-down build load deploy dev-up dev-down status logs

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

# Load the locally built image into the kind cluster.
# WHY: kind runs inside Docker. When K8s tries to pull an image, it looks
# inside the kind node's local image cache — not your Mac's Docker daemon.
# "kind load" copies the image from your Mac into that node's cache.
# This replaces the need for a registry during local development.
load:
	kind load docker-image llm-backend:local --name llm

# Apply all Kubernetes manifests to the cluster.
# "kubectl apply -f" is idempotent — safe to run multiple times.
# It creates resources that don't exist and updates ones that do.
deploy:
	kubectl apply -f k8s/

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
	@echo "  Prometheus  : http://localhost:30090"
	@echo "  Grafana     : http://localhost:30030"

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
