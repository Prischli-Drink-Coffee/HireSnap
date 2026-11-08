"""Mail: the three backends, the failure paths, and the promise never to block a request.

WHY this file: service/infrastructure/mail.py is the one module whose *success* path
cannot run in this environment (there is no SMTP server) and whose *failure* path is
what decides whether a user can reset a password. test_infra_fallbacks.py already
pins the selection rules; the branches left uncovered were exactly the ones that
matter operationally:

  * SMTP send - the whole method, including STARTTLS, SSL, login and the four
    distinct errors it can raise;
  * the file sink failing to write (a read-only volume, a path that is a directory);
  * send_now_or_later swallowing that failure, because a broken mail relay must not
    turn a successful action into a 500;
  * the .env bootstrap, which only runs once per process and therefore needs its
    memo cleared to be exercised at all.

The assertions are about what was sent, what was raised, and which log line marked
the failure - never about the exact wording of a debug message.
"""

import os
import sys
import types

import pytest

from service.infrastructure import mail as mail_module

LOGGER_NAME = "service.infrastructure.mail"


class FakeSmtpClient(object):
    """Records the SMTP conversation. Every step is configurable to fail once."""

    def __init__(self, fail_on=None, error=None):
        self.calls = []
        self.fail_on = fail_on
        self.error = error
        self.message = None

    def _step(self, name, *args):
        self.calls.append((name,) + args)
        if self.fail_on == name:
            raise self.error

    def ehlo(self):
        self._step("ehlo")

    def starttls(self):
        self._step("starttls")

    def login(self, user, password):
        self._step("login", user, password)

    def send_message(self, message):
        self.message = message
        self._step("send_message")

    def quit(self):
        self._step("quit")


@pytest.fixture
def smtp(monkeypatch):
    """Install a fake smtplib module and hand back a factory for its clients."""
    module = types.ModuleType("smtplib")
    built = []

    def _factory(cls_name):
        def _build(host, port, timeout=None):
            client = FakeSmtpClient(
                fail_on=state["fail_on"],
                error=state["error"],
            )
            client.calls.append(("connect", cls_name, host, port, timeout))
            built.append(client)
            return client

        return _build

    state = {"fail_on": None, "error": RuntimeError("smtp boom")}
    module.SMTP = _factory("SMTP")
    module.SMTP_SSL = _factory("SMTP_SSL")
    module.SMTPException = RuntimeError
    monkeypatch.setitem(sys.modules, "smtplib", module)
    monkeypatch.setattr(mail_module, "smtplib", module)

    def _configure(fail_on=None, error=None):
        state["fail_on"] = fail_on
        if error is not None:
            state["error"] = error

    return types.SimpleNamespace(module=module, clients=built, configure=_configure)


# --------------------------------------------------------------------------- #
# _bootstrap_env: once per process, best effort
# --------------------------------------------------------------------------- #
def test_bootstrap_env_loads_the_backend_env_file_once(monkeypatch, tmp_path):
    """The .env next to the package is read on the first get_mailer() and never again.

    The memo is what makes this cheap; clearing it is the only way to observe the
    load, so the test does that explicitly rather than relying on import order.
    """
    loaded = []
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda path: loaded.append(path)
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)
    monkeypatch.setattr(mail_module._bootstrap_env, "_done", False, raising=False)
    monkeypatch.setattr(mail_module.os.path, "isfile", lambda path: True)

    mail_module._bootstrap_env()
    mail_module._bootstrap_env()

    assert len(loaded) == 1
    assert loaded[0].endswith(".env")
    assert "backend" in loaded[0]


def test_bootstrap_env_survives_a_missing_dotenv_package(monkeypatch):
    """python-dotenv is not a hard dependency: without it the module still works."""
    monkeypatch.setitem(sys.modules, "dotenv", None)
    monkeypatch.setattr(mail_module._bootstrap_env, "_done", False, raising=False)

    mail_module._bootstrap_env()

    assert mail_module._bootstrap_env._done is True


