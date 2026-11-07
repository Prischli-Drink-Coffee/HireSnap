"""HTTP layer of the HireSnap ML sidecar.

This process exists so that the embedding pipeline can cost GPU memory, a CUDA
runtime and multi-gigabyte model weights WITHOUT the business API paying for any
of it. The backend keeps routing, auth and MySQL; this service only turns bytes
into vectors and audio into text. Separation is at process level on purpose: a
CUDA out-of-memory kill then takes down the sidecar, not the API that is serving
logins (docs/SIDECARS.md section 4).

CONTRACT (also tabulated in ml/README.md)
-----------------------------------------
GET  /health          -> 200 {status, service, version}
GET  /v1/capabilities -> 200 {video_embedding, text_embedding, transcription, cuda, models}
POST /v1/embed/video  -> 200 {vector, dim, model}
POST /v1/embed/text   -> 200 {vector, dim, model}
POST /v1/transcribe   -> 200 {text, model}

Body rules:
  * /v1/embed/video and /v1/transcribe take the RAW media bytes as the request
    body (Content-Type: application/octet-stream, or anything not multipart).
    A multipart form with a "file" part is also accepted when python-multipart is
    installed, because every other upload tool in this repository speaks
    multipart - but the raw form is the documented one: it needs no parser, no
    extra dependency and it streams.
  * /v1/embed/text takes JSON {"text": "..."}.

Errors are ALWAYS {"error": <machine code>, "detail": <human text>} with 400,
404, 413, 415, 422, 500 or 503, per docs/SERVICE_CONTRACTS.md section 2.1. The
FastAPI defaults are overridden so a validation failure does not arrive as
{"detail": [...]} - the backend transport maps the code, and a caller that has to
parse two error shapes gets it wrong.

WHY 503 IS THE COMMON FAILURE AND 500 IS RARE
---------------------------------------------
This sidecar is the one service whose dependencies are mostly OUTSIDE the image:
the weights and the Cosmos checkpoints are bind mounts, and the GPU belongs to the
host. A missing mount, a CPU-only host or an absent torch is a DEPLOYMENT state,
not a bug, so those paths answer 503 with a sentence that names the missing path.
500 means the pipeline actually ran and failed - which is a code problem.
"""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

import settings
from engine import EngineError, EngineUnavailable, engine

logger = logging.getLogger("ml")

# Every code this service may emit, with its status. Keeping the set explicit is
# what lets _error() assert: a typo in a code becomes a loud failure in the test
# suite rather than a log line nobody reads.
ERROR_CODES = frozenset(
    {
        "invalid_request",  # 422 malformed body / bad JSON / missing field
        "empty_body",  # 400 zero bytes where media was expected
        "request_too_large",  # 413 body or text over the configured ceiling
        "unsupported_media_type",  # 415 multipart without a multipart parser
        "not_found",  # 404 no such route
        "method_not_allowed",  # 405 wrong verb
        "service_unavailable",  # 503 no torch / no CUDA / no weights / no checkpoints
        "inference_failed",  # 500 the pipeline ran and failed
        "internal",  # 500 anything unforeseen
    }
)

# Media types we are willing to feed to the pipeline. Not a security control (the
# pipeline's own ffmpeg call is what validates the container) - it exists so a
# caller that posts JSON by mistake gets 415 instead of a confusing 500 several
# seconds later.
_MEDIA_HINTS = ("video/", "audio/", "application/octet-stream")


class ApiError(Exception):
    """A refusal raised by the HTTP layer itself (not by the engine)."""

    def __init__(self, code: str, detail: str, status: int) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status


def _error(code: str, detail: str, status: int) -> JSONResponse:
    """The single error shape this service speaks."""
    assert code in ERROR_CODES, "undeclared error code: " + code
    return JSONResponse({"error": code, "detail": detail}, status_code=status)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hooks, in the lifespan form FastAPI does not deprecate.

    The body is deliberately trivial: nothing here may load a model or touch the
    GPU, because a container that cannot reach its weights still has to answer
    /health with a truthful "degraded" (docs/SERVICE_CONTRACTS.md 1.1).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Before anything imports transformers: HF_HOME is read by the libraries
    # themselves at import time, not by settings.hf_home().
    settings.export_environment()
    # One line that answers "which weights did this container actually mount".
    logger.info(
        "ml sidecar %s starting: %s", settings.SERVICE_VERSION, settings.describe()
    )
    engine.start_watchdog()
    try:
        yield
    finally:
        engine.stop_watchdog()
        engine.unload()


app = FastAPI(
    title="HireSnap ML sidecar",
    version=settings.SERVICE_VERSION,
    lifespan=lifespan,
    # No /docs, no /redoc: this is an internal service on the compose network, and
    # an interactive console on a GPU box is a liability with no upside. The
    # contract lives in ml/README.md and ml/tests/test_contract.py.
    docs_url=None,
    redoc_url=None,
)


