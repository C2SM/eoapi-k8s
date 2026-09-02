# CSCS Dev Dex/OIDC Auth

This directory is the active CSCS dev auth mode. It adds a local Dex OIDC provider, oauth2-proxy, and Traefik ForwardAuth/errors middlewares:

```text
Dex local OIDC provider
  -> oauth2-proxy
  -> Traefik ForwardAuth
  -> eoAPI / stac-fastapi / FastAPI services
```

It is intended for development only. Browser login and bearer-token curl testing behave more like the future CSCS Keycloak setup, but users are local Dex static-password users.

## Hosts

Dex is served at the root of:

```text
https://dex-prometheus-dev.c2sm-tds.c2sm.cscs.ch
```

The Dex issuer and discovery URL are:

```text
Issuer: https://dex-prometheus-dev.c2sm-tds.c2sm.cscs.ch
Discovery: https://dex-prometheus-dev.c2sm-tds.c2sm.cscs.ch/.well-known/openid-configuration
```

eoAPI still uses this oauth2-proxy callback:

```text
https://prometheus-dev.c2sm-tds.c2sm.cscs.ch/oauth2/callback
```

Dex discovery must be reachable from the oauth2-proxy pod at the public issuer URL. The Dex ingress is therefore not protected by the IP allowlist or ForwardAuth middleware. eoAPI and `/oauth2` remain protected by the existing IP allowlist, and eoAPI remains protected by oauth2-proxy ForwardAuth.

## Groups

This stack pins Dex to `ghcr.io/dexidp/dex:v2.45.1`. Dex `v2.45.x` supports `groups` on `staticPasswords`, so the example static users include Keycloak-style slash-prefixed groups:

```text
/eoapi-dev-users
/eoapi-admin
/eoapi-noaa
/nasa-users
/dyamond-users
```

oauth2-proxy requests `openid email profile groups` and allows `/eoapi-dev-users` globally. This is still whole-site authorization only. Dataset-level filtering is not implemented here.

The current static users are:

```text
kservis@example.org         kservis         /eoapi-dev-users,/eoapi-admin
noaa-reader@example.org     noaa-reader     /eoapi-dev-users,/eoapi-noaa
nasa-reader@example.org     nasa-reader     /eoapi-dev-users,/nasa-users
dyamond-reader@example.org  dyamond-reader  /eoapi-dev-users,/dyamond-users
```

## Secrets

Do not commit real secrets. `examples/` only documents the required keys.

This dev stack uses Dex memory storage, so no storage or signing secret is required. Restarting Dex can invalidate local sessions and signing keys, which is acceptable for this disposable dev mode.

Generate a Dex client secret, oauth2-proxy cookie secret, and bcrypt password hashes:

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
```

Create the Kubernetes secrets:

```bash
kubectl create namespace eoapi-dev --dry-run=client -o yaml | kubectl apply -f -

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

`htpasswd` is commonly provided by `apache2-utils` or `httpd-tools`.

## Deploy

Apply the IP allowlist and Dex auth manifests:

```bash
kubectl apply -f deploy/cscs/traefik-ipallowlist.yaml
kubectl apply -f deploy/cscs/auth-dex/
```

Install or upgrade eoAPI with the Dex auth overlay after the base CSCS values:

```bash
helm upgrade --install eoapi ./charts/eoapi \
  -n eoapi-dev \
  --create-namespace \
  -f deploy/cscs/values-cscs-dev.yaml \
  -f deploy/cscs/values-cscs-dev-auth-dex.yaml \
  --set gitSha=$(git rev-parse HEAD | cut -c1-10)
```

The eoAPI ingress middleware chain is:

```text
eoapi-dev-eoapi-dev-ipallowlist@kubernetescrd,
eoapi-dev-eoapi-dev-oauth-errors@kubernetescrd,
eoapi-dev-eoapi-dev-oauth-forwardauth@kubernetescrd,
eoapi-dev-eoapi-strip-prefix-middleware@kubernetescrd
```

The `/oauth2` ingress is separate and only uses the IP allowlist middleware, avoiding a ForwardAuth login loop.

The Dex ingress is also separate and intentionally has no auth middleware. If Dex discovery returns `403 Forbidden` during oauth2-proxy startup, check that the Dex ingress does not reference `eoapi-dev-eoapi-dev-ipallowlist@kubernetescrd`.

After changing the Dex ingress middleware, apply it and restart oauth2-proxy so OIDC discovery is retried immediately:

```bash
kubectl apply -f deploy/cscs/auth-dex/dex-ingress.yaml
kubectl -n eoapi-dev rollout restart deployment/eoapi-dev-oauth2-proxy
```