# --------------------------------------------------------------------------- #
# _render_body
# --------------------------------------------------------------------------- #
def test_render_body_builds_a_plain_text_message():
    message = mail_module._render_body("Subject", "body text")

    assert message["Subject"] == "Subject"
    assert message.get_content().strip() == "body text"
    assert message.is_multipart() is False


def test_render_body_adds_an_html_alternative_when_given():
    """multipart/alternative: an HTML body never replaces the plain-text part."""
    message = mail_module._render_body("Subject", "plain", html="<b>rich</b>")

    assert message.is_multipart() is True
    subtypes = [part.get_content_type() for part in message.iter_parts()]
    assert subtypes == ["text/plain", "text/html"]


def test_render_body_defaults_an_empty_subject():
    message = mail_module._render_body("", "body")

    assert message["Subject"] == "(no subject)"


# --------------------------------------------------------------------------- #
# FileMailer
# --------------------------------------------------------------------------- #
def test_file_mailer_appends_a_readable_block(tmp_path):
    path = tmp_path / "outbox.log"

    assert mail_module.FileMailer(str(path)).send("a@example.com", "Hi", "body") is True

    written = path.read_text(encoding="utf-8")
    assert "To: a@example.com" in written
    assert "Subject: Hi" in written
    assert "body" in written


def test_file_mailer_writes_into_the_current_directory_when_the_path_has_no_directory(
    monkeypatch, tmp_path
):
    """MAIL__FILE_PATH=outbox.log means "next to the process", not "in /" - the
    dirname guard is what keeps os.makedirs("") from raising FileNotFoundError.
    """
    monkeypatch.chdir(tmp_path)
    mailer = mail_module.FileMailer("plain-outbox.log")

    assert mailer.send("a@example.com", "Hi", "body") is True
    assert (tmp_path / "plain-outbox.log").is_file()
    # The constructor made it absolute, which is what lets the dirname guard run.
    assert os.path.dirname(mailer.path) == str(tmp_path)


def test_file_mailer_tolerates_a_path_with_no_directory_part():
    """The dirname guard exists so os.makedirs("") is never attempted.

    FileMailer absolutises its argument, so this shape can only be reached by
    handing the object an empty path directly - but that is exactly the guard's
    job, and without it the failure would be a FileNotFoundError escaping send().
    """
    mailer = mail_module.FileMailer("outbox.log")
    mailer._path = ""

    assert mailer.send("a@example.com", "Hi", "body") is False


def test_file_mailer_reports_a_write_failure_instead_of_raising(tmp_path):
    """A file sink that cannot write is a configuration bug, not a request failure."""
    mailer = mail_module.FileMailer(str(tmp_path))

    assert mailer.send("a@example.com", "Hi", "body") is False


def test_file_mailer_describe_names_the_path():
    described = mail_module.FileMailer("outbox.log").describe()

    assert described["backend"] == "file"
    assert described["enabled"] is True
    assert described["path"].endswith("outbox.log")


# --------------------------------------------------------------------------- #
# SmtpMailer
# --------------------------------------------------------------------------- #
def _smtp_mailer(**kwargs):
    defaults = dict(host="smtp.example.com", port=2525, user="user", password="secret")
    defaults.update(kwargs)
    return mail_module.SmtpMailer(**defaults)


def test_smtp_send_runs_the_expected_conversation(smtp):
    mailer = _smtp_mailer()

    assert mailer.send("a@example.com", "Hi", "body") is True

    client = smtp.clients[0]
    assert client.calls[0] == ("connect", "SMTP", "smtp.example.com", 2525, 10.0)
    names = [call[0] for call in client.calls]
    assert names == ["connect", "ehlo", "starttls", "ehlo", "login", "send_message", "quit"]
    assert client.message["To"] == "a@example.com"
    assert client.message["From"] == mail_module.DEFAULT_FROM


