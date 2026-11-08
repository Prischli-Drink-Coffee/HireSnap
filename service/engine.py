"""Adapters between the HTTP layer and the HDL pipeline that this sidecar OWNS.

WHERE THE ML CODE LIVES
-----------------------
It lives next door: frames.py, video_embedding.py, text_embedding.py,
video_to_text.py and user_embedding.py are siblings of this file, and the vendored
Cosmos tokenizer is service/cosmos_tokenizer/. The pipeline was MOVED here out of
backend/src/modelling and backend/cosmos_tokenizer when the sidecar was created, and
both of those trees are now deleted: what is under ml/service/ is the only copy. Nothing
here imports from backend/ and there is no sys.path manipulation anywhere in ml/.

WHY WE DO NOT DUPLICATE BY IMPORTING, AND WHY GPTHub DOES DUPLICATE
------------------------------------------------------------------
R:/GPTHub ships whisper/ and video/ as sidecars that each carry their own copy of
the service code, and that is right THERE: those are unrelated products with no
shared owner, so there is nobody to import from. Here the ML pipeline had exactly
one owner, and the first version of this service tried to keep it that way by
putting backend/ on sys.path and importing src.modelling.*. It did not survive
contact with reality - two defects in that code had to be worked around to import
it at all (an unguarded demo call at module level, and a checkpoint path relative to
the CWD), so the shim grew an AST rewrite and a symlink probe, and the image had to
copy the entire backend tree. That is the drift we were trying to avoid, wearing a
different hat. Owning the modules outright is the honest version: one copy, in one
repository, with those defects actually fixed (see the module docstrings for each).

backend/ now holds business logic only: it calls this service over HTTP through
service/services/ml_client.py and contains no ML import at all, which is what
scripts/check_ml_boundary.py enforces.

This module is the ONE place that decides whether a dependency is present, keeps
loaded models, and turns failures into a status code: EngineUnavailable -> 503 (a
deployment state: no torch, no weights, no GPU) and EngineError -> 500 (the pipeline
ran and failed). Nothing here imports torch at module scope, so the process answers
/health and /v1/capabilities in milliseconds on a cold container.
"""

from __future__ import annotations

import gc
import importlib
import importlib.util
import logging
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import settings

# Sibling modules. Importing them is cheap BY DESIGN: every heavy dependency
# (torch, torchvision, torchaudio, transformers, sentence_transformers, moviepy)
# is imported inside the function that needs it, which is why the test suite can
# import this engine on a host with no CUDA stack.
import text_embedding
import user_embedding
import video_to_text

logger = logging.getLogger("ml")


# --------------------------------------------------------------------------- #
# Error taxonomy. The HTTP layer maps these onto status codes; keeping the mapping
# in one place means a new failure mode cannot accidentally become a 500 ("we are
# broken") when it is really a 503 ("the dependency is not mounted yet").
# --------------------------------------------------------------------------- #
class EngineError(Exception):
    """The request was well formed but inference failed. HTTP 500.

    The pair (code, detail) is what the HTTP layer renders as {error, detail}. It is
    stored explicitly instead of being left in Exception.args so that str(exc) is the
    sentence an operator reads, not the repr of a two-element tuple: app.py forwards
    str(exc) as the response detail, and "('service_unavailable', 'UNet3D weights not
    found at ...')" both repeats the code and buries the actionable part.
    """

    code = "inference_failed"

    def __init__(self, code: Optional[str] = None, detail: str = "") -> None:
        super().__init__(detail)
        if code:
            self.code = code
        self.detail = detail

    def __str__(self) -> str:
        return self.detail or type(self).__name__


class EngineUnavailable(EngineError):
    """A dependency is missing: no torch, no CUDA, no weights, no checkpoints.

    HTTP 503, never 500. The difference is operational: 503 says "retry later or
    mount the volume", 500 says "read the traceback". A missing bind mount reported
    as 500 sends the operator to the wrong layer (docs/SERVICE_CONTRACTS.md 2.1).
    """

    code = "service_unavailable"


