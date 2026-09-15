# CSCS dev auth: Authelia

Authelia is the OIDC provider, the shared browser session, and the Traefik
ForwardAuth gate for the paths with no auth of their own. Development only —
users are local file-based Authelia users, not CSCS accounts.

```text
Authelia  auth-prometheus-dev.c2sm-tds.c2sm.cscs.ch
  ├── ForwardAuth ──→ /raster, /vector, /multidim, / (doc server)
  ├── client stac-browser ──→ stac-auth-proxy validates the token on /stac
  └── client narthex     ──→ narthex-backend validates the token on /api
```

`/browser` is public static UI and `/stac` authenticates itself, so both are
`bypass` rules in `access_control`. narthex sits outside the gate entirely.

## Hosts

Issuer and discovery:

```text
https://auth-prometheus-dev.c2sm-tds.c2sm.cscs.ch
https://auth-prometheus-dev.c2sm-tds.c2sm.cscs.ch/.well-known/openid-configuration
```

Authelia's ingress is deliberately **not** behind the IP allowlist: Let's
Encrypt HTTP-01 must reach it, and discovery/JWKS must be fetchable at the
public issuer URL from inside the cluster. If discovery starts returning 403,
check no allowlist middleware has crept onto `authelia-ingress.yaml`.
Everything else keeps the allowlist. Certs and DNS are automatic (cert-manager
+ external-dns from the Ingress host).

## Users and groups

Groups keep their leading slash, matching what
`charts/eoapi/data/stac-auth-proxy/custom_filters.py` expects: `/eoapi-noaa` →
noaa, `/nasa-users` → nasa, `/dyamond-users` → dyamond, `/eoapi-admin` bypasses
filtering. Dropping the slash silently reduces every user to public collections.

`/eoapi-dev-users` is the whole-site gate, enforced by `access_control` and by
the `eoapi_users` authorization policy on each client.

```text
kservis         kservis@example.org         /eoapi-dev-users,/eoapi-admin
noaa-reader     noaa-reader@example.org     /eoapi-dev-users,/eoapi-noaa
nasa-reader     nasa-reader@example.org     /eoapi-dev-users,/nasa-users
dyamond-reader  dyamond-reader@example.org  /eoapi-dev-users,/dyamond-users
```

Either username or email works at the prompt. See
`examples/users-database.yml`.

## OIDC clients

| client_id | used by | redirect URI |
|---|---|---|
| `stac-browser` | stac-browser's login; also the widget's token source | `https://prometheus-dev.c2sm-tds.c2sm.cscs.ch/browser/auth` |
| `narthex` | the narthex admin SPA | `https://narthex-prometheus-dev.c2sm-tds.c2sm.cscs.ch/auth/callback` |
| `narthex-python-client` | `narthex_client` in a launched notebook, reading non-public collections off `/stac` | none — device flow |

The first two are public + PKCE, so no client secrets exist anywhere. Audience
wiring must stay in sync across three files:

```text
authelia-configmap.yaml               audience: ['stac-browser'] / ['narthex']
values-cscs-dev-stac-auth-proxy.yaml  ALLOWED_JWT_AUDIENCES: "stac-browser"
values-cscs-dev.yaml                  narthex-backend.oidcAudiences: [narthex, stac-browser]
```

`ALLOWED_JWT_AUDIENCES` lists only `stac-browser` because nothing injects an
`Authorization` header onto `/stac`.

### narthex-python-client (device flow)

A launched notebook re-queries the origin STAC API to resolve what a named list
references. Anonymously that silently returns the filtered catalog — a
restricted collection 404s as if deleted — so it has to present a token. The
notebook runs on `jupyter-santis.cscs.ch` against CSCS SSO, a different IdP
with no session here and nowhere to redirect a kernel, so the only workable
grant is the device authorization grant (RFC 8628).

It reuses the `stac-browser` audience, the one value both stac-auth-proxy and
narthex-backend already accept, so one notebook token reaches both without
touching either config; the extra `narthex-python-client` audience only makes
these tokens identifiable in logs. `consent_mode` is `explicit`, unlike the
SPAs — in a device flow the consent screen is the only thing naming the app the
user is approving, and the defence against device-code phishing.