# --------------------------------------------------------------------------- #
# Exception handlers - one error language for every failure path
# --------------------------------------------------------------------------- #
@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code == 404:
        return _error("not_found", "no such route: " + request.url.path, 404)
    if exc.status_code == 405:
        return _error(
            "method_not_allowed",
            "method " + request.method + " is not allowed here",
            405,
        )
    return _error("internal", str(exc.detail), exc.status_code)


@app.exception_handler(RequestValidationError)
async def _validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # str(exc.errors()) rather than the list itself: the list carries a "ctx"
    # object that is not reliably JSON-serialisable, and a failure INSIDE an error
    # handler turns a 422 into a 500.
    return _error("invalid_request", str(exc.errors())[:400], 422)


@app.exception_handler(ApiError)
async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
    return _error(exc.code, exc.detail, exc.status)


@app.exception_handler(EngineUnavailable)
async def _unavailable(request: Request, exc: EngineUnavailable) -> JSONResponse:
    logger.warning("request refused, dependency missing: %s", exc)
    return _error("service_unavailable", str(exc), 503)


@app.exception_handler(EngineError)
async def _engine_error(request: Request, exc: EngineError) -> JSONResponse:
    logger.exception("inference failed: %s", exc)
    return _error(exc.code, str(exc), 500)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    # Deliberately does not leak the exception text: docs/SERVICE_CONTRACTS.md 2.1
    # forbids paths and tracebacks in detail, and this is the one path where the
    # text is guaranteed to be a Python message about internals.
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return _error("internal", "unexpected error, see the ml container logs", 500)


# --------------------------------------------------------------------------- #
# Upload plumbing
# --------------------------------------------------------------------------- #
def _scratch_dir() -> Path:
    """Where a request body is spooled before the pipeline sees it.

    Prefers DATA_PATH (the mounted volume, docker/.env.example:73) so a 300 MB
    upload lands on the storage the operator provisioned for it, and falls back to
    the system temp dir when that path is not writable - a CPU developer checkout
    has no /app/data and must still be able to exercise the 413 path.
    """
    candidate = settings.data_path() / "ml-incoming"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write-probe"
        probe.write_bytes(b"")
        probe.unlink()
        return candidate
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "ml-incoming"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _declared_length(request: Request) -> Optional[int]:
    raw = request.headers.get("content-length")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def _read_media(request: Request, max_bytes: int) -> Tuple[Path, str]:
    """Spool the request body to a temp file and return (path, original name).

    Streaming, not await request.body(): the size guard must fire BEFORE the
    container allocates the whole upload, otherwise MAX_UPLOAD_MB only decides how
    long it takes to run out of memory.
    """
    declared = _declared_length(request)
    if declared is not None and declared > max_bytes:
        raise ApiError(
            "request_too_large",
            "body of " + str(declared) + " bytes exceeds MAX_UPLOAD_MB ("
            + str(settings.max_upload_mb()) + " MiB)",
            413,
        )

    scratch = _scratch_dir()
    handle = tempfile.NamedTemporaryFile(
        dir=str(scratch), prefix="upload-", suffix=".bin", delete=False
    )
    path = Path(handle.name)
    written = 0
    try:
        with handle:
            async for chunk in request.stream():
                written += len(chunk)
                if written > max_bytes:
                    raise ApiError(
                        "request_too_large",
                        "body exceeds MAX_UPLOAD_MB ("
                        + str(settings.max_upload_mb())
                        + " MiB); the check is incremental so nothing past the limit "
                        "is buffered",
                        413,
                    )
                handle.write(chunk)
    except BaseException:
        # Any outcome other than "we own a complete file" must not leave a
        # half-written video on the data volume.
        path.unlink(missing_ok=True)
        raise

    if written == 0:
        path.unlink(missing_ok=True)
        raise ApiError("empty_body", "the request body was empty", 400)
    return path, _original_name(request)


def _original_name(request: Request) -> str:
    """Best-effort original filename, used for the log line and the suffix only.

    The pipeline shells out to ffmpeg, which sniffs the container rather than
    trusting the name, so a wrong or missing name cannot break inference.
    """
    name = request.query_params.get("filename") or request.headers.get("x-filename")
    if name:
        return os.path.basename(name)
    disposition = request.headers.get("content-disposition") or ""
    marker = "filename="
    if marker in disposition:
        return os.path.basename(disposition.split(marker, 1)[1].strip().strip('"'))
    return "upload.bin"


async def _multipart_media(
    request: Request, max_bytes: int
) -> Optional[Tuple[Path, str]]:
    """Extract a "file" part from a multipart body, or None when this is not one.

    WHY support both shapes: docker/ and the frontend both upload multipart, so a
    human poking this service with curl -F will try multipart first. The parser is
    optional - without python-multipart the request is refused with a clear 415
    rather than the AssertionError Starlette would otherwise raise.
    """
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("multipart/form-data"):
        return None
    try:
        form = await request.form()
    except Exception:  # missing python-multipart, or a malformed body
        raise ApiError(
            "unsupported_media_type",
            "multipart is not available in this image; POST the media bytes as the "
            "raw request body instead",
            415,
        ) from None
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise ApiError("invalid_request", "multipart body has no 'file' part", 422)
    payload = await upload.read()
    if not payload:
        raise ApiError("empty_body", "the 'file' part was empty", 400)
    if len(payload) > max_bytes:
        raise ApiError(
            "request_too_large",
            "file exceeds MAX_UPLOAD_MB (" + str(settings.max_upload_mb()) + " MiB)",
            413,
        )
    scratch = _scratch_dir()
    handle = tempfile.NamedTemporaryFile(
        dir=str(scratch), prefix="upload-", suffix=".bin", delete=False
    )
    with handle:
        handle.write(payload)
    name = os.path.basename(getattr(upload, "filename", "") or "upload.bin")
    return Path(handle.name), name


