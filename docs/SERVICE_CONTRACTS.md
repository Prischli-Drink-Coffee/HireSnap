# Service contracts

This document defines what each service promises to its callers: the shape of a
health response, the shape of an error, the status codes in use, and the order in which
deadlines apply. It is written as a **target contract** in places where the current
application does not meet it; those places are marked **GAP** and cross-referenced from
[KNOWN_GAPS.md](KNOWN_GAPS.md).

The rule behind every decision here: a contract exists so that a client, a probe, or an
operator can tell "working" from "broken" without reading logs. Anything that requires
reading logs to distinguish is not a contract.

## 1. Health

### 1.1 The contract

```http
GET /health
200 OK
Content-Type: application/json

{
  "status": "ok",
  "service": "gateway",
  "version": "1.27.3"
}
```

| Field | Type | Meaning |
|-------|------|---------|
| `status` | string | `ok` when the service can serve traffic; `degraded` when it can serve but a dependency is down; `down` is never returned by a live process (a dead process cannot answer). |
| `service` | string | The service name exactly as it appears in compose: `gateway`, `backend`, `frontend`, `mysql`, `cache`, `storage`, `ml`. |
| `version` | string | The deployed version: image tag, or the application's version constant. Not the API version. |

The response must be cheap: no database query, no model load, no filesystem scan. A
health check that can fail because MySQL is slow turns a database incident into a
restart loop, which is worse than the original outage.

### 1.2 Status codes for health

| Code | Meaning | What a probe should do |
|------|---------|------------------------|
| `200` | `status: "ok"` or `"degraded"` | Stay up. `degraded` is informational; do not restart on it. |
| `503` | `status: "down"` — only from a dependency-aware endpoint that deliberately reports failure | Restart or alert. |
| `502` | Gateway could not reach the upstream | Upstream is down; restart the upstream, not the gateway. |

### 1.3 What exists today — GAP

```
$ grep -RIn "health" backend/src/pipeline/server.py
(no matches)
```

**The backend has no health route.** It mounts `/server`, `/public` and `/data`
(`backend/src/pipeline/server.py:30-32`) and nothing else. What exists instead:

- **`GET /health`, at the root, served by the gateway itself.** It returns a static
  `{"status":"ok","service":"gateway"}` body (`infra/nginx/templates/site.conf.template:60-64`),
  `access_log off`, and is never proxied. It answers even when the backend is down,
  which is the point: a liveness probe that fails because a *dependency* is down restarts
  the wrong container.
- It does **not** carry `version`. Adding one would mean rendering the gateway image tag
  into the template, and the tag is already visible in `docker compose ps`. The field
  is specified above so that any future endpoint agrees on the shape.
- Container health checks therefore use `GET /server/openapi.json` — served by FastAPI
  itself, so a 200 proves the app imported and the router is up. It is heavier than a real
  health route but it is honest: it fails exactly when the app fails.
- `/api/health` does **not** exist. `/api/` is rewritten to `/server/`, so that
  request would become `/server/health` and 404. Use `/health` at the root.
