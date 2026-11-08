"""service/api/security.py: the JWT Bearer dependency every authenticated route uses.

WHY THE THREE FAILURE MODES ARE PINNED ONE BY ONE: this dependency is the only thing between
an unauthenticated request and a write endpoint, and all three of its rejections are the same
HTTP 403 with a DIFFERENT body. A client that retries on "Invalid token or expired token."
must not silently start retrying on a missing header, so the exact strings matter.

WHY THE CRYPTOGRAPHY-DEPENDENT CASE IS A LOUD SKIP: PyJWT needs the optional `cryptography`
package to sign or verify RS256, and neither the refactor venv nor the CI venv
(requirements.txt only) installs it - measured: jwt.algorithms.has_crypto is False in both.
The skip names the package so a reader knows the hole is environmental and what to install,
and the behaviour that IS reachable without it (every token is rejected, never a raised
NotImplementedError) is asserted rather than skipped.

WHY decode_jwt IS PATCHED FOR THE ACCESS-LEVEL CASES: without cryptography no RS256 token can
be verified, so the branch under test (sub == "admin" for access_level=1) would be
unreachable. The patch replaces the decoder - the one boundary this module has - not the
logic under test.
"""

from __future__ import annotations

import asyncio
import inspect

import jwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from service.api import security as security_module
from service.api.security import JWTBearer
from service.shared import tokens as tokens_module

HAS_CRYPTO = jwt.algorithms.has_crypto


def _request(*headers):
    raw = [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers]
    return Request(
        {"type": "http", "method": "GET", "path": "/", "headers": raw, "query_string": b""}
    )


def _call(bearer, request):
    """Drive the async dependency the way FastAPI's dependency solver does."""
    return asyncio.run(bearer(request))


def _reject(bearer, request):
    """Run the dependency and return (status, detail) of the HTTPException it raises."""
    with pytest.raises(HTTPException) as caught:
        _call(bearer, request)
    return caught.value.status_code, caught.value.detail


# --- the rejections ---------------------------------------------------------
def test_a_missing_header_is_rejected_by_the_starlette_base_class():
    """auto_error=True is the production setting, so "Not authenticated" is the client-visible body.

    DOCUMENTED QUIRK: the superclass raises before JWTBearer's own branch runs, so the
    "Invalid authorization code." message below is only reachable with auto_error=False.
    """
    assert _reject(JWTBearer(), _request()) == (403, "Not authenticated")


def test_a_missing_header_with_auto_error_off_falls_through_to_the_own_branch():
    assert _reject(JWTBearer(auto_error=False), _request()) == (403, "Invalid authorization code.")


def test_a_non_bearer_scheme_is_rejected_by_the_superclass_not_by_this_class():
    """REPORTED, NOT FIXED: JWTBearer's "Invalid authentication scheme." branch is DEAD CODE.

    starlette's HTTPBearer already rejects a non-bearer scheme with "Invalid authentication
    credentials" when auto_error is on, and returns None (-> "Invalid authorization code.")
    when it is off. The class's own scheme check cannot be reached through __call__ in either
    mode; it is unreachable code, so it is not asserted as a message a client can receive.
    """
    assert _reject(JWTBearer(), _request(("Authorization", "Basic abc"))) == (
        403,
        "Invalid authentication credentials",
    )
    assert _reject(JWTBearer(auto_error=False), _request(("Authorization", "Basic abc"))) == (
        403,
        "Invalid authorization code.",
    )


def test_a_token_that_cannot_be_verified_is_rejected_as_expired():
    """Both a malformed token and a missing key end in the same 403 the client sees.

    The decoder raises (bad signature, no key file, or no cryptography) and verify_jwt
    swallows it on purpose: an auth dependency must answer, not propagate a 500 with a
    filesystem path in it.
    """
    assert _reject(JWTBearer(), _request(("Authorization", "Bearer not-a-jwt"))) == (
        403,
        "Invalid token or expired token.",
    )


def test_verify_jwt_never_raises_for_an_unverifiable_token():
    """The bool contract: a False lets __call__ choose the status; an exception would 500."""
    assert JWTBearer().verify_jwt("not-a-jwt") is False


# --- the accepted path (decoder patched: no RS256 without cryptography) -----
def test_a_verified_token_is_returned_as_the_raw_credential(monkeypatch):
    """Routes receive the token string itself - the header "Bearer " prefix is already gone."""
    monkeypatch.setattr(security_module, "decode_jwt", lambda _token: {"sub": "user", "ID": 7})

    result = _call(JWTBearer(), _request(("Authorization", "Bearer header.payload.signature")))

    assert result == "header.payload.signature"


def test_access_level_zero_accepts_any_subject(monkeypatch):
    monkeypatch.setattr(security_module, "decode_jwt", lambda _token: {"sub": "user"})

    assert _call(JWTBearer(access_level=0), _request(("Authorization", "Bearer t"))) == "t"


def test_access_level_one_rejects_a_non_admin_subject_as_a_403(monkeypatch):
    """REPORTED, NOT FIXED: the 401 "Have not access" is raised INSIDE verify_jwt's try/except.

    The inner HTTPException is caught by the module's own "except Exception", so it becomes
    False and the caller renders 403 "Invalid token or expired token.". A level-1 route
    therefore cannot distinguish "not an admin" from "expired" - the client sees one status.
    Pinned as-is: it is a documented behaviour of the moved code, and changing it would change
    every admin endpoint's error contract.
    """
    monkeypatch.setattr(security_module, "decode_jwt", lambda _token: {"sub": "user"})

    assert _reject(JWTBearer(access_level=1), _request(("Authorization", "Bearer t"))) == (
        403,
        "Invalid token or expired token.",
    )


