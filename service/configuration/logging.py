"""Logging setup for the service (rich console + a file), plus `get_logger`.

WHAT CHANGED FROM THE OLD `src/utils/custom_logging.py` and why:

  * The old module called `logging.basicConfig(filename="setup.log")` with a
    RELATIVE path at import time, so merely importing any module created
    `setup.log` in whatever directory the process happened to be in. That is how
    a stray `setup.log` kept appearing at the repository root and inside the
    package directory. The file handler now points at an absolute path
    (`logs/app.log` next to the backend package, which is the same file
    `service/configuration/logging.yaml` configures for uvicorn) and, if that
    directory cannot be created, logging degrades to console-only instead of
    raising during import.
  * `setup_logging()` still returns a memoised, configured logger so the old call
    sites (`log = setup_logging()`) keep working unchanged.
  * `get_logger()` is the name the router modules use. It is the same logger, with
    a name that says what it returns.

The logger name stays `"sd"`: it is in log lines of every existing deployment and
renaming it would break log filters for no gain.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler
from rich.pretty import install as pretty_install
from rich.theme import Theme
from rich.traceback import install as traceback_install

_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(pathname)s | %(message)s"
LOGGER_NAME = "sd"

#: Set once, so a second import (uvicorn's reloader) does not stack handlers.
_log: Optional[logging.Logger] = None


def _log_file() -> Path:
    from service.configuration.paths import log_file

    return log_file()


def load_logging_config(path: Optional[Path] = None) -> dict:
    """Read the YAML and resolve its ONE relative path to an absolute one.

    WHY THIS EXISTS. The YAML names the file handler's target as "logs/app.log",
    which logging.config resolves against the process's working directory. Inside
    the image that happens to be /app and the file lands where the bind mount
    expects, so nothing complained - but uvicorn's reloader starts its child with
    whatever directory it inherits, a developer starting the server from backend/
    writes a different file than one starting it from the repository root, and a
    process launched from a directory with no logs/ fails to configure the handler
    at all (dictConfig raises and the service does not start).

    The YAML stays the source of truth for the format, the levels, the handlers and
    the logger tree. Only the filename is rewritten, and only when it is relative,
    so a deployment that mounts the log somewhere else can still write an absolute
    path there. There is ONE caller of the YAML (service/main.py, which hands the
    result to uvicorn and to dictConfig), which is what keeps this honest.
    """
    import yaml

    from service.configuration.paths import LOGGING_YAML

    source = Path(path) if path is not None else LOGGING_YAML
    with open(source, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle.read())

    handler = (config.get("handlers") or {}).get("file") or {}
    filename = handler.get("filename")
    if filename and not Path(filename).is_absolute():
        handler["filename"] = str(_log_file())
    return config


def setup_logging(clean: bool = False, debug: bool = False) -> logging.Logger:
    """Configure and return the process logger. Idempotent."""
    global _log

    if _log is not None:
        _log.setLevel(logging.DEBUG if debug else logging.INFO)
        return _log

    target = _log_file()
    file_handler: Optional[logging.Handler] = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if clean and target.is_file():
            target.unlink()
        file_handler = logging.FileHandler(target, mode="a", encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    except OSError as exc:  # read-only mount, missing permission, full disk
        file_handler = None
        print(f"[logging] file logging disabled: {exc}", file=sys.stderr)

    console = Console(
        log_time=True,
        log_time_format="%H:%M:%S-%f",
        theme=Theme(
            {
                "traceback.border": "black",
                "traceback.border.syntax_error": "black",
                "inspect.value.border": "black",
            }
        ),
    )
    pretty_install(console=console)
    traceback_install(
        console=console,
        extra_lines=1,
        width=console.width,
        word_wrap=False,
        indent_guides=False,
        suppress=[],
    )
    rich_handler = RichHandler(
        show_time=True,
        omit_repeated_times=False,
        show_level=True,
        show_path=False,
        markup=False,
        rich_tracebacks=True,
        log_time_format="%H:%M:%S-%f",
        level=logging.DEBUG if debug else logging.INFO,
        console=console,
    )
    rich_handler.set_name("rich" if not debug else "rich-debug")

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False
    # Guard against a re-import stacking a second rich handler.
    if not any(isinstance(h, RichHandler) for h in logger.handlers):
        logger.addHandler(rich_handler)
    if file_handler is not None and not any(
        isinstance(h, logging.FileHandler) for h in logger.handlers
    ):
        logger.addHandler(file_handler)

    _log = logger
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """The configured logger. `name` is accepted for call-site symmetry.

    A child logger is NOT returned on purpose: every module writing through one
    logger is what keeps the existing log format and the existing log-file
    rotation behaviour identical to the old code.
    """
    return setup_logging()


#: Kept so a module that did `from service.configuration.logging import log`
#: (the old style) still gets a logger.
log = setup_logging()
