# CSCS dev auth: Keycloak

CSCS's production Keycloak (`auth.cscs.ch`, realm `cscs`) is the OIDC provider and
the shared SSO session. Users are CSCS accounts; roles are managed through the
CSCS portal. Keycloak has no ForwardAuth endpoint, so oauth2-proxy gates the
paths with no auth of their own.

```text
Keycloak  auth.cscs.ch/auth/realms/cscs
  ├── prometheus-tds (confidential)      oauth2-proxy ForwardAuth ──→ /raster, /vector, /multidim
  ├── prometheus-tds-web (public, PKCE)  stac-browser ──→ stac-auth-proxy validates the token on /stac
  │                                      narthex SPA  ──→ narthex-backend validates the token on /api
  └── prometheus-tds-jupyter (public, device flow)  notebooks ──→ /stac, see Notebook clients
```

`/browser` is public static UI, `/stac` authenticates itself and `/` is the
landing page that sends visitors on to the catalogue, so all three are
`skip_auth_routes` in oauth2-proxy. narthex sits outside the gate entirely.
Map tiles without a gate session get a 401 instead of a login redirect
(`api_routes`): every redirect leaves a CSRF cookie behind, and a map's worth
of them made `/browser` answer `400 Request Header Or Cookie Too Large`.

**Status: live** on eoapi-dev since 2026-10-30 (`v1.3.0-prometheus`), which
also retired Authelia - see [Roll back](#roll-back).

## Configuration

| What | Where |
|---|---|
| Issuer, logout URL, client ID, audience, SPA scopes | `keycloak:` section of `../values-cscs-dev-auth-keycloak.yaml` |
| Notebook client (`stacOidcClientId`) | each `narthex-launch` entry of `narthex-frontend.launchConfigs` in `../values-cscs-dev.yaml` - the one value outside the overlay, see [Notebook clients](#notebook-clients) |
| Gate client, cookie | `oauth2-proxy-configmap.yaml` |
| Gate roles (`allowed_groups`) | `oauth2-proxy-middleware.yaml` |
| Role → collections | `charts/eoapi/data/stac-auth-proxy/custom_filters.py` |
| Gate client secret, cookie secret | Secret `oauth2-proxy-keycloak-secret` (below) |

The `keycloak:` section is not read by the chart: it holds YAML anchors, so
each endpoint and client ID is written once and every consumer in that file
follows it.

## What is known about the realm

CSCS first issued `prometheus-tds` on its TDS Keycloak (`auth-tds.cscs.ch`),
then moved it to production with a new secret. Verified on `auth.cscs.ch`:
the secret is accepted, the client has no service account and the device
grant is disabled. Some token facts below come from the TDS client's
service-account token, which prod cannot issue - confirm them with a real
login (the `AuthSuccess` and `custom_filters` log lines show them).

- **A confidential client for the gate, a public one for the SPAs.** The gate
  uses `prometheus-tds` with its secret. stac-browser and the narthex SPA run
  in the browser, which cannot keep a secret, so they use `prometheus-tds-web`
  (`keycloak.client`): no secret, PKCE, created by CSCS 2026-10-29 with both
  SPAs' origins as web origins. Without a secret a token request with a bad
  code gets `invalid_grant` rather than `unauthorized_client` - that is how
  to tell a public client from outside. Its tokens carry `groups` (seen
  2026-10-30 in the `custom_filters` lines of browser logins).
- **`aud` is `account`**, which practically every token of the realm carries.
  stac-auth-proxy and narthex-backend accept it for now; once CSCS adds an
  Audience mapper putting the client in `aud`, switch `keycloak.audience` to
  the client ID.
- **`groups` holds the `prometheus_*` group names.** CSCS says the realm has
  no `prometheus_*` roles, so they are Keycloak groups. CSCS reported
  `prometheus_all`, `prometheus_nasa`, `prometheus_tds_nasa`,
  `prometheus_dyanamo`, `prometheus_tds_dynamo` (spelled as reported); a real
  token (2026-10-28, see [Notebook clients](#notebook-clients)) carries
  `prometheus_dynamo`, and every group twice, as `/name` and `name`.
  `custom_filters.py` reads them from `groups`, realm roles and the client's
  own roles, and maps `prometheus_all` to unfiltered, `*_nasa` to `nasa`, and
  `*_dynamo`, `*_dyanamo` and `*_dyamond` - the spelling they are to be fixed
  to - to `dyamond`, each with and without `tds_`. No role maps to `noaa`.
- **Access tokens live 300s**; `cookie_refresh = "4m"` stays below that.
- **`email_verified` is false**, hence `insecure_oidc_allow_unverified_email`
  in the gate - nothing authorizes by email.
- **The device grant is disabled** for the client.
- **`groups` is a default client scope**, so it arrives without being
  requested.

### Redirect URIs

| Client | URI | for | registered |
|---|---|---|---|
| `prometheus-tds` | `https://prometheus-dev.c2sm-tds.c2sm.cscs.ch/oauth2/callback` | the gate | yes, 2026-10-29 |
| `prometheus-tds-web` | `https://prometheus-dev.c2sm-tds.c2sm.cscs.ch/browser/auth`, `…/browser/silent-renew.html` | stac-browser | yes, 2026-10-29 |
| `prometheus-tds-web` | `https://narthex-prometheus-dev.c2sm-tds.c2sm.cscs.ch/auth/callback` | narthex | yes, 2026-10-29 |

No post-logout redirect URI is registered, and none is needed: Keycloak
accepts a client's redirect URIs as post-logout ones and passes `state` back,
so the browser's logout returns to `/browser/silent-renew.html?state=<page>`,
which forwards to that page (`browser.oidcLogoutUrl` in
`../values-cscs-dev-auth-keycloak.yaml`).

Check one without logging in - Keycloak answers an unregistered one with
`Invalid parameter: redirect_uri`:

```bash
curl -s -G \
  https://auth.cscs.ch/auth/realms/cscs/protocol/openid-connect/auth \
  --data-urlencode client_id=prometheus-tds --data-urlencode response_type=code \
  --data-urlencode scope=openid \
  --data-urlencode redirect_uri=https://prometheus-dev.c2sm-tds.c2sm.cscs.ch/oauth2/callback \
  | grep -o 'Invalid parameter: [a-z_]*' || echo registered
```

## Notebook clients

The narthex-launch notebooks log in to `/stac` themselves, with the device
flow and no secret. `prometheus-tds` requires its secret and answers that with
`unauthorized_client`, so CSCS issued two public clients for them:

| Client | For |
|---|---|
| `prometheus-tds-jupyter` | this deployment (TDS/dev) |
| `prometheus-prod-jupyter` | a future production deployment - nothing here uses it, and it has never had a real login |

Verified 2026-10-28 with a device-flow login on `prometheus-tds-jupyter`: `iss`
`https://auth.cscs.ch/auth/realms/cscs`, `aud` `["broker", "account"]`, `azp`
`prometheus-tds-jupyter`, `preferred_username` set, every group as both
`/name` and `name`. It passes stac-auth-proxy's `ALLOWED_JWT_AUDIENCES`
(`account`) and narthex-backend's `oidcAudiences`.

Each `narthex-launch` launch config names the client in `stacOidcClientId`.
It sits in `../values-cscs-dev.yaml`, not the overlay, because Helm replaces
lists whole and the overlay would have to repeat the entire list. The narthex
frontend passes it to the launcher as `?stac_client_id=`; the launcher in the
eobs-lab uenv writes it into the `STAC_OIDC_CLIENT_ID` line of a new notebook,
and rewrites that line in existing notebooks on every launch. The notebooks
take the issuer from `/stac`'s advertised OIDC discovery URL.
`santis-test-notebook` opens a notebook directly and never reaches the
launcher, so it has none.

- **Needs narthex v1.2.0 or later** in two places: the narthex-frontend image,
  and the eobs-lab uenv (`NARTHEX_TAG` in software-stack-recipes'
  `recipes/eobs-lab/`). An older frontend ignores the field, and an older
  launcher the extra query argument. Tested on jupyter-santis 2026-10-30.
- **Required.** Unset, the notebooks fall back to `narthex-python-client`,
  the retired Authelia client, and their login fails.

## Network

oauth2-proxy (discovery at startup, token redemption), stac-auth-proxy
(discovery, JWKS) and narthex-backend (JWKS) all need c2sm-tds →
`auth.cscs.ch:443`. CSCS's firewall blocked it (148.187.144.0/24, checked
2026-10-06) until CSCS opened it, by 2026-10-07. Step 1 of [Deploy](#deploy)
checks it.

Browsers need it too. It answered from one outside network that day and timed
out from another; where it does not, tunnel through a login node:

```bash
ssh -N -D 1080 -J $USER@ela.cscs.ch $USER@santis.alps.cscs.ch &
```

and a throwaway Firefox profile that uses it, with remote DNS:

```bash
mkdir -p /tmp/kc-ff
printf '%s\n' 'user_pref("network.proxy.type", 1);' \
  'user_pref("network.proxy.socks", "127.0.0.1");' \
  'user_pref("network.proxy.socks_port", 1080);' \
  'user_pref("network.proxy.socks_remote_dns", true);' > /tmp/kc-ff/user.js
firefox --no-remote --profile /tmp/kc-ff https://prometheus-dev.c2sm-tds.c2sm.cscs.ch/
```

## Secret

Nothing secret in git; `examples/` documents the keys, and `kubectl apply -f`
on this directory does not recurse into it. The client secret is in the
gitignored `.keycloak_prometheus-tds.env` at the repo root.
`.keycloak_prometheus-prod.env` next to it holds the `prometheus-prod` client
CSCS issued for a production deployment; nothing here uses it.

```bash
kubectl -n eoapi-dev create secret generic oauth2-proxy-keycloak-secret \
  --from-literal=client-secret="$(sed -n 's/^KEYCLOAK_CLIENT_SECRET=//p' .keycloak_prometheus-tds.env)" \
  --from-literal=cookie-secret="$(openssl rand -base64 32 | tr -- '+/' '-_')"
```

## Deploy

The order for a first install, or after the gate was removed: everything the
live site depends on changes only in step 4, and only once oauth2-proxy has
proven it can reach Keycloak. Later upgrades are step 4 alone, plus
`kubectl apply -f deploy/cscs/auth-keycloak/` when this directory changes.

Deploy from a commit, never from a dirty tree: `gitSha` labels the release
with `HEAD`, and uncommitted changes follow `git switch`, so a later redeploy
of another ref would deploy them again.

```bash
# 1. Preconditions. Must print 200 - a timeout means the firewall above is
#    still closed: stop here, nothing has changed.
kubectl -n eoapi-dev exec deploy/eoapi-stac-auth-proxy -- python -c \
  "import urllib.request as u; print(u.urlopen('https://auth.cscs.ch/auth/realms/cscs/.well-known/openid-configuration', timeout=15).status)"
#    and the gate's redirect URI must be registered (check above).

# 2. Note the revision to roll back to.
helm -n eoapi-dev history eoapi --max 1

# 3. The secret (above), then the gate. Nothing references it yet.
kubectl apply -f deploy/cscs/auth-keycloak/
kubectl -n eoapi-dev rollout status deploy/eoapi-dev-oauth2-proxy --timeout=120s
#    Not ready -> `kubectl -n eoapi-dev logs deploy/eoapi-dev-oauth2-proxy`,
#    then remove it with the `delete` commands under Roll back. The live
#    site is untouched.

# 4. Switch the release over.
helm upgrade --install eoapi ./charts/eoapi -n eoapi-dev \
  -f deploy/cscs/values-cscs-dev.yaml \
  -f deploy/cscs/values-cscs-dev-stac-auth-proxy.yaml \
  -f deploy/cscs/values-cscs-dev-auth-keycloak.yaml \
  --set gitSha=$(git rev-parse HEAD | cut -c1-10)
kubectl -n eoapi-dev rollout status deploy/eoapi-stac-auth-proxy --timeout=180s
kubectl -n eoapi-dev exec deploy/eoapi-stac-auth-proxy -- \
  grep -c prometheus_all /app/src/stac_auth_proxy/custom_filters.py   # >0
```

The eoAPI ingress middleware chain is:

```text
eoapi-dev-eoapi-dev-ipallowlist@kubernetescrd,
eoapi-dev-eoapi-dev-oauth-forwardauth@kubernetescrd,
eoapi-dev-eoapi-strip-prefix-middleware@kubernetescrd
```

References are `<namespace>-<name>@kubernetescrd`; a wrong name silently drops
the whole router instead of erroring, so check the rendered annotation.

## Logs

```bash
kubectl -n eoapi-dev logs -f deploy/eoapi-dev-oauth2-proxy
kubectl -n eoapi-dev logs -f deploy/eoapi-stac-auth-proxy | grep -v healthz
kubectl -n eoapi-dev logs -f deploy/eoapi-narthex-backend
```

| Line | Meaning |
|---|---|
| oauth2-proxy `[AuthSuccess] Authenticated via OAuth2: Session{… PreferredUsername:… groups:[…]}` | a gate login, with **every** role oauth2-proxy saw: `groups` claim values as-is, realm roles as `role:<name>`, client roles as `role:<client>:<name>`. Logged for everyone who signs in, whether or not they pass the gate. |
| oauth2-proxy request line `… <user> … 202` / `403` / `302` or `401` | a gate check: passed / signed in but no `allowed_groups` role / not signed in (browser sent to login, AJAX refused). The path shows as `/?allowed_groups=…` (the middleware address), not the requested one; host, user, client IP and time identify it. |
| oauth2-proxy `Error redeeming code` / `Error creating session during OAuth2 callback` | redirect URI or secret wrong / a claim it needs is missing |
| stac-auth-proxy `custom_filters: user=… roles=[…] readable=[…]` | every `/stac` request: who, every role found in the token, which `auth:groups` values that grants (`all` = unfiltered). Anonymous is `user=- roles=[]`. |
| stac-auth-proxy `Token audience validation failed` / `Token validation failed` | a presented token rejected: wrong `aud` / bad signature, expiry or issuer |

In a browser, `https://prometheus-dev.c2sm-tds.c2sm.cscs.ch/oauth2/userinfo`
shows the gate session's user, email and groups.

## Test

1. **Gate.** Open `https://prometheus-dev.c2sm-tds.c2sm.cscs.ch/raster/` in the
   tunnelled Firefox: CSCS login, then the raster landing page for a user with
   a `prometheus_*` group and `Forbidden` for one without. The `AuthSuccess`
   line shows which form the groups arrived in.
2. **Anonymous `/stac`.** `curl -s https://prometheus-dev.c2sm-tds.c2sm.cscs.ch/stac/collections | jq '.collections[].id'`
   returns the public collection only.
3. **stac-browser login.** "Log in" on a catalog page: CSCS login, back on the
   same page, restricted collections now listed. The `custom_filters` line for
   that user must show the `prometheus_*` groups - `roles=[]` means
   `prometheus-tds-web` tokens lack `groups`, which CSCS has to add.
4. **narthex.** Log in to `https://narthex-prometheus-dev.c2sm-tds.c2sm.cscs.ch`,
   and save an item from stac-browser with the SaveToNarthex widget.

## Roll back

Between Keycloak releases: `helm -n eoapi-dev rollback eoapi <revision>`. The
gate in this directory is applied with kubectl, not part of the release.

Authelia was retired with `v1.3.0-prometheus` on 2026-10-30: its Deployment,
Redis, PostgresCluster and Secret are deleted. `v1.2.1-prometheus` (Helm
revision 83) is the last release that used it. Going back means recreating
Authelia from that tag's `deploy/cscs/auth-authelia/` - its secrets survive
only in the gitignored `.authelia_*.env` files - deploying that tag, and then
removing the gate (release first, gate second: the reverse leaves the ingress
pointing at a deleted middleware, which drops every route):

```bash
kubectl -n eoapi-dev delete deploy,svc,ingress,configmap,middleware.traefik.io \
  -l app.kubernetes.io/part-of=eoapi-dev-auth-keycloak
kubectl -n eoapi-dev delete secret oauth2-proxy-keycloak-secret
```

## Notes

- **Logout ends the CSCS SSO session**, i.e. every CSCS app including
  jupyter-santis. The gate's own cookie outlives it until its next refresh
  (`cookie_refresh`), which then fails and sends the user back through
  Keycloak; `/oauth2/sign_out` clears it directly.
- **The gate's cookie is bigger than Authelia's was.** It carries Keycloak's
  access (~1.7 KB), ID and refresh tokens, split across `_0`, `_1`, …
  cookies. If gated paths start answering 431 or 400, move oauth2-proxy to
  `session_store_type = "redis"`.
- **No CORS preflight bypass on the gate.** Traefik always calls the auth
  server with GET, so oauth2-proxy never sees an OPTIONS. Nothing calls
  `/raster` or `/vector` cross-origin today.
- **narthex_client** discovers the issuer from `/stac`'s `auth:schemes`, so it
  follows this change on its own. Its client is the launch config's
  `stacOidcClientId`, since it uses the device grant, which is disabled on
  `prometheus-tds` - see [Notebook clients](#notebook-clients).