def test_access_level_one_accepts_the_admin_subject(monkeypatch):
    monkeypatch.setattr(security_module, "decode_jwt", lambda _token: {"sub": "admin"})

    assert _call(JWTBearer(access_level=1), _request(("Authorization", "Bearer t"))) == "t"


# --- the untestable-without-cryptography case -------------------------------
@pytest.mark.skipif(
    not HAS_CRYPTO,
    reason=(
        "cryptography is not installed, so PyJWT cannot sign or verify RS256 "
        "(pip install cryptography). The rejection path needs no key and IS covered above."
    ),
)
def test_the_rs256_round_trip_the_deployment_actually_uses(monkeypatch):
    """The real algorithm, end to end: sign with the private key, verify with the public one.

    Only meaningful with `cryptography` installed, hence the skip. When it does run it proves
    the claim the whole auth stack rests on - that the pair compose mounts at
    /app/secrets/certs can actually produce and accept a token.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )

    token = tokens_module.encode_jwt({"sub": "user"}, private_key=private_pem)
    decoded = tokens_module.decode_jwt(token, public_key=public_pem)
    assert decoded["sub"] == "user"

    # And through the dependency, with the module's decoder pointed at the same public key.
    monkeypatch.setattr(
        security_module,
        "decode_jwt",
        lambda raw: tokens_module.decode_jwt(raw, public_key=public_pem),
    )
    assert _call(JWTBearer(), _request(("Authorization", "Bearer " + token))) == token


# --- the module contract FastAPI depends on --------------------------------
def test_the_request_annotation_is_a_real_class_not_a_string():
    """service/api/security.py deliberately has NO "from __future__ import annotations".

    With postponed annotations FastAPI reads __call__'s parameter as the string "Request",
    cannot resolve it while building a route that depends on JWTBearer, and the whole router
    import dies. This test makes that regression a one-line failure instead of a stack trace
    from a different module.
    """
    annotation = inspect.signature(JWTBearer.__call__).parameters["request"].annotation

    assert annotation is Request


def test_the_dependency_is_a_httpbearer_so_openapi_still_publishes_the_scheme():
    """Subclassing HTTPBearer is what puts the padlock and the credentials prompt in /docs."""
    bearer = JWTBearer()

    assert isinstance(bearer, security_module.HTTPBearer)
    assert bearer.scheme_name == "JWTBearer"
    assert bearer.access_level == 0


# --- the token helpers this dependency is built on --------------------------
def test_validate_token_type_accepts_the_matching_type_and_names_both_on_mismatch():
    payload = {"TOKEN_TYPE_FIELD": "access_token_type"}

    assert tokens_module.validate_token_type(payload, "access_token_type") is True

    with pytest.raises(HTTPException) as caught:
        tokens_module.validate_token_type(payload, "refresh_token_type")
    assert caught.value.status_code == 401
    # Both values are in the message, in repr form: that is what makes the log line usable.
    assert caught.value.detail == (
        "Invalid token type 'refresh_token_type' excepted 'access_token_type'"
    )


def test_encode_and_decode_round_trip_with_a_symmetric_algorithm(monkeypatch):
    """HS256 keeps the round trip testable without cryptography; the payload shape is shared."""
    token = tokens_module.encode_jwt({"sub": "user"}, private_key="secret", algorithm="HS256")
    decoded = tokens_module.decode_jwt(token, public_key="secret", algorithm="HS256")

    assert decoded["sub"] == "user"
    assert "iat" in decoded and "exp" in decoded


def test_encode_jwt_honours_an_explicit_expiry_delta():
    from datetime import timedelta

    token = tokens_module.encode_jwt(
        {"sub": "user"},
        private_key="secret",
        algorithm="HS256",
        expire_timedelta=timedelta(minutes=90),
    )
    decoded = tokens_module.decode_jwt(token, public_key="secret", algorithm="HS256")

    assert decoded["exp"] - decoded["iat"] == 90 * 60


def test_create_access_token_carries_role_user_and_type(monkeypatch):
    """The three claims the frontend routes on, without needing a signable key."""
    captured = {}

    def fake_encode_jwt(**kwargs):
        captured.update(kwargs["payload"])
        return "signed"

    monkeypatch.setattr(tokens_module, "encode_jwt", fake_encode_jwt)
    user = _user(role="admin", user_id=11, kind="candidate")

    assert tokens_module.create_access_token(user) == "signed"
    assert captured["sub"] == "admin"
    assert captured["user_id"] == 11
    assert captured["type"] == "candidate"
    assert captured["TOKEN_TYPE_FIELD"] == "access_token_type"


def test_create_refresh_token_carries_only_the_role_in_a_longer_window(monkeypatch):
    captured = {}

    def fake_create_jwt(**kwargs):
        captured.update(kwargs)
        return "refresh"

    monkeypatch.setattr(tokens_module, "create_jwt", fake_create_jwt)

    user = _user(role="user", user_id=1, kind="employer")

    assert tokens_module.create_refresh_token(user) == "refresh"
    assert captured["token_data"] == {"sub": "user"}
    expire_days = tokens_module.settings.auth_jwt.refresh_token_expire_days
    assert captured["expire_timedelta"].days == expire_days


def _user(*, role, user_id, kind):
    """A stand-in with the three attributes create_*_token touch (no DB model needed)."""

    class _User:
        Role = role
        ID = user_id

        class Type:
            value = kind

    return _User()
