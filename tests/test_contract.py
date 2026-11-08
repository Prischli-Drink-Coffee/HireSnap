"""HTTP contract of the ML sidecar - verified with NO torch and NO weights.

WHY EVERY TEST USES THE STUB ENGINE
------------------------------------
The contract of this service is the JSON it speaks, not the numbers the models produce.
A test that loaded UNet3D would need a GPU, the weights that are deliberately NOT in the
repository (ml/README.md, "Weights"), ffmpeg and a minute per case - so it would never run
in CI, and the contract would be untested in practice. tests/conftest.py installs the stub;
this file asserts the layer above it: routing, validation, size limits, status codes and the
{error, detail} shape.

What this deliberately does NOT cover: that the vectors are correct, that the Cosmos
checkpoints load, that CUDA works. Those are properties of the pipeline modules beside the
engine, and the runbook owns the GPU smoke path (AGENTS.md, "Testing Guidelines").

WHERE THE ROUTE TABLE IS PINNED
-------------------------------
This file asserts the SEMANTICS of each route (a 200 carries these fields, this failure maps
to that status). The enumeration - which routes exist, in what order, with which operation
id - is pinned against a committed file in tests/test_route_snapshot.py.

HOW THE IMPORTS WORK WITHOUT A sys.path HACK
--------------------------------------------
ml/pyproject.toml declares [tool.pytest.ini_options] pythonpath = ["service"], so pytest puts
ml/service on sys.path itself and these tests import the flat modules by their bare names
(import app, import settings). That is how the image imports them (PYTHONPATH=/app/service)
and how one module imports another, so a test cannot pass on a layout the container does not
have. There was a sys.path.insert here once; deleting it is what makes the declaration
testable.

Run it (no GPU, no weights, no torch) from the repository root:
    python -m pytest ml/tests -q
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_module
import settings
from engine import EngineError, EngineUnavailable
from tests.conftest import VECTOR, StubEngine

# ml/service: the modules directory. Used only to point the subprocess probes below at the
# same imports pytest builds for this process - it is NOT put on sys.path here.
SERVICE_DIR = Path(__file__).resolve().parents[1] / "service"


def _probe(source: str, env_extra=None):
    """Run a snippet in a fresh interpreter that can import only ml/service.

    A subprocess is the only honest way to ask what `import app` costs: this process has
    already imported pytest, its plugins and every module earlier tests touched, so its
    sys.modules says nothing about a cold container start.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SERVICE_DIR)
    env.update(env_extra or {})
    completed = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()

