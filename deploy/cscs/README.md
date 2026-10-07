# CSCS eoAPI Dev Deployment

This deploys eoAPI to the `eoapi-dev` namespace on the `c2sm-tds` cluster with Traefik ingress, TLS, IP allowlisting, and retained NVMe-backed Crunchy Postgres storage.

Apply the Traefik middleware first:

```bash
kubectl apply -f deploy/cscs/traefik-ipallowlist.yaml
```

Install or upgrade the release - on this branch, in the order given in
`deploy/cscs/auth-keycloak/README.md` ("Deploy for testing"):

```bash
helm upgrade --install eoapi ./charts/eoapi \
  -n eoapi-dev \
  --create-namespace \
  -f deploy/cscs/values-cscs-dev.yaml \
  -f deploy/cscs/values-cscs-dev-stac-auth-proxy.yaml \
  -f deploy/cscs/values-cscs-dev-auth-keycloak.yaml \
  --set gitSha=$(git rev-parse HEAD | cut -c1-10)
```

Render locally before applying:

```bash
helm template eoapi ./charts/eoapi \
  -n eoapi-dev \
  -f deploy/cscs/values-cscs-dev.yaml \
  -f deploy/cscs/values-cscs-dev-stac-auth-proxy.yaml \
  -f deploy/cscs/values-cscs-dev-auth-keycloak.yaml \
  --set gitSha=$(git rev-parse HEAD | cut -c1-10)
```

The ingress middleware annotation uses Traefik's Kubernetes CRD reference form:

```text
eoapi-dev-eoapi-dev-ipallowlist@kubernetescrd
```

## CSCS Keycloak Auth (in test)

`deploy/cscs/auth-keycloak/` is the auth path on this branch. CSCS's
production Keycloak (`auth.cscs.ch`, realm `cscs`, client `prometheus-tds`) is the
OIDC provider and the shared SSO session; oauth2-proxy, using that confidential
client, is the Traefik ForwardAuth gate for `/raster`, `/vector`, `/multidim`
and the `/` doc server. `/stac` authenticates itself via stac-auth-proxy,
`/browser` is public static UI, and narthex sits outside the gate as its own
OIDC client. The IP allowlist stays on everything.

Every IdP-facing value is in `values-cscs-dev-auth-keycloak.yaml`, endpoints
and client ID in its `keycloak:` section; the gate's client secret is the
`oauth2-proxy-keycloak-secret` Secret. Authelia stays deployed, unreferenced,
until this is accepted, so `main` can be redeployed at any time.

`deploy/cscs/auth-keycloak/README.md` has what is known about the realm, the
redirect URIs to register, the network precondition (the cluster cannot reach
`auth.cscs.ch` yet), the deploy order, the logs to watch and the rollback.

All three values files, every time: omitting the Keycloak one fails the render,
omitting the stac-auth-proxy one silently disables group filtering and the
browser login button.
