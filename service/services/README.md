# Services — the vertical-slice template

This directory holds one package per business domain. Each is a **vertical slice**: the
router, the rules and the SQL for one table live together, instead of being spread across
three flat layer directories. The rule that makes it a slice rather than a folder is that
a slice may import its own layers and `service.shared`, and must not import another slice
except through that slice's application service.

The shape is copied from the reference superproject
(`R:\GPTHub\backend\service\services\README.md`), with two deliberate differences
recorded at the bottom: routes are named after the route rather than after the table, and
there is no `domain/` layer in any slice yet.

---

## Directory structure

```
services/
└── <domain>/
    ├── __init__.py
    ├── application/                       # Use cases: the rules, framework-free
    │   ├── __init__.py
    │   └── <domain>_service.py            # class <Domain>Service
    ├── persistence/                       # Data access: raw SQL, one repository
    │   ├── __init__.py
    │   └── <domain>_repository.py
    ├── presentation/                      # HTTP layer, this slice only
    │   ├── __init__.py
    │   └── routers/
    │       ├── __init__.py
    │       └── <route>_api/
    │           ├── __init__.py
    │           └── <route>_api.py         # module-level `router = APIRouter()`
    └── (domain/)                          # NOT PRESENT YET - see the last section
```

A slice may legitimately be smaller than this. `jobs/` has only `application/` because it
is driven by the queue, not by HTTP, and inventing an empty `presentation/` for it would
be a directory that lies about what exists. `matching/` has an `application/` and a
`presentation/` but no `persistence/` of its own: its scores are written through the three
link slices, each of which owns its table.

**An `__init__.py`-only layer is a placeholder, not a TODO.** `auth/persistence/` and
`matching/persistence/` exist so the shape is uniform, and they contain nothing on
purpose: `auth` reads the `users` table through the users slice, and `matching` has no
table. `backend/scripts/check_backend_layout.py` requires `application/` and
`presentation/` on every slice except the ones in its `SLICES_WITHOUT_PRESENTATION`, and
says nothing about the optional layers.

---

## The thirteen slices

| Domain | Table | Route prefix | Service class |
|--------|-------|--------------|---------------|
| `auth` | (users) | `/signup/`, `/signin/` | `AuthService` |
| `users` | `users` | `/users/` | `UserService` |
| `candidates` | `info_candidates` | `/info_candidates/` | `InfoCandidateService` |
| `vacancies` | `vacancies` | `/vacancies/` | `VacancyService` |
| `magics` | `magics` | `/magics/` | `MagicService` |
| `personalities` | `personalities` | `/personalities/` | `PersonalityService` |
| `personality_vacancies` | `personality_vacancies` | `/personality_vacancies/` | `PersonalityVacancyService` |
| `personality_candidates` | `personality_candidates` | `/personality_candidates/` | `PersonalityCandidateService` |
| `candidate_vacancies` | `candidate_vacancies` | `/candidate_vacancies/` | `CandidateVacancyService` |
| `files` | `files` | `/files/` | `FileService` |
| `embeddings` | `embeddings` | `/embeddings/` | `EmbeddingService` |
| `matching` | (none) | `/upload_video`, `/get_all_vacancies` … | `MatchingService` |
| `jobs` | (none) | **no route, on purpose** | `JobService` |

`candidates/` is the slice for the `info_candidates` table. The package is named after the
domain; the route keeps the table's name because the frontend already calls
`/info_candidates/` and a rename would be a breaking API change for a cosmetic gain. The
router module inside it is `info_candidates_api` for the same reason.

---

## Layer rules

These are enforced, not aspirational: `backend/tests/test_service_boundary.py` walks the
import graph with `ast` and `backend/scripts/check_backend_layout.py` walks the tree. A
rule here that no test checks would be a comment.

### `application/`
- Imports its own slice and `service.shared`; other slices only through their service
  class, never their repository.
- **No FastAPI names at all** — not even `HTTPException`. Failures are raised as
  `service.shared.errors` (`NotFoundError`, `ConflictError`, `InvalidPayloadError`,
  `UnauthorizedError`), which is what lets the same rule be called from a Celery task,
  a test and a route without an HTTP server in the room.
