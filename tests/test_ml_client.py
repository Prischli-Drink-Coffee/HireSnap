"""The ml transport, HTTP-only: configuration, error mapping and public surface.

WHY THESE TESTS EXIST
---------------------
ml_client.py used to decide, from one environment variable, whether the ML
pipeline ran in the API process or in the ml sidecar. The in-process branch has
been removed together with torch from the backend image, so there is now exactly
one transport and the properties worth pinning are different:

  * an unconfigured deployment must still have a DEFINED target (the sidecar's
    default address), not a silent in-process fallback - that fallback was the
    reason the backend shipped ~2 GB of weights;
  * a sidecar that is missing its weights answers 503 and a proxy in front of it
    answers 502/504 - each must become a named error rather than a bare
    HTTPError, and the URL that was tried must be in the message;
  * the module must never grow a local import of src.modelling or
    sentence_transformers again, because that is what put torch back in the
    image.

Nothing here imports torch, opens a socket, needs a GPU or a running sidecar:
the single HTTP seam, ml_client._http_post, is replaced with a fake that returns
a canned response object. That seam is deliberately one small function so this
file can test the real decision logic - status mapping, body decoding, vector
conversion - instead of a mock of it.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from src.services import ml_client


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeResponse:
    """The subset of requests.Response that ml_client reads."""

    def __init__(self, status_code: int, body=None, text: str = None) -> None:
        self.status_code = status_code
        if text is not None:
            self.text = text
        else:
            self.text = json.dumps(body if body is not None else {})
        self._body = body

    def json(self):
        if self._body is None and self.text is not None:
            raise ValueError("no json body")
        return self._body


class FakeHTTP:
    """Records calls and returns a prepared response (or raises)."""

    def __init__(self, response=None, raises: Exception = None) -> None:
        self.response = response
        self.raises = raises
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self.raises is not None:
            raise self.raises
        return self.response


def _clear_url_env(monkeypatch) -> None:
    for name in ("ML_SERVICE_URL", "ML__SERVICE_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def remote(monkeypatch):
    """An explicitly configured sidecar."""
    monkeypatch.setenv("ML_SERVICE_URL", "http://ml:8080")
    return "http://ml:8080"


@pytest.fixture()
def unconfigured(monkeypatch):
    """No ML_SERVICE_URL: the state of a deployment that forgot to set it."""
    _clear_url_env(monkeypatch)
    return ml_client.DEFAULT_SERVICE_URL


@pytest.fixture()
def media(tmp_path):
    """A file that exists, so the client does not reject it before the transport."""
    path = tmp_path / "candidate.mp4"
    path.write_bytes(b"fake-mp4-bytes")
    return path


# --------------------------------------------------------------------------- #
# Configuration: one transport, always, with a defined default
# --------------------------------------------------------------------------- #
def test_unset_url_falls_back_to_the_sidecar_default(unconfigured):
    """There is no in-process branch to fall back to any more.

    The old behaviour ("unset means import src.modelling and run here") is what
    forced the backend image to carry torch; the replacement is the sidecar's own
    listen address, so an unconfigured deployment still has a defined target and
    fails with one actionable MLUnavailable instead of loading models.
    """
    assert ml_client.DEFAULT_SERVICE_URL == "http://127.0.0.1:8080"
    assert ml_client.service_url() == "http://127.0.0.1:8080"
    assert ml_client.is_remote() is True


def test_set_url_means_http(remote):
    assert ml_client.service_url() == "http://ml:8080"
    assert ml_client.is_remote() is True


def test_double_underscore_spelling_is_accepted(monkeypatch):
    """The manifest nests as ML__SERVICE_URL; compose flattens it, but a bare
    shell export does not, and both must work."""
    monkeypatch.delenv("ML_SERVICE_URL", raising=False)
    monkeypatch.setenv("ML__SERVICE_URL", "http://ml:8080")
    assert ml_client.service_url() == "http://ml:8080"


def test_empty_url_is_treated_as_unset(monkeypatch):
    """ML_SERVICE_URL= is how a compose file says "not configured"; it must not
    produce a request to the empty string."""
    for value in ("", "   "):
        monkeypatch.setenv("ML_SERVICE_URL", value)
        assert ml_client.service_url() == ml_client.DEFAULT_SERVICE_URL


def test_trailing_slash_is_stripped(monkeypatch):
    monkeypatch.setenv("ML_SERVICE_URL", "http://ml:8080/")
    assert ml_client.service_url() == "http://ml:8080"


def test_describe_transport_is_always_http(monkeypatch):
    """The startup banner and the per-task embedder name come from here.

    "in-process" must never appear again: it was the marker of a transport that
    no longer exists, and src/workers/tasks.py formats this value into the name it
    records for every embedding task.
    """
    _clear_url_env(monkeypatch)
    described = ml_client.describe_transport()
    assert described["transport"] == "http"
    assert described["service_url"] == "http://127.0.0.1:8080"
    assert described["timeouts"] == ml_client._timeouts()

    monkeypatch.setenv("ML_SERVICE_URL", "http://ml:8080")
    assert ml_client.describe_transport()["transport"] == "http"


def test_describe_transport_hides_credentials(monkeypatch):
    monkeypatch.setenv("ML_SERVICE_URL", "http://user:secret@ml:8080")
    described = ml_client.describe_transport()
    assert described["transport"] == "http"
    assert "secret" not in described["service_url"]
    assert described["service_url"] == "http://ml:8080"


# --------------------------------------------------------------------------- #
# The backend must stay free of the ML stack
# --------------------------------------------------------------------------- #
def test_module_import_pulls_in_neither_torch_nor_the_local_pipeline():
    """A regression guard for the whole point of this change.

    Checked in a subprocess: an earlier import in this interpreter (or a pytest
    plugin) could mask the regression, and the answer must hold for a fresh start
    of the API.
    """
    probe = (
        "import sys; import src.services.ml_client as m; "
        "print('torch' in sys.modules); "
        "print('src.modelling' in sys.modules); "
        "print(m.service_url())"
    )
    env = dict(os.environ)
    for name in ("ML_SERVICE_URL", "ML__SERVICE_URL"):
        env.pop(name, None)
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    torch_imported, modelling_imported, url = completed.stdout.strip().splitlines()
    assert torch_imported == "False", completed.stdout
    assert modelling_imported == "False", completed.stdout
    assert url == "http://127.0.0.1:8080", completed.stdout


def test_module_source_imports_no_local_inference():
    """Cheap, explicit guard on the file itself.

    The subprocess test above proves the import graph today; this one fails on the
    line that reintroduces it, even when the new import sits inside a function
    behind a branch no test executes - which is exactly how the old fallback
    survived review. Parsed rather than grepped so that prose in a docstring may
    still explain what was removed.
    """
    tree = ast.parse(Path(ml_client.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = {
        name
        for name in imported
        if name.split(".")[0] in {"torch", "sentence_transformers", "transformers", "moviepy"}
        or name.startswith("src.modelling")
    }
    assert forbidden == set(), "ml_client.py must stay free of " + str(sorted(forbidden))


# --------------------------------------------------------------------------- #
# HTTP success path
# --------------------------------------------------------------------------- #
def test_http_video_embedding_returns_a_float32_vector(remote, media, monkeypatch):
    fake = FakeHTTP(
        FakeResponse(200, {"vector": [0.5, 0.25, 0.125], "dim": 3, "model": "Unet3D"})
    )
    monkeypatch.setattr(ml_client, "_http_post", fake)

    result = ml_client.get_user_embedding(str(media))

    # The return TYPE matters: callers np.save() it and feed it to
    # cosine_distances, so a list would break arithmetic three modules away.
    assert isinstance(result, np.ndarray)
    assert result.dtype == np.float32
    np.testing.assert_allclose(result, [0.5, 0.25, 0.125])
    assert fake.calls[0]["url"] == "http://ml:8080/v1/embed/video"
    # The media bytes, not a multipart body: no parser on either side.
    assert fake.calls[0]["data"] == b"fake-mp4-bytes"
    assert fake.calls[0]["headers"]["content-type"] == "application/octet-stream"
    assert fake.calls[0]["headers"]["x-filename"] == "candidate.mp4"


def test_request_goes_to_the_default_url_when_unconfigured(
    unconfigured, media, monkeypatch
):
    """The default is a real target, not a sentinel: the request is addressed to
    it, so the error an operator reads names the address that was tried."""
    fake = FakeHTTP(FakeResponse(200, {"vector": [1.0], "dim": 1}))
    monkeypatch.setattr(ml_client, "_http_post", fake)
    ml_client.get_user_embedding(str(media))
    assert fake.calls[0]["url"] == "http://127.0.0.1:8080/v1/embed/video"


def test_http_video_embedding_writes_the_npy_when_asked(remote, media, monkeypatch, tmp_path):
    """The retired local implementation has this side effect; the transport keeps it."""
    fake = FakeHTTP(FakeResponse(200, {"vector": [1.0, 0.0], "dim": 2, "model": "m"}))
    monkeypatch.setattr(ml_client, "_http_post", fake)
    target = tmp_path / "embedding.npy"

    ml_client.get_user_embedding(str(media), output_embedding_path=str(target))

    assert target.is_file()
    np.testing.assert_allclose(np.load(str(target)), [1.0, 0.0], rtol=0, atol=1e-6)


def test_http_text_embedding_and_transcript(remote, media, monkeypatch):
    responses = [
        FakeResponse(200, {"vector": [0.6, 0.8], "dim": 2, "model": "MiniLM"}),
        FakeResponse(200, {"text": " hello ", "model": "wav2vec2"}),
    ]
    sent = []

    def fake(url, **kwargs):
        sent.append(url)
        return responses[len(sent) - 1]

    monkeypatch.setattr(ml_client, "_http_post", fake)

    vector = ml_client.get_text_embedding("senior python engineer")
    text = ml_client.get_video_transcript(str(media))

    np.testing.assert_allclose(vector, [0.6, 0.8])
    assert text == " hello "  # the transport does not reformat the service's answer
    assert sent == ["http://ml:8080/v1/embed/text", "http://ml:8080/v1/transcribe"]


def test_text_embedding_sends_only_the_text(remote, monkeypatch):
    """model_id is not forwarded: the sidecar pins its own text model, and two
    deployments answering with different vectors for the same text would be a
    silent ranking bug."""
    fake = FakeHTTP(FakeResponse(200, {"vector": [1.0], "dim": 1}))
    monkeypatch.setattr(ml_client, "_http_post", fake)
    ml_client.get_text_embedding("x", model_id="some-other-model")
    assert fake.calls[0]["json"] == {"text": "x"}


# --------------------------------------------------------------------------- #
# HTTP error mapping
# --------------------------------------------------------------------------- #
def test_5xx_is_unavailable_and_keeps_the_service_message(remote, media, monkeypatch):
    monkeypatch.setattr(
        ml_client,
        "_http_post",
        FakeHTTP(
            FakeResponse(
                503,
                {"error": "service_unavailable", "detail": "UNet3D weights not found at /app/src/weights/Unet3D.pth"},
            )
        ),
    )
    with pytest.raises(ml_client.MLUnavailable) as caught:
        ml_client.get_user_embedding(str(media))
    # The operator needs the service's own words: "mount this volume", not "HTTPError".
    assert caught.value.code == "service_unavailable"
    assert caught.value.status == 503
    assert "Unet3D.pth" in caught.value.detail
    assert caught.value.__class__ is ml_client.MLUnavailable


@pytest.mark.parametrize("status", [500, 502, 504, 429])
def test_retryable_statuses_are_unavailable(remote, media, monkeypatch, status):
    monkeypatch.setattr(
        ml_client, "_http_post", FakeHTTP(FakeResponse(status, {"error": "internal"}))
    )
    with pytest.raises(ml_client.MLUnavailable):
        ml_client.get_user_embedding(str(media))


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "empty_body"),
        (413, "request_too_large"),
        (415, "unsupported_media_type"),
        (422, "invalid_request"),
    ],
)
def test_4xx_is_rejected_with_the_service_code(remote, media, monkeypatch, status, code):
    monkeypatch.setattr(
        ml_client, "_http_post", FakeHTTP(FakeResponse(status, {"error": code, "detail": "nope"}))
    )
    with pytest.raises(ml_client.MLRejected) as caught:
        ml_client.get_user_embedding(str(media))
    assert caught.value.code == code
    assert caught.value.status == status


def test_transport_failure_is_unavailable_not_a_propagated_requests_error(remote, media, monkeypatch):
    monkeypatch.setattr(
        ml_client, "_http_post", FakeHTTP(raises=ConnectionError("name resolution"))
    )
    with pytest.raises(ml_client.MLUnavailable) as caught:
        ml_client.get_user_embedding(str(media))
    assert caught.value.code == "ml_service_unreachable"
    assert "name resolution" in caught.value.detail


def test_unreachable_sidecar_message_names_the_url_and_the_fix(monkeypatch, media):
    """The whole failure mode of a defaulted URL is a sidecar that is not running.

    The message has to say which address was tried and which variable changes it,
    because that line is all an operator sees in the log.
    """
    monkeypatch.delenv("ML_SERVICE_URL", raising=False)
    monkeypatch.delenv("ML__SERVICE_URL", raising=False)
    monkeypatch.setattr(
        ml_client, "_http_post", FakeHTTP(raises=OSError("connection refused"))
    )
    with pytest.raises(ml_client.MLUnavailable) as caught:
        ml_client.get_user_embedding(str(media))
    assert caught.value.code == "ml_service_unreachable"
    assert caught.value.status is None
    assert "http://127.0.0.1:8080" in caught.value.detail
    assert "ML_SERVICE_URL" in caught.value.detail
    assert "start the ml service" in caught.value.detail


def test_missing_local_file_is_rejected_before_any_request(remote, tmp_path, monkeypatch):
    fake = FakeHTTP(FakeResponse(200, {"vector": [1.0], "dim": 1}))
    monkeypatch.setattr(ml_client, "_http_post", fake)
    with pytest.raises(ml_client.MLRejected) as caught:
        ml_client.get_user_embedding(str(tmp_path / "absent.mp4"))
    assert caught.value.code == "media_not_found"
    assert caught.value.status == 400
    assert fake.calls == []  # nothing was sent


# --------------------------------------------------------------------------- #
# Contract violations on a 2xx body
# --------------------------------------------------------------------------- #
def test_non_json_body_is_a_contract_error(remote, media, monkeypatch):
    monkeypatch.setattr(
        ml_client, "_http_post", FakeHTTP(FakeResponse(200, None, text="<html>gateway</html>"))
    )
    with pytest.raises(ml_client.MLContractError) as caught:
        ml_client.get_user_embedding(str(media))
    assert caught.value.code == "invalid_response"


def test_empty_body_is_a_contract_error(remote, media, monkeypatch):
    monkeypatch.setattr(ml_client, "_http_post", FakeHTTP(FakeResponse(200, None, text="")))
    with pytest.raises(ml_client.MLContractError) as caught:
        ml_client.get_user_embedding(str(media))
    assert caught.value.code == "empty_response"


def test_dim_mismatch_is_a_contract_error(remote, media, monkeypatch):
    """A service that lies about its dimension is worse than one that is down."""
    monkeypatch.setattr(
        ml_client,
        "_http_post",
        FakeHTTP(FakeResponse(200, {"vector": [1.0, 2.0], "dim": 384, "model": "m"})),
    )
    with pytest.raises(ml_client.MLContractError) as caught:
        ml_client.get_user_embedding(str(media))
    assert "dim=384" in caught.value.detail


def test_missing_vector_is_a_contract_error(remote, media, monkeypatch):
    monkeypatch.setattr(
        ml_client, "_http_post", FakeHTTP(FakeResponse(200, {"dim": 3, "model": "m"}))
    )
    with pytest.raises(ml_client.MLContractError):
        ml_client.get_user_embedding(str(media))


def test_transcript_without_text_is_a_contract_error(remote, media, monkeypatch):
    monkeypatch.setattr(
        ml_client, "_http_post", FakeHTTP(FakeResponse(200, {"model": "wav2vec2"}))
    )
    with pytest.raises(ml_client.MLContractError) as caught:
        ml_client.get_video_transcript(str(media))
    assert caught.value.code == "invalid_response"


# --------------------------------------------------------------------------- #
# Timeouts
# --------------------------------------------------------------------------- #
def test_timeouts_are_generous_enough_for_a_cold_gpu(remote, monkeypatch):
    """A cold deployment re-downloads and loads models; a 30 s default would make
    the first request of a fresh stack fail while the sidecar was healthy."""
    connect, read = ml_client._timeouts()
    assert connect <= 30
    assert read >= 300
    assert read == ml_client.DEFAULT_READ_TIMEOUT


def test_timeouts_are_overridable(remote, monkeypatch):
    monkeypatch.setenv("ML_SERVICE_TIMEOUT", "42")
    monkeypatch.setenv("ML_SERVICE_CONNECT_TIMEOUT", "7")
    assert ml_client._timeouts() == (7.0, 42.0)


def test_timeouts_fall_back_on_garbage(remote, monkeypatch):
    """A typo must not turn a request into a ValueError at send time."""
    monkeypatch.setenv("ML_SERVICE_TIMEOUT", "later")
    assert ml_client._timeouts()[1] == ml_client.DEFAULT_READ_TIMEOUT


# --------------------------------------------------------------------------- #
# Public surface
# --------------------------------------------------------------------------- #
def test_error_hierarchy_is_catchable_as_one_type():
    assert issubclass(ml_client.MLUnavailable, ml_client.MLClientError)
    assert issubclass(ml_client.MLRejected, ml_client.MLClientError)
    assert issubclass(ml_client.MLContractError, ml_client.MLClientError)
    error = ml_client.MLRejected("request_too_large", "too big", 413)
    assert str(error) == "request_too_large (413): too big"
    assert str(ml_client.MLUnavailable("x", "y")) == "x: y"


def test_documented_surface_is_exported():
    """The removal of the fallback is not allowed to shrink the module's surface.

    src/workers/tasks.py calls describe_transport and get_user_embedding by name,
    and operators read service_url/is_remote in the health output.
    """
    expected = {
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
    }
    assert set(ml_client.__all__) == expected
    for name in expected:
        assert hasattr(ml_client, name), name
    # The timeout knobs are part of the documented surface too.
    assert isinstance(ml_client.DEFAULT_CONNECT_TIMEOUT, float)
    assert isinstance(ml_client.DEFAULT_READ_TIMEOUT, float)
    assert callable(ml_client._timeouts)