Access tokens live 7 days (`lifespans.custom.notebook`) because that is the
only lever on re-login frequency: Authelia grants `offline_access`, and so
refresh tokens, only in the authorization code and hybrid flows, never the
device grant. The cost is that a presented token cannot be revoked before it
expires — stac-auth-proxy validates it against the JWKS rather than
introspecting — so group changes lag by up to a week.

Drive the flow by hand, no notebook needed:

```bash
AUTH=https://auth-prometheus-dev.c2sm-tds.c2sm.cscs.ch
curl -s -X POST $AUTH/api/oidc/device-authorization \
  -d 'client_id=narthex-python-client' -d 'scope=openid profile email groups'
# open verification_uri, enter user_code, approve, then:
curl -s -X POST $AUTH/api/oidc/token \
  -d 'grant_type=urn:ietf:params:oauth:grant-type:device_code' \
  -d "device_code=$DEVICE_CODE" -d 'client_id=narthex-python-client'
```

The access token must be a JWT (not opaque), `alg` RS256, `aud` containing
`stac-browser`, and carry `groups` — all four or stac-auth-proxy hands back the
anonymous view.

## Secrets

Nothing secret in git; `examples/` documents the keys, and `kubectl apply -f`
on this directory does not recurse into it.

```bash
openssl genrsa -out oidc-jwks.pem 4096   # keep stable: rotating invalidates every token

# users-database.yml carries argon2 hashes - copy examples/users-database.yml
# and fill each one in with:
#   docker run --rm ghcr.io/authelia/authelia:4.39.22 \
#     authelia crypto hash generate argon2 --no-confirm --password '<pw>'

kubectl -n eoapi-dev create secret generic authelia-secret \
  --from-literal=session-secret="$(openssl rand -hex 64)" \
  --from-literal=storage-encryption-key="$(openssl rand -hex 64)" \
  --from-literal=oidc-hmac-secret="$(openssl rand -hex 64)" \
  --from-literal=identity-validation-jwt-secret="$(openssl rand -hex 64)" \
  --from-literal=redis-password="$(openssl rand -hex 32)" \
  --from-file=oidc-jwks.pem=./oidc-jwks.pem \
  --from-file=users-database.yml=./users-database.yml
```

The Postgres password is *not* here: PGO generates it with the `authelia`
PostgresCluster and publishes it as `authelia-pguser-authelia`, which the
Deployment mounts directly.

## Storage

Two backing stores, both required, neither holding anything irreplaceable:

| What | Where | If it is lost |
|---|---|---|
| OIDC grants, opaque ids, preferences | PostgresCluster `authelia` | OIDC audit trail; `sub` values are re-minted |
| Browser sessions | Redis (`eoapi-dev-authelia-redis`) | everyone signs in again |

Losing either loses **no** named lists: narthex owns rows by
`preferred_username`, not by the `sub` this database mints.

Both replaced a simpler arrangement that did not survive contact with real
use, and neither should be reverted:

- **SQLite → Postgres.** Not a size problem. Authelia's schema has no index on
  `signature`, the column the token endpoint looks a code up by, so a code
  exchange scanned the whole authorization-code table — at ~47k rows that was
  10–50s per login. And SQLite's single-writer `journal_mode=delete` turned
  concurrent OIDC requests into `database is locked`, which reaches the user as
  a login that simply fails.
- **In-memory sessions → Redis.** Every restart of the Authelia pod — a config
  edit, an image bump, a node drain — signed every user out of every app,
  which defeats the point of one shared session.

Authelia migrates its own schema at startup (`Storage schema is being checked
for updates`), so a fresh database needs no migration step.

## Deploy

```bash
kubectl apply -f deploy/cscs/traefik-ipallowlist.yaml
kubectl apply -f deploy/cscs/auth-authelia/
kubectl apply -f deploy/cscs/narthex-ingress.yaml

helm upgrade --install eoapi ./charts/eoapi -n eoapi-dev --create-namespace \
  -f deploy/cscs/values-cscs-dev.yaml \
  -f deploy/cscs/values-cscs-dev-stac-auth-proxy.yaml \
  -f deploy/cscs/values-cscs-dev-auth-authelia.yaml \
  --set gitSha=$(git rev-parse HEAD | cut -c1-10)
```

