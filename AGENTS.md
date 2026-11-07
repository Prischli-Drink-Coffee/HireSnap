HireSnap is a Git superproject. Product code lives in three submodules that are also
valid standalone checkouts:

- `backend/` — FastAPI application (Python 3.9) and **business logic only**. The server
  entry point is `src/pipeline/server.py`; it mounts a `/server` FastAPI app, a `/public`
  app, and a static `/data` directory on the root app. Persistence is raw PyMySQL against
  MySQL (`src/database/`), business logic in `src/services/` and `src/repository/`,
  config in `src/config.yaml` and `src/logging.yaml`. It contains **no** GPU framework:
  inference is an HTTP call to `ml/` (`src/services/ml_client.py`), and
  `scripts/check_ml_boundary.py` fails a commit that puts torch back in here.
- `frontend/` — Create React App (react-scripts 5.0.1, React 18, Chakra UI). The axios
  instance is `src/API/instance.js`; the API base URL is hardcoded in
  `src/API/apiConsts.js`.
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
quietly adopting `HEAD` (`scripts/adopt-submodules.sh:218-232`). It is idempotent and
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

Backend, inside `backend/`:

- `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
  — the current install path. `requirements.txt` no longer carries the ML stack: no
  torch, no CUDA, no `transformers`, because inference left this tree for `ml/`. It pins
  its remaining block and floats the rest, so a rebuild can still resolve different
  versions. `backend/docker/requirements.lock.txt` is a fully pinned **snapshot** of it
  (not a resolved lock: no hashes, no transitive closure) and is what the image installs
  when `REQUIREMENTS_FILE` is overridden. `backend/requirements_platform.txt` is a
  separate, short list (celery, redis, boto3) needed only by worker or S3 images. See
  `docs/KNOWN_GAPS.md` §4 and §15.
- `python3 src/pipeline/server.py` — run the server locally (reads `backend/.env`,
  requires `HOST`, `SERVER_PORT`, `DEBUG`, `DB*`, `DATA_PATH`, and the JWT expiry keys).

Frontend, inside `frontend/`:

- `npm ci && npm start` — dev server on port 3000.
- `npm test`, `npm run build` — unit tests (Jest via react-scripts) and the production
  bundle. The bundle is static output in `build/`, served by nginx in the image.

ML sidecar, inside `ml/`:

- `python service/app.py` — serve on `0.0.0.0:8080`. `ml/` is a self-contained
  checkout: its modules import each other as top-level names from `ml/service/`, and
  nothing here reaches into `../backend/`. Run it from `ml/` (or with `ml/service` on
  `PYTHONPATH`), never from the superproject root — the old `ML_BACKEND_ROOT` shim is
  gone and `scripts/check_ml_boundary.py` fails a commit that brings it back.
- `python -m pytest tests -q` — the contract tests, from inside `ml/`. They must pass
  with **no** torch, no weights and no GPU: `/health` answering `degraded` is a passing
  state, and a test that needs a CUDA device belongs in a manual check, not here.
- `ml/README.md` is the canonical description of the routes, the request shapes and
  the error registry. When a route changes, that file and `ml/tests/test_contract.py`
  change in the same commit as `ml/service/app.py`.

## Coding Style & Naming Conventions

Python targets 3.9, four-space indentation. Keep route handlers thin — `server.py`
should validate and delegate; queries belong in `src/repository/`, rules in
`src/services/`. Use the existing `Env` helper (`backend/env.py`) for configuration
instead of reading `os.environ` directly, so that behavior stays consistent with how
the container injects values. Logging goes through `src/utils/custom_logging.py`.

React uses function components, two-space indentation, and `.jsx` for components.
Reuse Chakra primitives and `src/theme.js` tokens rather than hard-coded colors.
Network calls go through `src/API/instance.js` — do not create a second axios client
with its own base URL.

Shell and Python files are LF-only; `.gitattributes` enforces this. A CRLF
`#!/bin/bash` breaks container entrypoints with `bash\r: No such file or directory`.

## Testing Guidelines

Place backend tests under `backend/tests/` as `test_*.py`. Frontend unit tests live
beside the code as `*.test.js` (CRA convention) — `src/App.test.js` is the existing
example. Add regression coverage when you touch auth (`src/utils/jwt_bearer.py`),
upload handling (`src/utils/write_file_into_server.py`), or URL construction
(`src/utils/return_url_object.py`), because those paths are shared by every screen.

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
  state — and fails on the first violation it finds. `make lint` is a different gate: it
  runs `pre-commit run --all-files`, which invokes those same guards plus the
  formatting, YAML/JSON, gitleaks and large-file hooks. Both are required before a PR;
  `make lint` fails loudly when `pre-commit` is not installed instead of reporting an
  unverified success.

## Where Generated State Lives

| Path | Content | Safe to delete? |
|------|---------|-----------------|
| `docker/.env.dev`, `docker/.env.prod` | Local copies of the manifest with real values | Yes — regenerate with `python docker/sync_env.py --mode dev` (or `--mode prod`) |
| named volume `mysql_data` (`mysql_data_dev` in dev) | **The database.** InnoDB data directory | **No** — and `docker compose down -v` deletes it |
| `infra/storage/` | Uploaded videos and `.npy` embeddings (bind mount at `/app/data`) | **No** — user data |
| `infra/certs/` | JWT RSA key pair, mounted read-only at `/app/src/certs` | **No** — all issued tokens become invalid |
| `infra/models/` | `weights/` and `pretrained_ckpts/`, mounted read-only | **No** — multi-gigabyte, licence-bound, not re-downloadable |
| `infra/logs/` | `app.log` written by `src/logging.yaml` (bind mount at `/app/logs`) | Yes, but read it first — it is the post-mortem |
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
semantics that InnoDB does not tolerate (`docker/docker-compose.yaml:47-57`).
