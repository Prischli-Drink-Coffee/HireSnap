"""Mail: SMTP when configured, a file/log sink otherwise, never in the request path.

WHY this module exists: HireSnap sends nothing today - password resets and
interview invitations are a product need, not a platform one, but they all fail
the same way in development: there is no SMTP server, so the feature becomes
untestable and the credentials end up hardcoded by whoever ships first. The
platform layer therefore provides one sender whose development backend needs no
infrastructure at all:

  MAIL__ENABLED=false (default)  every call is a no-op that logs one line.
  MAIL__ENABLED=true + MAIL__BACKEND=file
                                 messages are appended to MAIL__FILE_PATH as
                                 plain text. This is the local default: a
                                 developer can read the reset link without an
                                 inbox, and nothing leaves the machine.
  MAIL__ENABLED=true + MAIL__BACKEND=smtp
                                 real delivery through MAIL__HOST/PORT with
                                 STARTTLS when MAIL__STARTTLS=true.

Fire-and-forget contract (mirror of R:/GPTHub/.../mail/dispatcher.py): the
request path must not wait on a TCP handshake. send_now_or_later() runs the send
in a daemon thread and swallows every error. A user who cannot receive a
notification must not get a 500 for an action that already succeeded - the log
line is the signal that mail broke, not the HTTP status.

smtplib is stdlib, so no import is deferred for it; the deferral that matters is
the optional celery publish, which lives in service/infrastructure/messaging/tasks.py.
"""

from __future__ import annotations

import logging
import os
import smtplib
import threading
import time
from email.message import EmailMessage

from service.configuration.flags import read_flag

logger = logging.getLogger(__name__)

DEFAULT_SMTP_PORT = 587
DEFAULT_FROM = "no-reply@hiresnap.local"
DEFAULT_TIMEOUT = 10.0
DEFAULT_FILE_PATH = "mail_outbox.log"


class MailError(RuntimeError):
    """Delivery failed. Only direct callers see this; the async helper swallows it."""


def _env(*names, default=""):
    """First present environment name wins (see service/infrastructure/cache.py for the WHY)."""
    for name in names:
        if name in os.environ:
            return os.environ[name]
    return default


def _bootstrap_env():
    """Load backend/.env once, best effort, without importing env.Env."""
    if getattr(_bootstrap_env, "_done", False):
        return
    _bootstrap_env._done = True
    try:
        from dotenv import load_dotenv
    except Exception:  # pragma: no cover - bare install
        return
    try:
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        env_path = os.path.join(backend_dir, ".env")
        if os.path.isfile(env_path):
            load_dotenv(env_path)
    except Exception:  # pragma: no cover - defensive
        logger.debug("mail: could not load backend/.env")


def _render_body(subject, body, html=None):
    """Build the RFC 5322 message once, so every backend sends the same bytes."""
    message = EmailMessage()
    message["Subject"] = str(subject or "(no subject)")
    message.set_content(str(body or ""))
    if html:
        # multipart/alternative: clients that render HTML use it, the plain-text
        # part stays for clients that do not (and for spam scoring).
        message.add_alternative(str(html), subtype="html")
    return message


class DisabledMailer(object):
    """Backend used when MAIL__ENABLED is false. Logs, sends nothing."""

    name = "disabled"

    def send(self, to, subject, body, html=None):
        logger.debug(
            "mail backend=disabled status=skipped to=%s subject=%r "
            "action=set MAIL__ENABLED=true to deliver",
            _join(to),
            subject,
        )
        return False

    def describe(self):
        return {"backend": self.name, "enabled": False}


