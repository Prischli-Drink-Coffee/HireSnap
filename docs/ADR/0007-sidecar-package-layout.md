# ADR 0007 — One importable package per sidecar

- **Status:** accepted
- **Date:** 2026-11-08
- **Affects:** `backend/` (the whole checkout: `src/` becomes `service/`, scripts and the SQL dump move, dead code is deleted),
  `frontend/src/` (flat tree becomes `app/`, `pages/`, `shared/`, `theme/`, `styles/`),
  `frontend/jsconfig.json` (new), `backend/tests/test_backend_structure.py` (new),
  `AGENTS.md`, `README.md`, `docs/ARCHITECTURE.md`, `docs/RUNBOOK.md`, `docs/SIDECARS.md`

## Context

Both application sidecars had reached the same shape: a working service wrapped in a
checkout that was never organised as a package.

**Backend.** `backend/src/` is a directory, not a package boundary. Three properties made
it un-importable in the sense a test needs:

| Defect | Evidence in the tree as it stood | Why documentation cannot fix it |
|--------|-----------------------------------|---------------------------------|
| The checkout patches `sys.path` to make its own imports work | The old `backend/src/__init__.py` (lines 3-5) prepended the project root; nothing else declared the root as an import root | Any consumer has to reproduce the same `sys.path` edit or the imports fail. A path hack is invisible to every linter and to the Python import system alike |
| Importing the application reads configuration and touches the filesystem | The old `backend/src/pipeline/server.py` built `Env()` at module scope (line 21), `backend/env.py` loaded `.env` inside it (line 23), and `server.py` mounted a static directory resolved from the project root at import time (line 32) | There is no way to answer "does this module import" without a `.env` file and a working directory, so a broken import is discovered at container start, not in a test |
| The entry point is also the application | `backend/src/pipeline/server.py` is 1103 lines: 58 route decorators, three `.mount()` calls and a `run_server()` that calls uvicorn | Route registration, configuration, business rules and SQL all live in one module, so a change to any of them is a change to the file that the container's `CMD` names |

Nothing asserted the layout, so it drifted: `src/images/` (10 files) sat inside the importable
tree, `src/stuff/` held 7 notebooks including a 253 KB one, `HireSnap.sql` sat at the
checkout root next to `env.py`, and a Windows-only `setup/` directory plus `main.sh`, `setup.sh`,
`pm2.sh` and `update_bitsandbytes.py` each described a different install story.

**Frontend.** The Create React App tree was flat: 121 `.js`/`.jsx` files directly under or one
level below `src/`, reached by relative paths. Measured on the tree before this refactor:
**35 of the 121 files were reachable from `src/index.js` and 86 were not.** The unreachable
86 are not spare parts — they are screens, tables and API services from a different
product (a warehouse/sklad domain: `tmc`, `writeoff`, `warehouse`, `supplier`, `material`,
`deliveryPlaces` ...) that was copied in and never removed. Seven component folders also
carried a `.module.less` next to its compiled `.module.css` sibling, so two files described one
style block and only one of them was read.

The two checkouts also disagreed about how a repo root should look: `backend/` kept its
install scripts and dump at the root, `frontend/` kept none, and there was no file in either
that stated the intended layering. The mirror is the reference stack at `R:/GPTHub`: a
sidecar there is an importable package with thin routers, a services layer and
infrastructure behind it, and its tests assert the tree.

## Decision

**One importable package per sidecar, with the layering stated in the tree and asserted by
a test.** The name of the package is not the point; being importable, and having the
layer boundaries be *directories* rather than a convention, is.

| Change | Detail |
|--------|--------|
| `backend/service/` is the package | `main.py` is the entry point, run as `python -m service.main` from `backend/` (image: `WORKDIR /app` with `PYTHONPATH=/app`). It calls `uvicorn.run("service.api.app:app")` so `--reload` still works |
| Four layers, one direction | `api/` (routers that validate and delegate, plus `security.py`) → `services/` (business rules, one module per vertical) → `repositories/` (the only layer that writes SQL) → `infrastructure/` (cache, storage, mail, uploads, database, messaging — everything swappable) |
| Cross-cutting code has a home | `configuration/` (env, logging, `logging.yaml`, paths), `domain/models.py` (the row models), `shared/` (hashing, tokens, validation, URLs, debug helpers) |
| Import is side-effect free | `service/api/app.py` exposes `create_app()` and `app`; importing them opens no MySQL connection and reads no `.env`, so the test suite runs with neither |
| The backend declares a health route | `GET /server/health` returns `{"status", "service", "version"}`. Before this, the API had no probe of its own and the compose healthcheck curled `/server/openapi.json` |
| The shell entry points are deleted, not moved | `setup.sh`, `main.sh`, `inference.sh`, `setup.bat`, `main.bat`, `pm2.sh` and the `setup_*`/`check_*` helpers are gone. The image runs `python -m service.main`, and the supported install is the dev container plus `pip install -r requirements.txt`; `update_bitsandbytes.py` went with the CUDA stack, which now lives in `ml/` |
| The schema lives with the database code | `database/HireSnap.sql` is the dump, `service/infrastructure/database/schema.py` is the loader that applies it |
| The frontend gets four top-level concerns | `app/` (router, providers), `pages/` (one PascalCase file per screen, grouped by audience), `shared/` (`api/`, `auth/`, `components/`, `hooks/`, `utils/`, `assets/`), `theme/` and `styles/` |
| Imports become absolute from `src/` | `frontend/jsconfig.json` sets `compilerOptions.baseUrl` to `src`, so a module imports `shared/api/instance` or `pages/auth/SignInPage` and no `../../API/` path survives |
| The layout is executable | `backend/tests/test_backend_structure.py` asserts the package layout, and the boundary guard `scripts/check_ml_boundary.py` already asserts that no module under the backend imports a GPU framework. A new top-level directory is now a failing test, not a silent drift |
| Dead code is deleted, not documented | The 86 unreachable frontend files and the 7 duplicate `.less` files are removed; the notebooks leave the importable tree for `backend/notebooks/` |

