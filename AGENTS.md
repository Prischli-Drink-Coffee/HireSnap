HireSnap is a Git superproject. Product code lives in three submodules that are also
valid standalone checkouts:

- `backend/` — FastAPI application (Python 3.9) and **business logic only**, organised as
  **vertical slices**: `service/services/<domain>/` holds one directory per bounded context
  (`auth`, `users`, `candidates`, `vacancies`, `magics`, `personalities`,
  `personality_vacancies`, `personality_candidates`, `candidate_vacancies`, `files`,
  `embeddings`, `matching`, `jobs`), and inside a slice the layers are
  `application/<domain>_service.py` (the rules, framework-free),
  `persistence/<domain>_repository.py` (the SQL), and `presentation/routers/<domain>_api/`
  (the router module exposing `router`, plus a `schemas.py` only where a router needs a model
  the domain does not own). A `domain/` layer is permitted and **no slice needed one** - the
  entities are the pydantic models in `service/models/`, shared across slices. Router module
  names follow the route they serve, not the slice:
  `candidates` serves `/info_candidates/` from `presentation/routers/info_candidates_api/`,
  `matching` serves the four `/.../inference/` routes from `inference_api/`, and `jobs` has
  no presentation layer at all. `service/main.py` is the entry point — `python -m service.main`
  from `backend/`; `service/api/app.py` builds the app, mounts `/server`, an empty `/public`
  and a static `/data`, includes the routers listed in `service/api/registry.py`, and
  registers the single exception handler that renders a `ServiceError` as `{'detail': ...}`
  with its own status. So the application layer raises `NotFoundError`, `ConflictError`,
  `InvalidPayloadError` or `UnauthorizedError` (`service/shared/errors.py`) and imports no
  framework; the HTTP mapping exists in exactly one place. `service/settings.py` is a frozen
  dataclass built once by `get_settings()` over the same env keys, `service/composition/` the
  only place wiring happens, `service/models/` the pydantic models, `service/configuration/`
  process config (`env.py` survives as the `Env` compatibility shim), `service/shared/`
  cross-cutting helpers, and `service/infrastructure/` the swappable I/O — cache, storage,
  mail, uploads, database, celery, and `ml_sidecar.py`, the HTTP client for `ml/`. Persistence
  is raw PyMySQL against MySQL. It contains **no** GPU framework:
  `scripts/check_ml_boundary.py` fails a commit that puts torch back in here.
- `frontend/` — Create React App (react-scripts 5.0.1, React 18, Chakra UI), layered
  pages → features → shared. `src/pages/` composes screens, `src/features/<domain>/` owns
  one domain's components and is imported through its `index.js` barrel, `src/shared/` holds
  the common components, hooks, auth helpers, the one API layer, and `src/app/` the router
  (`router/index.js` building the hash router, `router/routes.jsx` the table,
  `router/guards.jsx` the access checks) plus the providers. Route paths, roles and
  pagination numbers come from `src/constants.js`. `jsconfig.json` sets `baseUrl` to `src`,
  so imports are absolute from `src/` and no file climbs with `../../../`. The axios
  instance is `src/shared/api/instance.js`, the base URL is resolved in
  `src/shared/api/apiConsts.js`. The router is a **hash** router (`createHashRouter`),
  because the bundle is static files behind nginx with no SPA fallback for deep paths, and
  every file under `src/` must be reachable from `src/index.js` —
  `npm run find:unreachable` fails the build if one is not. `frontend/DESIGN.md` is the
  canonical description of the tree and the theme contract.
