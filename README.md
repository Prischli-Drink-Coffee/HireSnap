# HireSnap

HireSnap is a candidate-screening service: a candidate uploads a short self-presentation
video, the backend extracts a personality profile (Big Five / MBTI) plus text and video
embeddings, and the service matches candidates against vacancies.
The repository is a **git superproject**. Product code lives in three independent
submodules that also exist as standalone checkouts:

| Submodule   | Stack                                                          | Branch     |
|-------------|----------------------------------------------------------------|------------|
| `backend/`  | Python 3.9, FastAPI + uvicorn, PyMySQL — business logic only    | `backend`  |
| `frontend/` | React 18, CRA / react-scripts 5.0.1, Chakra UI, axios           | `frontend` |
| `ml/`       | Python 3.9, FastAPI, CUDA image — the inference sidecar         | `ml`       |

All three point at the same remote (`Prischli-Drink-Coffee/HireSnap`) and differ only by
branch; the superproject itself is on `dev`. Deployment code, infrastructure and
documentation are owned by the superproject and are described below.

> **Deployment status:** `docker/` and `infra/` carry a complete container stack — prod,
> dev, and four optional profiles (`cache`, `storage`, `celery`, `mail`) around a
> five-service core (`mysql`, `backend`, `frontend`, `gateway`, `ml`). `ml/` is **not**
> one of those profiles: it is a core service of both stacks, because the API has no
> in-process inference path left to fall back to. Inference is one HTTP call from
> `backend/service/infrastructure/ml_sidecar.py`; whether the host has a GPU is a
> separate switch (`GPU=1`, which merges a one-block override file). It wraps the existing
> application code without restructuring it apart from that boundary, which
> [ADR/0006](docs/ADR/0006-ml-sidecar-owns-inference.md) records; the application's known
> defects are documented rather than silently patched. The backend carries a platform layer
> (cache, storage, mail, celery) whose facades and workers are wired while its business
> **call sites are still thin** — no route uses them yet, and the vertical slices are a
> package boundary rather than an aggregate model (§25 of the same file). Read
> [docs/KNOWN_GAPS.md](docs/KNOWN_GAPS.md) before you deploy, and
> [docs/RUNBOOK.md](docs/RUNBOOK.md) before you operate it.

## Repository layout

| Path                    | Owned by    | What lives there |
|-------------------------|-------------|------------------|
| `backend/`             | submodule   | FastAPI app in **vertical slices**: `service/services/<domain>/` holds one directory per business domain, with `domain/`, `application/` (the rules), `persistence/` (the SQL) and `presentation/` (the router and its schemas) inside it. `service/main.py` is the entry point, `service/api/` the HTTP assembly, `service/composition/` the only wiring root, `service/models/` the pydantic models. Schema dump `database/HireSnap.sql`; tests under `tests/`, run as `python -m pytest -q` from `backend/`. |
| `frontend/`            | submodule   | CRA app layered pages → features → shared: `src/pages/` composes screens, `src/features/<domain>/` owns one domain's components behind an `index.js` barrel, `src/shared/` is the common layer and the single API client. `npm start` / `npm run build`; `jsconfig.json` makes imports absolute from `src/`; API base URL in `frontend/src/shared/api/apiConsts.js`. |
| `docker/`              | superproject| Stack code: `docker-compose.yaml` (prod), `docker-compose.dev.yaml` (dev), `.env.example` (the only committed manifest), `run.sh`, `build.sh`, `sync_env.py`, `sidecar_profiles.sh`, `compose_preflight.sh`. |
| `infra/`               | superproject| Host-mounted configuration and data: `infra/nginx/` (gateway config, templates, certs), `infra/mysql/` (init SQL), `infra/storage/` (uploads, embeddings), `infra/certs/` (JWT keys). |
| `backend/docker/`      | submodule   | Backend `Dockerfile` (build context is the repo root, `..`). |
| `frontend/docker/`     | submodule   | Frontend `Dockerfile` (static nginx image). |
| `ml/`                 | submodule   | Inference sidecar — the owner of the model code: `service/app.py`, `service/engine.py`, `service/settings.py`, the pipeline modules and `cosmos_tokenizer/`, `docker/Dockerfile`, `tests/`, and `README.md` (the canonical HTTP contract). Stateless, no database, no auth. |
| `docs/`                | superproject| Architecture, environment reference, runbook, service contracts, sidecars, secrets, known gaps, ADRs. |
| `scripts/`             | superproject| Cross-repo guards (`check_*.py`), run by `make guards`, pre-commit and CI, plus `adopt-submodules.sh` for turning the three checkouts into submodules and `run_guard.sh`, which picks a Python that actually starts before invoking a guard. |
| `.github/`             | superproject| CI (`.github/workflows/ci.yml`): a loop over every `scripts/check_*.py` guard (seven today) plus `compose config -q` for both stacks, gitleaks, pre-commit, backend pytest, ml contract tests (no GPU) and the frontend build. |
| `Makefile`             | superproject| Thin wrappers over `docker/run.sh` and `docker/build.sh`: `make env`, `make run-dev`, `make run-prod`, `make smoke`, `make guards`, `make logs`, `make down`. |

