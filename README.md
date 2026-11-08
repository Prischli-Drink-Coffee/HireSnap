# HireSnap ML sidecar

Inference as a separate process: it turns an uploaded video into a candidate
embedding vector and audio into text, and it **owns the model code** - the pipeline that
lived in `backend/src/modelling/` was moved here and that tree is deleted, so this is the
only copy. The business API keeps auth, routing and MySQL; this service keeps torch, the
GPU, the CUDA runtime and the multi-gigabyte weights.

**Why a separate process and not a library call:** a CUDA out-of-memory kill then takes
down the sidecar, not the API serving logins. The split is at process level on purpose;
see `docs/ADR/0006-ml-sidecar-owns-inference.md` for the decision record.

**This is a third submodule** (`ml`, branch `ml`) of the superproject. It is a complete
checkout on its own: it imports nothing from `../backend/`, and its image is built from
this directory (`context: ../ml`) rather than from the repository root.

## Layout

```
ml/
├── service/                    # the application: FLAT top-level modules, no package
│   ├── app.py                  # FastAPI HTTP surface - the whole contract
│   ├── engine.py               # model lifetime, readiness probe, idle unload
│   ├── settings.py             # env getters; importing it never fails
│   ├── frames.py               # ffmpeg frame extraction (argv list, no shell)
│   ├── text_embedding.py       # sentence-transformer helpers
│   ├── video_embedding.py      # UNet3D + Cosmos tokenizer access
│   ├── video_to_text.py        # wav2vec2 transcription
│   ├── user_embedding.py       # the composed candidate embedding
│   └── cosmos_tokenizer/       # vendored NVIDIA tokenizer (see "Vendored code")
├── docker/
│   ├── Dockerfile              # CUDA runtime base + python 3.9, non-root
│   └── requirements.txt        # pinned model stack against the cu116 index
├── tests/                      # developer-only; excluded from the image
│   ├── conftest.py             # stub engine + hermetic-env fixture
│   ├── test_contract.py        # HTTP contract; NO torch, NO weights
│   ├── test_route_snapshot.py  # app.py vs snapshots/ml_routes.json
│   ├── test_service_boundary.py# layout, imports, config surface (AST)
│   ├── fixtures/               # error_cases.json: every emit-able error
│   └── snapshots/              # ml_routes.json: the pinned route contract
├── scripts/                    # developer-only; excluded from the image
│   ├── check_ml_service_layout.py  # this tree's own guard (stdlib only)
│   └── ml_gate.sh              # runs ruff/pytest/the guard with an interpreter that works
├── pyproject.toml              # tooling only - deliberately not a package
├── .python-version             # 3.9: the minor the image installs (deadsnakes)
└── README.md                   # this file: the canonical route + env reference
```

The modules are **flat**: `import settings`, never `from service import settings`.
`ml/service` is on `sys.path` in both places that matter - `PYTHONPATH=/app/service` in
the image, `pythonpath = ["service"]` in `pyproject.toml` for pytest - so a test can
only pass on a layout the container also has. `service/` has **no** `__init__.py` and
there is no `sys.path.insert` anywhere in this tree; the only package marker is
`tests/__init__.py`, which exists so the suite can import its own fixtures by name
(`from tests.conftest import StubEngine`) and is never shipped - `.dockerignore`
excludes `tests/` and `scripts/` from the image, so `app.py` is loaded as a bare module
in the container exactly as it is here.

## HTTP contract

Port **8080** inside the compose network; never published to the host in prod. Every
response is plain JSON and every error is `{"error": <code>, "detail": <text>}`
(`docs/SERVICE_CONTRACTS.md` 2.1).

| Method | Path | Request | 200 response |
|--------|------|---------|--------------|
| GET | `/health` | - | `{status, service, version}` |
| GET | `/v1/capabilities` | - | `{video_embedding, text_embedding, transcription, cuda, models}` |
| POST | `/v1/embed/video` | raw media bytes (`Content-Type: video/*`, `audio/*` or `application/octet-stream`), or multipart with a `file` part | `{vector, dim, model}` |
| POST | `/v1/embed/text` | JSON `{"text": "..."}` | `{vector, dim, model}` |
| POST | `/v1/transcribe` | raw media bytes, same rules as the video route | `{text, model}` |

