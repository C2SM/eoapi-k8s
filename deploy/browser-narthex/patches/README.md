# stac-browser patches

Applied to the `STAC_BROWSER_REF` checkout by the `Dockerfile` before
`npm install`, with `git apply` so a patch that no longer matches the pinned
ref fails the build rather than applying somewhere unintended.

Everything here exists because it cannot be done from runtime config.
`browser.authConfig` reaches stac-browser as a `SB_authConfig` **JSON** env
var, so it can only carry JSON-representable values — and the settings below
need a constructed object, a code path, or both.

**Re-check every patch when bumping `STAC_BROWSER_REF`**, and drop any whose
fix has landed upstream.

## 0001-oidc-session-persistence-and-silent-renew.patch

Four fixes to `src/auth/oidc.js`, all in stac-browser's `oidc-client-ts`
wiring.

1. **`userStore` → `localStorage`.** oidc-client-ts defaults the *user* store
   to `sessionStorage` (the *state* store already defaults to
   `localStorage`). sessionStorage is per-tab, so following a link into a new
   tab and coming back, or restoring a window, shows a logged-out Browser
   while the IdP session is still perfectly valid — and the re-login that
   follows is silent, which reads like the login button is lying.

2. **`maxSilentRenewTimeoutRetries: 0`.** Left undefined, oidc-client-ts's
   `SilentRenewService` retries a timed-out silent renew every 5 seconds with
   no limit.

3. **The silent-renew callback.** This is the one that matters.
   `silent_redirect_uri` defaults to `redirect_uri`, so the hidden renewal
   iframe loads `/auth` — the same route as the redirect callback — and
   stac-browser runs `signinRedirectCallback()` there. That never posts the
   result back to the parent window, so the parent's `signinSilent()` always
   times out. Combined with (2) that is an unbounded loop: against this
   deployment's Authelia it ran at a steady 15s interval for as long as a tab
   stayed open, minting a full authorization grant each time and never
   redeeming it — 45,200 authorization codes against 67 access tokens, which
   eventually made every login take tens of seconds.

   Note that the iframe itself is fine: the provider answers `prompt=none`
   from the session cookie with a 303 straight back to the app's own origin,
   so a provider that sends `X-Frame-Options: DENY` on its own rendered pages
   (Authelia does) does not block this — nothing of the provider's is ever
   framed. Only the callback dispatch was wrong. The fix branches on whether
   the page is framed and calls `signinSilentCallback()` there, which is what
   makes renewal work without needing `offline_access`.

4. **Dead-session cleanup and logout.** `addAccessTokenExpired` cleared the UI
   but left the expired user in storage, so the Browser showed "Log in" on top
   of a stored token; `resume()` also assumed `signinSilent()` resolves.
   Both now remove the user. `logout()` no longer calls `signoutRedirect()`,
   which throws `No end session endpoint` against a provider that advertises
   none (Authelia 4.39 does not, and has no config option for it) — leaving
   the user signed in at the IdP with nothing to indicate it failed. It now
   clears local state and, if `authConfig.logoutUrl` is set, sends the browser
   to the provider's own logout page.

Fixes 1, 2 and 4 are useful anywhere. Fix 3 is the one that matters here.