## Quickstart

Prerequisites: Docker Engine 24+ with Compose v2, Bash (or Git Bash / WSL on Windows),
`openssl`, and Python 3.9+ for `docker/sync_env.py`. No Python or Node installation is
needed to *run* the stack — both are installed inside the images.

```bash
git clone --recurse-submodules <this-repo> HireSnap
cd HireSnap

python docker/sync_env.py --mode dev     # 1. create docker/.env.dev from the manifest
# 2. edit docker/.env.dev: DB__USER, DB__PASSWORD, MYSQL_ROOT_PASSWORD,
#    MYSQL_PASSWORD, PUBLIC_BASE_URL
./docker/run.sh --dev up                 # 3. build-if-needed and start the dev stack
```

`sync_env.py` is the supported way to create a deployment file. It is append-only and
value-blind: it adds keys the manifest declares but your file is missing, never rewrites an
existing value, never prints a value, and writes atomically with mode `0600`. `cp`
works for the very first creation and then quietly goes stale after every manifest change
(`python docker/sync_env.py --check` is the drift check).

Then open:

| URL | What it is |
|-----|------------|
| <http://localhost:8088/> | Web UI through the gateway (`NGINX_PUBLISH_HTTP`, default `8088`) |
| <http://localhost:8088/health> | Gateway liveness probe — static JSON, never proxied |
| <http://localhost:8088/api/docs> | Backend OpenAPI UI, reachable through the `/api/ -> /server/` rewrite |
| <http://localhost:8080/server/docs> | Backend directly — **dev only** (the app serves `/server`, not `/`) |
| <http://localhost:3000/> | CRA dev server with hot reload — **dev only** |

