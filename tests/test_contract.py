"""HTTP contract of the ML sidecar - verified with NO torch and NO weights.

WHY every test stubs the engine
-------------------------------
The contract of this service is the JSON it speaks, not the numbers the models
produce. A test that loaded UNet3D would need a GPU, ~2 GB of weights that are
deliberately NOT in the repository (see ml/README.md "Weights"), ffmpeg and a minute
per case - so it would never run in CI, and the contract would be untested in
practice. Instead app.engine is replaced by a stub whose only job is to return a
known three-field payload or raise a known typed error. That isolates the layer this
file is about: routing, validation, size limits, status codes and the
{error, detail} shape.

What this deliberately does NOT cover: that the vectors are correct, that the Cosmos
checkpoints load, that CUDA works. Those are properties of the pipeline modules next
to the engine, and the runbook owns the GPU smoke path.

HOW THE IMPORTS WORK WITHOUT A sys.path HACK
--------------------------------------------
ml/pyproject.toml declares [tool.pytest.ini_options] pythonpath = ["service"], so
pytest puts ml/service on sys.path itself and these tests import the flat modules by
their bare names (import app, import settings). That is the same way the image
imports them (PYTHONPATH=/app/service) and the same way one module imports another,
so the test cannot pass on a layout the container does not have. Before this file
there was a sys.path.insert here; deleting it is what makes the declaration testable.

Run it (no GPU, no weights, no torch needed) from the repository root:
    python -m pytest ml/tests -q
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

import app as app_module
import settings
from engine import EngineError, EngineUnavailable

# ml/service: the modules directory. Only used to point the subprocess probes below at
# the same imports pytest builds for this process - it is NOT put on sys.path here.
SERVICE_DIR = Path(__file__).resolve().parents[1] / "service"

VECTOR = [0.1, 0.2, 0.3, 0.4]


class StubEngine:
    """Stand-in for engine.MLEngine.

    Mirrors only the surface app.py uses: health_status, capabilities, embed_video,
    embed_text, transcribe, start_watchdog, stop_watchdog, unload. Each operation can
    be told to raise a typed engine error instead of returning, which is how the
    503/500 mapping is exercised without a GPU.
    """

    def __init__(self) -> None:
        self.raise_with = None  # None | EngineUnavailable | EngineError
        self.calls = []

    # -- readiness ------------------------------------------------------ #
    def health_status(self) -> str:
        return "ok"

    def capabilities(self, refresh: bool = False) -> Dict[str, Any]:
        return {
            "video_embedding": True,
            "text_embedding": True,
            "transcription": True,
            "cuda": True,
            "models": {
                "video": settings.video_model_id(),
                "text": settings.text_model_id(),
                "asr": settings.asr_model_id(),
            },
            "_weights_present": True,
            "_checkpoints_present": True,
            "_torch_present": True,
        }

    # -- operations ----------------------------------------------------- #
    def _maybe_raise(self) -> None:
        if self.raise_with is not None:
            raise self.raise_with

    def embed_video(self, media_path: Path) -> Dict[str, Any]:
        self._maybe_raise()
        self.calls.append(("embed_video", Path(media_path).stat().st_size))
        return {"vector": VECTOR, "dim": len(VECTOR), "model": settings.video_model_id()}

    def embed_text(self, text: str) -> Dict[str, Any]:
        self._maybe_raise()
        self.calls.append(("embed_text", text))
        return {"vector": VECTOR, "dim": len(VECTOR), "model": settings.text_model_id()}

    def transcribe(self, media_path: Path) -> Dict[str, Any]:
        self._maybe_raise()
        self.calls.append(("transcribe", Path(media_path).stat().st_size))
        return {"text": "hello world", "model": settings.asr_model_id()}

    # -- lifecycle ------------------------------------------------------ #
    def start_watchdog(self) -> None:
        self.calls.append(("start_watchdog", None))

    def stop_watchdog(self) -> None:
        self.calls.append(("stop_watchdog", None))

    def unload(self) -> None:
        self.calls.append(("unload", None))


@pytest.fixture()
def stub(monkeypatch):
    """Replace the process-wide engine for one test."""
    fake = StubEngine()
    monkeypatch.setattr(app_module, "engine", fake)
    return fake


@pytest.fixture()
def client(stub):
    # The context manager is what runs startup/shutdown, i.e. the same path the
    # container takes. It must not need a model, a GPU or a mounted volume.
    with TestClient(app_module.app) as test_client:
        yield test_client


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
    # Every value is a lowercase string - the contract own wording.
    assert all(isinstance(value, str) for value in body.values())


def test_health_reports_degraded_without_failing(client, monkeypatch):
    """A degraded replica still answers 200: it is alive, just incomplete."""
    monkeypatch.setattr(StubEngine, "health_status", lambda self: "degraded")
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


def test_app_import_does_not_pull_in_torch():
    """Importing the app must stay cheap and must not need a CUDA stack.

    docs/SERVICE_CONTRACTS.md 1.1 forbids /health doing a model load; the same
    reasoning applies one level earlier - if importing app.py imported torch, the
    container would pay seconds of CUDA initialisation before it could answer its
    first probe, and a CPU host would die on a missing driver. Checked in a
    subprocess so an earlier test in this session cannot mask the import.

    The heavy imports are lazy INSIDE the pipeline functions precisely so this can
    hold on a machine where torch is installed but torchvision, torchaudio or
    moviepy is not.
    """
    probe = "import sys, app; print('torch' in sys.modules)"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SERVICE_DIR)
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "False", completed.stderr


def test_importing_the_app_opens_no_weight_file(tmp_path):
    """Import-time side effects are what a service cannot afford.

    The pipeline modules used to run a demo embedding call at module level, which
    is why the first version of this service had to rewrite their AST before it
    could import them. This asserts the class of bug is gone: importing the app
    must not so much as open a .pth or .jit file.
    """
    probe = (
        "import builtins, sys\n"
        "opened = []\n"
        "real = builtins.open\n"
        "def spy(file, *a, **k):\n"
        "    opened.append(str(file))\n"
        "    return real(file, *a, **k)\n"
        "builtins.open = spy\n"
        "import app\n"
        "builtins.open = real\n"
        "weights = [p for p in opened if p.endswith(('.' + 'pth', '.' + 'jit', '.' + 'npy'))]\n"
        "print(len(weights))\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SERVICE_DIR)
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().splitlines()[-1] == "0", completed.stdout


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
    """The engine keeps _weights_present and friends; the API must not."""
    body = client.get("/v1/capabilities").json()
    assert not [key for key in body if key.startswith("_")]


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


# --------------------------------------------------------------------------- #
# Errors - docs/SERVICE_CONTRACTS.md 2.1: always {error, detail}
# --------------------------------------------------------------------------- #
def _assert_error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error", "detail"}, body
    assert body["error"] == code
    assert isinstance(body["detail"], str) and body["detail"]
    # The contract forbids stack traces and file paths in detail. A Windows drive
    # letter or a Traceback line is the shape a leak takes.
    assert "Traceback" not in body["detail"]
    assert ":\\" not in body["detail"]


def test_unknown_route_is_a_typed_404(client):
    _assert_error(client.get("/v1/nope"), 404, "not_found")


def test_wrong_method_is_a_typed_405(client):
    _assert_error(client.get("/v1/embed/text"), 405, "method_not_allowed")


def test_empty_body_is_400(client):
    _assert_error(
        client.post(
            "/v1/embed/video",
            content=b"",
            headers={"content-type": "application/octet-stream"},
        ),
        400,
        "empty_body",
    )


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


# --------------------------------------------------------------------------- #
# Error-code registry
# --------------------------------------------------------------------------- #
def test_every_emitted_code_is_declared():
    """_error() asserts the code is in ERROR_CODES; keep the two in step."""
    assert "service_unavailable" in app_module.ERROR_CODES
    assert "invalid_request" in app_module.ERROR_CODES
    assert "request_too_large" in app_module.ERROR_CODES
    assert "inference_failed" in app_module.ERROR_CODES
    # Codes the backend transport maps by name (backend/service/services/ml_client.py,
    # a separate checkout - named here as prose only, this suite imports nothing
    # from it).
    for required in ("service_unavailable", "request_too_large", "not_found"):
        assert required in app_module.ERROR_CODES


def test_no_openapi_console_is_exposed(client):
    """An interactive console on a GPU box is a liability with no upside."""
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
