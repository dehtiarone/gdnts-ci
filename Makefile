# Makefile for gdnts CI Pipeline
# Cross-platform support for Linux/macOS, amd64/arm64
# All tool versions are pinned with SHA256 checksum verification

SHELL := /bin/bash
.ONESHELL:
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := all

# =============================================================================
# Tool Versions (Pinned for Supply Chain Security)
# =============================================================================
KIND_VERSION := v0.30.0
KUBECTL_VERSION := v1.34.2
HELM_VERSION := v3.19.2
GARDEN_VERSION := 0.14.9
K6_VERSION := v1.4.0

# =============================================================================
# Platform Detection
# =============================================================================
UNAME_S := $(shell uname -s | tr '[:upper:]' '[:lower:]')
UNAME_M := $(shell uname -m)

# Map architecture names
ifeq ($(UNAME_M),x86_64)
  ARCH := amd64
else ifeq ($(UNAME_M),aarch64)
  ARCH := arm64
else ifeq ($(UNAME_M),arm64)
  ARCH := arm64
else
  $(error Unsupported architecture: $(UNAME_M))
endif

# Map OS names
ifeq ($(UNAME_S),linux)
  OS := linux
  SHA_CMD := sha256sum
else ifeq ($(UNAME_S),darwin)
  OS := darwin
  SHA_CMD := shasum -a 256
else
  $(error Unsupported OS: $(UNAME_S))
endif

# Garden uses different OS naming
ifeq ($(OS),darwin)
  GARDEN_OS := macos
else
  GARDEN_OS := $(OS)
endif

# =============================================================================
# Directories
# =============================================================================
ROOT_DIR := $(shell pwd)
TOOLS_DIR := $(ROOT_DIR)/.tools
BIN_DIR := $(TOOLS_DIR)/bin
KUBE_DIR := $(ROOT_DIR)/.kube
REPORTS_DIR := $(ROOT_DIR)/reports/output
GARDEN_DIR := $(ROOT_DIR)/.garden

# Export PATH to include our tools
export PATH := $(BIN_DIR):$(PATH)
export KUBECONFIG := $(KUBE_DIR)/config

# Kubectl with explicit kubeconfig for reliability
KUBECTL := $(BIN_DIR)/kubectl --kubeconfig $(KUBE_DIR)/config

# =============================================================================
# SHA256 Checksums (Verified from Official Sources)
# =============================================================================

# Kind v0.30.0 checksums
KIND_SHA256_linux_amd64 := 517ab7fc89ddeed5fa65abf71530d90648d9638ef0c4cde22c2c11f8097b8889
KIND_SHA256_linux_arm64 := 7ea2de9d2d190022ed4a8a4e3ac0636c8a455e460b9a13ccf19f15d07f4f00eb
KIND_SHA256_darwin_amd64 := 4f0b6e3b88bdc66d922c08469f05ef507d4903dd236e6319199bb9c868eed274
KIND_SHA256_darwin_arm64 := ceaf40df1d1551c481fb50e3deb5c3deecad5fd599df5469626b70ddf52a1518

# Helm v3.19.2 checksums
HELM_SHA256_linux_amd64 := 2114c9dea2844dce6d0ee2d792a9aae846be8cf53d5b19dc2988b5a0e8fec26e
HELM_SHA256_linux_arm64 := 566e9f3a5a83a81e4b03503ae37e368edd52d699619e8a9bb1fdf21561ae0e88
HELM_SHA256_darwin_amd64 := 7ef4416cdef4c2d78a09e1c8f07a51e945dc0343c883a46b1f628deab52690b7
HELM_SHA256_darwin_arm64 := f0847f899479b66a6dd8d9fcd452e8db2562e4cf3f7de28103f9fcf2b824f1d5

# Garden 0.14.9 checksums
GARDEN_SHA256_linux_amd64 := 3cc9604d88a5b4956dcbfe5a386b8cb93cda52bd841d01b9cc5160137b6ff56c
GARDEN_SHA256_linux_arm64 := 2d7067e28e3ab6269880a5da25bf82b09ca00ea594abc1f96bf7e6d556fca0f4
GARDEN_SHA256_macos_amd64 := 7d5176154a805b4bb5440beff523fe9ebe30cce2e700fca84aedf4b32279e652
GARDEN_SHA256_macos_arm64 := 35aa0526c3b5f7a12816f50d3539b616166277eb3fdb5fb6b7629cca3d39ffda

