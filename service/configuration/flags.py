"""Strict boolean parsing for environment flags.

WHY THIS MODULE EXISTS. Every flag used to be read by a local copy of the same
six-line helper: `TRUE, TRUE, True, "yes", "on", "1"` meant on and EVERY other
non-empty string meant off - including "ture", "tru", "TRUE " with a stray byte and
the Russian "да". Six copies of that rule meant a typo silently disabled mail, the
cache or the workers while every health endpoint stayed green, and it was the only
setting class in the process that failed without saying anything.

`DEBUG` was already strict (service/main.py raises unless it is exactly TRUE or
FALSE). This module applies the same standard to the rest, and keeps the accepted
spellings deliberately wide (the manifest writes `false`, compose may write
`0`, a shell may export `no`): the goal is to reject what nobody meant, not to
narrow what somebody did.

WHY it raises instead of warning: a warning on a container's stderr is read by
nobody, and "mail is on" is a claim the operator makes about production. A rejected
boot is louder than an inbox that never fills, and the message names the variable and
the value so the fix is mechanical.

WHAT IS NOT HERE. Numbers stay forgiving on purpose (service/settings.py:65-73 falls
back instead of failing) because a malformed timeout degrades one call, while a
malformed boolean silently changes which components exist.
"""

from __future__ import annotations

import os
from typing import Optional

#: Spellings that mean "on". Lowercase; input is case-folded before comparison.
TRUTHY = frozenset({"1", "true", "yes", "on", "enabled", "y", "t"})

#: Spellings that mean "off". Also includes the empty string, below.
FALSY = frozenset({"0", "false", "no", "off", "disabled", "n", "f"})


class FlagValueError(ValueError):
    """An environment flag holds something that is neither true nor false."""

    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value
        super().__init__(
            f"{name} must be a boolean (one of 1/true/yes/on/enabled or "
            f"0/false/no/off/disabled), got {value!r}"
        )


def first_present(*names: str, default: str = "") -> str:
    """The first name in *names* that EXISTS in the environment, else *default*.

    Presence beats order: compose may inject both `CACHE__BACKEND` and
    `CACHE_BACKEND` and they must not disagree, so an explicitly empty value counts
    as present and is handled by the caller rather than skipped.
    """
    for name in names:
        if name in os.environ:
            return os.environ[name]
    return default


def parse_flag(raw: str, name: str = "flag") -> bool:
    """Parse one already-read value; nothing that is neither true nor false passes.

    Split out from :func:`read_flag` so a caller that has already resolved a value
    (or a test) can check the parsing rule without touching os.environ. An EMPTY value
    raises rather than counting as off: "set but blank" is the shape a half-finished
    edit leaves behind (`MAIL__ENABLED=`), and reading it as a deliberate false is
    the silent failure this module exists to end.
    """
    text = (raw or "").strip().lower()
    if not text:
        raise FlagValueError(name, raw)
    if text in TRUTHY:
        return True
    if text in FALSY:
        return False
    raise FlagValueError(name, raw)


def read_flag(*names: str, default: bool = False) -> bool:
    """Read a flag from the environment, raising on a value nobody could have meant.

    An ABSENT variable returns *default*: absence is a legitimate "not configured",
    and every caller here has a documented default. A PRESENT variable must parse -
    that is the whole point, since `MAIL__ENABLED=ture` is presence without meaning.
    `read_flag(..., default=True)` is how an opt-out flag (MAIL__STARTTLS) keeps
    working: unset means on, set means it has to say so.
    """
    if not names:
        raise ValueError("read_flag needs at least one environment name")
    if not any(name in os.environ for name in names):
        return default
    raw = first_present(*names)
    return parse_flag(raw, names[0])


def describe_flag(name: str, value: Optional[str]) -> str:
    """A one-line, value-carrying description for a log line or an assertion message."""
    if value is None:
        return f"{name}=<absent>"
    return f"{name}={value!r} -> {'on' if value.strip().lower() in TRUTHY else 'off'}"