**`/health` returns exactly three fields** and does no work: no model load, no DB, no
filesystem scan. `status` is `ok` when all three capabilities are available and
`degraded` otherwise - never "down", because a live process that cannot reach its
weights is degraded, not broken. It stays 200 either way so a compose healthcheck does
not restart a container that is merely waiting for a mount.

**`/v1/capabilities`** answers what this replica can do right now, computed from the
filesystem and the interpreter (a cached readiness probe, 15 s TTL), never from a loaded
model:

```json
{
  "video_embedding": true,
  "text_embedding": true,
  "transcription": true,
  "cuda": true,
  "models": {
    "video": "Unet3D+Cosmos-Tokenizer-CV4x8x8",
    "text": "all-MiniLM-L6-v2",
    "asr": "facebook/wav2vec2-large-960h-lv60-self"
  }
}
```

`video_embedding` requires torch, the UNet3D weights and the Cosmos checkpoint; it does
**not** require a GPU any more (see "GPU" below). `cuda` is reported separately because it
decides whether a 40-minute clip takes minutes or an hour.

### Errors

| Status | `error` | When |
|--------|---------|------|
| 400 | `empty_body` | zero bytes where media or text was expected |
| 404 | `not_found` | no such route |
| 405 | `method_not_allowed` | wrong verb |
| 413 | `request_too_large` | body over `MAX_UPLOAD_MB`, text over `MAX_TEXT_CHARS` |
| 415 | `unsupported_media_type` | JSON posted to a media route, or multipart without `python-multipart` |
| 422 | `invalid_request` | malformed JSON, missing `text` field |
| 500 | `inference_failed` | the pipeline ran and failed (corrupt video, NaN output) |
| 500 | `internal` | anything unforeseen; the detail deliberately does not leak a traceback |
| 503 | `service_unavailable` | a dependency is missing: no torch, no weights, no checkpoint, `DEVICE=cuda` with no card |

The 503-vs-500 split is the operationally important one: **503 says "mount the volume or
check the runtime", 500 says "read the logs"**. A missing bind mount reported as 500
sends the operator to the wrong layer.

## Configuration

Read at call time by `service/settings.py`, so `import settings` never fails and a typo
in an optional key cannot stop the process from starting and reporting `degraded`.

| Variable | Default | Meaning |
|----------|---------|---------|
| `ML_HOST` | `0.0.0.0` | bind address; `127.0.0.1` would be unreachable from the network |
| `ML_PORT` | `8080` | HTTP port inside the container |
| `DATA_PATH` | `<ml>/data`; the image creates `/app/data`, and `docker/docker-compose.yaml` passes `/app/ml-scratch` on the `ml_scratch` volume | scratch spool for request bodies; **not** the backend uploads directory |
| `WEIGHTS_DIR` | `<ml>/models/weights` (`/app/models/weights`) | holds `Unet3D.pth` |
| `PRETRAINED_CKPT_DIR` | `<ml>/models/pretrained_ckpts` (`/app/models/pretrained_ckpts`) | holds `Cosmos-Tokenizer-CV4x8x8/encoder.jit` |
| `HF_HOME` | unset (`/app/.cache/huggingface` in the image) | Hugging Face cache; must be a volume or every recreate re-downloads |
| `DEVICE` | unset = auto | `cuda`, `cpu`, or empty to pick at first use |
| `FFMPEG_BINARY` | `ffmpeg` | ffmpeg executable for frame extraction |
| `MAX_UPLOAD_MB` | `512` | ceiling on one media body; `0` rejects everything |
| `MAX_TEXT_CHARS` | `20000` | ceiling on one `/v1/embed/text` input |
| `MODEL_IDLE_UNLOAD_SECONDS` | `0` (never) | quiet period after which loaded models are dropped and VRAM released |
| `ML_VIDEO_MODEL_ID` | `Unet3D+Cosmos-Tokenizer-CV4x8x8` | identifier echoed in every video response |
| `ML_TEXT_MODEL_ID` | `all-MiniLM-L6-v2` | sentence-transformer id; same model the backend used before |
| `ML_ASR_MODEL_ID` | `facebook/wav2vec2-large-960h-lv60-self` | CTC checkpoint for transcription |