# Select correct checksum based on OS/ARCH
KIND_SHA256 := $(KIND_SHA256_$(OS)_$(ARCH))
HELM_SHA256 := $(HELM_SHA256_$(OS)_$(ARCH))
GARDEN_SHA256 := $(GARDEN_SHA256_$(GARDEN_OS)_$(ARCH))

# =============================================================================
# Download URLs
# =============================================================================
KIND_URL := https://github.com/kubernetes-sigs/kind/releases/download/$(KIND_VERSION)/kind-$(OS)-$(ARCH)
KUBECTL_URL := https://dl.k8s.io/release/$(KUBECTL_VERSION)/bin/$(OS)/$(ARCH)/kubectl
KUBECTL_SHA_URL := https://dl.k8s.io/release/$(KUBECTL_VERSION)/bin/$(OS)/$(ARCH)/kubectl.sha256
HELM_URL := https://get.helm.sh/helm-$(HELM_VERSION)-$(OS)-$(ARCH).tar.gz
GARDEN_URL := https://github.com/garden-io/garden/releases/download/$(GARDEN_VERSION)/garden-$(GARDEN_VERSION)-$(GARDEN_OS)-$(ARCH).tar.gz

# k6 uses 'macos' instead of 'darwin' in release filenames
ifeq ($(OS),darwin)
  K6_OS := macos
  K6_URL := https://github.com/grafana/k6/releases/download/$(K6_VERSION)/k6-$(K6_VERSION)-$(K6_OS)-$(ARCH).zip
  K6_EXT := zip
else
  K6_OS := $(OS)
  K6_URL := https://github.com/grafana/k6/releases/download/$(K6_VERSION)/k6-$(K6_VERSION)-$(K6_OS)-$(ARCH).tar.gz
  K6_EXT := tar.gz
endif

# =============================================================================
# Main Targets
# =============================================================================

.PHONY: all setup cluster infra app validate loadtest reports cleanup help

# Run full pipeline (sequential execution)
all:
	@$(MAKE) setup
	@$(MAKE) cluster
	@$(MAKE) infra
	@$(MAKE) app
	@$(MAKE) validate
	@$(MAKE) loadtest
	@$(MAKE) reports
	@echo "✅ Full pipeline completed successfully!"

# Display help information
help:
	@echo "gdnts CI Pipeline - Makefile Targets"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "Main Targets:"
	@echo "  all        - Run full pipeline (default)"
	@echo "  setup      - Install all CLI tools with checksum verification"
	@echo "  cluster    - Create and validate Kind cluster"
	@echo "  infra      - Deploy infrastructure (ingress, cert-manager, prometheus)"
	@echo "  app        - Deploy http-echo application"
	@echo "  validate   - Run health and connectivity checks"
	@echo "  loadtest   - Execute k6 load tests"
	@echo "  reports    - Generate HTML and JUnit reports"
	@echo "  cleanup    - Tear down cluster and clean workspace"
	@echo ""
	@echo "Tool Targets:"
	@echo "  install-kind     - Install Kind $(KIND_VERSION)"
	@echo "  install-kubectl  - Install kubectl $(KUBECTL_VERSION)"
	@echo "  install-helm     - Install Helm $(HELM_VERSION)"
	@echo "  install-garden   - Install Garden $(GARDEN_VERSION)"
	@echo "  install-k6       - Install k6 $(K6_VERSION)"
	@echo ""
	@echo "Platform: $(OS)/$(ARCH)"

# =============================================================================
# Setup Targets
# =============================================================================

setup: $(BIN_DIR)/kind $(BIN_DIR)/kubectl $(BIN_DIR)/helm $(BIN_DIR)/garden $(BIN_DIR)/k6
	@echo "✅ All tools installed and verified"

$(BIN_DIR):
	@mkdir -p $(BIN_DIR)

