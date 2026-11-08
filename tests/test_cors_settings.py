"""CORS resolution: the one place a documented env key actually reaches the app.

WHY this file exists: `CORS_ALLOW_ORIGINS` sat in docker/.env.example and in both
deployment files for the whole life of the harness while
`service/api/app.py` hardcoded `allow_origins=["*"]`. An operator who narrowed it
believed the API surface narrowed; it did not. These tests pin the resolution
rules from `_cors_settings()` so the knob cannot silently stop being read again.

WHAT IS DELIBERATELY ASSERTED (the parts a browser actually depends on):

  * the DEFAULT is what the service shipped before this change, so no existing
    deployment changes behaviour by upgrading;
  * a wildcard origin is never paired with credentials - the browser rejects
    `Access-Control-Allow-Origin: *` and `Allow-Credentials: true` together, and
    Starlette would emit both. Shipping an illegal pair is worse than shipping a
    narrow one, because the failure is invisible until a cross-origin request;
  * an explicit list keeps credentials, which is the legal and intended shape;
  * malformed input degrades to the wildcard with a warning instead of raising at
    import - a typo in a variable must never make the API unimportable.

RESET: the autouse fixture in conftest.py already strips platform flags, so each
test sets the variable it needs and the module-level value is restored by
monkeypatch.
"""

from __future__ import annotations

import pytest

from service.api import app as app_module


def _settings(monkeypatch: pytest.MonkeyPatch, value: str | None) -> dict:
    if value is None:
        monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", value)
    return app_module._cors_settings()


def test_unset_keeps_the_legacy_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _settings(monkeypatch, None) == {"allow_origins": ["*"], "allow_credentials": False}


def test_blank_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _settings(monkeypatch, "   ")["allow_origins"] == ["*"]


def test_explicit_wildcard_is_the_legacy_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, "*")
    assert settings["allow_origins"] == ["*"]
    assert settings["allow_credentials"] is False


def test_json_array_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, '["https://a.example","https://b.example"]')
    assert settings["allow_origins"] == ["https://a.example", "https://b.example"]
    # An explicit list is the legal pairing for credentials, so they stay on.
    assert settings["allow_credentials"] is True


def test_comma_list_is_parsed_and_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, " https://a.example , https://b.example ,")
    assert settings["allow_origins"] == ["https://a.example", "https://b.example"]
    assert settings["allow_credentials"] is True


def test_json_array_containing_only_a_wildcard_stays_wildcard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch, '["*"]')
    assert settings["allow_origins"] == ["*"]
    assert settings["allow_credentials"] is False


def test_malformed_json_degrades_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    # A broken value must not turn an import into a 500 at boot.
    settings = _settings(monkeypatch, "[not json")
    assert settings["allow_origins"] == ["*"]
    assert settings["allow_credentials"] is False


def test_empty_array_degrades_to_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _settings(monkeypatch, "[]")["allow_origins"] == ["*"]


def test_the_middleware_receives_the_resolved_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end: the value must reach CORSMiddleware, not just the helper."""
    from starlette.middleware.cors import CORSMiddleware

    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://app.example")
    built = app_module.create_app()
    cors = [m for m in built.user_middleware if m.cls is CORSMiddleware]
    assert cors, "create_app() no longer installs CORSMiddleware"
    options = cors[0].kwargs
    assert options["allow_origins"] == ["https://app.example"]
    assert options["allow_credentials"] is True