All **three** values files, every time: omitting the stac-auth-proxy one
silently disables group filtering *and* the browser login button.

The eoAPI ingress middleware chain is:

```text
eoapi-dev-eoapi-dev-ipallowlist@kubernetescrd,
eoapi-dev-eoapi-dev-authelia-forwardauth@kubernetescrd,
eoapi-dev-eoapi-strip-prefix-middleware@kubernetescrd
```

References are `<namespace>-<name>@kubernetescrd`; a wrong name silently drops
the whole router instead of erroring, so check the rendered annotation.

## Validate

```bash
curl -s https://auth-prometheus-dev.c2sm-tds.c2sm.cscs.ch/.well-known/openid-configuration | jq
curl -s https://prometheus-dev.c2sm-tds.c2sm.cscs.ch/stac/collections | jq '.collections[].id'
curl -i https://narthex-prometheus-dev.c2sm-tds.c2sm.cscs.ch/api/named-lists
```

Anonymous `/stac/collections` must return exactly the public collection
(`noaa-emergency-response`), and narthex `/api` must answer JSON, never HTML.

In a browser: log in once at `/browser/`, open narthex, come back. No second
prompt, no consent screen. Then check the access token carries slash-prefixed
groups (`/nasa-users`, not `nasa-users`) — if every user suddenly sees only the
public collection, the `groups` claim isn't reaching the token, so look at
`claims_policies.eoapi` and `browser.oidcScope`.

Expected collection counts: kservis 10 (admin, unfiltered), noaa-reader 1,
nasa-reader 5, dyamond-reader 7, anonymous 1.

## Notes

- **No OIDC single sign-out.** Authelia advertises no `end_session_endpoint`
  and 4.39 has no option to enable one, so RP-initiated logout is unavailable:
  an app calling it gets `No end session endpoint` thrown at it. Ending the
  shared session means visiting
  `https://auth-prometheus-dev.c2sm-tds.c2sm.cscs.ch/logout`, which is what
  both `narthex-frontend.logoutUrl` and `browser.oidcLogoutUrl` point at. Both
  apps clear their own tokens first and then send the browser there, so
  "Log out" ends the session everywhere. Without that URL configured, logout
  is local-only and the next login is silent — which reads like the login
  button is lying.
- **No `offline_access`, deliberately.** Refresh tokens and silent login are
  mutually exclusive on Authelia 4.39. It forces a consent screen on every
  authorization request carrying `offline_access` whatever `consent_mode`
  says, and `pre-configured` mode does not help: it stores the grant with
  `offline_access` stripped out, so the stored consent never matches and the
  prompt comes back every time. Verified — the row in
  `oauth2_consent_preconfiguration` reads `openid|email|profile|groups` after
  consenting to a request that included it.

  The SPAs renew through a hidden iframe to the authorization endpoint with
  `prompt=none` instead, which Authelia answers from the session cookie with a
  303 back to the app's own origin. Its `X-Frame-Options: DENY` does not
  interfere: that header only ever rides on pages Authelia itself renders, and
  in a successful silent renew none are. Sessions therefore last as long as the
  Authelia session (12h, 4h inactivity), which is the right bound for a browser
  app anyway.

  Requesting any scope not also granted in the client's `scopes` here makes
  Authelia reject the whole authorization request.
- **No bearer-token curl testing.** Authelia does not implement the ROPC
  password grant. If an API smoke test is needed, use a `client_credentials`
  client with the `authelia.bearer.authz` scope.
- **Traefik's request-header ceiling here is ~3-4 KB.** A ~1.2 KB access token
  plus cookies is already close, so adding claims to `claims_policies.eoapi`
  spends real budget.
- **Keycloak later:** point `browser.oidcDiscoveryUrl`,
  `narthex-{backend,frontend}.oidcIssuer` and
  `stac-auth-proxy.env.OIDC_DISCOVERY_*` at the new issuer, register the same
  two clients, drop the users file. Group names stay slash-prefixed so
  `custom_filters.py` is unchanged. The Authelia-shaped piece is the
  ForwardAuth gate for `/raster`, `/vector`, `/multidim` and `/` — Keycloak has
  no ForwardAuth endpoint, so those paths would need their own auth layer.