async def _receive_media(request: Request, max_bytes: int) -> Tuple[Path, str]:
    """One entry point for both accepted upload shapes."""
    multipart = await _multipart_media(request, max_bytes)
    if multipart is not None:
        return multipart
    media_type = request.headers.get("content-type", "")
    if media_type and not media_type.startswith(_MEDIA_HINTS):
        raise ApiError(
            "unsupported_media_type",
            "content-type " + media_type + " is not media; send the file bytes as "
            "the raw body with an octet-stream or video/* type",
            415,
        )
    return await _read_media(request, max_bytes)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/health")
async def health() -> Dict[str, str]:
    """Liveness/readiness for the compose healthcheck and the gateway.

    EXACTLY three keys, all lowercase strings, per docs/SERVICE_CONTRACTS.md 1.1.
    The reason a replica is degraded deliberately does NOT appear here: /health is
    polled forever and must stay cheap, and a health body that grows extra fields
    is how two deployments stop being comparable. /v1/capabilities carries the
    detail.
    """
    return {
        "status": engine.health_status(),
        "service": settings.SERVICE_NAME,
        "version": settings.SERVICE_VERSION,
    }


@app.get("/v1/capabilities")
async def capabilities(refresh: bool = False) -> Dict[str, Any]:
    """What this replica can actually do, without loading a model.

    refresh=true bypasses the 15 s probe cache - useful right after an operator
    mounts the weights volume and wants to know whether the sidecar noticed.
    """
    probe = engine.capabilities(refresh=refresh)
    return {
        "video_embedding": probe["video_embedding"],
        "text_embedding": probe["text_embedding"],
        "transcription": probe["transcription"],
        "cuda": probe["cuda"],
        "models": probe["models"],
    }


class TextRequest(BaseModel):
    """Body of /v1/embed/text."""

    text: str


@app.post("/v1/embed/video")
async def embed_video(request: Request) -> Dict[str, Any]:
    """Video (frames + speech + text head) embedding for one upload."""
    media_path, name = await _receive_media(request, settings.max_upload_bytes())
    try:
        logger.info("embed/video begin name=%s bytes=%s", name, media_path.stat().st_size)
        # run_in_threadpool, not a direct call: the pipeline is CPU/GPU-bound for
        # tens of seconds and would otherwise block /health for the whole of it,
        # which makes the container look dead and the orchestrator restart it.
        result = await run_in_threadpool(engine.embed_video, media_path)
        logger.info("embed/video done name=%s dim=%s", name, result["dim"])
        return result
    finally:
        media_path.unlink(missing_ok=True)


@app.post("/v1/embed/text")
async def embed_text(payload: TextRequest) -> Dict[str, Any]:
    """Sentence embedding for one string. Same model the API container used to run."""
    text = payload.text.strip()
    if not text:
        raise ApiError("empty_body", "text is empty", 400)
    if len(text) > settings.max_text_chars():
        raise ApiError(
            "request_too_large",
            "text of " + str(len(text)) + " characters exceeds MAX_TEXT_CHARS ("
            + str(settings.max_text_chars()) + ")",
            413,
        )
    return await run_in_threadpool(engine.embed_text, text)


@app.post("/v1/transcribe")
async def transcribe(request: Request) -> Dict[str, Any]:
    """Speech-to-text for one upload."""
    media_path, name = await _receive_media(request, settings.max_upload_bytes())
    try:
        logger.info("transcribe begin name=%s", name)
        result = await run_in_threadpool(engine.transcribe, media_path)
        logger.info("transcribe done name=%s chars=%s", name, len(result["text"]))
        return result
    finally:
        media_path.unlink(missing_ok=True)


def main() -> None:
    """Module entry point, used by the container command and by hand.

    Passes the app OBJECT, not the "app:app" import string: with the string,
    uvicorn imports this module a second time under its real name, so the process
    would hold two engines - one whose models are used and one whose watchdog is
    started. Same process, two configurations, and the wrong one wins at shutdown.
    """
    import uvicorn  # noqa: PLC0415 - only needed when actually serving

    uvicorn.run(
        app,
        host=settings.host(),
        port=settings.port(),
        # One worker on purpose: the GPU is a single resource and every worker
        # would load its own copy of UNet3D and wav2vec2. Scale by adding GPUs and
        # container replicas, not threads.
        workers=1,
        log_level="info",
    )


if __name__ == "__main__":
    main()