- `ml/` — the inference sidecar (Python 3.9, FastAPI, CUDA image) and the **owner of the
  model code**, in the flat layout the other sidecars use: `service/app.py` is the whole
  HTTP surface (`/health`, `/v1/capabilities`, `/v1/embed/video`, `/v1/embed/text`,
  `/v1/transcribe`), `service/engine.py` owns the models and their lock,
  `service/settings.py` reads the flat `ML_*` environment, and the pipeline modules
  (`frames.py`, `text_embedding.py`, `video_embedding.py`, `video_to_text.py`,
  `user_embedding.py`, `cosmos_tokenizer/`) sit beside them as top-level imports — no
  `src.` prefix and no `sys.path` insert into `backend/`. `ml/docker/Dockerfile` builds it,
  `ml/pyproject.toml` declares it, and `ml/README.md` is the canonical contract — update it
  before the docs when the routes change. It has no database, no auth and no state.

Deployment code owned by the superproject: `docker/` (compose files, env templates,
`run.sh`, `build.sh`, `sync_env.py`, `sidecar_profiles.sh`, `compose_preflight.sh`),
`infra/` (host-mounted nginx config, MySQL init SQL, storage and cert directories),
`scripts/` (cross-repo guards), `docs/` (architecture, runbook, contracts, ADRs).

Each submodule is on its own branch of the same remote — `backend`, `frontend` and
`ml`. A change that spans all three is three commits, one per submodule, plus one
pointer bump in the root repository.

## Submodule Adoption

The root repository starts empty with three ordinary directories. Adopt them once, before
the first commit, with the helper — never by hand:

```bash
bash scripts/adopt-submodules.sh --check   # print the plan, change nothing
bash scripts/adopt-submodules.sh           # write .gitmodules, stage all three gitlinks
```

The script reads each `origin` URL and the branch that checkout is on (`backend`,
`frontend`, `ml`), records `branch = <name>` in `.gitmodules` so `git submodule update --remote`
follows the right line, and refuses a dirty or detached service checkout rather than
quietly adopting `HEAD` (`scripts/adopt-submodules.sh:200-233`). It is idempotent and
never commits. After it runs, `git status` shows `.gitmodules` plus three `160000` entries;
review that diff and commit it. From then on the root repo tracks one commit per service
and a pointer bump is the only way a submodule change reaches the superproject.

CI checks `git submodule status` and the guards run on every commit, but neither can see
inside a gitlink — a superproject commit that touches no file still needs the cross-repo
guards to run, which is why `.pre-commit-config.yaml` sets `always_run: true` on them.

## Build, Test, and Development Commands

Root level (requires Docker Engine 24+ with Compose v2, and Bash; `make` is a thin
wrapper over the scripts in `docker/` and only saves typing):

- `python docker/sync_env.py --mode dev` — create `docker/.env.dev` from the only
  committed manifest, `docker/.env.example`. Append-only and value-blind: it never
  rewrites an existing value and never prints one. `python docker/sync_env.py --check`
  reports drift. `make env` does the same and also generates the RSA pair.
- `./docker/run.sh --dev up` — the supported entry point (`make run-dev`). It resolves
  the mode into a compose file, an env file, `-p PROJECT_NAME`, the sidecar profiles and a
  preflight; a bare `docker compose up` skips all of it and starts nothing, because every
  service declares a profile.
- `./docker/run.sh --prod up` (`make run-prod`) — the prod stack. Only the gateway
  publishes a port.
- `make logs`, `make ps`, `make down`, `make guards` — inspect, stop, and run the
  `scripts/check_*.py` gates. `make smoke` proves the proxy path end to end.
- `make coverage` — branch coverage of `backend/service/` against the floor in
  `backend/pyproject.toml` (`fail_under = 90`, several points below the 94% the CI
interpreter measures - see `docs/RUNBOOK.md` §16.1 for why it is not set one point under).
It drives the `coverage` CLI, not
  `pytest --cov`: pytest-cov reads `.coveragerc` and ignores `pyproject.toml`, records
  statement data despite `branch = true`, and the next run dies with "Can't combine
  statement coverage data with branch data". CI runs the same command in its own step.

Backend, inside `backend/`:

