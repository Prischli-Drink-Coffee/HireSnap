"""service/configuration/flags.py: the one boolean parser.

WHY THIS MODULE NEEDS A TEST OF ITS OWN: it exists because six private copies of the same
rule turned a typo into "off" without a word, so the behaviour worth pinning is the
rejection - an unparseable value must raise, not fall back. Nothing above this module can
prove that: a caller that catches the error would look identical to one that never raised.

WHY THE ACCEPTED SPELLINGS ARE TABLED RATHER THAN TREATED AS ONE RULE: the manifest writes
`false`, compose may write `0`, a shell may export `no` or `TRUE`, and an operator may write
`enabled`. A narrowed parser would break a working deployment, which is a worse outcome than
the bug being fixed, so the wide set is part of the contract.
"""

from __future__ import annotations

import pytest

from service.configuration.flags import (
    FALSY,
    TRUTHY,
    FlagValueError,
    describe_flag,
    first_present,
    parse_flag,
    read_flag,
)

#: Every spelling the manifest, compose, a shell or a human might reasonably use.
TRUTHY_SPELLINGS = ("1", "true", "yes", "on", "enabled", "y", "t", "TRUE", "True", " 1 ")
FALSY_SPELLINGS = ("0", "false", "no", "off", "disabled", "n", "f", "FALSE", "False", " 0 ")

#: Values that are present but mean nothing. Each one was silently False before this module.
MEANINGLESS = (
    "",
    "   ",
    "ture",
    "tru",
    "fasle",
    "enable",
    "disable",
    "maybe",
    "2",
    "-1",
    "ja",
    "да",
)


@pytest.mark.parametrize("raw", TRUTHY_SPELLINGS)
def test_parse_flag_reads_every_documented_true_spelling(raw):
    assert parse_flag(raw) is True


@pytest.mark.parametrize("raw", FALSY_SPELLINGS)
def test_parse_flag_reads_every_documented_false_spelling(raw):
    assert parse_flag(raw) is False


def test_the_two_sets_do_not_overlap():
    """A spelling in both sets would make the result depend on the branch order."""
    assert TRUTHY & FALSY == frozenset()


@pytest.mark.parametrize("raw", MEANINGLESS)
def test_parse_flag_refuses_a_value_nobody_meant(raw):
    """The whole point: silence is what this module was built to remove."""
    with pytest.raises(FlagValueError):
        parse_flag(raw, "MAIL__ENABLED")


def test_the_error_carries_the_name_and_the_value():
    with pytest.raises(FlagValueError) as caught:
        parse_flag("ture", "MAIL__ENABLED")

    assert caught.value.name == "MAIL__ENABLED"
    assert caught.value.value == "ture"
    assert "MAIL__ENABLED" in str(caught.value)
    assert "ture" in str(caught.value)
    # A ValueError too, so a caller that already handles those keeps working.
    assert isinstance(caught.value, ValueError)


def test_the_error_message_lists_the_accepted_spellings():
    """An operator reading the boot failure must not need the source to fix it."""
    with pytest.raises(FlagValueError) as caught:
        parse_flag("maybe", "CELERY__ENABLED")

    message = str(caught.value)
    for spelling in ("1/true/yes/on/enabled", "0/false/no/off/disabled"):
        assert spelling in message


def test_a_doubled_underscore_name_is_the_name_the_error_reports(monkeypatch):
    monkeypatch.setenv("MAIL__ENABLED", "ture")

    with pytest.raises(FlagValueError) as caught:
        read_flag("MAIL__ENABLED", "MAIL_ENABLED")

    # The first name is the canonical one, so that is the one the operator is told about.
    assert caught.value.name == "MAIL__ENABLED"


def test_read_flag_returns_the_default_when_nobody_set_anything(monkeypatch):
    monkeypatch.delenv("MAIL__ENABLED", raising=False)
    monkeypatch.delenv("MAIL_ENABLED", raising=False)

    assert read_flag("MAIL__ENABLED", "MAIL_ENABLED", default=False) is False
    assert read_flag("MAIL__STARTTLS", default=True) is True


def test_an_absent_flag_is_not_the_same_as_an_empty_one(monkeypatch):
    """Absence is "not configured"; blank is a half-finished edit and must raise."""
    monkeypatch.delenv("MAIL__ENABLED", raising=False)
    assert read_flag("MAIL__ENABLED", default=False) is False

    monkeypatch.setenv("MAIL__ENABLED", "")
    with pytest.raises(FlagValueError):
        read_flag("MAIL__ENABLED", default=False)


def test_the_two_spellings_resolve_to_the_first_name_that_exists(monkeypatch):
    """compose writes CACHE__X, a developer shell may write CACHE_X."""
    monkeypatch.setenv("CACHE__ENABLED", "no")
    monkeypatch.setenv("CACHE_ENABLED", "yes")

    assert read_flag("CACHE__ENABLED", "CACHE_ENABLED") is False

    monkeypatch.delenv("CACHE__ENABLED")
    assert read_flag("CACHE__ENABLED", "CACHE_ENABLED") is True


def test_first_present_prefers_the_first_name_that_exists_over_a_non_empty_one(monkeypatch):
    """Presence beats order and beats emptiness - the caller decides what blank means."""
    monkeypatch.setenv("A__X", "")
    monkeypatch.setenv("A_X", "value")

    assert first_present("A__X", "A_X", default="fallback") == ""
    monkeypatch.delenv("A__X")
    assert first_present("A__X", "A_X", default="fallback") == "value"
    monkeypatch.delenv("A_X")
    assert first_present("A__X", "A_X", default="fallback") == "fallback"


def test_read_flag_refuses_to_be_called_with_no_names():
    with pytest.raises(ValueError):
        read_flag()


def test_describe_flag_never_raises_on_a_value_it_could_not_parse():
    """It is used in log lines, including ones written while handling a bad value."""
    assert describe_flag("MAIL__ENABLED", None) == "MAIL__ENABLED=<absent>"
    assert describe_flag("MAIL__ENABLED", "1") == "MAIL__ENABLED='1' -> on"
    assert describe_flag("MAIL__ENABLED", "ture") == "MAIL__ENABLED='ture' -> off"