**Naming convention:** the superproject env manifest uses double-underscore nesting
(`ML__PORT`) and the compose `environment:` block flattens it to the single-underscore
name the process sees (`ML_PORT`). `settings.py` reads **only the flat names**; it never
looks for `ML__*`. That translation belongs to `docker/docker-compose.yaml`.

`MODEL_IDLE_UNLOAD_SECONDS` exists to share one card: a sidecar that keeps wav2vec2
(~1.2 GB) plus the sentence transformer resident starves whatever else wants VRAM, and
"borrow the GPU for one batch" is the realistic ML deployment case. `0` is right for a
dedicated card, where reloading costs tens of seconds.

## Running it standalone

```bash
# from ml/
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"            # tooling only: pytest, httpx, ruff
pip install -r docker/requirements.txt   # the model stack (large; needs the cu116 index)

python service/app.py              # serves 0.0.0.0:8080
curl -s localhost:8080/health
curl -s localhost:8080/v1/capabilities
```

On Windows, `python service/app.py` works the same way; the launch directory does not
matter because every default in `settings.py` is derived from `__file__`.

### Tests

```bash
# from the repository root (the form Makefile, CI and the docs agree on)
python -m pytest ml/tests -q
# or from ml/ - same collected tests, same rootdir
python -m pytest tests -q
# the layout guard, which is not a pytest test (see below)
python ml/scripts/check_ml_service_layout.py
```

There is no `pytest.ini` and no `tox.ini`: every pytest key lives in `pyproject.toml`
(`testpaths = ["tests"]`, `pythonpath = ["service"]`, `addopts = "--strict-markers -ra"`,
the `integration`/`slow` markers). `pythonpath` is what lets the suite write `import app`
with no `sys.path` line anywhere, on the same layout the image runs.

The suite **must pass on a machine with no torch, no weights and no GPU**: `tests/conftest.py`
stubs `app.engine`, so the tests exercise routing, validation, size limits, status codes
and the error shape - not the vectors. Three files, each with one job:

| File | What it pins |
|------|--------------|
| `tests/test_contract.py` | The behaviour of each route: response fields, the size and media-type limits, the 503-vs-500 split, no traceback in a 500. Also the two import-hygiene proofs - importing `app` must not put `torch` in `sys.modules` and must not open a `.pth`/`.jit`/`.npy` file. |
| `tests/test_route_snapshot.py` | The route *table*: every `APIRoute` in `app.py`, its operation id and the exact top-level keys of its 200 response, compared against `tests/snapshots/ml_routes.json` in both directions (a route missing from the snapshot and a snapshot entry with no route both fail). |
| `tests/test_service_boundary.py` | The shape of the tree, by AST: no module imports `backend`, `src.*` or `service.*`; no relative imports; every import is a sibling, a declared dependency or stdlib; `settings.py` is the only reader of `os.environ`; every environment name is a documented key; the only nested package is the vendored tokenizer. |

`tests/fixtures/error_cases.json` is the table of **every** error the service can emit
(code, status, the request that provokes it) - the suite iterates it instead of hard-coding
one request per error, so adding an error case is a data change.

#### `tests/snapshots/ml_routes.json`

The committed copy of the contract: for each route its method, path, operation id, status,
the exact 200 response keys and their nested keys, plus the `error_codes`/`error_statuses`
registry and the routes that must stay disabled (`/docs`, `/redoc`). `app.py` is compared
against it, and so are `ERROR_CODES` and the README table - one file that moves when the
contract moves, so a route cannot be added to the code alone.