# --------------------------------------------------------------------------- #
# /health - docs/SERVICE_CONTRACTS.md 1.1
# --------------------------------------------------------------------------- #
def test_health_is_exactly_the_three_contract_fields(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    # Exact key set: an extra field here is how two deployments stop being
    # comparable, and the compose healthcheck parses this body.
    assert set(body) == {"status", "service", "version"}
    assert body["status"] in {"ok", "degraded"}
    assert body["service"] == settings.SERVICE_NAME == "ml"
    assert body["version"] == settings.SERVICE_VERSION
    # Every value is a string - the contract own wording.
    assert all(isinstance(value, str) for value in body.values())


def test_health_reports_degraded_without_failing(client, monkeypatch):
    """A degraded replica still answers 200: it is alive, just incomplete."""
    monkeypatch.setattr(StubEngine, "health_status", lambda self: "degraded")
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


def test_app_import_does_not_pull_in_torch():
    """Importing the app must stay cheap and must not need a CUDA stack.

    docs/SERVICE_CONTRACTS.md 1.1 forbids /health doing a model load; the same reasoning
    applies one level earlier - if importing app.py imported torch, the container would pay
    seconds of CUDA initialisation before it could answer its first probe, and a CPU host
    would die on a missing driver.

    The heavy imports are lazy INSIDE the pipeline functions precisely so this holds on a
    machine where torch is installed but torchvision, torchaudio or moviepy is not.
    """
    probe = "import sys\nimport app\nprint('torch' in sys.modules)"
    assert _probe(probe) == "False"


def test_app_import_also_pulls_in_no_other_heavy_framework():
    """The same rule for the rest of the stack: torch is not the only expensive import.

    torchvision, torchaudio, moviepy, transformers and sentence_transformers are all in the
    image, so none of them would fail an import - they would just make the first /health
    answer late and allocate before a request arrived. The probe reports the names that DID
    load, so a failure says which one.
    """
    probe = (
        "import sys\n"
        "import app\n"
        "heavy = ['torch', 'torchvision', 'torchaudio', 'moviepy',\n"
        "         'transformers', 'sentence_transformers', 'librosa']\n"
        "print(','.join(name for name in heavy if name in sys.modules))"
    )
    loaded = [name for name in _probe(probe).split(",") if name]
    assert not loaded, "importing app preloaded: %s" % (loaded,)


def test_importing_the_app_opens_no_weight_file():
    """Import-time side effects are what a service cannot afford.

    The pipeline modules used to run a demo embedding call at module level, which is why the
    first version of this service had to rewrite their AST before it could import them. This
    asserts the class of bug is gone: importing the app must not so much as open a .pth or
    .jit file.
    """
    probe = (
        "import builtins\n"
        "opened = []\n"
        "real = builtins.open\n"
        "def spy(file, *a, **k):\n"
        "    opened.append(str(file))\n"
        "    return real(file, *a, **k)\n"
        "builtins.open = spy\n"
        "import app\n"
        "builtins.open = real\n"
        "suffixes = ('.' + 'pth', '.' + 'jit', '.' + 'npy', '.' + 'pt', '.' + 'safetensors')\n"
        "weights = [p for p in opened if p.endswith(suffixes)]\n"
        "print(len(weights))"
    )
    assert _probe(probe).splitlines()[-1] == "0"

# --------------------------------------------------------------------------- #
# /v1/capabilities
# --------------------------------------------------------------------------- #
def test_capabilities_shape(client):
    response = client.get("/v1/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "video_embedding",
        "text_embedding",
        "transcription",
        "cuda",
        "models",
    }
    assert isinstance(body["video_embedding"], bool)
    assert isinstance(body["text_embedding"], bool)
    assert isinstance(body["transcription"], bool)
    assert isinstance(body["cuda"], bool)
    assert set(body["models"]) == {"video", "text", "asr"}
    assert all(isinstance(value, str) and value for value in body["models"].values())


def test_capabilities_never_leaks_internal_probe_keys(client):
    """The engine keeps _weights_present and friends; the API must not.

    Those keys say WHERE a capability is missing, which is exactly what an operator wants
    in the log and exactly what must not become part of a versioned contract.
    """
    body = client.get("/v1/capabilities").json()
    assert not [key for key in body if key.startswith("_")]


def test_capabilities_answers_degraded_rather_than_failing(client, monkeypatch):
    """A replica with no weights still reports, with booleans telling the truth."""
    monkeypatch.setattr(
        StubEngine,
        "capabilities",
        lambda self, refresh=False: {
            "video_embedding": False,
            "text_embedding": True,
            "transcription": True,
            "cuda": False,
            "models": {
                "video": settings.video_model_id(),
                "text": settings.text_model_id(),
                "asr": settings.asr_model_id(),
            },
            "_weights_present": False,
            "_checkpoints_present": False,
            "_torch_present": True,
        },
    )
    response = client.get("/v1/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["video_embedding"] is False
    assert body["cuda"] is False
    assert "video" in body["models"]


# --------------------------------------------------------------------------- #
# Embedding + transcription responses
# --------------------------------------------------------------------------- #
def test_embed_video_returns_vector_dim_model(client, stub):
    response = client.post(
        "/v1/embed/video",
        content=b"not-really-a-video",
        headers={"content-type": "application/octet-stream"},
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"vector", "dim", "model"}
    assert body["dim"] == len(body["vector"]) == len(VECTOR)
    assert body["vector"] == pytest.approx(VECTOR)
    assert body["model"] == settings.video_model_id()
    # The upload was handed to the engine and then deleted: a 300 MB temp file left
    # behind per request fills the data volume within a day.
    assert ("embed_video", len(b"not-really-a-video")) in stub.calls


def test_embed_text_returns_vector_dim_model(client):
    response = client.post("/v1/embed/text", json={"text": "senior python engineer"})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"vector", "dim", "model"}
    assert body["dim"] == len(body["vector"])
    assert body["model"] == settings.text_model_id()


def test_embed_text_trims_before_the_engine_sees_it(client, stub):
    """Surrounding whitespace belongs to the caller, not to the encoder.

    A transcript read out of a CSV line is routinely padded; passing the padding through
    changes the embedding for no reason a caller would recognise.
    """
    client.post("/v1/embed/text", json={"text": "  spaced out  \n"})
    assert ("embed_text", "spaced out") in stub.calls


def test_transcribe_returns_text_and_model(client):
    response = client.post(
        "/v1/transcribe",
        content=b"audio-bytes",
        headers={"content-type": "audio/wav"},
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"text", "model"}
    assert body["text"] == "hello world"
    assert body["model"] == settings.asr_model_id()


def test_multipart_upload_is_accepted_too(client):
    """docker/ and the frontend both speak multipart; refuse it and they break."""
    response = client.post(
        "/v1/embed/video",
        files={"file": ("clip.mp4", b"mp4-bytes", "video/mp4")},
    )
    assert response.status_code == 200
    assert response.json()["dim"] == len(VECTOR)

def test_multipart_keeps_the_uploaded_filename(client, stub, monkeypatch):
    """The frame extractor names its output directory after the clip (frames.py).

    A multipart upload has a filename and a raw upload does not, so this is the one place
    the two paths differ; app.py must hand the real name through rather than inventing one.
    """
    import app as module

    seen = {}
    real = module._receive_media

    async def spy(request, max_bytes):
        path, name = await real(request, max_bytes)
        seen["name"] = name
        return path, name

    monkeypatch.setattr(module, "_receive_media", spy)
    client.post(
        "/v1/embed/video",
        files={"file": ("interview-clip.mp4", b"mp4-bytes", "video/mp4")},
    )
    assert seen["name"] == "interview-clip.mp4"


# --------------------------------------------------------------------------- #
# Errors - docs/SERVICE_CONTRACTS.md 2.1: always {error, detail}
# --------------------------------------------------------------------------- #
def _assert_error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error", "detail"}, body
    assert body["error"] == code
    assert isinstance(body["detail"], str) and body["detail"]
    # The contract forbids stack traces and file paths in detail. A Windows drive letter
    # or a Traceback line is the shape a leak takes.
    assert "Traceback" not in body["detail"]
    assert ":\\" not in body["detail"]


def _error_cases(fixtures_dir: Path):
    """The documented error cases, read from tests/fixtures/error_cases.json.

    A table in a file, not eleven near-identical functions: the README has the same table
    in prose, and a case added to one should be a one-line diff in the other. Cases that
    need an environment override or a stubbed engine failure stay explicit below, because
    their subject is the limit or the exception mapping, not the route.
    """
    import json

    document = json.loads((fixtures_dir / "error_cases.json").read_text(encoding="utf-8"))
    return document["cases"]


def _send(client, case):
    """Issue one fixture case and return the response."""
    if "json" in case:
        return client.request(case["method"], case["path"], json=case["json"])
    if "files" in case:
        return client.request(case["method"], case["path"], files=case["files"])
    if "raw" in case:
        return client.request(
            case["method"],
            case["path"],
            content=case["raw"].encode("utf-8"),
            headers={"content-type": case["content_type"]},
        )
    return client.request(case["method"], case["path"])


def test_every_fixture_case_maps_to_its_documented_error(client, fixtures_dir):
    """One parametrised run over the table in tests/fixtures/error_cases.json."""
    cases = _error_cases(fixtures_dir)
    assert cases, "the error-case fixture is empty"
    problems = []
    for case in cases:
        response = _send(client, case)
        if response.status_code != case["status"]:
            problems.append(
                "%s -> %d, expected %d" % (case["id"], response.status_code, case["status"])
            )
            continue
        body = response.json()
        if sorted(body) != ["detail", "error"]:
            problems.append("%s body keys %s" % (case["id"], sorted(body)))
        elif body["error"] != case["code"]:
            problems.append("%s -> %s, expected %s" % (case["id"], body["error"], case["code"]))
    assert not problems, "\n  ".join(problems)


def test_the_fixture_codes_are_all_declared(client, fixtures_dir):
    """A fixture case may not document a code the service would refuse to emit."""
    for case in _error_cases(fixtures_dir):
        assert case["code"] in app_module.ERROR_CODES, case


def test_the_fixture_status_matches_the_snapshot(fixtures_dir, snapshots_dir):
    """The two committed tables (fixtures and snapshot) must agree on status per code.

    They are separate files on purpose - one drives requests, one pins the enumeration -
    and separate files drift; this test is the join.
    """
    import json

    statuses = json.loads(
        (snapshots_dir / "ml_routes.json").read_text(encoding="utf-8")
    )["error_statuses"]
    for case in _error_cases(fixtures_dir):
        assert statuses[case["code"]] == case["status"], case

def test_oversized_body_is_413(client, monkeypatch):
    """MAX_UPLOAD_MB=0 is the cheap way to prove the ceiling is enforced."""
    monkeypatch.setenv("MAX_UPLOAD_MB", "0")
    assert settings.max_upload_bytes() == 0
    _assert_error(
        client.post(
            "/v1/embed/video",
            content=b"x",
            headers={"content-type": "application/octet-stream"},
        ),
        413,
        "request_too_large",
    )


def test_oversized_text_is_413(client, monkeypatch):
    monkeypatch.setenv("MAX_TEXT_CHARS", "5")
    _assert_error(
        client.post("/v1/embed/text", json={"text": "much longer than five"}),
        413,
        "request_too_large",
    )


def test_empty_text_is_400(client):
    _assert_error(client.post("/v1/embed/text", json={"text": "   "}), 400, "empty_body")


def test_json_body_on_a_media_route_is_415(client):
    _assert_error(
        client.post(
            "/v1/embed/video",
            content=json.dumps({"oops": True}),
            headers={"content-type": "application/json"},
        ),
        415,
        "unsupported_media_type",
    )


def test_missing_text_field_is_422(client):
    _assert_error(
        client.post("/v1/embed/text", json={"prompt": "wrong key"}),
        422,
        "invalid_request",
    )


def test_malformed_json_is_422(client):
    _assert_error(
        client.post(
            "/v1/embed/text",
            content="{not json",
            headers={"content-type": "application/json"},
        ),
        422,
        "invalid_request",
    )


def test_missing_model_is_503_not_500(client, stub):
    """The distinction that matters operationally: mount a volume, do not debug."""
    stub.raise_with = EngineUnavailable(
        "service_unavailable", "UNet3D weights not found at /app/models/weights"
    )
    _assert_error(
        client.post(
            "/v1/embed/video",
            content=b"video",
            headers={"content-type": "application/octet-stream"},
        ),
        503,
        "service_unavailable",
    )


def test_pipeline_failure_is_500(client, stub):
    stub.raise_with = EngineError("inference_failed", "the pipeline produced NaN/Inf")
    _assert_error(client.post("/v1/embed/text", json={"text": "x"}), 500, "inference_failed")


def test_a_bare_exception_never_leaks_a_traceback(stub):
    """Anything unforeseen is 500/internal with a detail that names no internals.

    The client is built with raise_server_exceptions=False on purpose: TestClient re-raises
    by default so a test notices an unexpected crash, which is the right default for every
    other test here and the wrong one for the test whose subject IS the catch-all handler.
    """
    stub.raise_with = ZeroDivisionError("division by zero in /app/service/engine.py")
    with TestClient(app_module.app, raise_server_exceptions=False) as unguarded:
        _assert_error(
            unguarded.post("/v1/embed/text", json={"text": "x"}), 500, "internal"
        )


# --------------------------------------------------------------------------- #
# Error-code registry
# --------------------------------------------------------------------------- #
def test_every_emitted_code_is_declared():
    """_error() asserts the code is in ERROR_CODES; keep the two in step."""
    assert "service_unavailable" in app_module.ERROR_CODES
    assert "invalid_request" in app_module.ERROR_CODES
    assert "request_too_large" in app_module.ERROR_CODES
    assert "inference_failed" in app_module.ERROR_CODES
    # Codes the backend transport maps by name (backend/service/services/ml_client.py, a
    # separate checkout - named here as prose only, this suite imports nothing from it).
    for required in ("service_unavailable", "request_too_large", "not_found"):
        assert required in app_module.ERROR_CODES


def test_no_openapi_console_is_exposed(client):
    """An interactive console on a GPU box is a liability with no upside."""
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
