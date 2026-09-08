# Builds stac-browser (https://github.com/radiantearth/stac-browser) with
# narthex's SaveToNarthex widget (https://github.com/C2SM/narthex,
# widget/) compiled into the bundle at build time — stac-browser has no
# runtime widget-loading mechanism, widgets.config.js is a plain source
# file read by its own build. Otherwise identical to stac-browser's own
# upstream Dockerfile (same nginx-unprivileged runtime stage, same
# DYNAMIC_CONFIG/SB_pathPrefix runtime-config mechanism the eoapi chart's
# `browser.*` values already drive).
FROM node:lts-alpine AS build-step
ARG STAC_BROWSER_REF=v5.0.0
# narthex publishes a single moving pre-release tag; v1.0.0-alpha.2 no longer
# exists, so this default has to track whatever tag currently carries the
# narthex-widget.tar.bz2 release asset. Check before building:
#   gh release view <ref> --repo C2SM/narthex
ARG NARTHEX_REF=v1.0.0-alpha
ARG DYNAMIC_CONFIG=true
ARG historyMode="history"
ARG SB_CONFIG=""
ENV DYNAMIC_CONFIG="${DYNAMIC_CONFIG}"
ENV SB_historyMode="${historyMode}"
ENV SB_CONFIG="${SB_CONFIG}"

WORKDIR /app
RUN apk add --no-cache git github-cli

RUN git clone --branch "${STAC_BROWSER_REF}" --depth 1 \
      https://github.com/radiantearth/stac-browser.git .

# narthex's release.yml publishes widget/ as a narthex-widget.tar.bz2
# GitHub Release asset per tag (git archive of widget/, prefixed
# narthex-widget/) - fetch that instead of cloning the whole (private)
# repo. C2SM/narthex is private, so this needs a token with read access to
# it, passed as a BuildKit secret (not an ARG/ENV) so it never lands in an
# image layer or build history - see build-browser-narthex.yml's
# `secrets:` input, sourced from the NARTHEX_REPO_TOKEN repo secret.
RUN --mount=type=secret,id=narthex_repo_token \
    GH_TOKEN="$(cat /run/secrets/narthex_repo_token)" \
      gh release download "${NARTHEX_REF}" \
        --repo C2SM/narthex \
        --pattern narthex-widget.tar.bz2 \
        --output narthex-widget.tar.bz2 && \
    tar xjf narthex-widget.tar.bz2 && \
    cp narthex-widget/widgets.config.js ./widgets.config.js && \
    cp narthex-widget/SaveToNarthex.vue ./SaveToNarthex.vue && \
    rm -rf narthex-widget narthex-widget.tar.bz2

RUN npm install
RUN [ "${DYNAMIC_CONFIG}" = "true" ] && sed -i 's/<!--RC//;s/RC-->//' index.html
RUN npm run build


FROM nginxinc/nginx-unprivileged:1-alpine
ARG pathPrefix="/browser/"

USER root
RUN apk add --no-cache jq pcre-tools

COPY --from=build-step /app/config.schema.json /etc/nginx/conf.d/config.schema.json
COPY --from=build-step /app/dist /usr/share/nginx/html
COPY --from=build-step /app/docker/default.conf /etc/nginx/conf.d/default.conf.template
COPY --from=build-step /app/docker/docker-entrypoint.sh /docker-entrypoint.d/40-stac-browser-entrypoint.sh

# Remove default.conf so a skipped entrypoint fails loudly instead of serving nginx's welcome page.
RUN rm -f /etc/nginx/conf.d/default.conf && \
    chown -R nginx:nginx /usr/share/nginx/html && \
    chmod +x /docker-entrypoint.d/40-stac-browser-entrypoint.sh

ENV SB_pathPrefix="${pathPrefix}"

EXPOSE 8080

STOPSIGNAL SIGTERM

USER nginx
