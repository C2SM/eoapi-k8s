# CSCS eoAPI Dev Deployment

This deploys eoAPI to the `eoapi-dev` namespace on the `c2sm-tds` cluster with Traefik ingress, TLS, IP allowlisting, and retained NVMe-backed Crunchy Postgres storage.

Apply the Traefik middleware first:

```bash
kubectl apply -f deploy/cscs/traefik-ipallowlist.yaml
```

Install or upgrade the release:

```bash
helm upgrade --install eoapi ./charts/eoapi \
  -n eoapi-dev \
  --create-namespace \
  -f deploy/cscs/values-cscs-dev.yaml \
  --set gitSha=$(git rev-parse HEAD | cut -c1-10)
```

Render locally before applying:

```bash
helm template eoapi ./charts/eoapi \
  -n eoapi-dev \
  -f deploy/cscs/values-cscs-dev.yaml \
  --set gitSha=$(git rev-parse HEAD | cut -c1-10)
```

The ingress middleware annotation uses Traefik's Kubernetes CRD reference form:

```text
eoapi-dev-eoapi-dev-ipallowlist@kubernetescrd
```

## Authelia/OIDC Dev Auth

`deploy/cscs/auth-authelia/` is the active auth path. Authelia is the OIDC
provider, the shared browser session, and the Traefik ForwardAuth gate for
`/raster`, `/vector`, `/multidim` and the `/` doc server. `/stac` authenticates
itself via stac-auth-proxy, `/browser` is public static UI, and narthex sits
outside the gate as its own OIDC client. The IP allowlist stays on everything
except Authelia's own host, which must be publicly reachable for Let's Encrypt
and OIDC discovery.

Secrets, users/groups, the OIDC clients, deploy and validation steps are all in
`deploy/cscs/auth-authelia/README.md`.

```bash
kubectl apply -f deploy/cscs/auth-authelia/
kubectl apply -f deploy/cscs/narthex-ingress.yaml

helm upgrade --install eoapi ./charts/eoapi -n eoapi-dev --create-namespace \
  -f deploy/cscs/values-cscs-dev.yaml \
  -f deploy/cscs/values-cscs-dev-stac-auth-proxy.yaml \
  -f deploy/cscs/values-cscs-dev-auth-authelia.yaml \
  --set gitSha=$(git rev-parse HEAD | cut -c1-10)
```

All three values files, every time: omitting the stac-auth-proxy one silently
disables group filtering and the browser login button.
