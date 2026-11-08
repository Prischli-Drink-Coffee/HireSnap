# ADR 0005 — The enumerated source-level changes, and why the list is closed

- **Status:** accepted
- **Date:** 2026-11-07
- **Affects:** `frontend/src/shared/api/apiConsts.js`, `backend/service/shared/urls.py`,
  and the three vacancy/personality repositories — the paths as they read after
  [ADR/0007](0007-sidecar-package-layout.md) moved both trees. [ADR/0008](0008-vertical-slice-services.md)
  moved the repositories again, into their slices' `persistence/` directories
  (`service/services/{candidate_vacancies,personality_candidates,personality_vacancies}/persistence/`);
  the patches themselves are unchanged and only their location reads differently now

## Context

`backend/` and `frontend/` are separate repositories with their own history and their own
release cadence. The deployment superproject exists to run them **as they are**: container
images, a gateway, an env manifest, an init script. Every line changed inside those two
repositories is a line the next `git pull` can conflict with.

Three defects, though, cannot be worked around from outside:

| Defect | Why the edge cannot fix it |
|--------|-----------------------------|
| The frontend hardcodes its API base URL (`apiConsts.js`) | The value is compiled into the bundle. nginx can rewrite a prefix it receives; it cannot invent an absolute origin the browser was told to call. |
| The backend builds media URLs from `HOST`/`SERVER_PORT` | `HOST=0.0.0.0` is the only sane bind address in a container, so the emitted URL is unroutable. A proxy can rewrite a `Location` header; it cannot rewrite a URL inside a JSON body. |
| Three repositories used `HTTPException` without importing it | The guard branch raises `NameError` (a 500) instead of the intended 400. Nothing at the edge turns a 500 back into a 400. |

## Decision

Exactly **five** files change across three changes, each with a backward-compatible
default, and the list is closed. No other file in `backend/` or `frontend/` is edited by
this work.

| File | Change | Compatibility |
|------|--------|---------------|
| `frontend/src/shared/api/apiConsts.js` | Precedence: `REACT_APP_BASE_URL` → same-origin `/api/` when the host is not loopback → the original `http://localhost:8080/api/` | With no new variable and a `localhost` browser, behavior is byte-identical |
| `backend/service/shared/urls.py` | Prefer a non-empty `PUBLIC_BASE_URL` (trailing slashes stripped), else the original `HOST`:`SERVER_PORT` expression | Unset variable reproduces the old output exactly |
| `service/services/candidate_vacancies/persistence/candidate_vacancy_repository.py` | Add `from fastapi import HTTPException, status` | No behavior change where the guard already worked |
| `service/services/personality_candidates/persistence/personality_candidate_repository.py`, `service/services/personality_vacancies/persistence/personality_vacancy_repository.py` | Same one-line import | Same |

## Rationale

1. **A configuration seam beats a fork.** The alternative to a small patch is maintaining a
   fork of both repositories, which turns every upstream commit into a merge. A variable
   that defaults to the old behavior is the smallest possible seam: the deployment can be
   configured, and an unconfigured checkout is unchanged.
2. **The patches are testable in isolation.** `return_url_object` is a pure function of
   the environment; the base-URL resolver is a pure function of `process.env` and
   `window.location`. Neither needs a container, a database or a network to verify.
3. **The import fix is not a feature.** It restores the error the code already intended to
   raise. Leaving it would have meant documenting "an empty payload returns 500" as if that
   were a design decision.
4. **The list is closed because it has to be reviewable.** A short, enumerated list can be
   checked against `git status` in the two repositories; "a few small fixes" cannot.

## Alternatives rejected

- **Fork both applications and maintain the fork.** Every upstream change becomes a merge
  with no shared history, and the deployment team owns the whole tree forever.
- **Rewrite the base URL in the built bundle at deploy time (sed on the built `build/` directory).**
  Works until the first minifier change renames the literal; then it silently stops
  matching and the SPA calls the wrong origin with no error until a user reports it.
- **Have the gateway rewrite `Location` headers and JSON bodies.** nginx can rewrite a
  header; rewriting response bodies requires `sub_filter`, which breaks on gzip and on
  any chunked response, and would corrupt binary payloads.
- **Leave the `NameError` alone and document the 500.** Documenting a bug does not make
  the client's empty form valid.

## Consequences

- The five edited files are ordinary commits inside `backend/` and `frontend/`, landed
  before the first superproject commit. The superproject's `git status` therefore shows a
  clean tree and only the three gitlinks; any future re-application of these patches is a
  fresh commit in the owning submodule followed by a pointer bump, per
  [AGENTS.md](../../AGENTS.md).
- The patches must be re-applied (or kept as a branch) if either repository is reset to
  upstream. [scripts/adopt-submodules.sh](../../scripts/adopt-submodules.sh) refuses to
  adopt a dirty checkout for exactly this reason.
- `REACT_APP_BASE_URL` is baked at **build** time in production (`ARG` in
  `frontend/docker/Dockerfile`) and read at **runtime** in development (the dev server
  re-reads it). Changing it in prod therefore means a rebuild, not a restart — see
  [ENV.md §6](../ENV.md#6-frontend).
- A fifth patch would need a new ADR. The bar is "the edge demonstrably cannot fix it".