**Regenerate it by hand, in the same commit as the change, never from a test:** the point
of a snapshot is that a human looked at the diff. A helper that rewrote it on failure would
turn the gate into a rubber stamp.

#### `scripts/check_ml_service_layout.py`

A stdlib-only guard for the properties pytest cannot see, in the `[OK]`/`[FAIL]`/`[SUMMARY]`
shape of `scripts/check_*.py` in the superproject (same output, same exit codes 0/1/2, so
`make guards` can absorb it). It parses, never imports - it must run on any Python in the
repository, and importing `app.py` would need fastapi and start an engine:

1. no module at the `ml/` root (anything there is unreachable at runtime, because
   `PYTHONPATH` is `/app/service`, or a script that belongs in `scripts/`);
2. the only nested package under `service/` is `cosmos_tokenizer/`;
3. `app.py` and `settings.py` import no ML framework at module scope (`engine.py` may - it
   is the module that owns the models);
4. the README route table, `app.py` and the snapshot list the same routes, compared as sets.

Run it standalone from the repository root (`python ml/scripts/check_ml_service_layout.py`)
or from `ml/`. It is deliberately **not** collected by pytest: it is a repository gate
shaped for `make guards`, not a unit test, and it must stay runnable from a bare `ml/`
checkout, where `../scripts/` does not exist - which is why it duplicates twenty lines of
the report helper instead of importing it.

#### `scripts/ml_gate.sh`, and why the pre-commit hooks go through it

`.pre-commit-config.yaml` runs three local hooks - `ruff check .`, `pytest -q` and the
layout guard - through `scripts/ml_gate.sh <ruff|pytest|layout>`. The helper exists
because finding a usable interpreter turned out to be three defects wearing one coat:

1. **The name on `PATH` is not an interpreter.** On Windows `python` is the Microsoft
   Store alias, which starts nothing and exits 49. So every candidate is *run*, not
   looked up: the first one that answers `-c "import sys"` wins.
2. **Starting is not enough for `ruff` and `pytest`.** A candidate must also import the
   tool. `python3` on this host is a uv-managed 3.12 with no ruff, so a gate that only
   asked "does it start" picked it and failed with `No module named ruff` on a clean
   tree. The layout gate is stdlib-only, so for it starting is enough.
3. **The interpreter is chosen where it will run.** `ml/.python-version` pins `3.9`, the
   minor the image installs through deadsnakes. On a machine that has not installed 3.9,
   pyenv makes every shim *in this directory* exit 1 with `no global/local python version
   has been set`. The helper therefore `cd`s into the checkout before probing, and then
   resolves the winner to its real absolute path (`cygpath`-converted on Windows) before
   running it - a shim is a launcher that re-reads the working directory, so "it started
   during the probe" says nothing about the run two lines later.

When every name on `PATH` fails, the helper falls back to the standard install roots
(`~/.pyenv/pyenv-win/versions/*/python.exe`, `~/.pyenv/versions/*/bin/python3`,
`~/.local/share/uv/python/*/bin/python3`) and uses the first that can import the tool. The
pinned 3.9 is still what CI and the image use; the fallback only lets the check happen on
a developer machine that never installed it. `ML_GATE_PYTHON=/path/to/python` pins an
interpreter explicitly and is a hard failure when it cannot import the tool - a stale
pinned path must never look like a pass. With no usable interpreter at all the helper
prints `SKIP` and exits 0; CI installs 3.9 with `setup-python` and runs the same
commands, so the check still happens where it can be trusted.

Nothing here needs a database. `ml/` has no state, no auth and no schema.

## GPU

The image is built on `nvidia/cuda:11.6.2-cudnn8-runtime-ubuntu20.04` with Python 3.9
from deadsnakes, and torch is pinned to `1.12.1+cu116` from the PyTorch extra index -
those wheels are compiled against the CUDA 11.6 user-space libraries in that base image,
and a plain PyPI torch wheel bundles 11.7 libraries the driver rejects at import.