def test_smtp_send_uses_ssl_when_configured(smtp):
    """SSL means SMTP_SSL and no STARTTLS: the connection is already encrypted."""
    mailer = _smtp_mailer(ssl=True)

    assert mailer.send("a@example.com", "Hi", "body") is True

    client = smtp.clients[0]
    names = [call[0] for call in client.calls]
    assert client.calls[0][1] == "SMTP_SSL"
    assert "starttls" not in names


def test_smtp_send_skips_starttls_when_disabled(smtp):
    mailer = _smtp_mailer(starttls=False)

    assert mailer.send("a@example.com", "Hi", "body") is True

    assert "starttls" not in [call[0] for call in smtp.clients[0].calls]


def test_smtp_send_skips_login_without_a_user(smtp):
    mailer = _smtp_mailer(user="")

    assert mailer.send("a@example.com", "Hi", "body") is True

    assert "login" not in [call[0] for call in smtp.clients[0].calls]


def test_smtp_send_rejects_an_empty_recipient_list(smtp):
    mailer = _smtp_mailer()

    with pytest.raises(mail_module.MailError, match="mail_invalid_recipient"):
        mailer.send(" , ; ", "Hi", "body")

    assert smtp.clients == []


def test_smtp_send_reports_an_unreachable_server(smtp, monkeypatch):
    def _explode(host, port, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(smtp.module, "SMTP", _explode)

    with pytest.raises(mail_module.MailError, match="smtp_unavailable"):
        _smtp_mailer().send("a@example.com", "Hi", "body")


def test_smtp_send_reports_a_failed_starttls(smtp, caplog):
    smtp.configure(fail_on="starttls", error=RuntimeError("tls boom"))

    with pytest.raises(mail_module.MailError, match="smtp_starttls_failed"):
        _smtp_mailer().send("a@example.com", "Hi", "body")

    # The connection is still closed, because the failure happens inside the try
    # whose finally quits it.
    assert "quit" in [call[0] for call in smtp.clients[0].calls]


def test_smtp_send_reports_a_failed_delivery(smtp):
    smtp.configure(fail_on="send_message", error=RuntimeError("mailbox full"))

    with pytest.raises(mail_module.MailError, match="smtp_send_failed"):
        _smtp_mailer().send("a@example.com", "Hi", "body")


def test_smtp_send_swallows_a_failing_quit(smtp):
    """A server that hangs up after accepting the message must not fail the send."""
    smtp.configure(fail_on="quit", error=RuntimeError("already closed"))

    assert _smtp_mailer().send("a@example.com", "Hi", "body") is True


def test_smtp_describe_never_exposes_the_password():
    described = _smtp_mailer().describe()

    assert described == {
        "backend": "smtp",
        "enabled": True,
        "host": "smtp.example.com",
        "port": 2525,
        "starttls": True,
        "ssl": False,
        "authenticated": True,
    }
    assert "secret" not in repr(described)


def test_smtp_defaults_are_used_for_empty_arguments():
    mailer = mail_module.SmtpMailer(host="h", port="", user=None, timeout="")

    assert mailer.describe()["port"] == mail_module.DEFAULT_SMTP_PORT
    assert mailer._timeout == mail_module.DEFAULT_TIMEOUT
    assert mailer.describe()["authenticated"] is False


# --------------------------------------------------------------------------- #
# _split / get_mailer
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "recipients, expected",
    [
        ("a@example.com", ["a@example.com"]),
        ("a@example.com, b@example.com", ["a@example.com", "b@example.com"]),
        ("a@example.com; b@example.com", ["a@example.com", "b@example.com"]),
        (["a@example.com", " b@example.com "], ["a@example.com", "b@example.com"]),
        (("a@example.com", ""), ["a@example.com"]),
        ({"a@example.com"}, ["a@example.com"]),
        (None, []),
        ("   ", []),
    ],
)
def test_split_normalises_every_recipient_shape(recipients, expected):
    assert mail_module._split(recipients) == expected


def test_an_unknown_backend_falls_back_to_the_file_sink(monkeypatch, caplog):
    monkeypatch.setenv("MAIL__ENABLED", "true")
    monkeypatch.setenv("MAIL__BACKEND", "sendmail")

    with caplog.at_level("WARNING", logger=LOGGER_NAME):
        mailer = mail_module.get_mailer()

    assert mailer.name == "file"
    assert any("unknown_backend" in record.message for record in caplog.records)