- `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
  — the current install path. `requirements.txt` no longer carries the ML stack: no
  torch, no CUDA, no `transformers`, because inference left this tree for `ml/`. It pins
  its remaining block and floats the rest, so a rebuild can still resolve different
  versions. `backend/docker/requirements.lock.txt` is a fully pinned **snapshot** of it
  (not a resolved lock: no hashes, no transitive closure) and is what the image installs
  when `REQUIREMENTS_FILE` is overridden. `backend/requirements-platform.txt` is a
  separate, short list (celery, redis, boto3) needed only by worker or S3 images. See
  `docs/KNOWN_GAPS.md` §4 and §15.
- `python -m service.main` — run the server locally from `backend/` (reads
  `backend/.env` through `service/configuration/env.py`). It needs `HOST`,
  `SERVER_PORT`, `DEBUG`, `DB__*`, `DATA_PATH`, the two JWT path keys
  (`JWT_PRIVATE_KEY_PATH`, `JWT_PUBLIC_KEY_PATH`). `INIT_DB` makes it create the schema
  on boot, but only when `DEBUG` is also true: the schema bootstrap is a developer
  convenience, not a startup step. `--reload` is likewise driven by `DEBUG`.

Frontend, inside `frontend/`:

- `npm ci && npm start` — dev server on port 3000.
- `npm test` (Jest via react-scripts), `npm run lint` (`eslint src --ext .js,.jsx`),
  `npm run find:unreachable` (fails if a file under `src/` imports nothing) and
  `npm run build` — the production bundle, static output in `build/`, served by nginx
  in the image. The build is deliberately not a lint gate: `CI=true` would turn every
  ESLint warning into a build error.

ML sidecar, inside `ml/`:

- `python service/app.py` — serve on `0.0.0.0:8080`. `ml/` is a self-contained
  checkout: its modules import each other as top-level names from `ml/service/`, and
  nothing here reaches into `../backend/`. Run it from `ml/` (or with `ml/service` on
  `PYTHONPATH`), never from the superproject root — the old `ML_BACKEND_ROOT` shim is
  gone and `scripts/check_ml_boundary.py` fails a commit that brings it back.
- `make ml-test` — the contract tests, and the form all three callers agree on: the
  Makefile target, the CI job and the docs all run `python -m pytest ml/tests -q` from
  the **repository root** (`Makefile:388-389`, `.github/workflows/ci.yml:594`). `cd ml &&
  python -m pytest tests -q` gives the same result, because `ml/pyproject.toml` sets
  `pythonpath = ["service"]` and `testpaths = ["tests"]`; running from the root is
  preferred so `backend/tests/` fixtures cannot leak in. They must pass with **no**
  torch, no weights and no GPU: `/health` answering `degraded` is a passing state, and a
  test that needs a CUDA device belongs in a manual check, not here.
- `ml/README.md` is the canonical description of the routes, the request shapes and
  the error registry. When a route changes, that file and `ml/tests/test_contract.py`
  change in the same commit as `ml/service/app.py`.

## Coding Style & Naming Conventions

Python targets 3.9, four-space indentation. Put a change inside the slice that owns it:
`service/services/<domain>/` holds `application/`, `persistence/` and `presentation/`
(twelve slices have the router; `matching` keeps an empty `persistence/` placeholder and
`jobs` has neither), and a module may import downward — presentation → application →
persistence — never back up and never sideways into another slice's layers; `shared/`
imports no framework, and a slice that ever does need entities gets a framework-free
`domain/` of its own. **A rule that fails raises a domain error, not an
`HTTPException`**: `service/shared/errors.py` defines `ServiceError(status_code, detail)`
with `NotFoundError`, `ConflictError`, `InvalidPayloadError` and `UnauthorizedError`
underneath it, and the one exception handler registered on the `/server` sub-app turns any of
them into `{'detail': ...}` with its own status. That is the only place HTTP status codes are
decided; an application module that imports `fastapi` is a bug, and so is a handler that
returns a tuple because it wanted a 202. Wiring belongs in `service/composition/`, the only
place a concrete cache, storage backend, mailer, job runner or service object is constructed.
Route handlers validate and delegate, SQL belongs in `persistence/`, rules in
`application/`. A layer directory that contains only `__init__.py` is a placeholder, not a
to-do list. Read deployment configuration through `service/settings.py` (a frozen dataclass
built once by `get_settings()`) — or through the `Env` shim
(`service/configuration/env.py`) where code predates it — instead of `os.environ`, and take
loggers from `service/configuration/logging.py` (`get_logger`). Two gates enforce the shape:
`backend/tests/test_service_boundary.py` with the suite, and
`backend/scripts/check_backend_layout.py` without it — the second is the one
`make structure` runs, which is what makes the layout checkable in a commit hook that has
no virtualenv.

React uses function components, two-space indentation, and `.jsx` for components. The tree
is layered pages → features → shared: `src/pages/` composes screens, `src/features/<domain>/`
owns one domain's components and is imported through its `index.js` barrel (never by
reaching into `components/`), and `src/shared/` is the common layer. Reuse Chakra
primitives and the raw tokens in `src/theme/tokens.js` (mapped onto Chakra scales by
`src/theme/theme.js`, widget recipes in `src/theme/components.js`) rather than hard-coded
colors, and take route paths and pagination numbers from `src/constants.js`. Network calls
go through `src/shared/api/instance.js` and the route table in `src/shared/api/apiConsts.js`
— do not create a second axios client with its own base URL. Imports are absolute from
`src/` (`shared/components/...`), never by climbing out of the current directory, and a
root module is written `constants.js` rather than a bare `constants`, because Jest resolves
the bare specifier differently from webpack.

Shell and Python files are LF-only; `.gitattributes` enforces this. A CRLF
`#!/bin/bash` breaks container entrypoints with `bash\r: No such file or directory`.