- **With a GPU:** `docker-compose.gpu.yaml` adds the device reservation, and `GPU=1` makes
  `docker/run.sh` apply it. `NVIDIA_VISIBLE_DEVICES=all` is already set in the image, so
  the reservation is the only missing piece.
- **Without a GPU it still works**, on CPU, slowly. This is a change: the original code
  called `torch.cuda.amp.autocast()` unconditionally and could not run on a CPU host at
  all (`docs/KNOWN_GAPS.md` section 7). `video_embedding.autocast_context()` is now the
  single decision point - fp16 autocast only on CUDA, a no-op context elsewhere.
- **`DEVICE=cuda` with no visible card is a hard 503**, not a silent fall back: the
  operator asserted the host has a GPU, and quietly using the CPU would hide a broken
  driver for weeks.
- **One worker, one process.** The GPU is a single resource; every additional worker
  would load its own copy of UNet3D and wav2vec2. Scale by adding cards and replicas.
  `python3 app.py` passes the app object to uvicorn rather than the `app:app` import
  string, precisely so the module (and its engine and idle-unload watchdog) is built
  once.

## Ownership and mounts (the canonical contract)

### What this submodule owns

**All of the ML code.** `ml/service/` is the only copy of the pipeline - frame
extraction, the Cosmos tokenizer, UNet3D, the text head, wav2vec2 and the composition
step. The two trees it came from, `backend/src/modelling/` and
`backend/cosmos_tokenizer/`, are **deleted**; there is no second copy anywhere in the
superproject and no generated mirror of one.

- `service/cosmos_tokenizer/` is the only remaining vendored code: NVIDIA's tokenizer,
  carried **verbatim** (19 files, excluded from `ruff`, not restyled). It is a model
  dependency with its own upstream, so bug fixes belong upstream; if one is applied
  here it must be listed in the PR so it can be re-applied on the next vendoring.
- Everything else in `service/` is first-party and is where a pipeline change belongs.
- `backend/` keeps business logic only. It reaches this service over HTTP through
  `backend/service/infrastructure/ml_sidecar.py` and contains no ML import.

**One sentence, because it is the invariant:** *`ml/` imports nothing from `../backend/`*
- no `sys.path` insert, no `ML_BACKEND_ROOT`, no reach into `src.modelling`; the process
is built from this directory (`context: ../ml`) and runs on `PYTHONPATH=/app/service`.
`scripts/check_ml_boundary.py` is the guard that keeps it true.

### What it mounts, and where the weights appear inside the container

| Artifact | Host path (default) | Path **inside the container** | Env var | Mode |
|----------|---------------------|-------------------------------|---------|------|
| `Unet3D.pth` | `infra/models/weights/` | `/app/models/weights/Unet3D.pth` | `WEIGHTS_DIR` | `ro` |
| `Cosmos-Tokenizer-CV4x8x8/encoder.jit` | `infra/models/pretrained_ckpts/` | `/app/models/pretrained_ckpts/Cosmos-Tokenizer-CV4x8x8/encoder.jit` | `PRETRAINED_CKPT_DIR` | `ro` |
| HF cache (downloaded at first use) | named volume `hf_cache` | `/app/.cache/huggingface` | `HF_HOME` | `rw` |
| Request scratch | named volume `ml_scratch` | `/app/ml-scratch` | `DATA_PATH` | `rw` |

Three things about that table are deliberate:

1. **The container paths are `/app/models/...`, not `/app/src/weights` or
   `/app/pretrained_ckpts`.** The image has no `/app/src` at all - that was the layout
   when the backend tree was copied in - so a mount pointing there would land on a
   directory nothing reads, and the sidecar would report `degraded` with a full volume.
   `docker/docker-compose.yaml` sets `WEIGHTS_DIR=/app/models/weights` and
   `PRETRAINED_CKPT_DIR=/app/models/pretrained_ckpts` as container literals; the HOST
   side is the manifest value (`WEIGHTS_DIR=../infra/models/weights` in
   `docker/.env.example`), which is why the same variable name appears on both sides of
   the colon with different meanings. It is the compose substitution, not the process,
   that turns one into the other.