- **The one service that implements this shape is the ml sidecar.** `GET /health` on
`ml/service/app.py` returns exactly `{"status","service","version"}` with `service=ml`,
  `version=1.0.0`, and `status` of `ok` or `degraded` — never `down`, because a
  live process can still answer while a model is missing. It never loads a model, never
  touches a GPU and never reads a volume, so it stays cheap exactly as §1.1 requires.
  It answers 200 in both states by design: a probe that restarted the sidecar because
  the weights are not mounted would turn a configuration error into a crash loop.
  `GET /v1/capabilities` is the probe that tells you *what* is missing, and it is the
  one to alert on. Canonical description: [ml/README.md](../ml/README.md#http-contract).
- Adding a real `/server/health` route is an application change and outside this stack's
  mandate. If one is added it must return the shape in §1.1 and must not touch the
  database.

> Field naming is fixed: `status`, `service`, `version`, all lowercase, all strings,
> no nesting. Three flat fields parse in every language and every probe framework; the
> nested variants (`{"status":{"healthy":true}}`) do not.

### 1.4 The full inventory, and what each probe actually runs

Twelve services per stack. Only `ml` implements §1.1; every other probe is either a
transport check or a process check, and knowing which is which prevents the classic
mistake of reading a green container as a working feature.

| Service | Probe | What a green result actually proves |
|---------|-------|--------------------------------------|
| `gateway` | `curl -fsS http://localhost/health` | nginx is up and the config parsed. The body is static (`infra/nginx/templates/site.conf.template:60-64`), so it says nothing about the backend or the database. |
| `backend` | `curl -fsS http://127.0.0.1:$SERVER_PORT/server/openapi.json` | The FastAPI process imported, the routers mounted and uvicorn is accepting. It does **not** touch MySQL or the weights. |
| `frontend` | `wget -qO- http://localhost` | The static server answers with the SPA shell. It does not prove the bundle can reach the API — that is a browser-console fact. |
| `mysql` | `mysqladmin ping -h 127.0.0.1 -u$MYSQL_USER -p$MYSQL_PASSWORD --silent` | The server accepts authenticated connections. It does not prove the schema or the seed data exist ([RUNBOOK.md §8](RUNBOOK.md#8-database-initialization)). |
| `cache` | `redis-cli ping` | Redis answers on its own port. It does not prove the application's `CACHE__BACKEND` points here — in prod the default is `memory`. |
| `storage` | `mc ready local` | The MinIO server is ready to serve the alias. It does not prove the bucket exists or that `STORAGE__BACKEND=s3`. |
| `ml` | `curl -fsS http://127.0.0.1:${ML_PORT}/health` | The sidecar is up and returned `ok` or `degraded`. `degraded` is a **passing** probe: weights are a deployment fact, not a liveness fact. |
| `celery-worker-agents` | `celery -A src.workers.celery_app inspect ping -d celery@$HOSTNAME --timeout 10` | A worker process with that nodename answered a round trip **through the broker**, so it proves both the worker and Redis. |
| `celery-worker-maintenance` | same `inspect ping` | Same, for the maintenance queue. |
| `celery-worker-ml` | same `inspect ping` | Same, for the ML queue. It proves the worker is alive, not that the GPU or the weights work — the first real task is what asks that. |
| `celery-beat` | `test -s /tmp/celerybeat.pid && kill -0 $(cat /tmp/celerybeat.pid)` | The scheduler wrote its pid file and that pid is still alive. Deliberately no broker round trip: beat's job is to emit, and a broker outage shows up in the workers' probes instead of flapping beat. |
| `mailpit` | `/mailpit readyz` | Both the SMTP and the HTTP listeners are bound. It says nothing about whether anything sends mail. |

Two consequences worth stating once:

- **A green `docker compose ps` is a transport statement, not a feature statement.** The
  platform layer can be fully green (`cache`, `storage`, the three workers and `mailpit`
  all healthy) while no request path calls any of it
  ([KNOWN_GAPS.md §13](KNOWN_GAPS.md#13-the-platform-layer-is-delivered-but-its-call-sites-are-thin)).
- **Probes are written with a single `$` here on purpose.** In the compose files they
  appear as `$${ML_PORT}` and `$${SERVER_PORT}`: the doubled `$` is compose's escape, so
  the literal `${ML_PORT}` reaches the container's shell and expands there. A single `$`
  in the compose file would be interpolated on the host at `docker compose config` time.

## 2. Errors

### 2.1 The contract

```http
HTTP/1.1 404 Not Found
Content-Type: application/json

{
  "error": "not_found",
  "detail": "Vacancy not found"
}
```

| Field | Type | Meaning |
|-------|------|---------|
| `error` | string | A stable, machine-readable code. Never a sentence, never translated, never interpolated with user data. |
| `detail` | string | A human-readable explanation, safe to show to an operator. May be shown to an end user, so it must not leak internals. |

Rules:

- `error` is the API contract; `detail` is for humans. Clients branch on `error`, log
  `detail`.
- Never put a stack trace, SQL statement, file path, or secret in `detail`.
- `detail` may change wording at any time; `error` may not change without a version bump.

### 2.2 What the backend actually returns (partial GAP)

The application returns FastAPI's default error envelope, which is:

```json
{"detail": "Vacancy not found"}
```

That is, **`detail` alone, with no `error` code.** It matches the contract for exactly
half of the fields. The behavior is at least consistent — the application raises
`HTTPException(status_code=404, detail="Vacancy not found")` and similar across
`src/services/` — so clients can rely on `detail` being present and being a string.

Observed detail strings today (from `backend/src/`):

| Status | Detail strings in use |
|--------|-----------------------|
| 400 | Validation failures raised from the candidate and personality repositories. |
| 401 | `Invalid password`, `Invalid type user`, `User not found`, `Invalid token error`, `Have not access` |
| 403 | `Invalid authentication scheme.`, `Invalid token or expired token.`, `Invalid authorization code.` |
| 404 | `<Entity> not found` — Candidate vacancy, Embedding, File, Info candidate, Magic, Personality, Personality candidate, Personality vacancy, User, Vacancy |

Adding the `error` code is an application change (a single exception handler would do
it) and therefore outside this stack's mandate; it is listed in
[KNOWN_GAPS.md](KNOWN_GAPS.md#9-errors-have-no-stable-code).

### 2.3 What the gateway returns

The gateway is not the application, and its errors do not come from FastAPI. They are
generated by nginx itself and they **do** carry an `error` code — a different one, with
extra fields:

```json
// upstream unreachable / crashed / timed out  (site.conf.template:201-207)
{"error":"service_unavailable","status":503,"uri":"/api/vacancies","request_id":"6f1c..."}
```

```json
// nginx rejected the request itself        (site.conf.template:209-214)
{"error":"bad_request","status":400,"uri":"/api/vacancies","request_id":"6f1c..."}
```

Read the two together: a client that wants a stable code can branch on `error` when it
is present (gateway failures) and fall back to `detail` when it is absent (application
failures). A client that only branches on `error` will see `undefined` on every 404 that
came from the application, and a client that only reads `detail` will see `undefined` on
every `503`. That asymmetry is the real cost of not fixing the application envelope, and
it is why [KNOWN_GAPS.md](KNOWN_GAPS.md#9-errors-have-no-stable-code) keeps it open.

`request_id` is the same value as the `X-Request-ID` response header
(`infra/nginx/nginx.conf:106`), so a user-reported error can be found in the gateway log
(`log_format main`, `infra/nginx/nginx.conf:50-53`) even when the application never saw
the request.

## 3. Status codes

The codes the API actually uses, and what they mean for a client:

| Code | Used for | Client action |
|------|----------|---------------|
| `200` | Success with a body | Parse it. |
| `201` | Created (upload endpoints) | Read the returned object id. |
| `400` | Malformed request or a rule violation the caller can fix — also the gateway's `bad_request` | Fix the request; do not retry unchanged. |
| `401` | Missing, expired or invalid credentials — **or** insufficient access: `jwt_bearer.py` raises `401 Have not access` when a non-admin hits an admin route | Refresh the token, then retry once. |
| `403` | The credentials are structurally wrong (`Invalid authentication scheme.`) or the token did not verify | Do not retry; re-authenticate from scratch. |
| `404` | The addressed entity does not exist | Do not retry. |
| `413` | Body exceeds the gateway limit (`client_max_body_size 500m`, `infra/nginx/nginx.conf:71`) | Shrink the upload. |
| `422` | FastAPI request-validation failure (body/query schema) | Fix the payload. Not produced by hand-written `HTTPException`s. |
| `500` | Unhandled exception — a bug or a dependency failure | Retry with backoff only if idempotent; otherwise report. |
| `502` | Gateway only: upstream unreachable | Restart the upstream. |
| `503` | Gateway only: upstream unreachable, crashed or timed out — `service_unavailable` with `Retry-After: 30` | Wait for the header, then retry once. See §4 on why this is the code you will actually see for a slow backend. |
| `504` | Gateway only: upstream timed out. **Effectively unreachable in this stack** — see §4. | Same as `503`. |

The 401/403 split is not clean in the current code: a missing scheme and an expired
token both mean "authenticate", but they arrive as different codes. Clients should treat
`401` and `403` on an authenticated request identically — re-authenticate once, then
surface the error. This is a documented consequence of the code, not a design goal.

## 4. Deadlines

Timeouts nest, and the innermost deadline must fire first so that the outer layer
reports a specific error rather than a generic timeout. What the stack actually ships:

Every `nginx.conf:<line>` below is `infra/nginx/nginx.conf` (the gateway's main config); `frontend/nginx.conf` is the static-file server inside the frontend image and is not part of this path.

```
client / SPA axios               (no timeout configured — browser default)
   └── gateway proxy_connect_timeout    7 s      nginx.conf:120
   └── gateway proxy_send_timeout       86400 s  nginx.conf:121
   └── gateway proxy_read_timeout       86400 s  nginx.conf:122
         └── backend handler work       (no explicit budget)
               ├── MySQL query          (no explicit budget)
               └── ML transport        connect 10 s / read 900 s
                                       ML_SERVICE_CONNECT_TIMEOUT / ML_SERVICE_TIMEOUT
                                       (docker/.env.example:243,:247)
   └── gateway client_body_timeout      10 s     nginx.conf:72
   └── gateway client_header_timeout    10 s     nginx.conf:73
```

The read and send budgets are **24 hours, not 60 seconds**, on purpose: video embedding
runs for minutes and nginx's 60 s default would reset the connection while the backend
kept working, which surfaces to the user as a spurious `504` and to the operator as a
backend that "sometimes fails" while its logs show success
(`infra/nginx/nginx.conf:117-119`).

The ML transport budget is nested inside it deliberately: 900 s of read timeout is
shorter than 86400 s at the gateway, so when the sidecar hangs the client raises
`MLUnavailable` and the API returns a specific error instead of the gateway eventually
severing a request the backend still believes in. A video that legitimately needs more
than 15 minutes of GPU time fails at the client, by design — raising
`ML__TIMEOUT` past the gateway budget only moves the ambiguity.

Rules:

1. **Outer > inner, always.** If the gateway times out before the backend, the client
   sees an error with no information while the backend keeps working on a request nobody
   will read. The 86400 s upstream budget is the extreme version of this rule: it is
   deliberately far longer than any real request, so the *backend* decides when to give
   up.
2. **Long-running work must not live in a request.** Video embedding is the obvious
   offender: it is CPU/GPU-bound and can take minutes. Today it is requested
   synchronously, so the HTTP deadline *is* the work deadline. Raising the timeout is a
   coping strategy, not a fix; see [KNOWN_GAPS.md](KNOWN_GAPS.md).
3. **Uploads need a body budget as well as a time budget.**
   `client_max_body_size 500m` (`infra/nginx/nginx.conf:71`, mirroring the same knob at
   `R:/GPTHub/infra/nginx/nginx.conf:30` but raised from 200m to 500m) rejects early with
   `413` instead of streaming gigabytes into a doomed request. The application's own
   limit stays in force behind it — the edge limit is a cheap pre-filter, not the
   security boundary.
4. **Health checks get a budget that fits a slow start, not the shortest one.** The
   shipped probes are, in `docker/docker-compose.yaml`: backend
   `interval 30s / timeout 10s / retries 5 / start_period 180s` (:262-270 — 180 s is now
   headroom rather than a torch import, because the API image carries no GPU framework),
   frontend `30s / 5s / 5` (:303-308), gateway `30s / 5s / 5` (:342-350), mysql
   `10s / 5s / 10 / 60s` (:202-207), cache `10s / 5s / 5` (:363-368), storage
   `30s / 10s / 3` (:393-398), **ml `30s / 10s / 5 / start_period 300s`** (:522-530 —
   the slow starter: the first request downloads Hugging Face weights into `hf_cache`
   and then creates a CUDA context), the three celery workers
   `60s / 20s / 3 / 60s` (:604-609, :646-651, :698-703), celery-beat
   `60s / 10s / 3 / 60s` (:741-746) and mailpit `15s / 10s / 5 / 10s` (:778-785).
   A dev stack relaxes the backend to `interval 15s / start_period 60s`
   (`docker/docker-compose.dev.yaml:163`, `:166`) while the sidecar keeps its 300 s.
   A probe with a 2–3 s budget would mark a healthy container dead during its very first
   start, which is why the two slow starters — the backend's MySQL wait and the
   sidecar's model load — get the loosest budgets in the stack.

Nothing in the **application** enforces these numbers today — there are no explicit
timeouts in the backend and no timeout in the SPA's axios instance. The values above are
what the gateway enforces; they are stated here so that any future client code has a
reference to align with, and so that an operator seeing `503` knows which layer gave up.

## 5. Versioning

- The stack deploys a **commit**, not a semantic version. `version` in a health payload
  should be the image tag or the short commit hash.
- The API has no version prefix: routes live at `/server/<resource>/`. Changing that is
  an application change.
- Backwards compatibility is the frontend's problem: the SPA and the API are deployed
  together from the same superproject commit, so there is no independent client to keep
  compatible.

## 6. Gateway-specific contract

The gateway is the only published endpoint in production, so it owns the public
contract:

| Path | Upstream | Rewrite | Notes |
|------|----------|---------|-------|
| `/health` | none (static) | — | Answers even when the backend is down. `200` with `{"status":"ok","service":"gateway"}`. |
| `/api/...` | `backend:8080` | `rewrite ^/api/(.*)$ /server/$1 break;` | The prefix swap happens in `rewrite`, **not** in `proxy_pass` — the upstream is a variable, so nginx forbids a URI part there (`site.conf.template:77-104`, [ARCHITECTURE.md §3.1](ARCHITECTURE.md#31-the-api-prefix-and-the-server-mount)). Rate limited to `10r/s` with `burst=20 nodelay`. |
| `/server/...` | `backend:8080` | none | Passthrough. Same handler, so `/server/x` and `/api/x` are the same resource. |
| `/data/...` | `backend:8080` | none | Static uploads. `proxy_buffering off` so a video plays while it streams. |
| `/docs`, `/redoc`, `/openapi.json` | `backend:8080` | prefix swap | Rendered with `X-Forwarded-Prefix: /api` so the generated links point back through the gateway. |
| `/` and everything else | `frontend:80` | none | SPA, with `index.html` fallback for client-side routes. Hashed assets get `Cache-Control: immutable` for a year; `.html` gets `no-store`. |

Edge responses that do **not** come from the application: `413` (body over 500m),
`429`-style shedding via `limit_req` (nginx returns `503` for that zone, not 429),
`400` from `@client_error` and `503` from `@service_unavailable` (§2.3), all in JSON.

Response headers added at the edge: `X-Content-Type-Options: nosniff`,
`X-Frame-Options: SAMEORIGIN`, `X-XSS-Protection: 1; mode=block`,
`Referrer-Policy: strict-origin-when-cross-origin`, `X-Request-ID` — the same set as
`R:/GPTHub/infra/nginx/nginx.conf:48-51` (`infra/nginx/nginx.conf:102-106`), which exists
because the SPA is served from the same origin as the API and must not be framed or
MIME-sniffed by a third party. The `server` block re-declares them
(`site.conf.template:49-53`) because `add_header` does not merge across levels: a
`location` that declares any `add_header` discards every inherited one.
