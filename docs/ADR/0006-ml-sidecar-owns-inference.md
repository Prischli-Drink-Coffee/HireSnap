# ADR 0006 — The inference sidecar owns the model code

- **Status:** accepted
- **Date:** 2026-11-08
- **Affects:** `ml/` (the whole store, which becomes a submodule),
  `backend/src/services/ml_client.py`, the deleted `backend/src/modelling/` and
  `backend/cosmos_tokenizer/`, `backend/requirements.txt`, both compose files,
  `docker/docker-compose.gpu.yaml`, `scripts/check_ml_boundary.py`, `.gitmodules`

## Context

`ml/` was introduced as a *sidecar to the backend*: a separate container serving the
video/audio pipeline over HTTP. The container boundary was real; the code boundary was
not. `ml/` owned no model code at all — it put `backend/` on `sys.path` and imported the
backend's own modules under their original names, so the sidecar was one process running
another repository's files.

Four measurable problems came out of that shape:

| Defect | Evidence | Why an image or an env change cannot fix it |
|--------|----------|----------------------------------------------|
| The sidecar rewrote its dependency's source before executing it | `ml/engine.py:105-141` parses `src/modelling/user_embedding.py`, drops every top-level bare call statement, recompiles and executes it — because that file calls `get_user_embedding(video_path=...)` at module scope (`user_embedding.py:92`), which a plain import would run | Any edit to the backend module changes what the AST rewrite sees. Two repositories, one being silently rewritten by the other, is a dependency with no interface and no version |
| The sidecar resolved its checkpoints relative to the working directory | `user_embedding.py:45` hardcodes `pretrained_ckpts/Cosmos-Tokenizer-CV4x8x8/encoder.jit`; `ml/engine.py:315` (`_ensure_checkpoint_layout`) exists only to prove that relative path resolves | A mount path is deployment config; a hardcoded relative path is a property of the process's CWD. `ml/engine.py` had to work around a constant it did not own |
| The sidecar image copied the other repository's source | `ml/Dockerfile:218-220` copies `backend/src` to `/app/src`, `backend/cosmos_tokenizer` to `/app/cosmos_tokenizer`, then only three `ml/*.py` files | The image tag pins `ml/`, but its behaviour is whatever `backend/src/modelling/**` was at build time. A backend commit changed the sidecar's output with no `ml/` change and no sidecar rebuild |
| The backend image still installed the whole CUDA stack | `backend/requirements.txt` named torch, torchvision, transformers, sentence-transformers, moviepy, librosa, torchmetrics and torcheval for a REST API that, on the sidecar path, imports none of them | Deleting packages from a requirements file is a source change; no build arg, image layer or compose setting removes an import that is still in the code |

The `sys.path` insert itself is `ml/engine.py:95-102`. Nothing here is a bug in the sense
of a wrong line — every piece was defensible when `ml/` was a directory inside the backend.
The defect is the ownership: two deployables, one copy of the code, and the copy was
resolved by path ordering at runtime.

## Decision

**The inference sidecar owns the inference code, and `backend/` keeps an HTTP client.**
`ml/` becomes a repository of its own — a third submodule beside `backend/` and
`frontend/`, on its own branch of the same remote — and carries the pipeline as flat
modules under `ml/service/`, in the layout the GPTHub sidecar uses:

| Change | Detail |
|--------|--------|
| `ml/` becomes a submodule | `branch = ml` in `.gitmodules`, adopted by `scripts/adopt-submodules.sh` like the other two; the superproject records one commit, not a directory |
| The model code moves into `ml/service/` | `app.py`, `engine.py`, `settings.py`, `frames.py`, `text_embedding.py`, `video_embedding.py`, `video_to_text.py`, `user_embedding.py` and `cosmos_tokenizer/`, imported as top-level modules — no `src.` prefix, no `sys.path` insert |
| The shim goes | The `sys.path` insert and the AST rewrite are deleted. The module-scope demo call is replaced by a `if __name__ == "__main__":` guard, and the checkpoint path is read from configuration instead of the working directory, so `_ensure_checkpoint_layout` has nothing left to work around |
| `backend/` loses the pipeline | `backend/src/modelling/` and `backend/cosmos_tokenizer/` are deleted; `backend/src/pipeline/train.py` goes with them; the `torch` import in `src/utils/seed.py` becomes optional; `backend/requirements.txt` drops the ML stack and keeps `numpy`, `pandas` and `scikit-learn`, because candidate–vacancy matching is business logic |
| The client is HTTP-only | `backend/src/services/ml_client.py` keeps its public functions and drops the in-process branch. `ML_SERVICE_URL` unset now means `http://127.0.0.1:8080` (`ml_client.py:65`), not "import torch here" |
| `ml` becomes a core service | Both compose files build it from `context: ../ml`, `dockerfile: docker/Dockerfile`, on 8080, unpublished, on the `default` and `internal` networks — `profiles: ["prod"]` / `["dev"]` like the API, not an optional sidecar. The API and the three celery services take `ML__SERVICE_URL=http://ml:8080` and `depends_on: ml` with `condition: service_started` |
| The GPU requirement becomes an override | `docker/docker-compose.gpu.yaml` holds the `deploy.resources.reservations.devices` block and is layered on when `GPU=1`; a CPU-only host runs the same stack with `GPU=0` and the sidecar answers `degraded` |
| The boundary becomes executable | `scripts/check_ml_boundary.py` fails a commit when a module under `backend/src/` imports a GPU framework or the deleted `src.modelling`, when `backend/requirements.txt` names one of those distributions, or when either deleted tree reappears |

## Rationale

1. **A submodule is the only split where the boundary is a commit, not a convention.**
   Two directories in one repository can be untangled by an import at any time. Once `ml/`
   is a gitlink, moving inference back into the API is a visible change to the superproject
   and to `.gitmodules`, not a `sys.path` line someone adds on a Friday.
2. **Owning the code removes the class of defect, not one instance.** The AST rewrite,
   the CWD-relative checkpoint and the copied source tree were three symptoms of "the
   sidecar executes another repository's files". With the code in `ml/service/`, there is
   no other file to rewrite, no path to guess and no second copy to pin.
3. **The backend image gets smaller and its cold start gets shorter.** The API imports no
   GPU framework, so its image no longer installs a multi-gigabyte CUDA stack it never
   loads. That is a deployment property, not a style preference: the healthcheck's
   180-second start period existed because `import torch` was in the API's path.
4. **A one-way dependency can be checked mechanically.** `backend → ml` over HTTP is
   enforceable by an AST walk of one directory; "the sidecar must not import the
   implementation details of the API" was not enforceable at all.
5. **The GPU requirement became honest.** It used to be a compose line in the base file,
   which meant every environment *claimed* a GPU. It now lives in an override applied when
   `GPU=1`, so a laptop and a GPU host run the same stack with a different, greppable
   answer to "is this deployment supposed to have a card?".

## Alternatives rejected

- **Keep the `sys.path` shim and document it.** This was the status quo, and it is the
  reason every symptom above existed: the sidecar's behaviour depended on a file in
  another repository at build time, and its correctness depended on that file not
  containing a new top-level call. The cost of keeping it is unbounded — each new
  import-time side effect in the backend needs another AST rule — while the cost of the
  split is one migration.
- **Put the pipeline in a separate repository with its own remote.** Cleaner on paper,
  and it would have cost a third remote plus a version-and-publish step for a service
  that ships with this stack and is versioned with it. A submodule of the same remote
  gives the same commit boundary without inventing a release process nobody needs yet.
- **Keep inference inside the API process and drop the sidecar.** The simplest possible
  topology, and it was rejected because a CUDA OOM then takes the API down with it and no
  API replica can be scaled without duplicating several gigabytes of weights.
