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

`deploy/cscs/auth-authelia/` is the active CSCS dev auth path. Authelia is the OIDC provider, the shared session authority, and the Traefik ForwardAuth gate for most of the stack. It replaces Dex, which had no server-side browser session in any released version — so every distinct OIDC `client_id` forced a fresh login, and stac-browser then narthex meant logging in twice. Authelia keeps one session across clients: the hop is now a single silent authorization round-trip.

Authelia's ForwardAuth gate covers what has no auth layer of its own — `/raster`, `/vector`, `/multidim` and the `/` doc server — and issues the login redirect itself, so no Traefik `errors` middleware is needed there. `/stac` is fronted by stac-auth-proxy and `/browser` is public static UI, both `bypass` rules in Authelia's `access_control`.

oauth2-proxy survives, gating the narthex subdomain only. Authelia's ForwardAuth cannot inject a JWT (it sets `Remote-User`/`Remote-Groups`/`Remote-Email`/`Remote-Name`) and narthex-backend authenticates by validating a bearer token, so oauth2-proxy stays purely for `set_authorization_header`. `auth-authelia/README.md` explains why narthex is not running as its own OIDC client instead, and exactly what to change once it can.

The Traefik IP allowlist stays on everything except Authelia's own host, which must be publicly reachable for Let's Encrypt HTTP-01 and OIDC discovery.

Create the Authelia secret without committing real values:

```bash
SESSION_SECRET=$(openssl rand -hex 64)
STORAGE_ENCRYPTION_KEY=$(openssl rand -hex 64)
OIDC_HMAC_SECRET=$(openssl rand -hex 64)
IDENTITY_VALIDATION_JWT_SECRET=$(openssl rand -hex 64)

openssl genrsa -out oidc-jwks.pem 4096

# users-database.yml carries argon2 password hashes - see
# deploy/cscs/auth-authelia/examples/users-database.yml and the README for
# generating them with `authelia crypto hash generate argon2`.

kubectl -n eoapi-dev create secret generic authelia-secret \
  --from-literal=session-secret="$SESSION_SECRET" \
  --from-literal=storage-encryption-key="$STORAGE_ENCRYPTION_KEY" \
  --from-literal=oidc-hmac-secret="$OIDC_HMAC_SECRET" \
  --from-literal=identity-validation-jwt-secret="$IDENTITY_VALIDATION_JWT_SECRET" \
  --from-file=oidc-jwks.pem=./oidc-jwks.pem \
  --from-file=users-database.yml=./users-database.yml
```

`stac-browser` is a public client using PKCE and has no secret. oauth2-proxy's `eoapi-narthex` client is confidential; Authelia stores only a `$pbkdf2-sha512$` digest of its secret, injected from the same Secret at startup, so neither the secret nor the digest is in git. See `auth-authelia/README.md` for both commands.

Apply the auth manifests and the narthex ingress:

```bash
kubectl apply -f deploy/cscs/auth-authelia/
kubectl apply -f deploy/cscs/narthex-ingress.yaml
```

Install or upgrade with **all three** values files. Omitting the stac-auth-proxy overlay silently disables group filtering *and* the browser login button:

```bash
helm upgrade --install eoapi ./charts/eoapi \
  -n eoapi-dev \
  --create-namespace \
  -f deploy/cscs/values-cscs-dev.yaml \
  -f deploy/cscs/values-cscs-dev-stac-auth-proxy.yaml \
  -f deploy/cscs/values-cscs-dev-auth-authelia.yaml \
  --set gitSha=$(git rev-parse HEAD | cut -c1-10)
```

See `deploy/cscs/auth-authelia/README.md` for the discovery URL, the local users and their slash-prefixed groups, the non-obvious per-client OIDC settings (opaque-vs-JWT access tokens, audience granting, claims policies), validation commands, and why bearer-token curl testing is no longer available.