## Validate

From an IP allowed by `deploy/cscs/traefik-ipallowlist.yaml`, check:

```bash
kubectl -n eoapi-dev get pods,svc,ingress,middleware | grep -E 'dex|oauth'
curl -i https://prometheus-dev.c2sm-tds.c2sm.cscs.ch/oauth2/auth
curl -s https://dex-prometheus-dev.c2sm-tds.c2sm.cscs.ch/.well-known/openid-configuration | jq
```

The unauthenticated `/oauth2/auth` response should be `401`. Browser access to `https://prometheus-dev.c2sm-tds.c2sm.cscs.ch/browser/` should redirect to Dex login. After login, `/browser/` should load.

## Backend Header Verification

oauth2-proxy sets auth-request headers and the Traefik ForwardAuth middleware forwards them to backends:

```text
X-Auth-Request-User
X-Auth-Request-Email
X-Auth-Request-Groups
X-Auth-Request-Preferred-Username
```

If eoAPI does not already expose a debug endpoint that shows request headers, temporarily route an echo backend through the same ForwardAuth middleware:

```bash
kubectl -n eoapi-dev create deployment auth-header-echo \
  --image=ealen/echo-server:0.9.2 \
  --port=80

kubectl -n eoapi-dev expose deployment auth-header-echo \
  --port=80 \
  --target-port=80

cat <<'EOF' | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: auth-header-echo
  namespace: eoapi-dev
  annotations:
    traefik.ingress.kubernetes.io/router.entrypoints: "websecure"
    traefik.ingress.kubernetes.io/router.tls: "true"
    traefik.ingress.kubernetes.io/router.middlewares: "eoapi-dev-eoapi-dev-ipallowlist@kubernetescrd,eoapi-dev-eoapi-dev-oauth-errors@kubernetescrd,eoapi-dev-eoapi-dev-oauth-forwardauth@kubernetescrd"
spec:
  ingressClassName: traefik
  rules:
    - host: prometheus-dev.c2sm-tds.c2sm.cscs.ch
      http:
        paths:
          - path: /auth-header-echo
            pathType: Prefix
            backend:
              service:
                name: auth-header-echo
                port:
                  number: 80
  tls:
    - hosts:
        - prometheus-dev.c2sm-tds.c2sm.cscs.ch
      secretName: eoapi-dev-tls
EOF
```

After logging in as `kservis`, open:

```text
https://prometheus-dev.c2sm-tds.c2sm.cscs.ch/auth-header-echo
```

Confirm the echo response includes values like:

```text
X-Auth-Request-User: kservis
X-Auth-Request-Email: kservis@example.org
X-Auth-Request-Groups: /eoapi-dev-users,/eoapi-admin
X-Auth-Request-Preferred-Username: kservis
```

Remove the temporary echo resources after testing:

```bash
kubectl -n eoapi-dev delete ingress auth-header-echo
kubectl -n eoapi-dev delete service auth-header-echo
kubectl -n eoapi-dev delete deployment auth-header-echo
```

## Bearer-Token Curl Testing

Dex mode enables oauth2-proxy `skip_jwt_bearer_tokens = true` for dev API testing. Obtain an ID token from Dex with the local password grant:

```bash
TOKEN_RESPONSE=$(curl -s \
  -u "eoapi-dev:$DEX_CLIENT_SECRET" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'grant_type=password' \
  --data-urlencode 'scope=openid email profile groups' \
  --data-urlencode 'username=kservis@example.org' \
  --data-urlencode "password=$KSERVIS_PASSWORD" \
  https://dex-prometheus-dev.c2sm-tds.c2sm.cscs.ch/token)

TOKEN=$(echo "$TOKEN_RESPONSE" | jq -r '.id_token')

curl -H "Authorization: Bearer $TOKEN" \
  https://prometheus-dev.c2sm-tds.c2sm.cscs.ch/stac/collections
```

This token flow is for development smoke testing only. Production CSCS Keycloak should use the approved user/device/client flow for obtaining tokens.

## Future CSCS Keycloak Mode

When CSCS Keycloak is ready, remove this local Dex stack. The replacement should mostly change:

- replace the Dex issuer URL with the CSCS Keycloak issuer URL
- replace the Dex client secret with the CSCS Keycloak client secret
- remove Dex static users
- source groups from CSCS Keycloak
- optionally change oauth2-proxy provider from generic `oidc` to `keycloak-oidc`

Downstream FastAPI authorization can continue consuming the same `X-Auth-Request-Groups` style headers. The oauth2-proxy ForwardAuth/errors structure and eoAPI ingress middleware chain should remain broadly the same.
