# ADR 0008 — Vertical slices under `service/services/`

- **Status:** accepted
- **Date:** 2026-11-08
- **Affects:** `backend/service/` (`api/routers/`, `repositories/`, `services/` and `domain/` are
  replaced by thirteen slices under `service/services/`, plus `composition/`, `models/`,
  `api/registry.py` and `settings.py`), `backend/tests/test_service_boundary.py` (new),
  `backend/scripts/check_backend_layout.py` (new),
  `backend/tests/snapshots/critical_endpoint_openapi.json` (new), `AGENTS.md`,
  `docs/ARCHITECTURE.md`, `docs/SIDECARS.md`, `docs/SERVICE_CONTRACTS.md`, `docs/RUNBOOK.md`,
  `docs/KNOWN_GAPS.md`

## Context

[ADR/0007](0007-sidecar-package-layout.md) gave the backend one importable package and a
one-way layer rule. It did not decide what goes *inside* the package, so the package
reproduced the shape of the module tree it replaced: one directory per technical layer, and
the business meaning of a module carried only by its filename.

Measured on the tree this ADR replaced (the numbers are the pre-move baseline, kept so the
move can be re-derived; the paths are gone):

| Directory | Contents | Size (at the time of writing) |
|-----------|----------|------|
| `service/api/routers/` | 12 route modules, one per resource, 57 route decorators between them | about 1,170 lines |
| `service/services/` | 14 flat modules: 11 per-vertical ones plus `matching.py`, `jobs_services.py` and `ml_client.py` | about 1,610 lines |
| `service/repositories/` | 10 modules, one per table group | about 375 lines (33-42 lines each) |
| `service/domain/` | `models.py`, one module for every pydantic model in the product | about 395 lines |

Today those are `service/services/<domain>/{application,persistence,presentation}/` (13
slice directories, 74 directories in total), with the models under `service/models/`
(`auth_models.py` and `db/db_models.py`) and the ML client at
`service/infrastructure/ml_sidecar.py`.

**A change to one business idea touched three sibling directories, and nothing in any of
them said the others existed.** Adding a field to a vacancy meant editing
`service/api/routers/vacancies.py` (which imported the model and
`service.services.vacancy_services`), `service/services/vacancy_services.py` (which
imported `service.repositories.vacancy_repository` and the model) and
`service/repositories/vacancy_repository.py` (which imported the connection and the
model). Three files in three directories - and the fourth thing that actually bound them, the
order in which their routers are mounted, lived in none of them: a tuple in
`service/api/app.py`, whose comment explains that the order matters because OpenAPI output
follows declaration order.

The layer rule leaked in the other direction too. `service/services/matching.py` imported
nine sibling service modules in one statement (`file_services`, `embedding_services`,
`personality_services`, `candidate_vacancy_services`, `personality_vacancy_services`,
`personality_candidate_services`, `info_candidate_services`, `user_services`,
`vacancy_services`) and nine models on the next line. A module that imports nine of its
neighbours is not inside a boundary; the directory is a filing cabinet and the dependency
graph is everything talking to everything.

The reader's cost is the same fact seen from outside: to answer "what does a vacancy do", you
read four directories and grep for a name. No single directory answers it.

The mirror is `R:/GPTHub/backend/service/services/`, and it is honest about the cost as well
as the benefit. Its `README.md` is 132 lines of hexagonal convention - ports, use cases, DTOs,
mappers, an error mapper, a nine-step checklist - yet the live verticals are nowhere near
uniform: `notifications/` is four files (`api.py`, `notification_service.py`,
`presentation/`, `tasks.py`) with no `domain/` and no `application/`, while
`chat/` has five layers and hundreds of modules. The shape is available and *earned per
domain*, not imposed on all of them.

## Decision

**One directory per business domain, with the layers inside it.** The unit of change is
`service/services/<domain>/`; `application/`, `persistence/` and `presentation/` are
directories *inside* that unit, not peers of it (a `domain/` is allowed by the gate and no
slice needed one, because the entities are the pydantic models in `service/models/`).