## Testing Guidelines

Place backend tests under `backend/tests/` as `test_*.py`. Two of them are structural and
must stay green while you move code: `test_service_boundary.py` (the slice layout and the
import direction) and `test_import_without_infra.py` (the app imports with no MySQL, no
`.env` and no torch). Frontend unit tests live beside the code as `*.test.js` (CRA
convention) — `src/App.test.js` is the existing example. Add regression coverage when you
touch auth (`service/api/security.py`), upload handling
(`service/infrastructure/uploads.py`), the ML boundary
(`service/infrastructure/ml_sidecar.py`), or URL construction (`service/shared/urls.py`),
because those paths are shared by every screen. Backend tests import the package, not a
script: `conftest.py` puts `backend/` on the path, so `python -m pytest -q` from
`backend/` is the whole command, and `python scripts/check_backend_layout.py` runs the
layout rules without pytest. The route surface is pinned by
`backend/tests/snapshots/critical_endpoint_openapi.json`: a test compares the generated
document against it, and you regenerate it with `python -m tests.snapshot_tool --write`
(from `backend/`) only when the contract change is intended.

Keep assertions deterministic: do not depend on network access, on pretrained model
weights, or on a live MySQL instance in unit tests. Integration behavior against the
real stack is covered by the runbook checks, not by the test suite.

## Commit & Pull Request Guidelines

**Commit inside the owning submodule first, then bump the pointer in the root
repository.** The root repo tracks a submodule as a single commit hash; a root-only
commit that "changes" a submodule file records nothing, and `git checkout` on another
machine will not reproduce your edit. Mirror of `R:\GPTHub\AGENTS.md:7`; the rule
exists because the alternative silently loses work.

- Use concise Conventional Commit subjects, e.g. `fix(auth): reject expired refresh tokens`
  or `feat(docker): pin mysql to 5.7.44`. Keep a commit scoped to one submodule, or to
  superproject files only.