def _find_spec(module: str) -> bool:
    """Is a module importable? Does NOT import it.

    find_spec walks sys.path and reads the top of the file; importing torch instead
    would cost seconds on the first call and make /health depend on CUDA
    initialisation, which docs/SERVICE_CONTRACTS.md 1.1 forbids.
    """
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):  # a broken parent package, not a missing one
        return False


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #
class MLEngine:
    """Lazy, cached, unloadable access to the pipeline that lives in this package.

    Lazy: nothing heavy is imported until a request needs it, so /health and
    /v1/capabilities answer in milliseconds on a cold container.
    Cached: reloading the sentence transformer or wav2vec2 per request would make the
    sidecar slower than the in-process call it replaces.
    Unloadable: MODEL_IDLE_UNLOAD_SECONDS drops cached handles after a quiet period so
    one card can be shared with other work.
    """

    # How long a readiness probe result is reused. Two stats and three find_spec
    # calls are cheap, but /health may be polled every 10 s forever and the answer
    # cannot usefully change faster than a volume mount appears.
    PROBE_TTL_SECONDS = 15.0

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._last_used = 0.0
        self._probe_at = 0.0
        self._probe: Optional[Dict[str, Any]] = None
        self._text_model: Any = None
        self._text_device: Optional[str] = None
        self._asr: Optional[Tuple[Any, Any]] = None
        self._asr_device: Optional[str] = None
        self._watchdog: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # -- capability / readiness ------------------------------------------- #
    def capabilities(self, refresh: bool = False) -> Dict[str, Any]:
        """What this process can actually do right now.

        Every field is derived from the filesystem and the interpreter, never from a
        loaded model: an operator asking "is this replica able to serve video
        embeddings" must not wait for a UNet to reach the GPU.
        """
        with self._lock:
            now = time.monotonic()
            if self._probe is not None and not refresh:
                if (now - self._probe_at) < self.PROBE_TTL_SECONDS:
                    return dict(self._probe)
            probe = self._probe_now()
            self._probe_at = now
            self._probe = probe
            return dict(probe)

    def _probe_now(self) -> Dict[str, Any]:
        torch_ok = _find_spec("torch")
        text_ok = _find_spec("sentence_transformers")
        asr_ok = torch_ok and _find_spec("transformers") and _find_spec("torchaudio")

        weights_ok = settings.unet_weights_path().is_file()
        ckpt_ok = settings.cosmos_encoder_path().is_file()

        cuda = False
        if torch_ok:
            # Deferred to here and only here: CUDA presence is the one fact that
            # cannot be read off the filesystem.
            try:
                import torch  # noqa: PLC0415 - intentional: keeps /health cheap

                cuda = bool(torch.cuda.is_available())
            except Exception:  # a broken torch install is "no GPU", not a crash
                cuda = False

        return {
            # The video path RUNS ON CPU now (autocast is conditional), so a card is
            # advertised but not required. It is still listed because it decides
            # whether a 40-minute clip takes minutes or an hour.
            "video_embedding": bool(torch_ok and weights_ok and ckpt_ok),
            "text_embedding": bool(text_ok),
            "transcription": bool(asr_ok),
            "cuda": cuda,
            "models": {
                "video": settings.video_model_id(),
                "text": settings.text_model_id(),
                "asr": settings.asr_model_id(),
            },
            # Not part of the HTTP contract; lets /health explain a "degraded"
            # status without a second probe.
            "_weights_present": bool(weights_ok),
            "_checkpoints_present": bool(ckpt_ok),
            "_torch_present": bool(torch_ok),
        }

    def health_status(self) -> str:
        """ok or degraded - never "down": a live process is not down.

        Cheap by construction (cached probe, no import, two stat calls). The strict
        three-field body in app.py means the reason is not in /health;
        /v1/capabilities carries it, which is where an operator looks when a probe
        says degraded.
        """
        probe = self.capabilities()
        full = (
            probe["video_embedding"]
            and probe["text_embedding"]
            and probe["transcription"]
        )
        return "ok" if full else "degraded"

    # -- greedy dependency checks ----------------------------------------- #
    def _require_torch(self) -> Any:
        if not _find_spec("torch"):
            raise EngineUnavailable(
                "service_unavailable",
                "torch is not installed in this image; the ml sidecar needs "
                "ml/docker/requirements.txt (it is not a backend dependency)",
            )
        import torch  # noqa: PLC0415

        return torch

    def _resolve_device(self) -> str:
        """cuda, cpu, or the best available when DEVICE is unset.

        CPU IS SUPPORTED. The pipeline used to call torch.cuda.amp.autocast()
        unconditionally, so a CPU host could not run it at all (docs/KNOWN_GAPS.md
        section 7); autocast_context() in video_embedding.py made that conditional.
        A SLOW but working CPU path is much better than a 503 on a laptop.
        """
        configured = settings.device()
        if configured == "cuda":
            torch = self._require_torch()
            if not torch.cuda.is_available():
                # Only an EXPLICIT request for the GPU is refused: the operator said
                # "this box has a card", and silently falling back to CPU would hide
                # a broken driver for weeks.
                raise EngineUnavailable(
                    "service_unavailable",
                    "DEVICE=cuda but no CUDA device is visible to this container "
                    "(check the nvidia runtime and NVIDIA_VISIBLE_DEVICES)",
                )
            return "cuda"
        if configured == "cpu":
            return "cpu"
        torch = self._require_torch()
        try:
            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:  # a half-installed torch is "no GPU"
            return "cpu"

    # -- models ------------------------------------------------------------ #
    def _text_model_for(self, device: str) -> Any:
        with self._lock:
            if self._text_model is None or self._text_device != device:
                # load_model() is in text_embedding.py so the model construction
                # lives next to the code that knows what a sentence embedding is.
                # The reason this is CACHED here and not there: the module-level
                # function built a fresh SentenceTransformer per call (that was the
                # reason the old sidecar bypassed it), and one card plus one process
                # only ever needs one copy.
                self._text_model = text_embedding.load_model(
                    settings.text_model_id(), device
                )
                self._text_device = device
            return self._text_model

    def _asr_for(self, device: str) -> Tuple[Any, Any]:
        with self._lock:
            if self._asr is None or self._asr_device != device:
                self._asr = video_to_text.load_models(
                    model_id=settings.asr_model_id(), device=device
                )
                self._asr_device = device
            return self._asr

    # -- public operations ------------------------------------------------- #
    def embed_video(self, media_path: Path) -> Dict[str, Any]:
        """Full candidate embedding for one video: vector, dim, model.

        Delegated wholesale to user_embedding.get_user_embedding - frames, tokenizer,
        UNet3D, ASR and text head in one call. It rebuilds its models per call, which
        is the price of a one-shot pipeline; embed_text below is the cached path a
        busier caller should use.
        """
        self._require_torch()
        weights = settings.unet_weights_path()
        if not weights.is_file():
            raise EngineUnavailable(
                "service_unavailable",
                "UNet3D weights not found at " + str(weights)
                + " (mount the host directory that holds Unet3D.pth onto "
                + str(weights.parent) + " via WEIGHTS_DIR; the deployment default is "
                + "infra/models/weights -> /app/models/weights)",
            )
        checkpoint = settings.cosmos_encoder_path()
        if not checkpoint.is_file():
            raise EngineUnavailable(
                "service_unavailable",
                "Cosmos tokenizer checkpoint not found at " + str(checkpoint)
                + " (mount the host directory that holds "
                + settings.COSMOS_TOKENIZER_DIRNAME
                + " onto " + str(settings.pretrained_ckpt_dir())
                + " via PRETRAINED_CKPT_DIR; the deployment default is "
                + "infra/models/pretrained_ckpts -> /app/models/pretrained_ckpts)",
            )

        # Scratch dirs are per request and owned here: the pipeline no longer deletes
        # a caller-supplied folder, but a shared one would still race between two
        # concurrent uploads.
        with tempfile.TemporaryDirectory(prefix="ml-frames-") as frames:
            with tempfile.TemporaryDirectory(prefix="ml-audio-") as audio:
                vector = user_embedding.get_user_embedding(
                    str(media_path),
                    unet_weights_path=str(weights),
                    frames_temp_folder=frames,
                    audio_temp_folder=audio,
                )
        self._last_used = time.monotonic()
        return self._vector_response(vector, settings.video_model_id())

    def embed_text(self, text: str) -> Dict[str, Any]:
        """Sentence embedding for one string, using the CACHED model.

        The arithmetic (encode, then L2 normalise) is the same as encode_texts in
        text_embedding.py; what differs is lifetime: this keeps the model resident,
        because a route that reloads ~90 MB per request is slower than the business
        API doing the work itself.
        """
        device = self._resolve_device()
        model = self._text_model_for(device)
        import numpy as np  # noqa: PLC0415 - keeps the module import light

        tensor = model.encode(text, device=device, convert_to_tensor=True)
        vector = np.asarray(tensor.detach().cpu().numpy(), dtype="float64")
        norm = float(np.linalg.norm(vector))
        if not np.isfinite(norm) or norm == 0.0:
            raise EngineError("inference_failed", "text encoder produced a zero vector")
        self._last_used = time.monotonic()
        return self._vector_response(vector / norm, settings.text_model_id())

    def transcribe(self, media_path: Path) -> Dict[str, Any]:
        """Speech-to-text for one media file, reusing the cached wav2vec2 handle."""
        device = self._resolve_device()
        processor, model = self._asr_for(device)
        sentence = video_to_text.transcribe_file(
            str(media_path), processor, model, device=device
        )
        self._last_used = time.monotonic()
        return {"text": (sentence or "").strip(), "model": settings.asr_model_id()}

    # -- lifecycle --------------------------------------------------------- #
    def _vector_response(self, vector: Any, model_id: str) -> Dict[str, Any]:
        import numpy as np  # noqa: PLC0415

        array = np.asarray(vector, dtype="float64").reshape(-1)
        if array.size == 0:
            raise EngineError("inference_failed", "the pipeline returned an empty vector")
        if not np.isfinite(array).all():
            # NaN/Inf would serialise as bare NaN, which is not valid JSON and breaks
            # every strict client.
            raise EngineError(
                "inference_failed",
                "the pipeline produced NaN/Inf - usually a corrupt or silent video",
            )
        return {
            "vector": [float(value) for value in array.tolist()],
            "dim": int(array.size),
            "model": model_id,
        }

    def unload(self) -> None:
        """Drop cached models and release whatever memory they hold.

        Only handles this process created are dropped; nothing is deleted from disk.
        Safe to call when nothing is loaded (tests, shutdown).
        """
        with self._lock:
            self._text_model = None
            self._text_device = None
            self._asr = None
            self._asr_device = None
        gc.collect()
        try:
            import torch  # noqa: PLC0415

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # unload must never raise
            pass

    def maybe_unload(self) -> bool:
        """Unload if the idle window has passed. True when it unloaded."""
        idle = settings.model_idle_unload_seconds()
        if idle <= 0 or self._last_used <= 0:
            return False
        if (time.monotonic() - self._last_used) < idle:
            return False
        self.unload()
        logger.info(
            "models unloaded after %.0fs idle - VRAM released for other work", idle
        )
        return True

    def start_watchdog(self) -> None:
        """Background unload thread; a no-op unless MODEL_IDLE_UNLOAD_SECONDS > 0.

        A thread and not an asyncio task: the unload happens after the last request,
        and a thread keeps working even while the event loop is busy with a long
        CPU-bound inference in the threadpool.
        """
        idle = settings.model_idle_unload_seconds()
        if idle <= 0 or self._watchdog is not None:
            return
        self._stop.clear()
        interval = max(1.0, min(30.0, idle / 2.0))

        def loop() -> None:
            while not self._stop.wait(interval):
                try:
                    self.maybe_unload()
                except Exception:  # a watchdog that dies stops unloading forever
                    logger.exception("idle unload failed")

        self._watchdog = threading.Thread(
            target=loop, name="ml-idle-unload", daemon=True
        )
        self._watchdog.start()
        logger.info("idle unload armed: %.0fs of quiet drops the models", idle)

    def stop_watchdog(self) -> None:
        self._stop.set()
        thread = self._watchdog
        self._watchdog = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)


# The process-wide engine. app.py imports this; tests replace the attribute with a
# stub so the HTTP contract can be exercised without torch or any weights.
engine = MLEngine()