| Change | Detail |
|--------|--------|
| One directory per bounded context | `service/services/<domain>/` for thirteen domains: `auth`, `users`, `candidates`, `vacancies`, `magics`, `personalities`, `personality_vacancies`, `personality_candidates`, `candidate_vacancies`, `files`, `embeddings`, `matching`, `jobs` |
| The layers inside the slice | `application/<name>_service.py` (the rules), `persistence/<name>_repository.py` (the SQL; ten slices have one, `auth` and `matching` hold an empty placeholder and `jobs` has neither), `presentation/routers/<name>_api/<name>_api.py` (the router, twelve slices; a `schemas.py` only where the router needs a model the domain does not own). A `domain/` layer is permitted by the gate and **no slice needed one in this pass**: the entities are pydantic models in `service/models/`, shared across slices |
| The failure path has one HTTP mapping | `service/shared/errors.py` defines `ServiceError(status_code, detail)` with `NotFoundError` (404), `ConflictError` (**404 on purpose** - the SPA keys off the `Email already exist` detail string), `InvalidPayloadError` (400) and `UnauthorizedError` (401). The application layer raises those and imports no framework; `service/api/app.py` registers the single handler on the `/server` sub-app that renders one as `{"detail": ...}` with its own status. Router module names follow the route, not the slice: `candidates` answers `/info_candidates/` from `info_candidates_api/`, `matching` answers the four `/.../inference/` routes from `inference_api/`, and `jobs` has no presentation layer |
| `composition/` is the only wiring root | `container.py` builds infrastructure, then repositories, then services, once per process and only when something asks; `infra.py`, `repositories.py` and `services.py` are the three mapping tables, and `state.py` is the **access surface** (`get_<domain>_service()` thirteen times, plus `get_cache()`, `get_storage()`, `get_mailer()`, `get_ml_client()`, `get_dispatcher()`). Nothing else constructs a collaborator, so the dependency graph is one file's worth of reading |
| The pydantic models get a home | `service/models/auth_models.py` and `service/models/db/db_models.py`: shared by the slices rather than owned by one of them (see the consequence on aggregates below) |
| Typed settings, read once | `service/settings.py` is a frozen dataclass built once by `get_settings()`, over the same env keys rather than a second manifest; `service/configuration/env.py` stays as the `Env` compatibility shim for the call sites that still use it, so this ADR does not double as a configuration rewrite |
| An HTTP client is infrastructure | `service/infrastructure/ml_sidecar.py` - the module that used to be `service/services/ml_client.py`. An adapter to another process is not a business rule, which is why it does not live in a slice ([ADR/0006](0006-ml-sidecar-owns-inference.md) owns the boundary itself) |
| The mount order becomes data | `service/api/registry.py` holds the ordered tuple of routers, one line per vertical. `service/api/app.py` keeps `create_app()`, the three mounts, CORS and `GET /server/health`; it stops being the file that also lists the product |
| An empty layer is a placeholder | A layer directory holding only `__init__.py` means "this domain has no such concern yet", not "somebody forgot". GPTHub keeps `services/jobs/domain/` empty for exactly this reason |
| The public contract does not move | 57 business routes, the `/server` prefix, an empty `/public`, static `/data` and `GET /server/health` are unchanged. This ADR moves modules; it changes no path, no method and no response model |
| The layout is executable | `backend/tests/test_service_boundary.py` asserts the slice shape and the import direction, and `backend/scripts/check_backend_layout.py` makes the same assertions available without pytest - which is what `make structure` runs when Docker and an env file are not around |

## Rationale

1. **The unit of change and the unit of reading become the same directory.** "What does a
   vacancy do" is answered by one `ls`, and a diff that touches one business idea is a diff
   under one path, which is what makes it reviewable by someone who does not know the tree.
2. **The layer rule survives because it is a directory rather than a promise.** In the flat
   tree, "routers must not write SQL" was a sentence in a code-review checklist. Inside a
   slice, `presentation/` importing SQL is visible in the import line of a file that sits
   next to the one it should have called.
3. **Wiring has one visible place.** `composition/` builds infra → repositories → services
   → container, so a collaborator that appears from nowhere is a bug rather than a habit - and
   it is the same four files the reference stack uses, which makes the two trees comparable
   file by file.
4. **The cost is bounded and mechanical.** Modules move and their imports are rewritten; the
   behaviour change in this pass is *none*. That is why the OpenAPI snapshot can be written
   first and required to match afterwards.
