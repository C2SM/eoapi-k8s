# CSCS Dev Authelia/OIDC Auth

This directory is the active CSCS dev auth mode. Authelia is the OIDC provider, the session authority, and the Traefik ForwardAuth gate for most of the stack:

```text
Authelia (OIDC provider + session authority)
  -> Traefik ForwardAuth  -> /raster, /vector, /multidim, / (doc server)
  -> OIDC client          -> stac-browser -> stac-auth-proxy validates the token on /stac
  -> OIDC client          -> oauth2-proxy -> ForwardAuth for the narthex subdomain,
                                             injecting a bearer token for narthex-backend
```

It replaces the previous `auth-dex/` stack. Dex is gone; oauth2-proxy survives in a much smaller role, gating exactly one host — see [Why oauth2-proxy is still here](#why-oauth2-proxy-is-still-here).

Development only: users are local file-based Authelia users rather than CSCS accounts.

## Why Authelia replaced Dex

Dex has no server-side browser session in any released version ([dexidp/dex#4560](https://github.com/dexidp/dex/issues/4560)). Every distinct OIDC `client_id` therefore forced a fresh credential entry: logging into stac-browser and then opening narthex meant **two full logins**. The old stack worked around this by making oauth2-proxy the session authority — a cookie scoped to the parent domain plus `set_authorization_header = true` injecting `Authorization: Bearer <id_token>` upstream — but that could never cover stac-browser's own in-app OIDC login, which talked to Dex directly.

Authelia keeps a real session shared across every client, so a second authorization request completes silently. Measured after the switch: hopping from stac-browser to narthex takes **one silent authorization round-trip, ~700-850 ms, zero login prompts**.

Retired with Dex:

| Old hazard | Status |
|---|---|
| Traefik `errors` middleware rewriting *any* 401/403 from *any* backend into an HTML redirect, so it had to be kept off every API path | Gone. Authelia's ForwardAuth issues the login redirect itself. |
| A router-level `Method(`OPTIONS`)` IngressRoute, because Traefik always calls ForwardAuth with GET (real verb only in `X-Forwarded-Method`) so `skip_auth_preflight` could never fire | Gone. Authelia reads `X-Forwarded-Method`, so an `access_control` `methods: ['OPTIONS']` bypass actually works. |
| `approval_prompt = "auto"`, needed because oauth2-proxy's `"force"` default overrode the IdP's skip-consent setting | Gone. `consent_mode: 'implicit'` per client. |
| Dex's `storage: memory` rotating OIDC signing keys on every restart, which then required restarting anything caching JWKS (narthex's `@lru_cache`d fetch) | Gone. The signing key is a static PEM from the Secret — verified identical across a `rollout restart`. |
| `cookie_domains` / `whitelist_domains` scoping oauth2-proxy's cookie to the parent domain so it could act as session authority for every app | Gone. Authelia holds the shared session; oauth2-proxy's cookie is now host-scoped to the single host it gates. |
| The `stac-browser` Dex client having no group requirement at all | Tightened. Each client carries `authorization_policy: 'eoapi_users'`. |

The first two rows apply to the eoAPI host only. The narthex host still runs oauth2-proxy, so its `errors` middleware and its `Method(`OPTIONS`)` bypass IngressRoute are still required there — see `../narthex-ingress.yaml`.

## Why oauth2-proxy is still here

Authelia's ForwardAuth sets `Remote-User` / `Remote-Groups` / `Remote-Email` / `Remote-Name` and **cannot inject a JWT**. narthex-backend authenticates by validating a bearer token, so something has to put one on the request. oauth2-proxy's `set_authorization_header` is that something, and it now does nothing else: it gates one host, as an Authelia client (`eoapi-narthex`), with a host-scoped cookie.

The intended alternative was `narthex-frontend.authMode: "oidc"` — the admin SPA as its own public Authelia client, no injection layer, no gate on the narthex host at all. That was built and tested here. It authenticates correctly and with no extra prompt, but **its first load in any new browser session takes 17-46 seconds and fires 95-262 authorization round-trips** at Authelia before settling.

The cause is in narthex's own frontend, not in this configuration. Its token getter (`od()` in the built bundle) is:

```js
async function od(){
  if (Ni) return null;
  if (Ca) return rd();                 // proxy mode: return early, never redirect
  const e = await rn().getUser();
  if (!e || e.expired) throw await rn().signinRedirect(), new Error("Redirecting to login");
  return e.access_token
}
```

Every API call goes through it, and it calls `signinRedirect()` whenever storage holds no user — with no exemption for the `/auth/callback` route it is currently sitting on, and no "redirect already in flight" latch. So on the callback page it races its own `signinRedirectCallback()`, navigates away mid-exchange, and starts over, until the callback handler happens to win. Note the `if (Ca) return rd()` early return: that is proxy mode, and it is why proxy mode has never shown this.

**To finish the consolidation once that is fixed upstream:**

1. set `narthex-frontend.authMode: "oidc"` in `values-cscs-dev.yaml`, with `oidcIssuer`, `oidcClientId: "narthex"` and `oidcRedirectUri: "https://narthex-prometheus-dev.c2sm-tds.c2sm.cscs.ch/auth/callback"` (that path is correct — it is narthex's own registered route);
2. replace the `eoapi-narthex` client in `authelia-configmap.yaml` with a public `narthex` client shaped exactly like `stac-browser`;
3. set `narthex-backend.oidcAudiences` to `["narthex", "stac-browser"]`;
4. strip the ForwardAuth gate, the `narthex-oauth-errors` middleware and the `Method(`OPTIONS`)` preflight IngressRoute from `../narthex-ingress.yaml`, leaving only the IP allowlist and the `/api` strip-prefix;
5. delete `oauth2-proxy-*.yaml` from this directory and the `oauth2-proxy-authelia-secret` / `oauth2-proxy-client-secret-digest` secret material.

All five steps have been exercised; the git history for this change contains them.

## Hosts

Authelia is served at the root of:

```text
https://auth-prometheus-dev.c2sm-tds.c2sm.cscs.ch
```

Issuer and discovery URL:

```text
Issuer:    https://auth-prometheus-dev.c2sm-tds.c2sm.cscs.ch
Discovery: https://auth-prometheus-dev.c2sm-tds.c2sm.cscs.ch/.well-known/openid-configuration
```

The Authelia ingress is intentionally **not** protected by the IP allowlist: Let's Encrypt HTTP-01 validation must reach it publicly, and OIDC discovery plus JWKS must be fetchable at the public issuer URL from inside the cluster (stac-auth-proxy, narthex-backend). If discovery starts returning `403 Forbidden`, check that no allowlist middleware has crept onto `authelia-ingress.yaml`. Everything else keeps the allowlist.

Because the login form is publicly reachable, the config enables Authelia's `regulation` (retry limit plus temporary ban). Dex had no equivalent.

Certs and DNS are automatic — cert-manager issues per-host HTTP-01 certificates and external-dns creates records straight from Ingress hosts. A new host needs neither manual DNS nor manual certs.

## Groups

Group names **keep their leading slash**, exactly as under Dex:

```text
/eoapi-dev-users
/eoapi-admin
/eoapi-noaa
/nasa-users
/dyamond-users
```

Authelia treats group names as arbitrary strings, so the Keycloak-style form survives untouched. This is load-bearing: `charts/eoapi/data/stac-auth-proxy/custom_filters.py` maps `/eoapi-noaa` → `noaa`, `/nasa-users` → `nasa`, `/dyamond-users` → `dyamond`, bypasses filtering for `/eoapi-admin`, and treats `"public"` as visible to everyone. Dropping the slash would silently reduce every user to the public collections.

`/eoapi-dev-users` is the whole-site gate, enforced twice: by `access_control` for the ForwardAuth-gated paths, and by the `eoapi_users` `authorization_policy` on each OIDC client.

Current users (see `examples/users-database.yml`):

```text
kservis         kservis@example.org         /eoapi-dev-users,/eoapi-admin
noaa-reader     noaa-reader@example.org     /eoapi-dev-users,/eoapi-noaa
nasa-reader     nasa-reader@example.org     /eoapi-dev-users,/nasa-users
dyamond-reader  dyamond-reader@example.org  /eoapi-dev-users,/dyamond-users
```

`authentication_backend.file.search.email: true` is enabled, so either the username or the email works at the login prompt.

## OIDC clients

| `client_id` | Type | Used by | Redirect URI |
|---|---|---|---|
| `stac-browser` | public, PKCE S256 | stac-browser's in-app login; also the token source for the SaveToNarthex widget | `https://prometheus-dev.c2sm-tds.c2sm.cscs.ch/browser/auth` |
| `eoapi-narthex` | confidential | oauth2-proxy, gating the narthex subdomain | `https://narthex-prometheus-dev.c2sm-tds.c2sm.cscs.ch/oauth2/callback` |

Authelia stores only a **hash** of a confidential client's secret. The `eoapi-narthex` digest (`$pbkdf2-sha512$...`) is injected into the ConfigMap by the same `template` filter that inlines the signing key, so neither the secret nor its digest is in git; oauth2-proxy gets the plaintext from `oauth2-proxy-authelia-secret`.

Authelia's `sub` is stable per user across clients, so a named list created through the widget (`aud: stac-browser`) and one created in the admin SPA (`aud: eoapi-narthex`) share the same owner — verified.

Three per-client settings are non-obvious and each is required:

- **`access_token_signed_response_alg: 'RS256'`** — Authelia access tokens are **opaque by default**. stac-browser sends `user.access_token` as its Bearer (hardcoded in stac-browser's `src/auth/oidc.js`), and both stac-auth-proxy and narthex-backend validate that token against JWKS. Without this every protected call fails. Dex hid the issue by making access tokens JWTs unconditionally.
- **`requested_audience_mode: 'implicit'`** — the `aud` of a JWT access token is the *granted* audience, and Authelia grants none unless the relying party sends an `audience` parameter, even for a whitelisted audience. `oidc-client-ts` sends no such parameter, so without `implicit` the `aud` is empty and both `ALLOWED_JWT_AUDIENCES` and narthex's `oidcAudiences` reject the token.
- **`claims_policy: 'eoapi'`** — Authelia 4.39 mints a deliberately minimal ID Token and serves non-standard claims from `/userinfo`. Both consumers read claims straight off the presented token and never call `/userinfo`, so the policy copies `groups` (and `email`, `preferred_username`) into **both** the ID token and the access token.

Audience wiring that must stay in sync across three files:

```text
authelia-configmap.yaml               audience: ['stac-browser'] / ['eoapi-narthex']
values-cscs-dev-stac-auth-proxy.yaml  ALLOWED_JWT_AUDIENCES: "stac-browser"
values-cscs-dev.yaml                  narthex-backend.oidcAudiences: [eoapi-narthex, stac-browser]
```

`ALLOWED_JWT_AUDIENCES` lists only `stac-browser` because nothing injects an `Authorization` header onto `/stac` any more: Authelia's ForwardAuth does not, and `/stac` is a `bypass` rule anyway. Under Dex it also had to list oauth2-proxy's client, whose gate covered the whole eoAPI host.

## ForwardAuth gate

`authelia-middleware.yaml` gates only what has no auth layer of its own: `/raster`, `/vector`, `/multidim` and the `/` doc server. `/stac` and `/browser` are exempted by `bypass` rules in `access_control`; the narthex subdomain is gated by oauth2-proxy instead (`oauth2-proxy-middleware.yaml`), for the bearer-token injection described above.

Authelia issues the redirect to the login portal itself for unauthenticated browser requests (verified: `302` with `Location: https://auth-prometheus-dev.../?rd=...&rm=GET`), so it needs no `errors` middleware companion. oauth2-proxy still does — `narthex-oauth-errors`, kept off `/api`.

The `forward-auth` authz endpoint is redefined in the config to use **`CookieSession` only**, dropping Authelia's default `HeaderAuthorization` strategy. With that strategy enabled, any request carrying an `Authorization` header Authelia cannot validate as one of *its own* bearer tokens short-circuits to a hard 401 — no redirect, no fallback to the session cookie. That is the exact inverse of oauth2-proxy's `skip_jwt_bearer_tokens`, and it would break any gated route that receives a foreign token.

The eoAPI ingress middleware chain is:

```text
eoapi-dev-eoapi-dev-ipallowlist@kubernetescrd,
eoapi-dev-eoapi-dev-authelia-forwardauth@kubernetescrd,
eoapi-dev-eoapi-strip-prefix-middleware@kubernetescrd
```

Middleware cross-references are `<namespace>-<name>@kubernetescrd`. A wrong name **silently drops the entire router** — requests fall through to some other route rather than erroring — so always confirm the rendered annotation with `helm template ... | grep router.middlewares`.

## Secrets

Do not commit real secrets. `examples/` only documents the required keys, and `kubectl apply -f` on this directory does not recurse into it.

Generate the secret values and the password hashes. Argon2 hashing uses the Authelia CLI from the same image that runs in-cluster:

```bash
SESSION_SECRET=$(openssl rand -hex 64)
STORAGE_ENCRYPTION_KEY=$(openssl rand -hex 64)
OIDC_HMAC_SECRET=$(openssl rand -hex 64)
IDENTITY_VALIDATION_JWT_SECRET=$(openssl rand -hex 64)

# OIDC signing key. Keep it stable: rotating it invalidates every issued
# token and every cached JWKS.
openssl genrsa -out oidc-jwks.pem 4096

authelia_hash() {
  docker run --rm ghcr.io/authelia/authelia:4.39.22 \
    authelia crypto hash generate argon2 --password "$1" --no-confirm |
    sed 's/^Digest: //'
}

read -rsp 'kservis password: ' KSERVIS_PASSWORD; echo
read -rsp 'noaa-reader password: ' NOAA_READER_PASSWORD; echo
read -rsp 'nasa-reader password: ' NASA_READER_PASSWORD; echo
read -rsp 'dyamond-reader password: ' DYAMOND_READER_PASSWORD; echo
```

Write the users database locally from `examples/users-database.yml`, substituting the hashes:

```bash
cp deploy/cscs/auth-authelia/examples/users-database.yml ./users-database.yml
# replace each <ARGON2_HASH_FOR_*> with the output of authelia_hash "$..._PASSWORD"
```

Create the Kubernetes secret:

```bash
kubectl create namespace eoapi-dev --dry-run=client -o yaml | kubectl apply -f -

kubectl -n eoapi-dev create secret generic authelia-secret \
  --from-literal=session-secret="$SESSION_SECRET" \
  --from-literal=storage-encryption-key="$STORAGE_ENCRYPTION_KEY" \
  --from-literal=oidc-hmac-secret="$OIDC_HMAC_SECRET" \
  --from-literal=identity-validation-jwt-secret="$IDENTITY_VALIDATION_JWT_SECRET" \
  --from-file=oidc-jwks.pem=./oidc-jwks.pem \
  --from-file=users-database.yml=./users-database.yml

rm -f ./oidc-jwks.pem ./users-database.yml   # or keep them somewhere gitignored
```

Then the oauth2-proxy client credentials. Authelia needs the digest, oauth2-proxy needs the plaintext:

```bash
OAUTH2_CLIENT_SECRET=$(openssl rand -hex 48)
OAUTH2_COOKIE_SECRET=$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')

DIGEST=$(docker run --rm ghcr.io/authelia/authelia:4.39.22 \
  authelia crypto hash generate pbkdf2 --variant sha512 --no-confirm \
  --password "$OAUTH2_CLIENT_SECRET" | sed -n 's/^Digest: //p')

kubectl -n eoapi-dev patch secret authelia-secret --type merge \
  -p "{\"stringData\":{\"oauth2-proxy-client-secret-digest\":\"$DIGEST\"}}"

kubectl -n eoapi-dev create secret generic oauth2-proxy-authelia-secret \
  --from-literal=client-id='eoapi-narthex' \
  --from-literal=client-secret="$OAUTH2_CLIENT_SECRET" \
  --from-literal=cookie-secret="$OAUTH2_COOKIE_SECRET"
```

Everything else — including `identity_providers.oidc.jwks[0].key`, which accepts only a PEM literal and never a path — is loaded from that Secret at startup. The key is inlined by Authelia's `template` config-file filter, enabled with `X_AUTHELIA_CONFIG_FILTERS=template` in the Deployment. **That is why `authelia-configmap.yaml` must contain no other Go template syntax.** Every other secret uses Authelia's `_FILE` environment-variable convention.

Sessions themselves are held **in memory** (no Redis), so restarting Authelia logs everyone out. Acceptable for this dev stack; the OIDC signing key is unaffected, so nothing needs a JWKS-cache restart.

## Deploy

Apply the IP allowlist and the Authelia manifests:

```bash
kubectl apply -f deploy/cscs/traefik-ipallowlist.yaml
kubectl apply -f deploy/cscs/auth-authelia/
kubectl apply -f deploy/cscs/narthex-ingress.yaml
```

Install or upgrade eoAPI with **all three** values files. Omitting the stac-auth-proxy one silently disables group filtering *and* the browser login button — this has already caused a live incident:

```bash
helm upgrade --install eoapi ./charts/eoapi \
  -n eoapi-dev \
  --create-namespace \
  -f deploy/cscs/values-cscs-dev.yaml \
  -f deploy/cscs/values-cscs-dev-stac-auth-proxy.yaml \
  -f deploy/cscs/values-cscs-dev-auth-authelia.yaml \
  --set gitSha=$(git rev-parse HEAD | cut -c1-10)
```

## Validate

From an IP allowed by `deploy/cscs/traefik-ipallowlist.yaml`:

```bash
kubectl -n eoapi-dev get pods,svc,ingress,middleware | grep -E 'authelia|narthex'

curl -s https://auth-prometheus-dev.c2sm-tds.c2sm.cscs.ch/.well-known/openid-configuration | jq
curl -s https://auth-prometheus-dev.c2sm-tds.c2sm.cscs.ch/jwks.json | jq '.keys[].kid'

# Anonymous access to public collections must still work (DEFAULT_PUBLIC=true).
curl -s https://prometheus-dev.c2sm-tds.c2sm.cscs.ch/stac/collections | jq '.collections[].id'

# narthex /api must answer with clean JSON, never an HTML redirect.
curl -i https://narthex-prometheus-dev.c2sm-tds.c2sm.cscs.ch/api/named-lists
```

The signing key must survive a restart — this is the Dex key-rotation bug being closed, so verify it rather than assume it:

```bash
curl -s https://auth-prometheus-dev.c2sm-tds.c2sm.cscs.ch/jwks.json | jq -r '.keys[].kid'
kubectl -n eoapi-dev rollout restart deployment/eoapi-dev-authelia
kubectl -n eoapi-dev rollout status deployment/eoapi-dev-authelia
curl -s https://auth-prometheus-dev.c2sm-tds.c2sm.cscs.ch/jwks.json | jq -r '.keys[].kid'   # same kid
```

In a browser, the decisive test: log in once at `https://prometheus-dev.c2sm-tds.c2sm.cscs.ch/browser/`, then open `https://narthex-prometheus-dev.c2sm-tds.c2sm.cscs.ch/` and go back. There must be **no second credential prompt and no consent screen**. Measured on the deployed stack: one silent authorization round-trip, ~700-850 ms.

Also confirm the `groups` claim arrives slash-prefixed by decoding the access token the browser holds — `/nasa-users`, not `nasa-users`.

Group filtering discriminates per group, and this is worth re-checking after any change to the users database or the claims policy. Observed collection counts for `/stac/collections`:

| user | groups | collections |
|---|---|---|
| `kservis` | `/eoapi-admin` | 10 (admin bypass: unfiltered) |
| `noaa-reader` | `/eoapi-noaa` | 1 (`noaa-emergency-response`) |
| `nasa-reader` | `/nasa-users` | 5 (4 nasa + public) |
| `dyamond-reader` | `/dyamond-users` | 7 (6 dyamond + public) |
| _(anonymous)_ | — | 1 (`noaa-emergency-response`) |

If every authenticated user suddenly sees only the public collection, the `groups` claim is not reaching the token — check `claims_policies.eoapi` and `browser.oidcScope`.

## Logout

There is no single sign-out. Authelia's discovery document advertises **no `end_session_endpoint`**, so RP-initiated logout is unavailable:

- stac-browser's in-app logout clears only its own stored tokens;
- narthex's "Sign out" goes to `/oauth2/sign_out`, clearing only oauth2-proxy's cookie (that is what `narthex-frontend.logoutUrl` points at);
- both leave the shared Authelia session intact, so the next login is silent — which is the flip side of the property that fixed the double login.

A real sign-out means visiting `https://auth-prometheus-dev.c2sm-tds.c2sm.cscs.ch/logout`. Worth telling users explicitly on a shared machine.

## Bearer-token curl testing

Not supported. Authelia does not implement the OAuth 2.0 Resource Owner Password Credentials grant, so the `grant_type=password` flow that `auth-dex/README.md` documented has no equivalent here — deliberately, since ROPC is deprecated and the future CSCS Keycloak setup would not offer it either.

If a token-based API smoke test is needed later, the Authelia-native route is a `client_credentials` client with the `authelia.bearer.authz` scope and an explicit `audience`, rather than impersonating a human user.

## Future CSCS Keycloak Mode

When CSCS Keycloak is ready, remove this local Authelia stack. The replacement should mostly change:

- point `browser.oidcDiscoveryUrl`, `narthex-backend.oidcIssuer` and `stac-auth-proxy.env.OIDC_DISCOVERY_URL` / `OIDC_DISCOVERY_INTERNAL_URL` at the CSCS Keycloak issuer, plus `oidc_issuer_url` in `oauth2-proxy-configmap.yaml`
- register the same two clients in Keycloak, keeping the redirect URIs and the audience/claim wiring above — Keycloak issues JWT access tokens and puts `groups` in tokens via a client scope/mapper, so the same three per-client concerns apply under different names
- swap the `eoapi-narthex` client secret for the Keycloak one; optionally change oauth2-proxy's provider from generic `oidc` to `keycloak-oidc`
- remove the local users database and source groups from Keycloak
- the group names stay slash-prefixed, so `custom_filters.py` needs no change

No re-architecting is needed for the OIDC clients: they are plain relying parties. Two pieces are Authelia-shaped and would need replacing:

- the **ForwardAuth gate** for `/raster`, `/vector`, `/multidim` and `/` — Keycloak has no ForwardAuth endpoint, so those paths would move behind oauth2-proxy (which is still deployed here anyway) or grow their own auth layer;
- `access_control` and `authorization_policies`, whose Keycloak equivalents are oauth2-proxy's `allowed_groups` / `skip_auth_routes` and Keycloak's own client scopes.
