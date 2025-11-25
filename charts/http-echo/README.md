# http-echo

![Version: 1.0.0](https://img.shields.io/badge/Version-1.0.0-informational?style=flat-square) ![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square) ![AppVersion: 1.0](https://img.shields.io/badge/AppVersion-1.0-informational?style=flat-square)

A Helm chart that deploys two http-echo instances (foo and bar) with shared TLS certificate,
ingress routing, and horizontal pod autoscaling support. Designed for testing and demonstration
of Kubernetes ingress, TLS termination, and load balancing capabilities.

**Homepage:** <https://github.com/dehtiarone/gdnts-ci>

## Prerequisites

- Kubernetes >=1.28.0-0
- Helm 3.x
- cert-manager (for TLS certificate provisioning)
- nginx-ingress controller (for ingress routing)

## Installation

### Add the repository (if published)

```bash
helm repo add gdnts-ci https://dehtiarone.github.io/gdnts-ci/charts
helm repo update
```

### Install the chart

```bash
# Install with default values
helm install http-echo gdnts-ci/http-echo

# Install in a specific namespace
helm install http-echo gdnts-ci/http-echo -n my-namespace --create-namespace

# Install with custom values
helm install http-echo gdnts-ci/http-echo -f my-values.yaml
```

### Install from local directory

```bash
helm install http-echo ./charts/http-echo -n my-namespace --create-namespace
```

## Uninstallation

```bash
helm uninstall http-echo -n my-namespace
```

## Architecture

This chart deploys two separate http-echo instances:

```
                    ┌─────────────────┐
                    │  Ingress        │
                    │  (nginx)        │
                    └────────┬────────┘
                             │
              ┌──────────────┴──────────────┐
              │                             │
              ▼                             ▼
    ┌─────────────────┐           ┌─────────────────┐
    │ foo.localhost   │           │ bar.localhost   │
    ├─────────────────┤           ├─────────────────┤
    │ Service: foo    │           │ Service: bar    │
    ├─────────────────┤           ├─────────────────┤
    │ Deployment: foo │           │ Deployment: bar │
    │ (2+ replicas)   │           │ (2+ replicas)   │
    └─────────────────┘           └─────────────────┘
              │                             │
              └──────────────┬──────────────┘
                             │
                    ┌────────┴────────┐
                    │ TLS Certificate │
                    │ (cert-manager)  │
                    └─────────────────┘
```

## Features

- **Two independent deployments**: foo and bar with separate scaling
- **TLS termination**: Automatic certificate provisioning via cert-manager
- **Horizontal Pod Autoscaler**: Optional CPU-based autoscaling
- **NetworkPolicy**: Pod isolation with ingress-only access
- **Security hardened**: Non-root user, seccomp profile, read-only filesystem

## Examples

### Enable HPA for both deployments

```yaml
foo:
  hpa:
    enabled: true
    minReplicas: 2
    maxReplicas: 10
    targetCPUUtilizationPercentage: 70

bar:
  hpa:
    enabled: true
    minReplicas: 2
    maxReplicas: 10
    targetCPUUtilizationPercentage: 70
```

### Custom resource limits

```yaml
foo:
  resources:
    requests:
      cpu: 100m
      memory: 64Mi
    limits:
      cpu: 500m
      memory: 128Mi
```

### Disable TLS (not recommended)

```yaml
ingress:
  tls:
    enabled: false
  annotations: {}

certificate:
  enabled: false
```

### Use external certificate

```yaml
certificate:
  enabled: false

ingress:
  tls:
    enabled: true
    secretName: my-existing-tls-secret
```

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| Vladyslav Dehtiar |  | <https://github.com/dehtiarone> |

## Source Code

* <https://github.com/dehtiarone/gdnts-ci/tree/main/charts/http-echo>

## Requirements

Kubernetes: `>=1.28.0-0`

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| bar | object | `{"host":"bar.localhost","hpa":{"enabled":false,"maxReplicas":3,"minReplicas":2,"targetCPUUtilizationPercentage":65},"replicas":2,"resources":{},"text":"bar"}` | @section Bar Deployment Configuration Configuration for the "bar" http-echo deployment |
| bar.host | string | `"bar.localhost"` | Hostname for ingress routing to bar service |
| bar.hpa | object | `{"enabled":false,"maxReplicas":3,"minReplicas":2,"targetCPUUtilizationPercentage":65}` | Horizontal Pod Autoscaler configuration for bar |
| bar.hpa.enabled | bool | `false` | Enable HPA for bar deployment |
| bar.hpa.maxReplicas | int | `3` | Maximum number of replicas |
| bar.hpa.minReplicas | int | `2` | Minimum number of replicas |
| bar.hpa.targetCPUUtilizationPercentage | int | `65` | Target CPU utilization percentage for scaling |
| bar.replicas | int | `2` | Number of replicas for bar deployment (ignored if HPA enabled) |
| bar.resources | object | Uses shared `resources` if not specified | Resource requests and limits for bar pods |
| bar.text | string | `"bar"` | Text response returned by the bar endpoint |
| certificate | object | `{"dnsNames":["foo.localhost","bar.localhost","foo.cluster.local","bar.cluster.local"],"duration":"2160h","enabled":true,"issuerRef":{"kind":"ClusterIssuer","name":"selfsigned-cluster-issuer"},"renewBefore":"360h"}` | @section Certificate Configuration Uses cert-manager to provision TLS certificates |
| certificate.dnsNames | list | foo and bar hostnames for localhost and cluster.local | DNS names to include in the certificate |
| certificate.duration | string | `"2160h"` | Certificate validity duration |
| certificate.enabled | bool | `true` | Enable cert-manager Certificate resource |
| certificate.issuerRef | object | `{"kind":"ClusterIssuer","name":"selfsigned-cluster-issuer"}` | Certificate issuer reference |
| certificate.issuerRef.kind | string | `"ClusterIssuer"` | Kind of issuer (ClusterIssuer or Issuer) |
| certificate.issuerRef.name | string | `"selfsigned-cluster-issuer"` | Name of the ClusterIssuer or Issuer |
| certificate.renewBefore | string | `"360h"` | Time before expiry to renew certificate |
| foo | object | `{"host":"foo.localhost","hpa":{"enabled":false,"maxReplicas":3,"minReplicas":2,"targetCPUUtilizationPercentage":65},"replicas":2,"resources":{},"text":"foo"}` | @section Foo Deployment Configuration Configuration for the "foo" http-echo deployment |
| foo.host | string | `"foo.localhost"` | Hostname for ingress routing to foo service |
| foo.hpa | object | `{"enabled":false,"maxReplicas":3,"minReplicas":2,"targetCPUUtilizationPercentage":65}` | Horizontal Pod Autoscaler configuration for foo |
| foo.hpa.enabled | bool | `false` | Enable HPA for foo deployment |
| foo.hpa.maxReplicas | int | `3` | Maximum number of replicas |
| foo.hpa.minReplicas | int | `2` | Minimum number of replicas |
| foo.hpa.targetCPUUtilizationPercentage | int | `65` | Target CPU utilization percentage for scaling |
| foo.replicas | int | `2` | Number of replicas for foo deployment (ignored if HPA enabled) |
| foo.resources | object | Uses shared `resources` if not specified | Resource requests and limits for foo pods |
| foo.text | string | `"foo"` | Text response returned by the foo endpoint |
| image.pullPolicy | string | `"Always"` | Image pull policy. Set to Always for production |
| image.repository | string | `"hashicorp/http-echo"` | Container image repository |
| image.tag | string | `v1.0` | Container image tag |
| ingress | object | `{"annotations":{"nginx.ingress.kubernetes.io/force-ssl-redirect":"true","nginx.ingress.kubernetes.io/ssl-redirect":"true"},"className":"nginx","enabled":true,"tls":{"enabled":true,"secretName":"http-echo-tls"}}` | @section Ingress Configuration |
| ingress.annotations | object | SSL redirect annotations for nginx | Additional ingress annotations |
| ingress.className | string | `"nginx"` | Ingress class name (e.g., nginx, traefik) |
| ingress.enabled | bool | `true` | Enable ingress resource creation |
| ingress.tls | object | `{"enabled":true,"secretName":"http-echo-tls"}` | TLS configuration for ingress |
| ingress.tls.enabled | bool | `true` | Enable TLS termination |
| ingress.tls.secretName | string | `"http-echo-tls"` | Name of the TLS secret (created by cert-manager if certificate.enabled) |
| networkPolicy | object | `{"enabled":true}` | @section Network Policy Configuration |
| networkPolicy.enabled | bool | `true` | Enable NetworkPolicy for pod-to-pod communication control Allows ingress from ingress-nginx namespace and egress for DNS |
| podDisruptionBudget | object | `{"enabled":false,"minAvailable":1}` | @section Pod Disruption Budget |
| podDisruptionBudget.enabled | bool | `false` | Enable PodDisruptionBudget |
| podDisruptionBudget.minAvailable | int | `1` | Minimum number of pods that must be available |
| resources | object | `{"limits":{"cpu":"50m","memory":"32Mi"},"requests":{"cpu":"10m","memory":"16Mi"}}` | Default resource requests and limits for all pods Used when foo.resources or bar.resources are not specified |
| resources.limits.cpu | string | `"50m"` | CPU limit |
| resources.limits.memory | string | `"32Mi"` | Memory limit |
| resources.requests.cpu | string | `"10m"` | CPU request |
| resources.requests.memory | string | `"16Mi"` | Memory request |
| service | object | `{"port":5678,"type":"ClusterIP"}` | @section Service Configuration |
| service.port | int | `5678` | Service port (http-echo listens on this port) |
| service.type | string | `"ClusterIP"` | Kubernetes service type |
| serviceAccount | object | `{"create":true,"name":""}` | @section Service Account Configuration |
| serviceAccount.create | bool | `true` | Create a service account |
| serviceAccount.name | string | Generated using fullname template | Service account name (auto-generated if empty) |
| tolerations | list | `[]` (no tolerations) | Tolerations for pod scheduling |

## Security Considerations

This chart implements several security best practices:

| Feature | Description |
|---------|-------------|
| Non-root user | Containers run as UID 10000 |
| Read-only filesystem | Container filesystem is read-only |
| Seccomp profile | RuntimeDefault seccomp profile applied |
| No privilege escalation | `allowPrivilegeEscalation: false` |
| Dropped capabilities | All Linux capabilities dropped |
| NetworkPolicy | Restricts pod-to-pod communication |
| Service account | `automountServiceAccountToken: false` |

## Troubleshooting

### Pods not starting

Check if the cert-manager ClusterIssuer exists:

```bash
kubectl get clusterissuer selfsigned-cluster-issuer
```

### Certificate not ready

Check certificate status:

```bash
kubectl get certificate http-echo-tls -o yaml
kubectl describe certificate http-echo-tls
```

### Ingress not routing

Verify ingress controller is running:

```bash
kubectl get pods -n ingress-nginx
kubectl get ingress http-echo -o yaml
```

----------------------------------------------
Autogenerated from chart metadata using [helm-docs](https://github.com/norwoodj/helm-docs)
