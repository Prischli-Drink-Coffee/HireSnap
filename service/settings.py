"""Environment-driven configuration for the HireSnap ML sidecar.

WHY EXPLICIT GETTERS AND NOT A SETTINGS OBJECT BUILT AT IMPORT TIME
-------------------------------------------------------------------
`import settings` must never fail. If a missing variable raised at import, one
typo in an optional key would take the container down before it could log which
key was wrong, and `docker compose config` could not even render a stack for a
host that legitimately has no GPU. Every getter therefore reads os.environ at
CALL time and falls back to a documented default, so the process starts and
answers /health with a truthful "degraded" even when every inference dependency
is missing (docs/SERVICE_CONTRACTS.md 1.1).

Nothing here reads a file, opens a socket or imports torch. A configuration
module that can fail for an unrelated reason makes the real failure harder to
find, and /health must stay cheap enough to poll forever.

NAMES: the manifest keeps the double-underscore nesting spelling (ML__HOST) and
the compose `environment:` block flattens it to the single-underscore name the
process sees (ML_HOST). That translation happens in docker/docker-compose.yaml,
not here: this module reads the FLAT names only and never looks for ML__*.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------- #
# Identity. Kept here rather than in app.py so a dependency-free check can import
# this module and still know who the service says it is.
# --------------------------------------------------------------------------- #
SERVICE_NAME = "ml"
# Bump when the SHAPE of a response changes (a field added, renamed or removed).
# /health publishes it and docs/SERVICE_CONTRACTS.md 1.1 makes it a contract: an
# operator must be able to tell two deployments apart without a shell.
SERVICE_VERSION = "1.0.0"

# /app/service in the image (docker/Dockerfile COPY service /app/service), or
# <repo>/ml/service on a developer checkout. This is the modules directory: the
# flat modules are importable because it is on sys.path (PYTHONPATH in the image,
# pythonpath = ["service"] in pyproject.toml for pytest).
SERVICE_DIR = Path(__file__).resolve().parent
# <repo>/ml or /app. Not the CWD, because the CWD is whatever the caller used.
REPO_ROOT = SERVICE_DIR.parent


def _raw(name: str) -> str:
    """Raw environment value, with "" treated as unset.

    WHY: `ML_PORT=` in a compose file is how an operator says "not configured",
    and compose passes the empty string through instead of dropping the variable.
    Collapsing "" to the default below is what the operator meant.
    """
    return (os.environ.get(name) or "").strip()


def _text(name: str, default: str) -> str:
    value = _raw(name)
    return value if value else default


def _int(name: str, default: int) -> int:
    value = _raw(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(
            f"{name} must be an integer, got {value!r} (check docker/.env.<mode>)"
        ) from None


def _float(name: str, default: float) -> float:
    value = _raw(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        raise ValueError(
            f"{name} must be a number of seconds, got {value!r} "
            "(check docker/.env.<mode>)"
        ) from None


def _path(name: str, default: Path) -> Path:
    """A filesystem path from the environment.

    Deliberately NOT .resolve(): a path that does not exist yet (a bind mount
    that is about to appear, a Hugging Face cache created on first use) must not
    raise, and resolving a Windows path inside a Linux container would produce a
    nonsense string.
    """
    value = _raw(name)
    if not value:
        return default
    return Path(value).expanduser()


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
def host() -> str:
    """Bind address. 0.0.0.0 is required inside a container: 127.0.0.1 would be
    reachable from the container only, and the compose healthcheck would hang."""
    return _text("ML_HOST", "0.0.0.0")


def port() -> int:
    """In-container HTTP port. 8080 keeps the healthcheck, the gateway upstream
    and every other service in the stack on one convention."""
    return _int("ML_PORT", 8080)


# --------------------------------------------------------------------------- #
# Storage: scratch space and model files
# --------------------------------------------------------------------------- #
def data_path() -> Path:
    """Writable directory for per-request scratch media.

    This is NOT the backend data directory, even though the key name matches:
    the backend DATA_PATH is the uploads directory and holds user data, while
    this one holds a spooled request body that is deleted as soon as the call
    returns. Same key, two lifecycles, so compose gives them two different
    container paths (/app/data vs /app/ml-scratch) that can never be confused by
    someone who execs into the wrong container.
    """
    return _path("DATA_PATH", REPO_ROOT / "data")


def weights_dir() -> Path:
    """Directory holding the trained checkpoints (Unet3D.pth).

    NOT in the repository (docs/SIDECARS.md, ml/README.md): the file is a trained
    artifact of a few hundred MB. A deployment bind-mounts it read-only; a
    developer drops it in ml/models/weights or sets WEIGHTS_DIR.
    """
    return _path("WEIGHTS_DIR", REPO_ROOT / "models" / "weights")


def unet_weights_path() -> Path:
    """The single weight file the video path loads."""
    return weights_dir() / "Unet3D.pth"


def pretrained_ckpt_dir() -> Path:
    """Root of the Cosmos tokenizer checkpoints.

    The pipeline used to read "pretrained_ckpts/Cosmos-Tokenizer-CV4x8x8/
    encoder.jit" RELATIVE TO THE WORKING DIRECTORY, which meant a mount could
    only be honoured from one CWD. The sidecar owns that code now, so the path is
    composed from this getter instead (see cosmos_encoder_path below) and the CWD
    is irrelevant.
    """
    return _path("PRETRAINED_CKPT_DIR", REPO_ROOT / "models" / "pretrained_ckpts")


# The directory name comes from NVIDIA Cosmos and is part of the artifact layout:
# it is what the .jit file is distributed under, so it is a constant, not config.
COSMOS_TOKENIZER_DIRNAME = "Cosmos-Tokenizer-CV4x8x8"


def cosmos_encoder_path() -> Path:
    """The Cosmos tokenizer encoder .jit the video path loads.

    ONE place that knows the artifact name, so the readiness probe in engine.py
    and the code that actually loads it cannot disagree about which file they
    mean - the class of bug that made PRETRAINED_CKPT_DIR a lie before.
    """
    return pretrained_ckpt_dir() / COSMOS_TOKENIZER_DIRNAME / "encoder.jit"


def ffmpeg_binary() -> str:
    """ffmpeg executable used for frame extraction.

    Event-driven: the repository only ever ran the distro ffmpeg from PATH, and a
    host that keeps a static build somewhere else should not need a rebuilt image
    to say where it is.
    """
    return _text("FFMPEG_BINARY", "ffmpeg")


def hf_home() -> Optional[Path]:
    """Hugging Face cache. None means "leave HF_HOME alone" (its own default is
    ~/.cache/huggingface, which inside the container is a writable home)."""
    value = _raw("HF_HOME")
    return Path(value).expanduser() if value else None


def export_environment() -> None:
    """Copy the settings the ML libraries read THEMSELVES into os.environ.

    WHY this is needed when everything else is a getter: transformers and
    sentence-transformers never call hf_home(), they read HF_HOME from the
    process environment at import time. Setting it here, once, before the first
    heavy import is the only way the cache lands in the mounted volume instead of
    the container writable layer, where it is lost on recreate and re-downloaded
    at multi-hundred-MB cost.
    """
    resolved = hf_home()
    if resolved is not None:
        os.environ["HF_HOME"] = str(resolved)


# --------------------------------------------------------------------------- #
# Device and models
# --------------------------------------------------------------------------- #
def device() -> str:
    """cuda, cpu, or "" for "decide when a model is first loaded".

    WHY the empty default: deciding here would mean importing torch in a
    configuration module, and then /health could no longer be the cheap endpoint
    docs/SERVICE_CONTRACTS.md 1.1 requires. engine.py resolves "" the first time
    it actually needs a device.
    """
    return _text("DEVICE", "").lower()


def video_model_id() -> str:
    """Identifier published in every video-embedding response. It names the
    composition (UNet3D head over Cosmos latents), not a single checkpoint."""
    return _text("ML_VIDEO_MODEL_ID", "Unet3D+Cosmos-Tokenizer-CV4x8x8")


def text_model_id() -> str:
    """Same model the backend used for candidate text, so vectors produced before
    and after this extraction stay comparable (all-MiniLM-L6-v2, 384 dims)."""
    return _text("ML_TEXT_MODEL_ID", "all-MiniLM-L6-v2")


def asr_model_id() -> str:
    """Same ASR checkpoint the backend loaded, so transcripts do not change with
    the deployment shape."""
    return _text("ML_ASR_MODEL_ID", "facebook/wav2vec2-large-960h-lv60-self")


# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #
def max_upload_mb() -> int:
    """Ceiling on one uploaded media body, in MiB.

    A video is not a form field: without a ceiling one 4 GB upload fills the
    container filesystem and every later request fails with a confusing disk
    error instead of a clear 413. 0 is legal and means "reject everything", which
    is the safe direction for a typo.
    """
    return _int("MAX_UPLOAD_MB", 512)


def max_upload_bytes() -> int:
    return max_upload_mb() * 1024 * 1024


def max_text_chars() -> int:
    """Ceiling on one /v1/embed/text input.

    The tokenizer truncates at 512 tokens anyway; the cap exists so a pasted
    document of 10 MB is refused in microseconds instead of being fed through the
    model. 413 for the same reason as the media cap.
    """
    return _int("MAX_TEXT_CHARS", 20000)


def model_idle_unload_seconds() -> float:
    """Seconds of inactivity after which loaded models are dropped.

    WHY this exists: one GPU has to be shared. A sidecar that keeps wav2vec2
    (~1.2 GB) plus the sentence transformer resident starves whatever else wants
    VRAM, and "borrow the card for one batch" is exactly the ML deployment case.
    0 (the default) means never unload, which is right for a dedicated card:
    reloading costs tens of seconds.
    """
    seconds = _float("MODEL_IDLE_UNLOAD_SECONDS", 0.0)
    return max(0.0, seconds)


# --------------------------------------------------------------------------- #
# Introspection for logs
# --------------------------------------------------------------------------- #
def describe() -> dict:
    """A log-safe snapshot of the effective configuration.

    Paths and model ids only: every value is a default or a non-secret setting,
    and the sidecar holds no credential by construction (it receives bytes and
    returns vectors). Printed once at startup so `docker compose logs ml` answers
    "which weights did it actually use" without a shell into the container.
    """
    resolved_hf = hf_home()
    return {
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "host": host(),
        "port": port(),
        "data_path": str(data_path()),
        "weights_dir": str(weights_dir()),
        "unet_weights": str(unet_weights_path()),
        "pretrained_ckpt_dir": str(pretrained_ckpt_dir()),
        "cosmos_encoder": str(cosmos_encoder_path()),
        "hf_home": str(resolved_hf) if resolved_hf else "",
        "device": device() or "auto",
        "ffmpeg": ffmpeg_binary(),
        "max_upload_mb": max_upload_mb(),
        "max_text_chars": max_text_chars(),
        "model_idle_unload_seconds": model_idle_unload_seconds(),
    }