`/health` is at the root, not under `/api/`: the `/api/` prefix is rewritten to
`/server/` unconditionally, so `/api/health` would become `/server/health` and 404
([docs/SERVICE_CONTRACTS.md](docs/SERVICE_CONTRACTS.md#13-where-each-health-route-actually-lives)).

Production differs in exactly three ways: the compose project is `hiresnap-prod`,
`docker/.env.prod` is used instead of `.env.dev`, and **only the gateway publishes a
port** — the backend and frontend stay reachable only inside the Docker network.

```bash
python docker/sync_env.py --mode prod    # then set real secrets and PUBLIC_BASE_URL
./docker/build.sh --prod
./docker/run.sh --prod up
```

`./docker/run.sh --help` lists every action (`up`, `down`, `logs`, `ps`, `exec`); a bare
`docker compose up` is **not** equivalent and starts nothing, because every service
declares a profile ([docs/RUNBOOK.md §1](docs/RUNBOOK.md#1-entry-points--and-why-not-a-bare-docker-compose)).
The root `Makefile` wraps the two scripts: `make env`, `make build`, `make run-dev`,
`make run-prod`, `make logs`, `make down`, `make smoke` and `make guards` call exactly
the commands above. `make` is convenience, not a dependency — every target has a
script form that works without it.

## Optional services

Four compose profiles extend the base stack. They are selected by keys in the env file
(`CACHE`, `STORAGE`, `CELERY`, `MAIL`, section 10 of `docker/.env.example`), not by a
command-line flag — `docker/run.sh` reads them and passes the matching `--profile`
(`docker/sidecar_profiles.sh`):

| Profile | Services | What it is | Default |
|---------|----------|------------|---------|
| `cache` | `cache` | `redis:7.2-alpine`, internal only, no password, no published port. Read by the platform layer (`backend/service/infrastructure/cache.py`); it is also the celery broker and result backend on **db 1**. No business call site uses it yet. | `CACHE=1` |
| `storage` | `storage` | MinIO object store. Read by the storage facade (`backend/service/infrastructure/storage.py`); no business call site uses it yet, and `STORAGE__BACKEND` stays `local` on purpose — flipping it would split uploads between the filesystem and the bucket. | `STORAGE=1` |
| `celery` | `celery-worker-agents`, `celery-worker-maintenance`, `celery-worker-ml`, `celery-beat` | Four processes on the same backend image, one per queue plus a single scheduler. `CELERY=1` also forces `CACHE=1` (a warning on stderr) because the broker is Redis. | `CELERY=0` |
| `mail` | `mailpit` | `axllent/mailpit:v1.20.5` — SMTP on `1025`, web inbox on `8025`. `MAIL=1` points the mailer at it with `STARTTLS=false`/`SSL=false`, because mailpit speaks plaintext. Dev publishes the inbox; prod publishes nothing. | `MAIL=0` |

`ml` is deliberately absent from that table. It is a **core service of both stacks**
(`profiles: ["prod"]` in the production file, `["dev"]` in the dev one), built from
`ml/docker/Dockerfile` with the build context set to `../ml`, so the image can only ever
see its own submodule. It listens on **8080**, publishes no host port in either mode, sits
on both networks and is stateless. The API has no in-process inference path left to fall
back to: `backend/service/infrastructure/ml_sidecar.py` posts to `ML_SERVICE_URL`,
taken from `ML__SERVICE_URL` in the manifest (`http://ml:8080` there, and
`docker/sidecar_profiles.sh` defaults it to the same when a deployment file leaves it
empty). Why the boundary is drawn there is
[ADR/0006](docs/ADR/0006-ml-sidecar-owns-inference.md).

The defaults encode risk: the two cheap stateless containers start unless you say
otherwise, and the two that need extra infrastructure do not. One ordering rule is worth
knowing before you flip a flag:

- **`CELERY=1` turns on the `celery` profile *and* the `cache` profile.** A broker without a
  Redis container is four workers failing to connect in a restart loop, so the helper
  forces `CACHE=1` and says so on stderr.

**The GPU is a third kind of switch, and it is not a profile.** `GPU=1` in the env file
makes `run.sh` merge `docker/docker-compose.gpu.yaml` into the stack — a single block, the
`deploy.resources.reservations.devices` reservation on `ml`. It lives in its own file
because a reservation is a **requirement on the host**: on a machine without the NVIDIA
container runtime, a stack that carries it does not start degraded, it fails to start. With
`GPU=0` (the default) the same images run on CPU, and the startup banner says
"compute   : cpu" so the choice is visible before a container is created.

`docker/run.sh` prints the extra dev URL when mail is on, the honest one-liner for ml
(`no host port; probe with ... exec ml curl -fsS http://127.0.0.1:8080/health`), the
`compute : gpu`/`cpu` line, and `jobs : inline in the API process (CELERY=0)` when the
workers are off — so the summary block is a quick confirmation of what you actually started.

The backend also carries a **platform layer** (cache facade, object storage, mail, celery
workers). The facades and the workers are wired and reachable; the **call sites are still
thin** — no business route reads the cache, stores to the bucket, or sends mail yet — so
turning a flag on buys working infrastructure, not new behaviour. Job state is the one
exception worth caring about: `CACHE=1` is what makes `JobService.status()` agree across
processes. See [docs/KNOWN_GAPS.md §13](docs/KNOWN_GAPS.md#13-the-platform-layer-is-delivered-but-its-call-sites-are-thin)
and the layer's own reference
[backend/docs/PLATFORM_LAYER.md](backend/docs/PLATFORM_LAYER.md).

## Adopting the three checkouts as submodules

`backend/`, `frontend/` and `ml/` are independent clones today. Turning them into
gitlinks is one idempotent script, because doing it by hand is three commands per service
and the failure mode is silent — the root repo records ordinary files, and every later
pointer bump then means nothing:

```bash
bash scripts/adopt-submodules.sh --check   # print the exact plan, touch nothing
bash scripts/adopt-submodules.sh           # adopt: writes .gitmodules, stages 3 gitlinks
```

The script reads each `origin` URL and current branch (`backend`, `frontend`, `ml`),
refuses a dirty or detached service checkout instead of adopting `HEAD` behind your back,
and never commits — you review `git status` and make the first commit yourself. Pass
`--force` only when adopting a dirty tree on purpose. The superproject's own branch is
`dev`; the three gitlinks each track their namesake branch of the same remote.

## Documentation

| Document | Read it when |
|----------|--------------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | You need the service graph, networks, ports, and the request path for `/api/`, `/data/`, `/`. |
| [docs/ENV.md](docs/ENV.md) | You are filling in an env file and need to know what each variable does and which service consumes it. |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | Something is broken, or you need build/run/logs/health/DB-init procedures. |
| [docs/SERVICE_CONTRACTS.md](docs/SERVICE_CONTRACTS.md) | You are adding a health check, a probe, or an error handler. |
| [docs/SIDECARS.md](docs/SIDECARS.md) | You are deciding whether to enable `cache`, `storage`, `celery` or `mail`, or you need the ml sidecar's contract. |
| [docs/SECRETS.md](docs/SECRETS.md) | You are generating keys, rotating the DB password, or running gitleaks. |
| [docs/KNOWN_GAPS.md](docs/KNOWN_GAPS.md) | **Always before a deploy.** Lists the defects this stack deliberately does not fix. |
| [docs/ADR/](docs/ADR/) | You want to know why the stack is shaped this way. |
| [AGENTS.md](AGENTS.md) | You are an agent or a contributor about to commit. |

## Contributing

Work inside the submodule that owns the code, commit there first, then bump the
submodule pointer in the root repository. A root-only commit that "changes" a submodule
file records nothing and does not survive a fresh clone.

Never commit: `.env` files (only `docker/.env.example` belongs in git), the JWT pair under
`infra/certs/`, model weights under `infra/models/`, uploads under `infra/storage/`, logs
under `infra/logs/`, or `docker/.run-state/`. See [AGENTS.md](AGENTS.md) for the full rules,
including where the database actually lives (a named Docker volume, not a directory under
`infra/`).

## Licence

See `backend/LICENSE`.