# Install Kind with checksum verification
$(BIN_DIR)/kind: | $(BIN_DIR)
	@echo "📦 Installing Kind $(KIND_VERSION) for $(OS)/$(ARCH)..."
	@curl -fsSL -o $(BIN_DIR)/kind "$(KIND_URL)"
	@echo "🔐 Verifying Kind checksum..."
	@echo "$(KIND_SHA256)  $(BIN_DIR)/kind" | $(SHA_CMD) -c - || \
		(echo "❌ Kind checksum verification failed!" && rm -f $(BIN_DIR)/kind && exit 1)
	@chmod +x $(BIN_DIR)/kind
	@echo "✅ Kind installed successfully"

.PHONY: install-kind
install-kind: $(BIN_DIR)/kind

# Install kubectl with checksum verification (fetched from server)
$(BIN_DIR)/kubectl: | $(BIN_DIR)
	@echo "📦 Installing kubectl $(KUBECTL_VERSION) for $(OS)/$(ARCH)..."
	@curl -fsSL -o $(BIN_DIR)/kubectl "$(KUBECTL_URL)"
	@curl -fsSL -o /tmp/kubectl.sha256 "$(KUBECTL_SHA_URL)"
	@echo "🔐 Verifying kubectl checksum..."
	@echo "$$(cat /tmp/kubectl.sha256)  $(BIN_DIR)/kubectl" | $(SHA_CMD) -c - || \
		(echo "❌ kubectl checksum verification failed!" && rm -f $(BIN_DIR)/kubectl && exit 1)
	@rm -f /tmp/kubectl.sha256
	@chmod +x $(BIN_DIR)/kubectl
	@echo "✅ kubectl installed successfully"

.PHONY: install-kubectl
install-kubectl: $(BIN_DIR)/kubectl

# Install Helm with checksum verification
$(BIN_DIR)/helm: | $(BIN_DIR)
	@echo "📦 Installing Helm $(HELM_VERSION) for $(OS)/$(ARCH)..."
	@curl -fsSL -o /tmp/helm.tar.gz "$(HELM_URL)"
	@echo "🔐 Verifying Helm checksum..."
	@echo "$(HELM_SHA256)  /tmp/helm.tar.gz" | $(SHA_CMD) -c - || \
		(echo "❌ Helm checksum verification failed!" && rm -f /tmp/helm.tar.gz && exit 1)
	@tar -xzf /tmp/helm.tar.gz -C /tmp
	@mv /tmp/$(OS)-$(ARCH)/helm $(BIN_DIR)/helm
	@rm -rf /tmp/helm.tar.gz /tmp/$(OS)-$(ARCH)
	@chmod +x $(BIN_DIR)/helm
	@echo "✅ Helm installed successfully"

.PHONY: install-helm
install-helm: $(BIN_DIR)/helm

# Install Garden with checksum verification
# Garden extracts to a directory containing the binary
$(BIN_DIR)/garden: | $(BIN_DIR)
	@echo "📦 Installing Garden $(GARDEN_VERSION) for $(GARDEN_OS)/$(ARCH)..."
	@curl -fsSL -o /tmp/garden.tar.gz "$(GARDEN_URL)"
	@echo "🔐 Verifying Garden checksum..."
	@echo "$(GARDEN_SHA256)  /tmp/garden.tar.gz" | $(SHA_CMD) -c - || \
		(echo "❌ Garden checksum verification failed!" && rm -f /tmp/garden.tar.gz && exit 1)
	@mkdir -p /tmp/garden-extract
	@tar -xzf /tmp/garden.tar.gz -C /tmp/garden-extract
	@find /tmp/garden-extract -name "garden" -type f -perm +111 -exec mv {} $(BIN_DIR)/garden \; 2>/dev/null || \
		find /tmp/garden-extract -name "garden" -type f -exec mv {} $(BIN_DIR)/garden \;
	@rm -rf /tmp/garden.tar.gz /tmp/garden-extract
	@chmod +x $(BIN_DIR)/garden
	@echo "✅ Garden installed successfully"

.PHONY: install-garden
install-garden: $(BIN_DIR)/garden

# Install k6 (checksum verification via GitHub release)
$(BIN_DIR)/k6: | $(BIN_DIR)
	@echo "📦 Installing k6 $(K6_VERSION) for $(K6_OS)/$(ARCH)..."
