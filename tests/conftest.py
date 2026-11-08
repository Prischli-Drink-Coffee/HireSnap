"""Shared fixtures and process isolation for the sidecar test suites.

WHY THE STUB ENGINE LIVES HERE AND NOT IN test_contract.py
---------------------------------------------------------
Two suites need it: the semantic contract asserts (test_contract.py) and the
committed route snapshot (test_route_snapshot.py). A stub defined in one test module
and imported by another works only while that module keeps a stable import name, and a
fixture in conftest.py is visible to every test file in this directory with no import
at all. R:/GPTHub/backend/tests/conftest.py keeps its fake collaborators here for the
same reason.

WHY THE ENGINE IS STUBBED AT ALL
---------------------------------
The contract of this service is the JSON it speaks, not the numbers the models
produce. A test that loaded UNet3D would need a GPU, the weights that are deliberately
NOT in the repository (ml/README.md, "Weights"), ffmpeg and a minute per case - so it
would never run in CI, and the contract would be untested in practice. app.engine is
replaced by a stub whose only job is to return a known three-field payload or raise a
known typed error, which isolates the layer these tests are about: routing,
validation, size limits, status codes and the {error, detail} shape.

What this deliberately does NOT cover: that the vectors are correct, that the Cosmos
checkpoints load, that CUDA works. Those are properties of the pipeline modules beside
the engine, and the runbook owns the GPU smoke path (AGENTS.md "Testing Guidelines").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pytest
from fastapi.testclient import TestClient

import app as app_module
import settings

# The vector the stub returns. Four values, because a dimension of 384 would make a
# failure message unreadable and the contract is about the SHAPE of the field.
VECTOR = [0.1, 0.2, 0.3, 0.4]

# Every environment name settings.py declares. Duplicated from settings.py on
# purpose: tests/test_service_boundary.py parses settings.py and asserts these two
# lists are equal, so a new key cannot be added to one and forgotten in the other.
# The tuple is what the hermetic fixture below clears.
CONFIG_ENV = (
    "ML_HOST",
    "ML_PORT",
    "DATA_PATH",
    "WEIGHTS_DIR",
    "PRETRAINED_CKPT_DIR",
    "FFMPEG_BINARY",
    "HF_HOME",
    "DEVICE",
    "ML_VIDEO_MODEL_ID",
    "ML_TEXT_MODEL_ID",
    "ML_ASR_MODEL_ID",
    "MAX_UPLOAD_MB",
    "MAX_TEXT_CHARS",
    "MODEL_IDLE_UNLOAD_SECONDS",
)


@pytest.fixture(autouse=True)
def _hermetic_environment(monkeypatch):
    """Run every test against the documented defaults, not the developer shell.

    WHY THIS EXISTS: every getter in settings.py reads os.environ at CALL time, which
    is what stops a typo in an optional key from taking the process down at import -
    and also what lets a stray MAX_UPLOAD_MB=0 in a shell turn this suite red for a
    reason that has nothing to do with the code. Clearing the keys before the test
    (monkeypatch restores them afterwards) makes the defaults the baseline, so a test
    that wants a value is explicit about it and a test that does not cannot be
    influenced by the machine it runs on.
    """
    for name in CONFIG_ENV:
        monkeypatch.delenv(name, raising=False)
    yield


class StubEngine:
    """Stand-in for engine.MLEngine.

    Mirrors only the surface app.py uses: health_status, capabilities, embed_video,
    embed_text, transcribe, start_watchdog, stop_watchdog, unload. Each operation can
    be told to raise a typed engine error instead of returning, which is how the
    503/500 mapping is exercised without a GPU.
    """

    def __init__(self) -> None:
        self.raise_with: Optional[BaseException] = None
        self.calls: list = []

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
def vector():
    """The known vector the stub returns, so assertions can compare against one copy."""
    return list(VECTOR)


@pytest.fixture()
def client(stub):
    """A TestClient over the real app with the stub engine in place.

    The context manager is what runs startup/shutdown, i.e. the same path the
    container takes. It must not need a model, a GPU or a mounted volume.
    """
    with TestClient(app_module.app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """tests/fixtures: input tables the tests read instead of hard-coding cases."""
    return Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def snapshots_dir() -> Path:
    """tests/snapshots: the committed HTTP contract this suite diffs against."""
    return Path(__file__).resolve().parent / "snapshots"
