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

### 1.3 Where each health route actually lives

The shape in §1.1 is now implemented twice — once by the gateway as a static body, once by
a real route in the API — and the two sidecars answer with their own. What each one
promises is different, and the difference is the reason this section exists.

- **`GET /health`, at the root, served by the gateway itself.** A static
  `{"status":"ok","service":"gateway"}` (`infra/nginx/templates/site.conf.template:60-64`),
  `access_log off`, never proxied. It answers even when the backend is down, which is the
  point: a liveness probe that fails because a *dependency* is down restarts the wrong
  container. It does not carry `version` — adding one would mean rendering the gateway
  image tag into the template, and the tag is already visible in `docker compose ps`. The
  field is specified in §1.1 so that every endpoint agrees on the shape.
- **`GET /server/health`, served by the API.** It returns the §1.1 body with
  `service=backend` and `version` from the application's `__version__`, and it touches no
  database: it answers while MySQL is down, which is what makes it a liveness probe
  rather than an availability probe. It is registered on the `/server` sub-app, not on the
  root app, so on the compose network it is `http://backend:8080/server/health` and
  through the gateway it is `http://<host>:8088/api/health` — the `/api/` → `/server/`
  rewrite of §3.1 applies to it like to every other API route
  ([ARCHITECTURE.md §3.1](ARCHITECTURE.md#31-the-api-prefix-and-the-server-mount)).
- **`GET /health`, served by `ml`.** The sidecar answers §1.1 exactly, with
  `service=ml`, and `status` of `ok` or `degraded` — never `down`, because a live process
  can still answer while a model is missing. It never loads a model, never touches a GPU
  and never reads a volume. `GET /v1/capabilities` is the probe that tells you *what* is
  missing, and it is the one to alert on. Canonical description:
  [ml/README.md](../ml/README.md#http-contract).
- **`GET /health`, served by the SPA container.** `frontend/nginx.conf` answers it
  itself, so the static container reports healthy while the API restarts.

The path is spelled `/server/health` and not `/health` on the API on purpose: the gateway
does not proxy `/health`, and a second root health URL behind a prefix that the edge
rewrites would be a route nobody can reach. `scripts/check_api_prefix_contract.py` asserts
that the API prefix and the mounts agree, which is what keeps this honest.

**What the container probes do.** Both compose files and the images still probe the
service with the endpoint each one can reach — the gateway with `/health`, the API with
`/server/openapi.json` (a 200 proves the app object, including its `StaticFiles` mount,
was constructed), the SPA with `wget http://localhost/health`, the sidecar with its own
`/health`. Only the sidecar's probe is a §1.1 contract check; the others are transport
checks, and §1.4 says what each of them actually proves.

> Field naming is fixed: `status`, `service`, `version`, all lowercase, all strings, no
> nesting. Three flat fields parse in every language and every probe framework; the
> nested variants (`{"status":{"healthy":true}}`) do not.

### 1.4 The full inventory, and what each probe actually runs

Twelve services per stack. Two of them implement §1.1 — `ml` and the API's
`/server/health` — and the gateway answers a static body of the same shape. Every other
probe is either a transport check or a process check, and knowing which is which prevents
the classic mistake of reading a green container as a working feature.

| Service | Probe | What a green result actually proves |
|---------|-------|--------------------------------------|
| `gateway` | `curl -fsS http://localhost/health` | nginx is up and the config parsed. The body is static (`infra/nginx/templates/site.conf.template:60-64`), so it says nothing about the backend or the database. |
| `backend` | `curl -fsS http://127.0.0.1:$SERVER_PORT/server/openapi.json` (the image's `HEALTHCHECK`), or `curl -fsS .../server/health` for the §1.1 body | The FastAPI process imported, the routers mounted and uvicorn is accepting. Neither probe touches MySQL, and only `/server/health` proves the §1.1 shape. |
| `frontend` | `wget -qO- http://localhost` | The static server answers with the SPA shell. It does not prove the bundle can reach the API — that is a browser-console fact. |
| `mysql` | `mysqladmin ping -h 127.0.0.1 -u$MYSQL_USER -p$MYSQL_PASSWORD --silent` | The server accepts authenticated connections. It does not prove the schema or the seed data exist ([RUNBOOK.md §8](RUNBOOK.md#8-database-initialization)). |
| `cache` | `redis-cli ping` | Redis answers on its own port. It does not prove the application's `CACHE__BACKEND` points here — in prod the default is `memory`. |
| `storage` | `mc ready local` | The MinIO server is ready to serve the alias. It does not prove the bucket exists or that `STORAGE__BACKEND=s3`. |
| `ml` | `curl -fsS http://127.0.0.1:${ML_PORT}/health` | The sidecar is up and returned `ok` or `degraded`. `degraded` is a **passing** probe: weights are a deployment fact, not a liveness fact. |
| `celery-worker-agents` | `celery -A service.infrastructure.messaging.celery_app inspect ping -d celery@$HOSTNAME --timeout 10` | A worker process with that nodename answered a round trip **through the broker**, so it proves both the worker and Redis. |
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

The application answers with **`detail` alone, with no `error` code** — half of the
contract above:

```json
{"detail": "Vacancy not found"}
```

`detail` is therefore the field a client can rely on: it is always present and always a
string. The `error` code is missing, and adding it is an application change recorded in
[KNOWN_GAPS.md](KNOWN_GAPS.md#9-errors-have-no-stable-code) rather than done here.

**How the envelope is produced.** A rule that fails raises a domain error, not a
`HTTPException`: `service/shared/errors.py` defines `ServiceError(status_code, detail)`
with `NotFoundError`, `ConflictError`, `InvalidPayloadError` and `UnauthorizedError`
beneath it, and **one** handler registered on the `/server` sub-app renders any of them as
`{"detail": <detail>}` with its own status. So `domain/` and `application/` modules import
no HTTP framework — the framework is a leaf at the edge of the process — and the status
each failure carries is decided in exactly one place. FastAPI's own request-validation
failure is the one exception: it never becomes a `ServiceError` and keeps its `422`.

Observed statuses and detail strings, unchanged by that refactor (preserving the wire
contract beat tidying it):

| Status | Detail strings in use |
|--------|-----------------------|
| 400 | Validation failures from the candidate and personality repositories (`InvalidPayloadError`). |
| 401 | `Invalid password`, `Invalid type user`, `User not found`, `Invalid token error` — all four from `service/services/auth/application/auth_service.py:66`, `:72`, `:84`, `:106`. |
| 403 | `Not authenticated` (the Starlette base class rejects a missing header, `auto_error=True`), `Invalid token or expired token.` (the token did not verify **or** the subject is not `admin` on a level-1 route), `Invalid authorization code.` (only reachable with `auto_error=False`). |
| 404 | `<Entity> not found` — Candidate vacancy, Embedding, File, Info candidate, Magic, Personality, Personality candidate, Personality vacancy, User, Vacancy. **Also `Email already exist`**, which reads like a 409 and has always been a 404: the refactor keeps it because changing it would break every client that already special-cases the 404. |

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
| `401` | A sign-in failure: wrong password, wrong account type, unknown user, or a token whose type does not match the route (`service/services/auth/application/auth_service.py:66`, `:72`, `:84`, `:106`) | Refresh the token, then retry once. |
| `403` | The header is missing (`Not authenticated`) or the token did not verify — including the non-admin case, which surfaces here rather than as a `401` (`backend/service/api/security.py:37-44`, see §2.2) | Do not retry; re-authenticate from scratch. |
| `404` | The addressed entity does not exist | Do not retry. |
| `413` | Body exceeds the gateway limit (`client_max_body_size 500m`, `infra/nginx/nginx.conf:71`) | Shrink the upload. |
| `422` | FastAPI request-validation failure (body/query schema) | Fix the payload. Not produced by hand-written `HTTPException`s. |
| `500` | Unhandled exception — a bug or a dependency failure | Retry with backoff only if idempotent; otherwise report. |
| `502` | Gateway only: upstream unreachable | Restart the upstream. |
| `503` | Gateway only: upstream unreachable, crashed or timed out — `service_unavailable` with `Retry-After: 30` | Wait for the header, then retry once. See §4 on why this is the code you will actually see for a slow backend. |
| `504` | Gateway only: upstream timed out. **Effectively unreachable in this stack** — see §4. | Same as `503`. |

Two documented quirks of the auth dependency are worth knowing before you branch on a status
(`backend/tests/test_security_jwt.py` pins both, `REPORTED, NOT FIXED`):

1. **A non-admin on a level-1 route gets `403`, not `401`.** `verify_jwt()` raises
   `401 Have not access` (`backend/service/api/security.py:52`) *inside its own*
   `try/except Exception`, so the exception is swallowed, the function returns `False`, and the
   caller renders `403 Invalid token or expired token.` (`test_security_jwt.py:123-137`). The
   client cannot tell "not an admin" from "expired".
2. **`Invalid authentication scheme.` is dead code.** Starlette's `HTTPBearer` rejects a
   non-bearer scheme before `JWTBearer.__call__` reaches its own check, so that branch
   (`security.py:33-36`) cannot be reached in either `auto_error` mode
   (`test_security_jwt.py:71-86`).

The 401/403 split is therefore narrower than the table suggests: on an authenticated request,
treat `401` and `403` identically — re-authenticate once, then surface the error. Both quirks
are recorded as [KNOWN_GAPS.md](KNOWN_GAPS.md) §30 and §31.

## 4. Deadlines

Timeouts nest, and the innermost deadline must fire first so that the outer layer
reports a specific error rather than a generic timeout. What the stack actually ships:

Every `infra/nginx/nginx.conf` reference below is the gateway's main config; `frontend/nginx.conf` is the static-file server inside the frontend image and is not part of this path.

```
client / SPA axios               (no timeout configured — browser default)
   └── gateway proxy_connect_timeout    7 s      infra/nginx/nginx.conf:120
   └── gateway proxy_send_timeout       86400 s  infra/nginx/nginx.conf:121
   └── gateway proxy_read_timeout       86400 s  infra/nginx/nginx.conf:122
         └── backend handler work       (no explicit budget)
               ├── MySQL query          (no explicit budget)
               └── ML transport        connect 10 s / read 900 s
                                       ML_SERVICE_CONNECT_TIMEOUT / ML_SERVICE_TIMEOUT
                                       (docker/.env.example:269,:273)
   └── gateway client_body_timeout      10 s     infra/nginx/nginx.conf:72
   └── gateway client_header_timeout    10 s     infra/nginx/nginx.conf:73
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
   `interval 30s / timeout 10s / retries 5 / start_period 180s` (:272-282 — 180 s is now
   headroom rather than a torch import, because the API image carries no GPU framework),
   frontend `30s / 5s / 5` (:315-319), gateway `30s / 5s / 5` (:354-362), mysql
   `10s / 5s / 10 / 60s` (:210-215), cache `10s / 5s / 5` (:375-379), storage
   `30s / 10s / 3` (:405-409), **ml `30s / 10s / 5 / start_period 300s`** (:538-546 —
   the slow starter: the first request downloads Hugging Face weights into `hf_cache`
   and then creates a CUDA context), the three celery workers
   `60s / 20s / 3 / 60s` (:617-622, :659-664, :712-717), celery-beat
   `60s / 10s / 3 / 60s` (:755-760) and mailpit `15s / 10s / 5 / 10s` (:792-799).
   A dev stack relaxes the backend to `interval 15s / start_period 60s`
   (`docker/docker-compose.dev.yaml:194`, `:197`) while the sidecar keeps its 300 s.
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
- **The route surface is snapshotted.** `backend/tests/snapshots/critical_endpoint_openapi.json`
  records the paths, methods, status codes and response models of the endpoints the SPA
  calls, plus `/server/health`. A test regenerates the document from the running app and
  compares it, so moving a route, renaming a field or changing a response model fails in the
  backend suite instead of in a browser.
- **Regenerating is a deliberate act**, and the command is run from `backend/`:

  ```bash
  python -m tests.snapshot_tool            # compare; what the test and CI do
  python -m tests.snapshot_tool --write    # accept the new contract, then read the diff
  ```

  The snapshot covers a chosen subset rather than the whole generated document: a full dump
  changes whenever a description is reworded, which trains a reviewer to accept it without
  reading. `docs/RUNBOOK.md` §16.2 has the same recipe from the operator's side.

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
