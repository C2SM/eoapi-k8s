# CSCS dev auth: Keycloak

CSCS's production Keycloak (`auth.cscs.ch`, realm `cscs`) is the OIDC provider and
the shared SSO session. Users are CSCS accounts; roles are managed through the
CSCS portal. Keycloak has no ForwardAuth endpoint, so oauth2-proxy gates the
paths with no auth of their own.

```text
Keycloak  auth.cscs.ch/auth/realms/cscs
  ├── prometheus-tds (confidential)      oauth2-proxy ForwardAuth ──→ /raster, /vector, /multidim, /
  ├── prometheus-tds-web (public, PKCE)  stac-browser ──→ stac-auth-proxy validates the token on /stac
  │                                      narthex SPA  ──→ narthex-backend validates the token on /api
  └── prometheus-tds-jupyter (public, device flow)  notebooks ──→ /stac, see Notebook clients
```

`/browser` is public static UI and `/stac` authenticates itself, so both are
`skip_auth_routes` in oauth2-proxy. narthex sits outside the gate entirely.

**Status: in test.** Authelia stays deployed alongside and untouched until this
is accepted - see [Roll back](#roll-back).

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
  to tell a public client from outside. Not yet seen: whether its tokens carry
  `groups`; the first browser login's `custom_filters` line shows it.
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
| `prometheus-tds` | `http://localhost:8765/callback` | `inspect-claims.py` | no |
| `prometheus-tds-web` | `https://prometheus-dev.c2sm-tds.c2sm.cscs.ch/browser/auth`, `…/browser/silent-renew.html` | stac-browser | yes, 2026-10-29 |
| `prometheus-tds-web` | `https://narthex-prometheus-dev.c2sm-tds.c2sm.cscs.ch/auth/callback` | narthex | yes, 2026-10-29 |

No post-logout redirect URI is registered, so logging out ends on Keycloak's
own page rather than back in the app.

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

- **Inert until narthex is released.** The field needs a narthex release and
  two rebuilds from it: the narthex-frontend image, and the eobs-lab uenv
  after bumping `NARTHEX_TAG` in software-stack-recipes'
  `recipes/eobs-lab/`. Merging before that is safe: an old frontend ignores
  the field, and an old launcher ignores the extra query argument.
- **Never on the Authelia deployment.** Unset, the notebooks fall back to
  `narthex-python-client`, the Authelia client: right under Authelia, a failed
  login under Keycloak, so the field is required once this branch is deployed.
  The client ID and the issuer must switch together, which they do only when
  this branch is deployed: it does not render without the Keycloak overlay,
  and [Roll back](#roll-back) reinstalls from a clean `main`, which has no
  `stacOidcClientId`. Do not bring the field to `main` while `main` deploys
  Authelia.

## Network

As of 2026-10-06, `auth.cscs.ch` (148.187.144.84) is **not reachable from the
cluster's pods**: every host in 148.187.144.0/24 (`auth-tds`, `www.cscs.ch`
too) times out from both worker nodes, while GitHub, PyPI and other CSCS hosts
connect. There is no NetworkPolicy in the namespace, so it is a CSCS firewall.
oauth2-proxy (discovery at startup, token redemption), stac-auth-proxy
(discovery, JWKS) and narthex-backend (JWKS) all need it, so CSCS must open
c2sm-tds → `auth.cscs.ch:443` before any of this can work.

Browsers need it too. It answered from one outside network that day and timed
out from another; where it does not, tunnel through a login node:

```bash
ssh -N -D 1080 -J $USER@ela.cscs.ch $USER@santis.alps.cscs.ch &
```

and use a throwaway Firefox profile with SOCKS5 `127.0.0.1:1080` and remote
DNS (see [Inspect claims](#inspect-claims)).

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

## Deploy for testing

Order matters: everything the live site depends on changes only in step 4, and
only once oauth2-proxy has proven it can reach Keycloak.

Deploy from a commit on this branch, never from a dirty tree: `gitSha` labels
the release with `HEAD`, and uncommitted changes follow `git switch`, so a
later "redeploy main" would deploy them again.

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
#    then remove it as in "Roll back" step 2. The live site is untouched.

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

Not applied, deliberately: `kubectl apply -f deploy/cscs/auth-authelia/` (gone
from this branch; Authelia keeps running, unreferenced) and nothing in
`narthex-ingress.yaml` or `traefik-ipallowlist.yaml` differs from `main`.

The eoAPI ingress middleware chain becomes:

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
3. **`/stac` with a user token** - stands in for the browser login:

   ```bash
   deploy/cscs/auth-keycloak/inspect-claims.py --env-file .keycloak_prometheus-tds.env \
     --socks 127.0.0.1:1080 --save-token /tmp/kc-token
   curl -s -H "Authorization: Bearer $(cat /tmp/kc-token)" \
     https://prometheus-dev.c2sm-tds.c2sm.cscs.ch/stac/collections | jq '.collections[].id'
   ```

   and the matching `custom_filters` log line. The token lives 300s.
4. **stac-browser login.** "Log in" on a catalog page: CSCS login, back on the
   same page, restricted collections now listed. The `custom_filters` line for
   that user must show the `prometheus_*` groups - `roles=[]` means
   `prometheus-tds-web` tokens lack `groups`, which CSCS has to add.
5. **narthex.** Log in to `https://narthex-prometheus-dev.c2sm-tds.c2sm.cscs.ch`,
   and save an item from stac-browser with the SaveToNarthex widget.

## Roll back

`main` renders exactly what is live today (checked 2026-10-06 against Helm
revision 69 and with `kubectl diff` on every kubectl-applied manifest; only the
`gitsha` label differs, as on any deploy). Rolling back is the release first,
then the objects this branch added - the reverse order would leave the
ingress pointing at a deleted middleware, which drops every route.

```bash
# 1. The release - either of:
helm -n eoapi-dev rollback eoapi <revision from step 2 of the deploy>
#    or, with this branch committed:
git switch main
git status --short deploy charts      # must print nothing
helm upgrade --install eoapi ./charts/eoapi -n eoapi-dev --create-namespace \
  -f deploy/cscs/values-cscs-dev.yaml \
  -f deploy/cscs/values-cscs-dev-stac-auth-proxy.yaml \
  -f deploy/cscs/values-cscs-dev-auth-authelia.yaml \
  --set gitSha=$(git rev-parse HEAD | cut -c1-10)

# 2. What this branch added. Redeploying main does not remove it.
kubectl -n eoapi-dev delete deploy,svc,ingress,configmap,middleware.traefik.io \
  -l app.kubernetes.io/part-of=eoapi-dev-auth-keycloak
kubectl -n eoapi-dev delete secret oauth2-proxy-keycloak-secret

# 3. Check: the chain names authelia-forwardauth again, and the filters are back.
kubectl -n eoapi-dev get ingress eoapi-ingress \
  -o jsonpath='{.metadata.annotations.traefik\.ingress\.kubernetes\.io/router\.middlewares}'
kubectl -n eoapi-dev exec deploy/eoapi-stac-auth-proxy -- \
  grep -c /eoapi-admin /app/src/stac_auth_proxy/custom_filters.py   # >0
```

Nothing else needs restoring: Authelia, its database and Redis were never
touched, narthex and pgstac data cannot be written through a login that does
not work yet, and oauth2-proxy keeps no state. Browsers may hold a stale
`__Secure-eoapi-dev-oauth2` cookie, which nothing reads after the rollback.

## Inspect claims

`inspect-claims.py` logs in (device grant, else authorization code), prints the
decoded ID token, access token and userinfo, and where each claim matching
`--match` (default `prometheus_`) turned up. Standard library only, Python
3.6+.

```bash
mkdir -p /tmp/kc-ff
printf '%s\n' 'user_pref("network.proxy.type", 1);' \
  'user_pref("network.proxy.socks", "127.0.0.1");' \
  'user_pref("network.proxy.socks_port", 1080);' \
  'user_pref("network.proxy.socks_remote_dns", true);' > /tmp/kc-ff/user.js

BROWSER="firefox --no-remote --profile /tmp/kc-ff %s" \
  deploy/cscs/auth-keycloak/inspect-claims.py --env-file .keycloak_prometheus-tds.env \
  --socks 127.0.0.1:1080
```

It needs `http://localhost:8765/callback` registered; with only another
redirect URI, pass `--flow code --redirect-uri <it>` and paste the URL the
browser lands on.

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
- **Retiring Authelia**, once this is accepted:
  `git archive c2sm/main deploy/cscs/auth-authelia | tar -x -C /tmp`, then
  `kubectl delete -f /tmp/deploy/cscs/auth-authelia/` and
  `kubectl -n eoapi-dev delete secret authelia-secret`. That drops its
  PostgresCluster and Redis PVC, neither of which holds anything needed:
  narthex owns named lists by `preferred_username`, not by Authelia's `sub`.
