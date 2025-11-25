# gdnts CI Repository

End-to-end Kubernetes CI/CD pipeline with load testing and observability.

## Features

- Declarative approach to deploy kind cluster, required components such as `ingress-nginx` using [Link text Here](https://link-url-here.org)
- Secure helm chart for provisioning apps - 100% checkov compliant
- CPU/RAM and load tests results stored in Prometheus
- Advanced script for extraction of data from Prometheus and generation of test reports
- Advanced scheduling approach (taints/tolerations) to distinct generic cluster infrastructure vs applications to reduce overlap during load testings


## Purpose

Demonstrates a production-grade CI pipeline that:
- Provisions a multi-node Kubernetes cluster (Kind)
- Deploys infrastructure (Ingress, TLS, Monitoring)
- Deploys sample applications with TLS termination
- Runs load tests with real-time metrics collection
- Generates performance reports (HTML + JUnit)

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    GitHub Actions / Local                   │
├─────────────────────────────────────────────────────────────┤
│  Makefile (orchestration)                                   │
│    ├── setup      → Install CLI tools (checksummed)         │
│    ├── cluster    → Kind cluster (1 control + 2 workers)    │
│    ├── infra      → Garden deploys Helm charts              │
│    ├── app        → Garden deploys http-echo                │
│    ├── validate   → Garden runs validation tasks            │
│    ├── loadtest   → k6 with Prometheus remote write         │
│    ├── reports    → Python generates HTML/JUnit             │
│    └── cleanup    → Tear down cluster                       │
├─────────────────────────────────────────────────────────────┤
│  Kind Cluster                                               │
│    ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│    │ control-pln │  │  worker-1   │  │  worker-2   │        │
│    │ - ingress   │  │ - foo pods  │  │ - bar pods  │        │
│    │ - cert-mgr  │  │             │  │             │        │
│    │ - prometheus│  │             │  │             │        │
│    └─────────────┘  └─────────────┘  └─────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

## Usage

### Local Development

```bash
# Full pipeline (setup → cluster → infra → app → validate → loadtest → reports)
make all

# Individual steps
make setup      # Install CLI tools with checksum verification
make cluster    # Create Kind cluster with 3 nodes
make infra      # Deploy ingress, cert-manager, prometheus
make app        # Deploy http-echo application
make validate   # Run Garden validation tasks
make loadtest   # Run k6 load test (4 minutes)
make reports    # Generate HTML and JUnit reports
make cleanup    # Tear down cluster and clean workspace

# View reports
open reports/output/report.html
```

### GitHub Actions

Pipeline runs automatically on:
- Push to `main` or `master` branch
- Pull requests to `main` or `master`
- Manual trigger via `workflow_dispatch`

**Manual trigger options:**
- `skip_cleanup`: Keep cluster after run for debugging

**Artifacts produced:**
- `html-report`: Human-readable performance report
- `junit-report`: JUnit XML for GitHub Actions integration

## Execution Sequence

```
1. setup
   └── Downloads kind, kubectl, helm, garden, k6
   └── Verifies SHA256 checksums for each binary
   └── Installs to .tools/bin/

2. cluster
   └── Creates Kind cluster from kind-config.yaml
   └── Topology: 1 control-plane + 2 worker nodes
   └── Port mappings: 8080→80, 8443→443
   └── Waits for all nodes to be Ready

3. infra
   ├── deploy-ingress
   │   └── Helm: ingress-nginx (NodePort mode)
   │   └── Garden task: validate-ingress-ready
   │
   ├── deploy-certmanager
   │   └── Helm: cert-manager
   │   └── Wait: cert-manager-webhook available
   │   └── K8s manifests: ClusterIssuer (self-signed CA)
   │
   └── deploy-prometheus
       └── kubectl: Prometheus CRDs from GitHub
       └── Helm: kube-prometheus-stack
       └── Garden task: validate-prometheus-ready

4. app
   └── Helm: http-echo chart (local)
   └── Creates foo deployment (2 replicas)
   └── Creates bar deployment (2 replicas)
   └── Creates shared TLS certificate
   └── Creates ingress routing rules

5. validate
   └── Garden: validate-http-echo-ready
   │   └── Waits for foo/bar deployments
   │   └── Waits for TLS certificate
   └── Garden: validate-ingress
       └── In-cluster curl tests to foo.localhost
       └── In-cluster curl tests to bar.localhost

6. loadtest
   └── k6 run with Prometheus remote write
   └── 4-minute test: 0→20→40→80→160→80→0 VUs
   └── Metrics pushed to Prometheus every 5s
   └── Targets: foo.localhost, bar.localhost

7. reports
   └── Python queries Prometheus API
   └── Collects: request rates, latencies, error rates
   └── Collects: CPU and memory usage per pod
   └── Generates HTML report with SVG graphs
   └── Generates JUnit XML for CI integration

8. cleanup
   └── Deletes Kind cluster
   └── Removes .tools/, .kube/, .garden/
   └── Removes reports/.venv/, reports/output/
```

## Configuration Files

### Kind Cluster (`kind-config.yaml`)
- 3 nodes: 1 control-plane + 2 workers
- Control-plane: runs infrastructure (tolerations applied)
- Workers: run application pods (workload=apps taint)
- Port mappings: localhost:8080→80, localhost:8443→443

### Prometheus (`infra/prometheus/values.yaml`)
- Scrape interval: 5 seconds
- Evaluation interval: 5 seconds
- Remote write receiver: enabled (for k6 metrics)
- Retention: 1 hour, 1GB max
- Grafana: disabled (metrics-only for CI)

### Load Test (`loadtest/scripts/load.js`)
- 7 VU stages over 4 minutes
- Peak: 160 concurrent virtual users
- Targets: foo.localhost, bar.localhost (round-robin)
- Thresholds: p95 < 500ms, error rate < 1%

### Application (`charts/http-echo/`)
- foo service: returns "foo" on HTTP request
- bar service: returns "bar" on HTTP request
- HPA: 2-3 replicas based on CPU utilization
- TLS: Certificate issued by self-signed CA

## Tools & Versions

| Tool | Version | Purpose |
|------|---------|---------|
| Kind | v0.30.0 | Local Kubernetes clusters |
| kubectl | v1.34.2 | Kubernetes CLI |
| Helm | v3.19.2 | Package manager for Kubernetes |
| Garden | 0.14.9 | Deployment orchestration |
| k6 | v1.4.0 | Load testing |
| Python | 3.11+ | Report generation |

All tools are downloaded with SHA256 checksum verification for supply chain security.

## Bill of Materials

### Helm Charts

| Chart | Version | Repository | Purpose |
|-------|---------|------------|---------|
| ingress-nginx | 4.14.0 | kubernetes.github.io/ingress-nginx | Ingress controller |
| cert-manager | v1.19.1 | charts.jetstack.io | TLS certificate management |
| kube-prometheus-stack | 79.7.1 | prometheus-community | Monitoring & metrics |

### Container Images

| Image | Tag | Purpose |
|-------|-----|---------|
| hashicorp/http-echo | 1.0 | Test application (foo/bar services) |
| curlimages/curl | 8.5.0 | Validation tests |

### Prometheus Operator CRDs

| Component | Version |
|-----------|---------|
| prometheus-operator | v0.86.2 |

## Dependencies & Upgrades

### System Requirements

- Docker (for Kind)
- Python 3.11+ (for reports)
- bash shell
- curl, tar, unzip (for tool installation)

### Upgrading Tool Versions

1. **Update version** in `Makefile`:
   ```makefile
   KIND_VERSION := v0.31.0  # New version
   ```

2. **Get new checksum** from official release:
   ```bash
   # Kind example
   curl -sL https://github.com/kubernetes-sigs/kind/releases/download/v0.31.0/kind-linux-amd64.sha256sum

   # Helm example
   curl -sL https://get.helm.sh/helm-v3.20.0-linux-amd64.tar.gz.sha256sum
   ```

3. **Update checksum** in `Makefile`:
   ```makefile
   KIND_SHA256_linux_amd64 := <new-checksum>
   KIND_SHA256_linux_arm64 := <new-checksum>
   KIND_SHA256_darwin_amd64 := <new-checksum>
   KIND_SHA256_darwin_arm64 := <new-checksum>
   ```

4. **Re-run setup** to download new version:
   ```bash
   rm -rf .tools && make setup
   ```

### Upgrading Helm Charts

1. **Update version** in `project.garden.yml`:
   ```yaml
   variables:
     ingressNginxVersion: "4.15.0"
   ```

2. **Update chart version** in corresponding `garden.yml`:
   ```yaml
   spec:
     chart:
       version: "${var.ingressNginxVersion}"
   ```

3. **For Prometheus CRDs**, update version in `Makefile`:
   ```makefile
   PROM_OP_VERSION := v0.87.0
   ```

## Troubleshooting

### Cluster won't start

```bash
# Check Docker resources (needs ~4GB RAM for 3 nodes)
docker system info | grep -E "Memory|CPUs"

# Check for existing cluster
.tools/bin/kind get clusters

# Force cleanup and recreate
make cleanup && make cluster
```

### Infrastructure deployment fails

```bash
# Check Garden status
.tools/bin/garden get status --env local

# Check Helm releases
.tools/bin/helm list -A --kubeconfig .kube/config

# Check pod status
.tools/bin/kubectl get pods -A --kubeconfig .kube/config
```

### Prometheus not receiving k6 metrics

```bash
# Check remote write endpoint is accessible
curl http://prometheus.localhost:8080/api/v1/status/config

# Check Prometheus is ready
.tools/bin/kubectl get pods -n monitoring --kubeconfig .kube/config
```

### Load test fails to connect

```bash
# Verify ingress is working
curl -H "Host: foo.localhost" http://127.0.0.1:8080/
curl -H "Host: bar.localhost" http://127.0.0.1:8080/

# Check ingress controller logs
.tools/bin/kubectl logs -n ingress-nginx -l app.kubernetes.io/name=ingress-nginx --kubeconfig .kube/config
```

### Reports generation fails

```bash
# Check Python environment
reports/.venv/bin/python --version

# Reinstall dependencies
rm -rf reports/.venv && make reports

# Check Prometheus accessibility
curl http://prometheus.localhost:8080/api/v1/query?query=up
```

## Project Structure

```
gdnts-ci/
├── .github/workflows/
│   └── k8s-ci.yml           # GitHub Actions workflow
├── .tools/                   # Downloaded CLI tools (gitignored)
├── .kube/                    # Kubeconfig for Kind (gitignored)
├── .garden/                  # Garden cache (gitignored)
├── charts/
│   └── http-echo/            # Application Helm chart
│       ├── Chart.yaml
│       ├── garden.yml        # Garden deployment + validation
│       ├── templates/
│       └── values.yaml
├── cluster/
│   └── garden.yml            # Kind lifecycle tasks
├── infra/
│   ├── certmanager/
│   │   ├── garden.yml        # cert-manager deployment
│   │   ├── manifests/        # ClusterIssuer definitions
│   │   └── values.yaml
│   ├── ingress/
│   │   ├── garden.yml        # ingress-nginx deployment + validation
│   │   └── values.yaml
│   └── prometheus/
│       ├── garden.yml        # Prometheus deployment + validation
│       └── values.yaml
├── loadtest/
│   └── scripts/
│       └── load.js           # k6 load test script
├── reports/
│   ├── pyproject.toml        # Python package definition
│   └── scripts/
│       └── generate_reports.py
├── validators/
│   └── garden.yml            # Ingress connectivity validation
├── kind-config.yaml          # Kind cluster configuration
├── project.garden.yml        # Garden project configuration
├── Makefile                  # Build orchestration
└── README.md                 # This file
```