class FileMailer(object):
    """Append each message to a file. The zero-infrastructure development sink.

    A file rather than a throwaway in-memory list: a developer clicks "forgot
    password", then wants to read the link - and an in-memory sink disappears on
    reload. Appending is also safe for several worker processes.
    """

    name = "file"

    def __init__(self, path=None):
        self._path = os.path.abspath(path or DEFAULT_FILE_PATH)
        self._lock = threading.Lock()

    @property
    def path(self):
        return self._path

    def send(self, to, subject, body, html=None):
        recipients = _join(to)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        block = ("=== %s ===\nTo: %s\nFrom: %s\nSubject: %s\n\n%s\n\n") % (
            stamp,
            recipients,
            _env("MAIL__FROM", "MAIL_FROM", default=DEFAULT_FROM),
            subject,
            body,
        )
        try:
            with self._lock:
                directory = os.path.dirname(self._path)
                if directory:
                    os.makedirs(directory, exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as handle:
                    handle.write(block)
        except OSError as exc:
            # A file sink that cannot write is a configuration bug, not a request
            # failure - log it as an error so it is not silently ignored.
            logger.error("mail backend=file status=write_failed path=%s error=%s", self._path, exc)
            return False
        logger.info(
            "mail backend=file status=stored to=%s subject=%r path=%s",
            recipients,
            subject,
            self._path,
        )
        return True

    def describe(self):
        return {"backend": self.name, "enabled": True, "path": self._path}


class SmtpMailer(object):
    """SMTP delivery. Imported and configured lazily, connected per send.

    Connecting per message rather than keeping a pooled connection is a
    deliberate trade: mail volume is a handful per hour, and a long-lived
    connection is exactly the thing that silently rots (server closes it after
    an idle timeout and the next send fails). The cost is irrelevant here.
    """

    name = "smtp"

    def __init__(
        self,
        host,
        port=DEFAULT_SMTP_PORT,
        user="",
        password="",
        sender=DEFAULT_FROM,
        starttls=True,
        timeout=DEFAULT_TIMEOUT,
        ssl=False,
    ):
        self._host = host
        self._port = int(port or DEFAULT_SMTP_PORT)
        self._user = user or ""
        self._password = password or ""
        self._from = sender or DEFAULT_FROM
        self._starttls = bool(starttls)
        self._timeout = float(timeout or DEFAULT_TIMEOUT)
        self._ssl = bool(ssl)

    def send(self, to, subject, body, html=None):
        recipients = _split(to)
        if not recipients:
            raise MailError("mail_invalid_recipient: empty recipient list")
        message = _render_body(subject, body, html)
        message["From"] = self._from
        message["To"] = ", ".join(recipients)
        try:
            if self._ssl:
                client = smtplib.SMTP_SSL(self._host, self._port, timeout=self._timeout)
            else:
                client = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
        except Exception as exc:
            logger.warning(
                "mail backend=smtp code=smtp_unavailable host=%s error=%s", self._host, exc
            )
            raise MailError("smtp_unavailable: %s" % (exc,)) from exc
        try:
            client.ehlo()
            if self._starttls and not self._ssl:
                try:
                    client.starttls()
                    client.ehlo()
                except Exception as exc:
                    logger.warning(
                        "mail backend=smtp code=starttls_failed host=%s error=%s", self._host, exc
                    )
                    raise MailError("smtp_starttls_failed: %s" % (exc,)) from exc
            if self._user:
                client.login(self._user, self._password)
            client.send_message(message)
        except MailError:
            raise
        except Exception as exc:
            logger.warning("mail backend=smtp code=send_failed host=%s error=%s", self._host, exc)
            raise MailError("smtp_send_failed: %s" % (exc,)) from exc
        finally:
            try:
                client.quit()
            except Exception:
                pass
        logger.info(
            "mail backend=smtp status=sent to=%s subject=%r", ", ".join(recipients), subject
        )
        return True

    def describe(self):
        return {
            "backend": self.name,
            "enabled": True,
            "host": self._host,
            "port": self._port,
            "starttls": self._starttls,
            "ssl": self._ssl,
            # The password is never part of a health payload or a log line.
            "authenticated": bool(self._user),
        }


def _split(to):
    if isinstance(to, (list, tuple, set)):
        return [str(item).strip() for item in to if str(item).strip()]
    return [part.strip() for part in str(to or "").replace(";", ",").split(",") if part.strip()]


def _join(to):
    return ", ".join(_split(to))


def get_mailer():
    """Mailer for the current environment. Constructed per call, never cached.

    WHY no cache: this object holds a password. Keeping it in a module global
    means a long-running process cannot be reconfigured without a restart, and it
    makes tests depend on import order. Construction is pure attribute copying -
    no connection is opened until send().
    """
    _bootstrap_env()
    # Strict on purpose: MAIL__ENABLED=ture used to read as "off", which is the one
    # mistake an operator cannot see - health stays green and the inbox stays empty.
    if not read_flag("MAIL__ENABLED", "MAIL_ENABLED", default=False):
        return DisabledMailer()

    backend = str(_env("MAIL__BACKEND", "MAIL_BACKEND", default="file")).strip().lower() or "file"
    if backend == "smtp":
        host = _env("MAIL__HOST", "MAIL_HOST", default="").strip()
        if not host:
            logger.warning(
                "mail backend=smtp status=unconfigured code=mail_host_missing "
                "fallback=disabled action=set MAIL__HOST (or use MAIL__BACKEND=file)"
            )
            return DisabledMailer()
        return SmtpMailer(
            host=host,
            port=_env("MAIL__PORT", "MAIL_PORT", default=str(DEFAULT_SMTP_PORT)),
            user=_env("MAIL__USER", "MAIL_USER", default=""),
            password=_env("MAIL__PASSWORD", "MAIL_PASSWORD", default=""),
            sender=_env("MAIL__FROM", "MAIL_FROM", default=DEFAULT_FROM),
            starttls=read_flag("MAIL__STARTTLS", "MAIL_STARTTLS", default=True),
            timeout=_env("MAIL__TIMEOUT", "MAIL_TIMEOUT", default=str(DEFAULT_TIMEOUT)),
            ssl=read_flag("MAIL__SSL", "MAIL_SSL", default=False),
        )
    if backend != "file":
        logger.warning("mail status=unknown_backend value=%s action=using file", backend)
    return FileMailer(_env("MAIL__FILE_PATH", "MAIL_FILE_PATH", default=DEFAULT_FILE_PATH))


def send_now(to, subject, body, html=None, mailer=None):
    """Deliver synchronously. For tests, CLI tools and worker tasks.

    Raises MailError on failure. Never call this from a request handler - use
    send_now_or_later for that.
    """
    backend = mailer or get_mailer()
    return backend.send(to, subject, body, html=html)


def send_now_or_later(to, subject, body, html=None):
    """Fire-and-forget send for request paths. Returns the worker thread.

    Every failure is logged and swallowed. The alternative - letting a broken
    SMTP relay fail the HTTP request - would mean a user cannot reset a password
    because a mail server is down, which inverts the priorities.

    daemon=True so an interpreter shutdown never waits on an SMTP handshake.
    """

    def _run():
        try:
            send_now(to, subject, body, html=html)
        except Exception as exc:
            logger.warning(
                "mail status=async_send_failed to=%s subject=%r error=%s "
                "action=request already succeeded; check MAIL__HOST",
                _join(to),
                subject,
                exc,
            )

    try:
        thread = threading.Thread(target=_run, name="hiresnap-mail", daemon=True)
        thread.start()
        return thread
    except Exception as exc:  # pragma: no cover - thread creation failures
        logger.warning("mail status=async_send_not_started error=%s", exc)
        return None


def mail_health():
    """Configuration summary for the platform health payload. Never sends."""
    backend = get_mailer()
    data = backend.describe()
    data["ok"] = data.get("backend") != "smtp" or bool(data.get("host"))
    return data