5. **The reference stack's heaviest conventions are deliberately not adopted.** GPTHub's
   `README.md` describes ports, DTOs, mappers and use-case classes; HireSnap takes the four
   directories and the wiring root, and lets a slice grow the rest when it needs it. A
   four-file vertical is a legitimate end state.

## Alternatives rejected

- **Keep the flat layer tree.** It works, and the cost is exactly what this ADR described: a
  change to one domain is a change in three directories, the mount order lives in the app
  factory, and the cross-imports in `matching.py` have nowhere to be declared. Keeping it
  means every future reader re-derives the mapping from filenames.
- **Go full hexagonal now** - ports, protocols, DTOs, mappers and use-case classes for
  repositories that are 33-42 lines. The reference stack does not do this uniformly either
  (`notifications/` is four files), and inventing five abstractions per domain before a
  second implementation of any port exists is the definition of speculative structure.
- **Move the routers into the slices and leave `repositories/` and `services/` global.**
  Half the win at most: the read path still crosses three top-level directories, and the SQL
  for a domain stays in a directory named after the SQL rather than after the domain.
- **One slice per route module** - twelve slices. `inference`, `files` and `embeddings`
  are three views of the same media pipeline; splitting them produces slices that import each
  other's persistence, which is the flat tree with longer paths.
- **Leave the router tuple in `app.py`.** The order has to be written somewhere, and
  `app.py` is the file that should change least: a registry turns "which routers exist" into a
  two-line diff that a reviewer can check against the directory listing.

## Consequences

- **A change touches one directory.** The vacancy example above becomes one path. The
  exception is a change that spans domains - `matching` legitimately reads candidates,
  vacancies and embeddings - and in the reference stack that is also a cross-slice import
  (`chat/` imports `billing/`), not a merge of the slices.
- **More packages, most of them small.** Thirteen slices times up to four layers is up to 52
  directories - 74 exist today once `__init__.py` packages are counted - and most hold one
  module. The placeholder convention is what keeps that from
  reading as unfinished work: an `__init__.py`-only layer is a statement about the domain.
- **Import paths change for every module that imported the old siblings.** This is the
  mechanical half and the reason the snapshot exists: a move that changes a response shape is
  now a failing test rather than a client bug.
- **The slice is not an aggregate root, and the models are not owned by it.** Repositories
  still hold raw SQL and return rows that the application layer turns into models; there is no
  service-level transaction boundary; the verticals share `service/models/db_models.py`
  instead of owning their entities. All three are deliberate in this pass and cheap to revisit
  later - recorded in [KNOWN_GAPS.md](../KNOWN_GAPS.md) §25 rather than pretended away.
- **One defect was carried across on purpose, and it is the slice's only debt.**
  `similarity_for_user()` writes the personality-vacancy link with a stale loop index
  (`personalities[j]`, where `j` is left over from the previous loop) - its own docstring calls
  it `KNOWN DEFECT, PRESERVED ON PURPOSE`
  (`backend/service/services/matching/application/matching_service.py:222-227`; the code is at
  `:247-266`). The move did not touch it, because correcting it rewrites stored rows and needs
  a backfill - a migration, not a refactoring step. It is recorded as
  [KNOWN_GAPS.md](../KNOWN_GAPS.md) §26; nothing calls the function today, so the debt is
  dormant rather than live.
- **The public contract is snapshotted.** `backend/tests/snapshots/critical_endpoint_openapi.json`
  is the recorded OpenAPI document, regenerated with
  `python -m tests.snapshot_tool --write` from `backend/`. A refactor that changes a
  status code, a response model or a route path fails that test; see
  [SERVICE_CONTRACTS.md](../SERVICE_CONTRACTS.md) §5.
- **Two gates, one of them cheap.** `backend/tests/test_service_boundary.py` runs with the
  suite; `backend/scripts/check_backend_layout.py` runs without Docker, without an env file and
  without pytest, which is why `make structure` is still the pre-commit answer. The glob in the
  target picks the new script up with no Makefile edit.
- **Reversing it means moving modules back.** There is no compatibility layer between the two
  shapes: `service/repositories/`, `service/domain/` and `service/api/routers/` are gone,
  so a revert is the inverse of this pass, not a flag.