2. **Those are also the `settings.py` defaults**, so a deployment that unsets either
   variable still reads the right directory instead of an empty one, and the 503 message
   names the same path the mount uses.
3. **Read-only, always.** The service never writes a checkpoint; a writable mount is how
   a half-downloaded `.jit` ends up replacing a good one.

The weights are **not in this repository** and must never be baked into an image layer:
`Unet3D.pth` and `encoder.jit` are multi-gigabyte, licence-bound artifacts, and a file
that size in a layer is rebuilt and pushed on every code change. `.gitignore` excludes
`models/`, `*.pth`, `*.jit` and `*.npy` for exactly that reason.

A developer without the weights can still run the whole contract: `/health` answers
`degraded`, `/v1/capabilities` reports `video_embedding: false`, `/v1/embed/text` works
after the sentence transformer downloads, and any video request returns a 503 naming the
exact missing path.

## Why the code was moved rather than imported (a decision, now history)

This section records why `ml/` holds its own copy of the pipeline instead of importing
one from `backend/`. It describes the round-1 design, which has been replaced; nothing
below is how the code works today.

`R:/GPTHub` ships `whisper/` and `video/` as sidecars that each carry their own copy of
the service code, and that is correct **there**: they are unrelated products with no
shared owner, so there is nobody to import from.

HireSnap is the opposite case - the ML pipeline had exactly one owner - so round 1 tried
to keep a single copy by putting `backend/` on `sys.path` at runtime and importing
`src.modelling.*`. That needed an AST rewrite of a module that ran itself on import, a
symlink probe for a checkpoint resolved against the working directory, and an image that
copied the entire backend tree - three workarounds for two defects, plus one separation
that existed only in the directory names. Round 2 **moved** the pipeline here and fixed
the defects instead, which is why the trees it came from are deleted:

| Defect in the old copy | Old module (deleted) | Fix, and where it lives now |
|------------------------|----------------------|-----------------------------|
| ran a demo embedding at module level | `modelling/user_embedding.py` | the call is gone; the CLI is under `__main__` (`service/user_embedding.py`) |
| deleted caller-owned temp directories | `modelling/user_embedding.py` | per-call `tempfile.mkdtemp`, only own dirs removed (`service/user_embedding.py`) |
| checkpoint path relative to the CWD | `modelling/user_embedding.py` | `settings.cosmos_encoder_path()` |
| unconditional CUDA autocast (CPU crash) | `modelling/user_embedding.py` | `video_embedding.autocast_context()` |
| ffmpeg via `shell=True` on an f-string | `modelling/get_frames.py` | argv list, no shell, stderr in the error (`service/frames.py`) |
| ffmpeg exit code never checked | `modelling/get_frames.py` | `FrameExtractionError` with the stderr tail (`service/frames.py`) |
| a fresh `SentenceTransformer` per call | `modelling/get_text_embedding.py` | cached in `engine.py` (`service/text_embedding.py`) |

`scripts/check_ml_boundary.py` enforces the result: no ML distribution in
`backend/requirements.txt`, no ML import under `backend/service/`, no `sys.path` rewrite and
no reach into `backend/` from `ml/service/`, and neither of the deleted trees may
reappear.

## Operational notes

- **Logs:** one line at startup dumps the effective configuration (paths, device, model
  ids) so `docker compose logs ml` answers "which weights did it actually use" without a
  shell. Every request logs begin/done with the byte count and the resulting dimension.
- **Scratch:** each request writes to its own `TemporaryDirectory`, deleted in a `finally`
  block. A 300 MB upload left behind per request fills the volume in a day.
- **Readiness vs. liveness:** the compose healthcheck calls `/health`, which is cheap by
  construction. A cold container that has not loaded a model reports `degraded` and stays
  up; that is the intended state, not a failure.
- **No `/docs`, no `/redoc`:** an interactive console on a GPU box is a liability with no
  upside. This file and `tests/test_contract.py` are the contract.