## Rationale

1. **A package boundary is the only one a test can hold.** `service/` as an import root means
   `python -c "import service.api.app"` is a real assertion about the tree. A directory called
   `src/` with a `sys.path` insert is an assertion about the caller.
2. **The layer names are what keeps the diff reviewable.** Putting SQL in `repositories/`
   and swappable I/O in `infrastructure/` is not decoration: it is the rule that lets a
   reviewer say "this router imports a repository" without reading the function.
3. **Deleting beats documenting for unreachable code.** 86 files were 71 percent of the
   frontend's JavaScript by count and none of it could execute. A comment saying "unused"
   costs every future reader a decision; deletion costs one commit and git remembers.
4. **The move is mechanical where it can be.** Modules were relocated and their imports
   rewritten; the only behaviour change in the same pass is the new health route and the
   import-time side effects moving behind `create_app()`. Everything else is a rename, which
   is why the test suite could be written first and kept green.
5. **Both checkouts stay standalone.** Neither sidecar gains a dependency on the
   superproject: `backend/` and `frontend/` are still valid checkouts with their own
   `requirements.txt` / `package.json`, and the mirror is a convention, not an import.

## Alternatives rejected

- **Keep `src/` and only rename the modules.** The `sys.path` insert and the import-time
  `Env()` survive, so the property that matters — importable with no MySQL, no `.env`, no
  GPU — is still unassertable. Renaming 57 modules without moving the boundary is the
  expensive half of the work with none of the benefit.
- **A shared package published to both sidecars.** `backend/` and `frontend/` share no
  language, and the one shared surface is HTTP. A published wheel would add a version to
  keep in step for zero code reuse (see [ADR/0006](0006-ml-sidecar-owns-inference.md), which
  rejected the same shape for the ML boundary).
- **Leave the 86 unreachable files and mark them.** They import deleted API services, so
  they do not merely sit there: a "restore this screen" attempt would have to resurrect
  the `sklad` API contract as well. Dry rot is cheaper to delete than to annotate.
- **Adopt a framework layout wholesale** (Django apps, `src/features/` slices, a monorepo).
  Each is a bigger decision than this one and would have to be justified against the
  upstream checkouts, which are not ours to restructure that far.

## Consequences

- **Every documented command changes.** `python src/pipeline/server.py`, `backend/main.sh`,
  `backend/setup.sh`, `create_sql.py` and `HireSnap.sql` at the checkout root no longer exist.
  [RUNBOOK.md](../RUNBOOK.md) and [AGENTS.md](../../AGENTS.md) were rewritten in the same
  pass; a stale command in either is now a broken instruction, not a typo.
- **The import root is `backend/`, not `backend/service/`.** Running a module from inside
  `service/` breaks the package-relative imports. That is documented once, in the runbook,
  and it is the single most likely way to hit this.
- **The layout is policed twice, and cheaply.** `backend/tests/test_backend_structure.py`
  fails on a second package at the backend root, on a loose `.py`/`.sh`/`.sql`/`.txt` left
  there, on a missing layer directory, on an import of the retired `src.*` namespace, on a
  router module nobody mounts, and on a data layer importing `service.api` /
  `service.repositories` / `service.services`. `scripts/check_ml_boundary.py` covers the
  GPU boundary and the same root-file rule from outside the test suite, which is what
  `make structure` runs when Docker and an env file are not available. A new top-level
  directory inside `service/` is therefore a failing test, not a silent drift; the
  intended shape is still described once in
  [ARCHITECTURE.md](../ARCHITECTURE.md#12-package-layout).
- **Deleted code is recoverable only from history.** The 86 frontend files and the
  `sklad` API services they import live in the `frontend/` git history, not in a branch,
  so restoring them means restoring the matching commit, not copying a folder back.
- **The health route is a new contract.** `GET /server/health` is now part of the public
  surface ([SERVICE_CONTRACTS.md](../SERVICE_CONTRACTS.md)) and the compose healthcheck
  depends on it, so removing it is a breaking change to the stack rather than to a client.
- **The frontend's absolute imports bind it to CRA's `jsconfig` handling.** A migration
  to Vite or Next would have to carry `baseUrl` over explicitly; until then every import
  that starts with a bare name is resolved from `src/`.
