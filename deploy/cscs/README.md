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

## Dex/OIDC Dev Auth

`deploy/cscs/auth-dex/` is the active CSCS dev auth path. It adds a local Dex OIDC provider, oauth2-proxy, and Traefik ForwardAuth/errors middlewares. The setup keeps the Traefik IP allowlist on eoAPI and `/oauth2`, requests OIDC groups, forwards identity headers to eoAPI backends, and does not implement dataset-level authorization. Dex discovery is intentionally reachable without the IP allowlist so oauth2-proxy can initialize from inside the cluster.

Create the Dex and oauth2-proxy secrets without committing real values:

```bash
DEX_CLIENT_SECRET=$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')
OAUTH2_COOKIE_SECRET=$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')

read -rsp 'Dex kservis password: ' KSERVIS_PASSWORD; echo
KSERVIS_HASH=$(printf '%s\n' "$KSERVIS_PASSWORD" | htpasswd -BinC 10 kservis | cut -d: -f2)

read -rsp 'Dex noaa-reader password: ' NOAA_READER_PASSWORD; echo
NOAA_READER_HASH=$(printf '%s\n' "$NOAA_READER_PASSWORD" | htpasswd -BinC 10 noaa-reader | cut -d: -f2)

read -rsp 'Dex nasa-reader password: ' NASA_READER_PASSWORD; echo
NASA_READER_HASH=$(printf '%s\n' "$NASA_READER_PASSWORD" | htpasswd -BinC 10 nasa-reader | cut -d: -f2)

read -rsp 'Dex dyamond-reader password: ' DYAMOND_READER_PASSWORD; echo
DYAMOND_READER_HASH=$(printf '%s\n' "$DYAMOND_READER_PASSWORD" | htpasswd -BinC 10 dyamond-reader | cut -d: -f2)

kubectl -n eoapi-dev create secret generic dex-secret \
  --from-literal=client-secret="$DEX_CLIENT_SECRET" \
  --from-literal=kservis-user-hash="$KSERVIS_HASH" \
  --from-literal=noaa-reader-user-hash="$NOAA_READER_HASH" \
  --from-literal=nasa-reader-user-hash="$NASA_READER_HASH" \
  --from-literal=dyamond-reader-user-hash="$DYAMOND_READER_HASH"

kubectl -n eoapi-dev create secret generic oauth2-proxy-dex-secret \
  --from-literal=client-id='eoapi-dev' \
  --from-literal=client-secret="$DEX_CLIENT_SECRET" \
  --from-literal=cookie-secret="$OAUTH2_COOKIE_SECRET"
```

Apply the Dex auth manifests:

```bash
kubectl apply -f deploy/cscs/auth-dex/
```

Install or upgrade with the Dex auth overlay after the base CSCS values:

```bash
helm upgrade --install eoapi ./charts/eoapi \
  -n eoapi-dev \
  --create-namespace \
  -f deploy/cscs/values-cscs-dev.yaml \
  -f deploy/cscs/values-cscs-dev-auth-dex.yaml \
  --set gitSha=$(git rev-parse HEAD | cut -c1-10)
```

See `deploy/cscs/auth-dex/README.md` for the Dex discovery URL, local static users, Keycloak-style group examples, validation commands, backend header verification, and bearer-token curl flow.