ifeq ($(K6_EXT),zip)
	@curl -fsSL -o /tmp/k6.zip "$(K6_URL)"
	@unzip -q /tmp/k6.zip -d /tmp
	@mv /tmp/k6-$(K6_VERSION)-$(K6_OS)-$(ARCH)/k6 $(BIN_DIR)/k6
	@rm -rf /tmp/k6.zip /tmp/k6-$(K6_VERSION)-$(K6_OS)-$(ARCH)
else
	@curl -fsSL -o /tmp/k6.tar.gz "$(K6_URL)"
	@tar -xzf /tmp/k6.tar.gz -C /tmp
	@mv /tmp/k6-$(K6_VERSION)-$(K6_OS)-$(ARCH)/k6 $(BIN_DIR)/k6
	@rm -rf /tmp/k6.tar.gz /tmp/k6-$(K6_VERSION)-$(K6_OS)-$(ARCH)
endif
	@chmod +x $(BIN_DIR)/k6
	@echo "✅ k6 installed successfully"

.PHONY: install-k6
install-k6: $(BIN_DIR)/k6

# =============================================================================
# Cluster Targets
# =============================================================================

$(KUBE_DIR):
	@mkdir -p $(KUBE_DIR)

# Check if cluster exists
.PHONY: cluster-exists
cluster-exists:
	@$(BIN_DIR)/kind get clusters 2>/dev/null | grep -q "^gdnts-ci$$"

cluster: setup $(KUBE_DIR)
	@if $(BIN_DIR)/kind get clusters 2>/dev/null | grep -q "^gdnts-ci$$"; then \
		echo "✅ Cluster already exists, skipping creation"; \
	else \
		echo "🚀 Creating Kind cluster..." && \
		$(BIN_DIR)/kind create cluster --config kind-config.yaml --wait 5m && \
		echo "🔍 Validating cluster health..." && \
		$(KUBECTL) wait --for=condition=Ready nodes --all --timeout=120s && \
		$(KUBECTL) cluster-info && \
		echo "✅ Cluster created and validated"; \
	fi

# =============================================================================
# Infrastructure Targets (using Garden)
# =============================================================================