- No `service.infrastructure.messaging` import: a slice does not enqueue its own work.
- Dependencies arrive through the constructor, defaulted to `None` and resolved lazily
  through `service.composition.state`. That is why a unit test can pass a fake
  repository and no container.

### `persistence/`
- Raw SQL through the module-level connection handle, `%s` placeholders, no ORM.
- One repository per table. It returns rows (`dict`), never models: the service decides
  what a row means, the repository only knows how to fetch it.
- May import `service.models` and `service.shared`; must not import `service.services`,
  `service.api` or `service.composition`.

### `presentation/`
- A FastAPI router, a `try/except ServiceError` around each handler that logs and
  re-raises, and nothing else. Handlers validate and delegate; they do not decide.
- Takes its service through `Depends(get_<domain>_service)`, so the router has no import
  of the concrete class and a test can mount it over a stub.
- Auth is declared on the decorator (`dependencies=[Depends(JWTBearer(access_level=0))]`),
  not inside the handler, so the OpenAPI document advertises it.

### What no layer may do
- `service.models` and `service.shared` may not import `service.api`, `service.services`,
  `service.composition` or a repository. They are the bottom of the graph; an upward
  import there is a cycle waiting for its second edge.

---

## Cross-slice dependencies

Three slices own a table that links two others. They validate the foreign keys before
writing, and they reach the siblings through `service.composition.state`:

| Slice | Validates |
|-------|-----------|
| `candidate_vacancies` | `users`, `vacancies` |
| `personality_vacancies` | `personalities`, `vacancies` |
| `personality_candidates` | `personalities`, `users` |

`matching` is the one slice that composes many others: it reads candidates, vacancies,
personalities and files, asks the `ml` sidecar for embeddings, and writes the scores
through the three link slices. It is last in `BUILD_ORDER` for that reason.

---

## Adding a slice: checklist

1. Create `service/services/<domain>/` with `__init__.py`, `application/`,
   `persistence/` and (unless it is queue-driven) `presentation/routers/<route>_api/`.
2. Write `application/<domain>_service.py`: a class, constructor-injected collaborators
   defaulted to `None`, failures as `service.shared.errors`.
3. Write `persistence/<domain>_repository.py`: the SQL, and nothing else.
4. Write the router: one handler per route, `Depends(get_<domain>_service)`, the same
   `try/except ServiceError` shape as its neighbours.
5. Add the domain to `SERVICE_MODULES`, `PERSISTENCE_MODULES` and `BUILD_ORDER` in
   `service/composition/services.py`, and to `SLICE_SERVICES` plus its accessor in
   `service/composition/state.py`.
6. Add the router to `ROUTERS` in `service/api/registry.py`. **Order matters**: FastAPI
   matches in declaration order.
7. Run `make structure` and `make test`. The layout guard fails on a router the registry
   does not mount; `tests/test_backend_structure.py` fails on a slice with a flat module
   where a package belongs.

Changing a route's path, method, response model or default is a **contract** change: it
moves `backend/tests/snapshots/critical_endpoint_openapi.json`, and the snapshot is
regenerated deliberately (`python -m tests.snapshot_tool --write`) rather than accepted
because it went red.

---

## Where this deviates from the reference

1. **Routers are named after the route, not the table.** `GPTHub` names the router
   `<domain>_api`; here `candidates/` holds `info_candidates_api/` and `matching/` holds
   `inference_api/`, because the route is what a reader is looking for and the two names
   genuinely differ. `check_backend_layout.py` matches the stem against the registry
   rather than deriving it from the directory name, so both spellings pass.
2. **No slice has a `domain/` layer yet.** The reference puts pure entities and domain
   exceptions there. Here the entities are the pydantic models in `service/models/db`
   (they are the OpenAPI schema, so they cannot move without changing the API document)
   and the exceptions are shared in `service.shared.errors`. Splitting a `domain/` out
   would be a rename with no behaviour behind it, so
   `tests/test_service_boundary.py` skips that rule with an explicit
   "not yet migrated" message instead of silently passing. When a slice grows a rule that
   is not a database round-trip, that is the moment to give it a `domain/`.
