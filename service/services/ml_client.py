"""HTTP transport for the ML pipeline: the ml sidecar is the only implementation.

WHY THERE IS NO IN-PROCESS PATH ANY MORE
----------------------------------------
This module used to switch on ML_SERVICE_URL: unset meant "import src.modelling.*
and run in-process", set meant "POST to the sidecar". That switch kept the API
importable on a machine with no GPU, but it also meant the backend shipped torch,
torchvision, transformers, sentence-transformers, moviepy and a Cosmos tokenizer
in its own image, and that a single misconfigured deployment silently loaded
multi-gigabyte weights into the process that serves logins.

The split is now real instead of optional: inference lives in ml/ (see
ml/README.md for the route contract) and this module only speaks HTTP. The
backend image no longer contains torch or CUDA, and there is exactly one code
path for every call, which is the part that was previously untested in
production. An environment that wants embeddings runs the sidecar; one that does
not, does not get them, and says so with MLUnavailable.

ML_SERVICE_URL (or the compose-nested ML__SERVICE_URL) selects the base URL. When
neither is set the default is http://127.0.0.1:8080, which is the sidecar's own
listen address: on the same host, in the same compose network, or behind a
forwarder, that is the address an operator means. A deployment that forgets the
variable now fails with one actionable MLUnavailable on the first ML request
instead of quietly loading models in the API process.

ERROR TAXONOMY
--------------
Callers today do not catch anything: upload_video lets an exception become a 500.
This module keeps that behaviour but names the failure, so a route can later map
it:

    MLClientError      base; carries .code, .status and .detail
      MLUnavailable    transport failed, timed out, or the sidecar said 503 -
                       retryable, and the operator's problem (start the sidecar,
                       mount the volume)
      MLRejected       the sidecar said 4xx - the input is wrong, retrying the
                       same bytes will fail again
      MLContractError  a 2xx body that does not match the contract (a proxy page,
                       a truncated response, a version mismatch)

The error messages are written for the operator who reads them in a log at 2am:
each one names the URL that was tried and the variable that decides it.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Read timeouts, in seconds. A video embedding runs ffmpeg, a Cosmos tokenizer, a
# UNet3D pass, wav2vec2 and a sentence transformer over up to MAX_UPLOAD_MB of
# media; on a cold GPU with an empty HF cache that is minutes, not seconds. A
# 30-second default would make the first request of a fresh deployment fail with a
# timeout even though the sidecar was working perfectly.
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 900.0

# Where the sidecar listens (ml/README.md, ml/settings.py: ML_HOST 0.0.0.0,
# ML_PORT 8080). Used when neither spelling of ML_SERVICE_URL is set, so an
# unconfigured deployment still has a defined target to report in its error.
DEFAULT_SERVICE_URL = "http://127.0.0.1:8080"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class MLClientError(RuntimeError):
    """Any failure of the ML transport, with a machine-readable code."""

    def __init__(self, code: str, detail: str, status: Optional[int] = None) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status

    def __str__(self) -> str:  # used in logs and in the eventual error response
        if self.status is None:
            return self.code + ": " + self.detail
        return self.code + " (" + str(self.status) + "): " + self.detail


class MLUnavailable(MLClientError):
    """The sidecar could not be reached, timed out, or is missing a dependency."""


class MLRejected(MLClientError):
    """The sidecar refused the request; the input is the problem."""


class MLContractError(MLClientError):
    """The sidecar answered 2xx with a body this client cannot use."""


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def service_url() -> str:
    """Base URL of the ml sidecar. Never empty; defaults to 127.0.0.1:8080.

    Both spellings are accepted. The compose manifest uses double-underscore
    nesting (ML__SERVICE_URL) and maps it to the flat name inside the container
    (AGENTS.md: "double underscore = nesting"), so a container sees
    ML_SERVICE_URL; a developer exporting the variable by hand in a shell the
    compose file never touched is more likely to type the flat name directly.
    Accepting both costs one lookup and removes an afternoon of confusion.

    Read on every call rather than memoised: the value is two dict lookups, and a
    cached module-level constant is exactly what makes this untestable and
    impossible to flip in a running process (a runbook remedy: set the variable,
    restart the worker).
    """
    for name in ("ML_SERVICE_URL", "ML__SERVICE_URL"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value.rstrip("/")
    return DEFAULT_SERVICE_URL


def is_remote() -> bool:
    """True when calls go to the sidecar.

    Always True now that the in-process transport is gone. The name is kept
    because it is part of the module's documented surface and reads correctly at
    a call site ("are embeddings fetched over the network?"), not because the
    answer can vary.
    """
    return True


def _timeouts() -> tuple:
    """(connect, read) timeouts, overridable per deployment."""
    try:
        raw_connect = os.environ.get("ML_SERVICE_CONNECT_TIMEOUT") or ""
        connect = float(raw_connect) if raw_connect else DEFAULT_CONNECT_TIMEOUT
    except ValueError:
        connect = DEFAULT_CONNECT_TIMEOUT
    try:
        raw_read = os.environ.get("ML_SERVICE_TIMEOUT") or ""
        read = float(raw_read) if raw_read else DEFAULT_READ_TIMEOUT
    except ValueError:
        read = DEFAULT_READ_TIMEOUT
    return (connect, read)


def _unreachable(base: str, exc: BaseException) -> MLUnavailable:
    """The one 'no sidecar' error, phrased so an operator can act on it.

    Names the URL that was tried and the variable that sets it. The previous
    wording ("cannot reach the ml sidecar at ...") is preserved as the prefix so
    existing log greps and the tests that match on it keep working.
    """
    return MLUnavailable(
        "ml_service_unreachable",
        "cannot reach the ml sidecar at " + base + ": " + str(exc)
        + " (start the ml service, or point ML_SERVICE_URL at it)",
        None,
    )


# --------------------------------------------------------------------------- #
# HTTP seam
# --------------------------------------------------------------------------- #
def _http_post(
    url: str,
    *,
    data: Optional[bytes] = None,
    json: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: Any = None,
) -> Any:
    """The single place this module actually speaks HTTP.

    Isolated on purpose: tests replace this function to exercise the error mapping
    without a server, a socket or a GPU, and the rest of the module stays honest
    about what it does rather than hiding behind a mock object.

    requests, not httpx: requests is already pinned in backend/requirements.txt
    and used by other modules, so the transport adds no dependency to the image.
    """
    import requests  # noqa: PLC0415 - keeps module import free of the transport

    return requests.post(
        url, data=data, json=json, headers=headers or {}, timeout=timeout
    )


def _decode(response: Any) -> Dict[str, Any]:
    """Parse a response body into a dict, or raise MLContractError.

    A gateway error page, an empty body or a JSON list all arrive here. Each one
    means the client and the service disagree about the contract, which is a
    deployment bug worth naming rather than an exception from the json module.
    """
    status = getattr(response, "status_code", 0)
    body = getattr(response, "text", "") or ""
    if not body.strip():
        raise MLContractError(
            "empty_response", "the ml sidecar returned an empty body", status
        )
    try:
        payload = response.json()
    except ValueError:
        raise MLContractError(
            "invalid_response",
            "the ml sidecar returned a non-JSON body (first 120 chars): "
            + body[:120].replace(chr(10), " "),
            status,
        ) from None
    if not isinstance(payload, dict):
        raise MLContractError(
            "invalid_response",
            "the ml sidecar returned " + type(payload).__name__ + ", expected an object",
            status,
        )
    return payload


def _raise_for_status(response: Any) -> None:
    """Translate a refusal into the typed error a caller can act on.

    The sidecar's own error/detail body is preserved as the code and the message,
    so an operator sees "service_unavailable (503): UNet3D weights not found at
    /app/src/weights/Unet3D.pth" instead of a bare HTTPError - which is the
    difference between mounting a volume and reading a traceback.
    """
    status = getattr(response, "status_code", 0)
    if 200 <= status < 300:
        return

    code = "http_" + str(status)
    detail = "the ml sidecar answered " + str(status)
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        code = str(payload.get("error") or code)
        detail = str(payload.get("detail") or detail)

    if status == 429 or status >= 500:
        # 429 and 5xx are "not now", not "not ever": retrying the same request is
        # expected to work once the GPU is free or the volume appears.
        raise MLUnavailable(code, detail, status)
    raise MLRejected(code, detail, status)


def _post_media(path: str, route: str) -> Dict[str, Any]:
    """Upload one media file to a sidecar route and return the decoded body."""
    base = service_url()

    media = Path(path)
    if not media.is_file():
        # Same class as a 4xx: the caller handed us a path that is not there.
        raise MLRejected(
            "media_not_found", "the file to process does not exist: " + str(media), 400
        )

    # Raw body, not multipart: it needs no parser on either side and it streams on
    # the service. The filename rides in a header so the sidecar's logs can name
    # the upload without trusting a multipart boundary.
    headers = {
        "content-type": "application/octet-stream",
        "x-filename": media.name,
    }
    payload = media.read_bytes()
    try:
        response = _http_post(
            base + route, data=payload, headers=headers, timeout=_timeouts()
        )
    except MLClientError:
        raise
    except Exception as exc:  # requests.ConnectionError, requests.Timeout, ...
        raise _unreachable(base, exc) from None

    _raise_for_status(response)
    body = _decode(response)
    logger.info(
        "ml sidecar %s answered for %s (%s bytes)", route, media.name, len(payload)
    )
    return body


def _post_json(route: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    base = service_url()
    try:
        response = _http_post(
            base + route,
            json=payload,
            headers={"content-type": "application/json"},
            timeout=_timeouts(),
        )
    except MLClientError:
        raise
    except Exception as exc:
        raise _unreachable(base, exc) from None
    _raise_for_status(response)
    return _decode(response)


def _vector(body: Dict[str, Any], what: str) -> Any:
    """Turn a {vector, dim, model} body into the ndarray callers expect.

    The return TYPE has to match what src.modelling.user_embedding returned before
    the split, because callers then np.save() the result and compare it with
    cosine distances. A list would serialise fine and then break arithmetic three
    modules away.
    """
    vector = body.get("vector")
    if not isinstance(vector, list) or not vector:
        raise MLContractError(
            "invalid_response", "the " + what + " response has no usable vector", 200
        )
    declared = body.get("dim")
    if isinstance(declared, int) and declared != len(vector):
        raise MLContractError(
            "invalid_response",
            "the " + what + " response declares dim=" + str(declared)
            + " but carries " + str(len(vector)) + " values",
            200,
        )
    import numpy as np  # noqa: PLC0415

    return np.asarray(vector, dtype="float32")


# --------------------------------------------------------------------------- #
# Public surface - signatures mirror the retired backend/src/modelling/*.py
# --------------------------------------------------------------------------- #
def get_user_embedding(
    video_path: str,
    unet_weights_path: str = "src/weights/Unet3D.pth",
    output_embedding_path: Optional[str] = None,
    frames_temp_folder: str = "temp",
    audio_temp_folder: str = "temp",
    num_frames: int = 16,
):
    """Embedding for one candidate video: ndarray of shape (384,).

    The signature is unchanged from src.modelling.user_embedding.get_user_embedding
    so the single call site in service.services.matching.upload_video did not have
    to change beyond its import. The keyword arguments are accepted and ignored
    with a log line: the sidecar owns its own scratch layout and its own weight
    path, and quietly forwarding a backend-relative path
    ("src/weights/Unet3D.pth") into a different container would load the wrong file
    or fail for a reason that makes no sense from the caller's side.

    The parameters stay because removing them would break any caller that passes
    them positionally, and the deprecation is not worth a second breaking change in
    the same release as the transport removal.
    """
    if frames_temp_folder != "temp" or audio_temp_folder != "temp":
        logger.debug(
            "frames_temp_folder/audio_temp_folder are ignored; "
            "the sidecar uses its own scratch directories"
        )
    if num_frames != 16:
        logger.warning(
            "num_frames=%s was requested but the sidecar runs the pipeline's fixed "
            "16-frame path; the value is ignored",
            num_frames,
        )

    body = _post_media(video_path, "/v1/embed/video")
    vector = _vector(body, "video embedding")
    if output_embedding_path:
        # Mirror the retired local implementation's side effect, so a caller that
        # asked for the .npy still gets it.
        import numpy as np  # noqa: PLC0415

        np.save(output_embedding_path, vector)
    logger.info(
        "video embedding from the ml sidecar: dim=%s model=%s",
        getattr(vector, "shape", [len(vector)])[0],
        body.get("model"),
    )
    return vector


def get_text_embedding(text: str, model_id: Optional[str] = None):
    """Embedding for one string, from the sidecar's /v1/embed/text.

    No call site uses this yet. It exists because the sidecar's contract is
    symmetric, and a transport that can reach only one of its three endpoints is a
    half-built bridge: the first caller that needs text embeddings (the vacancy
    side of the ranking in main_services.py) should not have to re-derive it.

    model_id is not forwarded: the sidecar pins its own text model (ml/README.md)
    and letting a caller choose one would give two deployments different vectors
    for the same text.
    """
    if model_id:
        logger.debug(
            "model_id=%s is ignored; the sidecar owns its text model", model_id
        )
    body = _post_json("/v1/embed/text", {"text": text})
    return _vector(body, "text embedding")


def get_video_transcript(video_path: str) -> str:
    """Speech-to-text for one media file, from the sidecar's /v1/transcribe.

    Same reasoning as get_text_embedding: the endpoint exists, so the transport
    exposes it rather than leaving a route only reachable by hand.
    """
    body = _post_media(video_path, "/v1/transcribe")
    text = body.get("text")
    if not isinstance(text, str):
        raise MLContractError(
            "invalid_response", "the transcribe response has no text field", 200
        )
    return text


def describe_transport() -> Dict[str, Any]:
    """Log-safe description of the transport, for the startup banner.

    Always "http": there is no second transport to report on any more. The key is
    kept because service/infrastructure/messaging/tasks.py formats it into the embedder name it records
    per task, and because a future transport (a queue, a batch endpoint) would
    want the same field rather than a new one.

    Never returns a URL with credentials in it: ML_SERVICE_URL is an internal
    compose name (http://ml:8080) and carries none, but this is the one place a
    future operator might put one, so userinfo is stripped defensively.
    """
    safe = service_url()
    if "@" in safe.split("//", 1)[-1]:
        scheme, _, rest = safe.partition("//")
        safe = scheme + "//" + rest.split("@", 1)[-1]
    return {"transport": "http", "service_url": safe, "timeouts": _timeouts()}


__all__ = [
    "MLClientError",
    "MLUnavailable",
    "MLRejected",
    "MLContractError",
    "service_url",
    "is_remote",
    "describe_transport",
    "get_user_embedding",
    "get_text_embedding",
    "get_video_transcript",
]
