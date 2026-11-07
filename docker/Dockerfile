# ===========================================================================
# HireSnap frontend image - CRA bundle served by nginx, plus a dev stage.
# ===========================================================================
#
# Build context is ./frontend (docker/docker-compose.yaml: context: ../frontend,
# dockerfile: ./docker/Dockerfile), so every path below is relative to the
# frontend repository: package.json, src/, public/, nginx.conf.
#
# Four stages, in the order the file reads them:
#   base    - node + node_modules. Shared so 'npm ci' happens exactly once.
#   dev     - CRA dev server on :3000. docker-compose.dev.yaml builds THIS
#             stage (target: dev); it is not a separate Dockerfile because a
#             second file would drift from package-lock.json.
#   builder - the production bundle. REACT_APP_BASE_URL is baked in HERE.
#   runtime - nginx:1.27-alpine with the bundle and the SPA config.
#
# Mirror of R:\GPTHub\frontend\docker\Dockerfile:1-43 (same four-stage shape,
# same 'dependencies before source' layer order, same alpine nginx runtime).
# ===========================================================================

# --- base ------------------------------------------------------------------
# node:18-alpine: react-scripts 5.0.1 needs Node >= 14 and is known-broken on
# Node 20+ for some of its webpack 5 plugins; 18 is the version the lockfile was
# generated against. Alpine (musl) is fine because nothing here compiles native
# addons - the C# leftovers (client.sln/client.esproj) are not used.
FROM node:18-alpine AS base

WORKDIR /app

# Dependencies first, source second: this is the layer-cache rule that makes a
# source-only change rebuild in seconds instead of re-running npm ci
# (R:\GPTHub\frontend\docker\Dockerfile:5-6 does the same with node:20).
# 'npm ci', never 'npm install': ci installs exactly package-lock.json and fails
# if the two files disagree, which is what a reproducible image needs.
# The BuildKit cache mount keeps the npm cache out of the image but available
# to the next build - without it every rebuild re-downloads ~1500 packages.
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm config set registry "${NPM_REGISTRY}" \
    && npm ci --no-audit --no-fund

# --- dev -------------------------------------------------------------------
# Used by docker/docker-compose.dev.yaml (target: dev). In this stage the source
# is BIND-MOUNTED over /app/src and /app/public, so it is deliberately not
# copied here: a copy would be shadowed by the mount and, worse, would be the
# version that runs when someone forgets the mount.
#
# REACT_APP_BASE_URL is a RUNTIME variable here (compose environment:), because
# 'react-scripts start' reads process.env when the dev server compiles. The
# bundle is compiled on demand, so nothing is baked. In prod the same value must
# be a build ARG - that asymmetry is CRA's, not ours.
FROM base AS dev

ENV HOST=0.0.0.0 \
    PORT=3000
# HOST=0.0.0.0 is not the CRA default on every version: bound to 127.0.0.1 the
# dev server is unreachable from a published port and the container looks
# 'up but empty'.
# WDS_SOCKET_PORT makes the dev server's HMR websocket advertise the port the
# browser actually connected to (the published one) instead of the container's.
# Without it, hot reload silently stops working behind Docker NAT.
ENV WDS_SOCKET_PORT=3000

EXPOSE 3000
CMD ["npm", "start"]

# --- builder ---------------------------------------------------------------
FROM base AS builder

COPY . .

# REACT_APP_BASE_URL is BAKED INTO THE BUNDLE at build time - CRA inlines
# every REACT_APP_* variable into the JavaScript. It is therefore passed as a
# build ARG by compose (docker-compose.yaml: args: REACT_APP_BASE_URL), NOT as
# a runtime environment: a runtime variable cannot change a finished bundle,
# and exposing one would create the illusion that it can.
# R:\GPTHub\frontend\docker\Dockerfile:19-35 makes exactly this argument, in
# the opposite direction: it REJECTS a base-URL ARG because the prod bundle must
# call its own origin. HireSnap needs the ARG because its API prefix is '/api/'
# while the backend serves '/server/' - the gateway rewrites between them
# (infra/nginx/templates/site.conf.template:66-104), so the value must match the
# gateway, and the gateway is a deployment fact, not a constant.
#
# The default '/api/' is the value for a same-origin deployment (prod): the
# browser calls <origin>/api/..., which the gateway rewrites to the backend's
# /server/.... Dev overrides it with the backend's published port.
ARG REACT_APP_BASE_URL=/api/
ARG NPM_REGISTRY=https://registry.npmjs.org
ENV REACT_APP_BASE_URL=${REACT_APP_BASE_URL}
# DaData client token for the supplier-requisites autocomplete
# (src/components/forms/suppliers/requisites.js). Same mechanism as the base
# URL and the same consequence: CRA inlines REACT_APP_* into the bundle, so
# this value SHIPS TO THE BROWSER and is not a secret - it must be a
# rate-limited client key. It is deliberately an ARG with an EMPTY default and
# not a required one: unset is a supported state in which the form degrades to
# manual entry, so a missing token must not fail the build. Passed from the
# compose build args as ${DADATA_TOKEN}; see docker/.env.example section 8.
ARG REACT_APP_DADATA_TOKEN=
ENV REACT_APP_DADATA_TOKEN=${REACT_APP_DADATA_TOKEN}
# Source maps off: GENERATE_SOURCEMAP=false keeps the whole frontend source tree
# out of the published static files (a .map is served with the bundle and is a
# verbatim copy of src/). Costs debuggability of the prod bundle, which is what
# the dev stage exists for.
ENV GENERATE_SOURCEMAP=false
# NOT setting CI=true: CRA turns every ESLint warning into an error under CI,
# so a harmless unused-variable warning would fail the image build. The lint
# gate belongs to scripts/check_*.py and make lint, not to the image.
RUN npm run build

# --- runtime ---------------------------------------------------------------
# nginx:1.27-alpine, pinned to the same minor as the gateway (1.27-bookworm).
# Two nginx images of different minors in one stack is a second set of
# behaviour differences for no benefit - and the config below is shared in
# spirit with the gateway's.
FROM nginx:1.27-alpine AS runtime

# The stock image ships a default server that would answer :80 for any Host;
# replacing it with the SPA config is what makes 'unknown path -> index.html'
# work. /etc/nginx/conf.d/default.conf is included by the image's nginx.conf.
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/build /usr/share/nginx/html

# NOT switched to 'USER nginx', deliberately. Two reasons:
#   1. the master process must bind :80, and in a container a non-root process
#      only keeps CAP_NET_BIND_SERVICE if it is re-granted explicitly - the
#      gateway template hardcodes the upstream 'http://frontend:80'
#      (infra/nginx/templates/site.conf.template:44), so the port cannot move;
#   2. the official image already drops privileges the right way: the master
#      runs as root and every worker as 'nginx', so request handling - the part
#      that parses untrusted input - is unprivileged.
# A USER directive here would look stricter and would actually fail to start.

EXPOSE 80

# /health is answered by nginx itself (frontend/nginx.conf) and never proxied:
# the SPA container's only job is to serve files, so a probe that reaches the
# API would make one container's health depend on another's.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=5 \
    CMD wget -qO- http://localhost/health >/dev/null || exit 1