def test_get_mailer_builds_a_new_object_every_call(monkeypatch):
    """No cache: the object holds a password, so reconfiguration must be possible."""
    monkeypatch.setenv("MAIL__ENABLED", "true")
    monkeypatch.setenv("MAIL__BACKEND", "file")

    assert mail_module.get_mailer() is not mail_module.get_mailer()


# --------------------------------------------------------------------------- #
# send_now_or_later: the request path must never wait or fail
# --------------------------------------------------------------------------- #
def test_send_now_or_later_swallows_a_delivery_failure(monkeypatch, caplog):
    """A broken relay must not turn an already-successful action into a 500."""
    calls = []

    def _explode(*args, **kwargs):
        calls.append((args, kwargs))
        raise mail_module.MailError("smtp_unavailable")

    monkeypatch.setattr(mail_module, "send_now", _explode)

    with caplog.at_level("WARNING", logger=LOGGER_NAME):
        thread = mail_module.send_now_or_later("a@example.com", "Hi", "body")
        thread.join(5)

    assert calls
    assert any("async_send_failed" in record.message for record in caplog.records)


def test_send_now_or_later_delivers_in_a_daemon_thread(monkeypatch):
    delivered = []

    def _record(to, subject, body, html=None):
        delivered.append((to, subject, body, html))

    monkeypatch.setattr(mail_module, "send_now", _record)

    thread = mail_module.send_now_or_later("a@example.com", "Hi", "body", html="<b>x</b>")
    thread.join(5)

    assert thread.daemon is True
    assert delivered == [("a@example.com", "Hi", "body", "<b>x</b>")]


def test_send_now_uses_the_configured_backend(monkeypatch, tmp_path):
    path = tmp_path / "outbox.log"
    monkeypatch.setenv("MAIL__ENABLED", "true")
    monkeypatch.setenv("MAIL__BACKEND", "file")
    monkeypatch.setenv("MAIL__FILE_PATH", str(path))

    assert mail_module.send_now("a@example.com", "Hi", "body") is True
    assert "Subject: Hi" in path.read_text(encoding="utf-8")


def test_mail_health_reports_ok_for_a_configured_backend(monkeypatch):
    monkeypatch.setenv("MAIL__ENABLED", "true")
    monkeypatch.setenv("MAIL__BACKEND", "file")

    assert mail_module.mail_health()["ok"] is True


def test_mail_health_cannot_tell_a_disabled_mailer_from_a_degraded_one(monkeypatch):
    """MAIL__BACKEND=smtp without MAIL__HOST degrades to disabled, and health says ok.

    Reported, not endorsed: mail_health() computes ok from the backend that was
    BUILT, and a half-written smtp configuration builds the disabled mailer, so it
    is indistinguishable from a deliberate MAIL__ENABLED=false. The degradation
    itself is correct (nothing is attempted); only the health signal is ambiguous.
    See the report: service/infrastructure/mail.py:346.
    """
    monkeypatch.setenv("MAIL__ENABLED", "true")
    monkeypatch.setenv("MAIL__BACKEND", "smtp")
    monkeypatch.delenv("MAIL__HOST", raising=False)
    monkeypatch.delenv("MAIL_HOST", raising=False)

    described = mail_module.mail_health()

    assert described["backend"] == "disabled"
    assert described["enabled"] is False
    assert described["ok"] is True  # the ambiguity: "off" and "cannot work" look alike


def test_mail_health_reports_an_smtp_backend_with_a_host(monkeypatch):
    monkeypatch.setenv("MAIL__ENABLED", "true")
    monkeypatch.setenv("MAIL__BACKEND", "smtp")
    monkeypatch.setenv("MAIL__HOST", "smtp.example.com")

    described = mail_module.mail_health()

    assert described["backend"] == "smtp"
    assert described["ok"] is True