- Never commit `.env` files, real credentials, JWT private keys (`infra/certs/`),
  uploads (`infra/storage/`), logs (`infra/logs/`), model weights (`infra/models/`) or
  `docker/.run-state/`. `.env.example` is the only env file that belongs in git.
- PRs should explain behavior and risk, link the issue when one exists, include a
  screenshot for visual changes, and list the checks you ran (`make smoke` plus the
  relevant service test command).
- `make guards` runs the `scripts/check_*.py` set — env-manifest parity, compose
  integrity, api-prefix contract, dockerfile pinning, healthcheck coverage, generated
  state, the ML boundary — and fails on the first violation it finds; it then runs the two
  submodule guards (`backend/scripts/check_backend_layout.py`,
  `ml/scripts/check_ml_service_layout.py`), seven checks plus two. `make structure` is the
  cheap subset for a move inside the tree: `scripts/check_ml_boundary.py` plus any
  `scripts/check_*structure*.py`, then three submodule guards — the layout of each
  checkout and the complexity ratchet (`backend/scripts/check_module_complexity.py`) — one
  superproject guard plus three, no docker and no env file needed (`Makefile:292-294`).
  A size-touching diff is reviewed against `backend/scripts/complexity_baseline.json`: it
  records the accepted over-ceiling debt, it may only shrink, and an entry that is no longer
  needed is printed for removal rather than failing the build. `make lint` is a
  different gate: it runs `pre-commit run --all-files`, which invokes those same guards
  plus the formatting, YAML/JSON, gitleaks and large-file hooks. Both are required before
  a PR; `make lint` fails loudly when `pre-commit` is not installed instead of reporting
  an unverified success. CI adds three more backend gates a local `make test` does not:
  the coverage floor (`make coverage`, `fail_under = 90` in `backend/pyproject.toml`),
  the complexity ratchet in its own step (the same script `make structure` runs), and the
  OpenAPI snapshot comparison — a contract change without `python -m tests.snapshot_tool
  --write` fails there rather than in a client.

## Where Generated State Lives

| Path | Content | Safe to delete? |
|------|---------|-----------------|
| `docker/.env.dev`, `docker/.env.prod` | Local copies of the manifest with real values | Yes — regenerate with `python docker/sync_env.py --mode dev` (or `--mode prod`) |
| named volume `mysql_data` (`mysql_data_dev` in dev) | **The database.** InnoDB data directory | **No** — and `docker compose down -v` deletes it |
| `infra/storage/` | Uploaded videos and `.npy` embeddings (bind mount at `/app/data`) | **No** — user data |
| `infra/certs/` | JWT RSA key pair, mounted read-only at `/app/secrets/certs`; `backend/service/certs/` is only the bare-checkout fallback | **No** — all issued tokens become invalid |
| `infra/models/` | `weights/` and `pretrained_ckpts/`, mounted read-only | **No** — multi-gigabyte, licence-bound, not re-downloadable |
| `infra/logs/` | `app.log` written by `service/configuration/logging.py` (bind mount at `/app/logs`) | Yes, but read it first — it is the post-mortem |
| named volume `hf_cache` | Hugging Face model cache | Yes — re-downloaded on next use |
| `docker/.run-state/` | Template fingerprints written by `run.sh` | Yes |
| `frontend/build/` | CRA production bundle | Yes — rebuilt by the image |

The database is a **named volume**, not `infra/mysql_data/`. `.gitignore` still lists
`/infra/mysql_data/` as a reserved guard in case someone switches the datadir to a bind
mount; it is not the live database path today. Deleting `infra/` therefore does **not**
delete your data, while `docker compose down -v` does.

Bind mounts under `infra/` are the ones a human must be able to see (uploads) or that must
survive a container recreate (keys, weights, logs). Everything a database engine touches
directly lives in a named volume, because a Windows bind-mounted datadir has NTFS-over-9p
semantics that InnoDB does not tolerate (`docker/docker-compose.yaml:178-184`).