- **Publish a shared `hiresnap-ml` wheel consumed by both services.** Two images, one
  library, and the same problem in a new place: the wheel must be rebuilt and republished
  for every pipeline fix, and the API would still link the CUDA stack to import it.

## Consequences

- The superproject now has **three** submodules, and a change that touches the pipeline and
  the API is one commit in each of two submodules plus one pointer bump in the
  superproject — the rule in [AGENTS.md](../../AGENTS.md) applies to `ml/` exactly as it
  does to the other two.
- `ml/` had to be adopted before the first commit that referenced it, because until
  `scripts/adopt-submodules.sh` ran, `git status` showed an untracked directory and a
  root-only commit would have recorded nothing. Adoption is done: `.gitmodules` names all
  three services and the root repo tracks three gitlinks.
- **Accepted cost: the sidecar still serialises GPU work.** One uvicorn process, one model
  cache and one lock means one inference at a time; concurrent uploads queue
  ([KNOWN_GAPS.md §23](../KNOWN_GAPS.md#23-the-sidecar-is-the-only-inference-path-and-it-serialises-gpu-work)).
- **Accepted cost: a CPU-only host is slow, not equivalent.** With `GPU=0` the stack
  starts, `/health` answers and the video path *works*, in float32 and minutes per video
  instead of seconds, because the once-unconditional `autocast` is now decided per device
  ([KNOWN_GAPS.md §7](../KNOWN_GAPS.md#7-autocast-was-unconditional-fixed-the-cpu-path-is-real)).
- **Accepted cost: weights are still not in the repository.** A fresh host must be given
  `infra/models/weights` and `infra/models/pretrained_ckpts` before the video path works;
  `/v1/capabilities` names what is missing without loading anything.
- **Accepted cost: the backend test suite cannot exercise a real sidecar.** It asserts the
  HTTP contract against a stub, so a contract drift is caught by `ml/tests/` on the other
  side, not by the API's tests.
- The migration is not reversible in one line: reverting means restoring the deleted trees
  and the shim, and the reason this ADR exists is that the shim was the defect.
- A change to the route surface now needs three edits in one commit: `ml/service/app.py`,
  `ml/README.md` and `ml/tests/test_contract.py`, plus the client's expectations in
  `backend/service/services/ml_client.py` when the payload shape moves.

## Addendum — the backend package moved afterwards

This ADR was accepted before the backend was reorganised. Nothing in its decision
changes; one path in it does. [ADR/0007](0007-sidecar-package-layout.md) moved
`backend/src/` to `backend/service/` and gave the API a `create_app()` that imports with no
MySQL, no `.env` and no GPU. The consequences for this ADR are mechanical:

- The HTTP client this ADR keeps is now `backend/service/services/ml_client.py` (was
  `backend/src/services/ml_client.py`). Its behaviour is unchanged: HTTP only, no in-process
  branch, `ML_SERVICE_URL` unset meaning `http://127.0.0.1:8080`.
- The guard that makes the boundary executable is unchanged in name and purpose
  (`scripts/check_ml_boundary.py`); its backend-side rule now reads `backend/service/`. The
  `src.modelling` / `backend/cosmos_tokenizer/` trees this ADR deleted must not reappear
  anywhere under `backend/`, and the guard fails the commit if they do.
- The tests that assert the boundary is real moved with the package.
  `backend/tests/test_ml_client.py:174-202` starts a fresh interpreter, imports
  `service.services.ml_client` and asserts that neither `torch` nor `src.modelling` reached
  `sys.modules` and that `service_url()` still falls back to `http://127.0.0.1:8080`; the
  test immediately after it parses the client's own AST, so a re-added import is caught on
  the line rather than in the graph. `backend/tests/test_import_without_infra.py:74-93`
  covers the other half for the app object: a subprocess from a temporary directory with
  every `DB_*` and `MYSQL_*` variable stripped must import `service.api.app`, answer
  `GET /server/health` with the documented body, hold no open MySQL socket and write no
  file into the working directory.
