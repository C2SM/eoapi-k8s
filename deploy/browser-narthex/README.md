# stac-browser + SaveToNarthex widget image

Builds a custom stac-browser image with narthex's `SaveToNarthex` widget
(github.com/C2SM/narthex, `widget/`) compiled into the bundle — see
`Dockerfile` for why this has to happen at build time rather than through
runtime config.

Built and pushed via `.github/workflows/build-browser-narthex.yml`
(manual `workflow_dispatch`, not tied to this repo's own tag/release
cycle): set `stac_browser_ref` to the stac-browser tag matching
`browser.image.tag` in `charts/eoapi/values.yaml`/`deploy/cscs/values-cscs-dev.yaml`,
and `narthex_ref` to the narthex release tag whose `widget/` you want
compiled in. Pushes `ghcr.io/c2sm/eoapi-browser-narthex:<image_tag>`.

The `widget/` source comes from narthex's own `narthex-widget.tar.bz2`
release asset (published per tag by narthex's `release.yml`) rather than a
git clone of the whole repo — `narthex_ref` must be a tag whose GitHub
Release actually has that asset attached (only tags built by/after
`release.yml` existed do). Since `C2SM/narthex` is private, this workflow
needs a **`NARTHEX_REPO_TOKEN` repo secret** — a PAT or fine-grained token
with at least `contents:read` on `C2SM/narthex` — for the Dockerfile's
`gh release download` step.

## Before pointing a real deployment at it

Verify the widget actually renders first — run the built image locally and
open an item/collection page in a browser, rather than trusting a green
build alone (the widget mechanism is exercised by stac-browser's own build,
not by anything in this Dockerfile):

```sh
docker run --rm -p 8080:8080 \
  -e SB_catalogUrl="https://earth-search.aws.element84.com/v1" \
  ghcr.io/c2sm/eoapi-browser-narthex:<image_tag>
```

then open `http://localhost:8080/collections/sentinel-2-l2a/items/<some-item-id>`
(grab a real id from `GET <catalogUrl>/collections/sentinel-2-l2a/items?limit=1`)
and confirm the widget renders at the bottom of the item panel. Only once
that's confirmed, point a real deployment's `browser.image.name`/`image.tag`
at this image (see `deploy/cscs/values-cscs-dev.yaml`, currently still on
the stock `ghcr.io/radiantearth/stac-browser` image).

`browser.customConfig` in `deploy/cscs/values-cscs-dev.yaml` already sets
`window.STAC_BROWSER_CONFIG.narthexApiUrl`/`narthexAdminUrl`, which is what
`widgets.config.js` reads at runtime — no further chart changes needed once
the image is swapped in.