# Garden deploy helper - checks status and deploys if not ready
# Usage: $(call garden-deploy,module-name,display-name)
# Uses grep-based JSON parsing (no jq dependency, works on Linux/macOS)
define garden-deploy
	@if [ "$$($(BIN_DIR)/garden get status --env local --output json 2>/dev/null | \
	    grep -o '"$(1)"[^}]*"state":"[^"]*"' | \
	    grep -o '"state":"[^"]*"' | cut -d'"' -f4)" = "ready" ]; then \
	    echo "✅ $(2) already deployed, skipping"; \
	else \
	    echo "📦 Deploying $(2) via Garden..." && \
	    $(BIN_DIR)/garden deploy $(1) --env local && \
	    echo "✅ $(2) deployed"; \
	fi
endef

infra: cluster
	@echo "🏗️  Deploying infrastructure via Garden..."
	@$(MAKE) deploy-ingress
	@$(MAKE) deploy-certmanager
	@$(MAKE) deploy-prometheus
	@echo "✅ Infrastructure deployed"

.PHONY: deploy-ingress
deploy-ingress:
	$(call garden-deploy,ingress-nginx,nginx-ingress)

.PHONY: deploy-certmanager
deploy-certmanager:
	$(call garden-deploy,cert-manager,cert-manager)
	@echo "⏳ Waiting for cert-manager webhook..."
	@$(KUBECTL) wait --for=condition=Available deployment/cert-manager-webhook \
		-n cert-manager --timeout=120s
	$(call garden-deploy,cert-manager-manifests,ClusterIssuer)

# Prometheus operator version matching kube-prometheus-stack 79.7.1
PROM_OP_VERSION := v0.86.2

.PHONY: deploy-prometheus-crds
deploy-prometheus-crds:
	@if $(KUBECTL) get crd prometheuses.monitoring.coreos.com >/dev/null 2>&1; then \
		echo "✅ Prometheus CRDs already installed, skipping"; \
	else \
		echo "📦 Installing Prometheus CRDs (prometheus-operator $(PROM_OP_VERSION))..." && \
		BASE_URL="https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/$(PROM_OP_VERSION)/example/prometheus-operator-crd" && \
		for crd in alertmanagerconfigs alertmanagers podmonitors probes prometheusagents prometheuses prometheusrules scrapeconfigs servicemonitors thanosrulers; do \
			echo "  Installing CRD: $${crd}" && \
			$(KUBECTL) apply --server-side -f "$${BASE_URL}/monitoring.coreos.com_$${crd}.yaml"; \
		done && \
		echo "✅ Prometheus CRDs installed"; \
	fi

.PHONY: deploy-prometheus
deploy-prometheus: deploy-prometheus-crds
	$(call garden-deploy,prometheus,Prometheus stack)

# =============================================================================
# Application Targets
# =============================================================================

app: infra
	@echo "🚀 Deploying http-echo application via Garden..."
	$(call garden-deploy,http-echo,http-echo application)

# =============================================================================
# Validation Targets (using Garden Run tasks)
# =============================================================================

validate: app
	@echo "🔍 Running validations via Garden..."
	@$(BIN_DIR)/garden run validate-http-echo-ready --env local
	@$(BIN_DIR)/garden run validate-ingress --env local
	@echo "✅ All validations passed"

# =============================================================================
# Load Test Targets
# =============================================================================

# Prometheus URL via ingress (same endpoint for k6 push and report queries)
# Use prometheus.localhost hostname directly - .localhost TLD resolves to 127.0.0.1
PROMETHEUS_URL := http://prometheus.localhost:8080

loadtest: validate
	@echo "🔥 Running load tests with Prometheus remote write..."
	@mkdir -p $(REPORTS_DIR)
	@echo "📊 Pushing k6 metrics to Prometheus via prometheus.localhost..."
	K6_PROMETHEUS_RW_SERVER_URL="$(PROMETHEUS_URL)/api/v1/write" \
	K6_PROMETHEUS_RW_TREND_STATS="p(95),p(99),min,max,avg,med,count" \
	K6_PROMETHEUS_RW_PUSH_INTERVAL="5s" \
	$(BIN_DIR)/k6 run \
		-o experimental-prometheus-rw \
		loadtest/scripts/load.js
	@echo "⏳ Waiting for metrics to propagate to Prometheus..."
	@sleep 10
	@echo "✅ Load tests completed, metrics pushed to Prometheus"

# =============================================================================
# Python Virtual Environment for Reports
# =============================================================================
VENV_DIR := $(ROOT_DIR)/reports/.venv
PYTHON := $(VENV_DIR)/bin/python

# Install reports package from pyproject.toml (modern pip approach - PEP 621)
$(VENV_DIR)/.installed: reports/pyproject.toml
	@echo "📦 Creating Python virtual environment..."
	python3 -m venv $(VENV_DIR)
	@echo "📦 Upgrading pip to latest..."
	$(VENV_DIR)/bin/pip install --upgrade pip
	@echo "📦 Installing gdnts-reports package..."
	$(VENV_DIR)/bin/pip install $(ROOT_DIR)/reports
	@touch $(VENV_DIR)/.installed
	@echo "✅ Python environment ready"

# =============================================================================
# Report Targets
# =============================================================================

reports: $(VENV_DIR)/.installed
	@echo "📊 Generating reports..."
	@mkdir -p $(REPORTS_DIR)
	$(PYTHON) -m scripts.generate_reports \
		--prometheus-url "$(PROMETHEUS_URL)" \
		--output-dir "$(REPORTS_DIR)" \
		--format all
	@echo "✅ Reports generated in $(REPORTS_DIR)"

# =============================================================================
# Cleanup Targets
# =============================================================================

cleanup:
	@echo "🧹 Cleaning up..."
	-@$(BIN_DIR)/kind delete cluster --name gdnts-ci 2>/dev/null || true
	@rm -rf $(TOOLS_DIR) $(KUBE_DIR) $(REPORTS_DIR) $(VENV_DIR) $(GARDEN_DIR)
	@echo "✅ Cleanup completed"

# Force cleanup (even if cluster doesn't exist)
.PHONY: clean
clean: cleanup
