# ADR 0003 — Rewrite `/api/` to `/server/` at the gateway instead of editing the frontend

- **Status:** accepted
- **Date:** 2026-11-07
- **Affects:** `infra/nginx/conf.d/`, `frontend/src/shared/api/apiConsts.js` (documented, not
  required), `docs/KNOWN_GAPS.md`

## Context

The two applications disagree about where the API lives:

- The frontend sends every request to `/api/...` — originally hardcoded as
  `http://localhost:8080/api/`, today the last fallback in
  `frontend/src/shared/api/apiConsts.js:46` — and consumed by
  the shared axios instance (`frontend/src/shared/api/instance.js:6-8`).
- The backend mounts its API at `/server` — `app.mount("/server", build_server_app())`
  at `backend/service/api/app.py:128`.

Neither side is wrong in isolation; together they 404 on every call. Something must
bridge them, and there are three places it could happen:

1. **Change the frontend** to call `/server/`.
2. **Change the backend** to mount at `/api`.
3. **Change neither** and reconcile at the edge.

## Decision

**Reconcile at the edge.** The gateway owns the public prefix
(`infra/nginx/templates/site.conf.template:77-104`):

```nginx
location /api/ {
    rewrite ^/api/(.*)$ /server/$1 break;
    proxy_pass $backend_upstream;          # set $backend_upstream http://backend:8080
}
```

The `rewrite` replaces the matched prefix `/api/` with `/server/`, and `break` stops
location re-evaluation so the rewritten URI is what gets proxied. The replacement string
keeps its trailing slash and `$1` carries the rest of the path, so `/api/vacancies/`
arrives as `/server/vacancies/`. Drop the `break`, or the slash in `/server/`, and
every call 404s.

Why not the shorter `proxy_pass http://backend:8080/server/`, which performs the same
substitution on its own? Because the upstream is a **variable**. `site.conf.template:43-44`
sets `$backend_upstream` and `$frontend_upstream` so that the address is resolved per
request by Docker's embedded DNS (`resolver 127.0.0.11 valid=10s`,
`infra/nginx/nginx.conf:34`) instead of once at startup, which is what keeps the gateway
working across a backend container restart with a new IP. nginx **rejects a URI part in
`proxy_pass` when the target is a variable** — the config fails to load — so the
substitution has to be an explicit `rewrite`. Same result, one legal spelling.

## Rationale

1. **Both repositories remain valid standalone checkouts.** `backend/` and `frontend/`
   are independent repos with their own history and their own deployments. An edit to
   the mount path changes the public contract of the backend for every other consumer —
   including whoever runs it without this stack.
2. **The change is reversible in one file.** If the application is later aligned (the
   frontend base URL becomes configurable, the backend mounts at `/api`), the rewrite is
   deleted from one nginx file and the stack keeps working. A source-level fix welded
   into two repositories cannot be undone as cheaply.
3. **The rewrite is the correct place for a public prefix.** `/api/` is a property of the
   *deployment*, not of either application: a versioned public prefix (`/api/v1/`) is a
   gateway concern in every stack that has one. Putting it in nginx means the next
   consumer of the backend is not forced to inherit our public naming.
4. **It keeps the failure visible.** The rewrite does not hide the drift: `/api/` and
   `/server/` both still exist, and the mismatch is documented as a known defect
   ([KNOWN_GAPS.md](../KNOWN_GAPS.md#1-api-prefix-drift-api-versus-server)) rather than
   erased. A future reader can see exactly what the application does.

## The one allowed frontend patch

`frontend/src/shared/api/apiConsts.js` is patched — by a different owner, not by this ADR's
scope — to make the base URL configurable while keeping the current value as the
fallback. That patch does **not** change the `/api/` prefix; it changes where the prefix
points (same origin in production instead of `localhost:8080`). The gateway rewrite
remains necessary either way.

## Alternatives rejected

- **Edit the frontend to call `/server/`.** Every deployed SPA would need a rebuild, the
  backend's public prefix would become whatever the SPA happens to use, and the two-repo
  contract changes for a purely deployment-shaped reason.
- **Edit the backend to mount at `/api`.** Breaks any other consumer and turns a
  deployment decision into an application compatibility problem. Also touches route
  mounting, which is more invasive than a string.
- **Double-mount both prefixes.** Works, but permanently doubles the API surface and makes
  the "one canonical URL" question unanswerable. It also silently accepts requests at a
  path nobody documented.

## Consequences

- Anything bypassing the gateway (a direct call to `backend:8080`) must use `/server/`.
  In dev that port is published, and the runbook says so.
- `/data/` is **not** rewritten: the backend already serves uploads at that prefix
  (`backend/service/api/app.py:139`), so a rewrite there would break them. The gateway
  block for `/data/` exists only to put API and media on one origin and avoid CORS.
- The gateway must be the only ingress in production, or the drift becomes user-visible.
  That is why prod publishes exactly one port.
- The rewrite applies to the prefix, not to the whole URL: query strings and fragments
  pass through untouched, and a request already addressed to `/server/...` is served by
  the separate passthrough location (`site.conf.template:132-139`) rather than rewritten
  twice.
