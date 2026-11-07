#!/usr/bin/env python3
"""Env parity guard: one manifest, no drift, no committed credential.

WHY this exists (mirror of R:/GPTHub/docker/run.sh:109-135, which compares the
compose file's variables against the env file before starting, and of
docker/sync_env.py --check in this repository): a key that exists in
.env.example but not in .env.dev/.env.prod does not fail loudly. The service
starts on a code default, and the difference between "the operator forgot a
key" and "the code has a bug" becomes invisible. A key that exists in a
deployment file but not in the template is the same defect from the other side:
nobody can reproduce that deployment from the committed manifest.

What is asserted:

  1. docker/.env.example exists and declares no duplicate keys (a duplicate
     silently overrides the earlier line - Compose takes the last value).
  2. Every deployment file that exists declares exactly the template's key set.
     A missing deployment file is a SKIP, not a pass: .env.dev/.env.prod are
     gitignored, so a fresh clone legitimately has neither, and claiming success
     there would be a lie.
  3. Every variable a compose file interpolates is declared by the template.
     The "${VAR:?message}" and "${VAR}" forms are hard requirements (the stack
     stops at 'up' naming the key); a defaulted form is a warning, because a
     default hides the omission.
  4. No real credential is committed in .env.example. Unambiguous credential
     formats (private-key blocks, provider token prefixes) are a FAIL; a
     secret-named key whose value merely looks random is a WARN with an explicit
     instruction, because "looks random" cannot be proven to be a real secret.

Exit codes: 0 ok (warnings and skips allowed), 1 violations, 2 usage/IO error.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

CHECK = "env-parity"
ROOT = Path(__file__).resolve().parent.parent
DOCKER_DIR = ROOT / "docker"
TEMPLATE = DOCKER_DIR / ".env.example"
DEPLOYMENTS = (DOCKER_DIR / ".env.dev", DOCKER_DIR / ".env.prod")
COMPOSE_FILES = (DOCKER_DIR / "docker-compose.yaml", DOCKER_DIR / "docker-compose.dev.yaml")

KEY_LINE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)=")
INTERPOLATION = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<rest>[^}]*)\}")

# A key whose value is a credential. Deliberately broad: a false positive costs a
# warning, a false negative costs a leaked password in a public repository.
SECRET_KEY = re.compile(
    r"(PASSWORD|PASSWD|SECRET|TOKEN|PRIVATE|CREDENTIAL|APIKEY|API_KEY|ACCESS_KEY"
    r"|_KEY$|^KEY$)",
    re.IGNORECASE,
)

# Values that are obviously documentation rather than credentials. Anything here
# is exempt from the entropy warning.
PLACEHOLDER = re.compile(
    r"^(?:change[-_]?me|changeme|placeholder|example|dummy|fake|todo|xxx+"
    r"|your[-_].*|<.*>|none|null|empty|unset|dev[-_]only"
    r"|replace[-_]?me|minioadmin|admin|root)$",
    re.IGNORECASE,
)

# A value that names a file rather than holding a secret. Checked before the
# entropy heuristic so that JWT_PRIVATE_KEY_PATH=/app/src/certs/jwt-private.pem
# is not reported as a leaked password.
PATH_EXT = re.compile(r"\.(?:pem|key|crt|cer|pub|p12|pfx|jks|json|ya?ml|toml|ini|conf|env|sql|gz|zip|txt)$", re.IGNORECASE)

# Unambiguous credential shapes. A match is a hard failure: these strings are not
# something a person types as a placeholder.
HARD_CREDENTIAL = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"^AKIA[0-9A-Z]{16}$"),                        # AWS access key id
    re.compile(r"^gh[pousr]_[A-Za-z0-9]{36,}$"),              # GitHub token
    re.compile(r"^github_pat_[A-Za-z0-9_]{20,}$"),
    re.compile(r"^xox[baprs]-[A-Za-z0-9-]{10,}$"),            # Slack token
    re.compile(r"^sk-[A-Za-z0-9]{20,}$"),                     # OpenAI-style key
    re.compile(r"^AIza[0-9A-Za-z_-]{35}$"),                   # Google API key
    re.compile(r"^eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."),   # JWT
    re.compile(r"^[A-Fa-f0-9]{64}$"),                         # 32-byte hex secret
)


class Findings:
    def __init__(self):
        self.failures = []
        self.warnings = []
        self.skips = []
        self.ok = []
        self.ran = 0

    def fail(self, message):
        self.failures.append(message)

    def warn(self, message):
        self.warnings.append(message)

    def skip(self, message):
        self.skips.append(message)

    def good(self, message):
        self.ran += 1
        self.ok.append(message)

    @property
    def status(self):
        if self.failures:
            return "fail"
        if not self.ran:
            return "skip"
        return "pass"

    def emit(self, as_json):
        if as_json:
            print(json.dumps({
                "check": CHECK,
                "status": self.status,
                "failures": self.failures,
                "warnings": self.warnings,
                "skips": self.skips,
                "ok": self.ok,
            }, indent=2, sort_keys=True))
        else:
            for message in self.failures:
                print("[FAIL] " + CHECK + ": " + message)
            for message in self.warnings:
                print("[WARN] " + CHECK + ": " + message)
            for message in self.skips:
                print("[SKIP] " + CHECK + ": " + message)
            for message in self.ok:
                print("[OK] " + CHECK + ": " + message)
            print("[SUMMARY] check=" + CHECK + " status=" + self.status
                  + " failures=" + str(len(self.failures))
                  + " warnings=" + str(len(self.warnings))
                  + " skipped=" + str(len(self.skips)))
        return 1 if self.failures else 0


def shannon(value):
    """Bits per character. Used only to rank suspicion, never as proof."""
    if not value:
        return 0.0
    counts = Counter(value)
    total = len(value)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def parse_env(text):
    """Ordered (key, value, line) triples from a KEY=value file."""
    pairs = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = KEY_LINE.match(stripped)
        if not match:
            continue
        key = match.group("key")
        pairs.append((key, stripped.split("=", 1)[1].strip(), number))
    return pairs


def compose_variables(text):
    """Every interpolation in a compose file, with its line.

    "$${NAME}" is NOT an interpolation and is skipped deliberately. Compose
    reads it as an escaped dollar and passes "${NAME}" through to the
    container, where the SHELL/healthcheck expands it from the container's own
    environment ($${HOSTNAME}, $${ML_PORT}, $${SERVER_PORT}). Those names are
    set by the image or the kernel, not by the manifest, so requiring them in
    .env.example would be asking an operator to declare HOSTNAME.
    """
    escaped = re.compile(r"\$\$\{")
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue  # a commented example is documentation, not interpolation
        masked = escaped.sub("\0\0\0", line)
        for match in INTERPOLATION.finditer(masked):
            # Report the original text, not the masked one, so the message can be
            # pasted into a search.
            found.append((match.group("name"), match.group("rest"), number, line.strip()))
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description="env manifest parity guard")
    parser.add_argument("--json", action="store_true", help="emit one JSON object")
    args = parser.parse_args(argv)

    report = Findings()

    if not TEMPLATE.is_file():
        if not DOCKER_DIR.is_dir():
            report.skip(
                "docker/ does not exist yet (owned by the docker teammate), so there is"
                " no env manifest to compare. NOT a pass."
            )
        else:
            report.fail(
                "docker/.env.example is missing - it is the only committed env manifest"
                " (R:/GPTHub convention, mirrored in AGENTS.md); every deployment file"
                " is generated from it."
            )
        return report.emit(args.json)

    template_pairs = parse_env(TEMPLATE.read_text(encoding="utf-8", errors="replace"))
    template_keys = [key for key, _, _ in template_pairs]
    duplicates = [key for key, count in Counter(template_keys).items() if count > 1]
    if duplicates:
        report.fail(
            "docker/.env.example declares duplicate key(s) " + ", ".join(sorted(duplicates))
            + " - the last assignment wins silently, so the earlier value is dead text."
        )
    else:
        report.good("docker/.env.example declares " + str(len(template_keys))
                    + " unique key(s)")

    template_map = {key: (value, number) for key, value, number in template_pairs}

    # --- 1. deployment files mirror the template ---------------------------
    for path in DEPLOYMENTS:
        mode = path.name.split(".")[-1]
        if not path.is_file():
            report.skip(
                "docker/" + path.name + " does not exist: it is gitignored and created by"
                " 'make env' / 'python docker/sync_env.py --mode " + mode + "'."
                " Nothing to compare here - NOT a pass."
            )
            continue
        pairs = parse_env(path.read_text(encoding="utf-8", errors="replace"))
        keys = {key for key, _, _ in pairs}
        missing = [key for key in template_keys if key not in keys]
        extra = sorted(keys - set(template_keys))
        if missing:
            report.fail(
                "docker/" + path.name + " is missing " + str(len(missing))
                + " key(s) from the template: " + ", ".join(missing)
                + " - a missing key means the service silently runs on a code default"
                " (fix: python docker/sync_env.py --mode " + mode + ")."
            )
        if extra:
            report.fail(
                "docker/" + path.name + " declares " + str(len(extra))
                + " key(s) absent from the template: " + ", ".join(extra)
                + " - that deployment cannot be reproduced from the committed manifest."
            )
        if not missing and not extra:
            report.good("docker/" + path.name + " declares exactly the template's "
                        + str(len(keys)) + " key(s)")

    # --- 2. compose interpolations are declared ----------------------------
    compose_seen = 0
    for path in COMPOSE_FILES:
        if not path.is_file():
            report.skip(
                "docker/" + path.name + " does not exist yet (docker teammate owns it),"
                " so its interpolations cannot be checked. NOT a pass."
            )
            continue
        compose_seen += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        undefined_required = []
        defaulted = []
        for name, rest, number, line in compose_variables(text):
            if name in template_map:
                continue
            if rest.startswith(":?") or rest.startswith("?"):
                undefined_required.append((name, number, line))
            else:
                defaulted.append((name, number, line))
        for name, number, line in undefined_required:
            report.fail(
                "docker/" + path.name + ":" + str(number) + " requires a value for '"
                + name + "', but docker/.env.example never declares it"
                " - 'docker compose up' stops with an interpolation error the operator"
                " cannot fix from the manifest. Line: " + line
            )
        for name, number, line in defaulted:
            report.warn(
                "docker/" + path.name + ":" + str(number) + " interpolates '" + name
                + "' with a default, and the template does not declare it"
                " - the default hides the omission. Line: " + line
            )
        if not undefined_required and not defaulted:
            report.good("docker/" + path.name + ": every interpolated variable is declared"
                        " by the template")
    if not compose_seen:
        report.skip("no compose file present, so interpolation coverage is unchecked.")

    # --- 3. secret hygiene in the committed template -----------------------
    hard = []
    suspicious = []
    for key, (value, number) in template_map.items():
        if not value:
            continue
        if any(pattern.search(value) for pattern in HARD_CREDENTIAL):
            hard.append((key, number))
            continue
        if not SECRET_KEY.search(key):
            continue
        if PLACEHOLDER.match(value):
            continue
        # A key can be secret-shaped while its value is a path (JWT_PRIVATE_KEY_PATH,
        # CREDENTIALS_FILE, ...). A filesystem path is not a credential - the file it
        # names is - so high entropy in a path is expected, not a leak.
        if value.startswith(("/", "./", "../", "~")) or PATH_EXT.search(value):
            continue
        # Long, high-entropy and not a documented placeholder: this is what a real
        # generated password looks like, and one in a committed file is a leak.
        if len(value) >= 16 and shannon(value) >= 3.0:
            suspicious.append((key, number))
    for key, number in hard:
        report.fail(
            "docker/.env.example:" + str(number) + " key " + key
            + " contains what is unmistakably a real credential (private key block,"
            " provider token or 32-byte hex secret). Remove it, rotate it, and replace"
            " the value with a placeholder such as change-me."
        )
    for key, number in suspicious:
        report.warn(
            "docker/.env.example:" + str(number) + " key " + key
            + " holds a high-entropy value that looks like a generated secret, not a"
            " placeholder. It is committed, so everyone who copies the template shares"
            " that value; prefer a documented placeholder and let 'make env' generate"
            " the real value per deployment."
        )
    if not hard and not suspicious:
        report.good("no credential-shaped value in the committed template")

    return report.emit(args.json)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - a guard must never traceback silently
        print("[FAIL] " + CHECK + ": unexpected error: " + repr(exc))
        sys.exit(2)
