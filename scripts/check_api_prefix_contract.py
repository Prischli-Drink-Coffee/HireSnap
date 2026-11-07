#!/usr/bin/env python3
r"""API prefix contract: the frontend, the gateway and the backend must agree.

WHY this exists (mirror of R:\GPTHub\scripts\check_api_prefix_contract.py and of
the routing comment in R:\GPTHub\infra\nginx\templates\site.conf.template:40-52):

  The public API path is decided in THREE files that never import each other:

    frontend/src/API/apiConsts.js   requests go to  <base>/api/...
    infra/nginx/templates/site.conf.template  rewrites /api/(.*) -> /server/$1
    backend/src/pipeline/server.py  app.mount("/server", app_server)

  Nothing in the codebase forces those three strings to stay consistent, and the
  failure is quiet in the worst way: nginx answers 404 for an unrouted prefix, the
  frontend's axios interceptor surfaces "Request failed with status code 404",
  and the developer looks at React. Every one of the three can be edited
  independently and each edit looks local and harmless.

  This guard reads all three and refuses to let them drift. It is deliberately a
  PARSER, not an import: importing apiConsts.js would need Node and a DOM, and
  importing server.py would drag in torch.

What is asserted:
  1. the browser-facing prefix is exactly /api/ (the value in apiConsts.js and the
     default of REACT_APP_BASE_URL in the compose file must be the same string);
  2. the gateway rewrites that exact prefix to the backend mount, preserving the
     tail ($1), and the backend mount is exactly what the rewrite targets;
  3. the backend actually mounts that path in server.py;
  4. the gateway exposes the FastAPI documentation under the same prefix, because
     a rewrite that drops /api/docs makes Swagger UI 404 even though the API works;
  5. /data/ is proxied unchanged (the backend mounts StaticFiles at /data, so any
     rewrite there would 404 every uploaded file).

Exit codes: 0 ok, 1 violations, 2 usage/IO error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

CHECK = "api-prefix-contract"
ROOT = Path(__file__).resolve().parent.parent

FRONTEND_CONSTS = Path("frontend/src/API/apiConsts.js")
BACKEND_SERVER = Path("backend/src/pipeline/server.py")
NGINX_TEMPLATE = Path("infra/nginx/templates/site.conf.template")
COMPOSE_PROD = Path("docker/docker-compose.yaml")

# "/api/" in a JS string literal, as the fallback branch of resolveBaseUrl().
JS_API_LITERAL = re.compile(r"['\"]((?:https?://[^'\"]*)?/api/)['\"]")
# app.mount("/server", app_server) - the first argument is the public path.
MOUNT = re.compile(r"app\.mount\(\s*['\"](?P<path>/[^'\"]*)['\"]")
# rewrite ^/api/(.*)$ /server/$1 break;
REWRITE = re.compile(
    r"rewrite\s+(?P<pattern>\^?/[A-Za-z0-9_./()-]*\(\.\*\)\$?)\s+"
    r"(?P<target>/[A-Za-z0-9_./$-]*)\s+(?P<flag>break|last|redirect|permanent)\s*;"
)
LOCATION = re.compile(r"^\s*location\s+(?P<mod>[=~*^]*)\s*(?P<path>[^\s{]+)\s*\{", re.M)
COMPOSE_ARG = re.compile(
    r"REACT_APP_BASE_URL[:=]\s*\$\{(?P<value>[^}]*)\}"
    r"|REACT_APP_BASE_URL[:=]\s*(?P<plain>/[^\s}]*)"
)
HEALTHCHECK_SERVER_PATH = re.compile(r"/server/[A-Za-z0-9_./-]+")
UPSTREAM_VAR = re.compile(
    r"set\s+\$(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+http://(?P<host>[A-Za-z0-9_.-]+:[0-9]+)\s*;"
)


class Report:
    def __init__(self) -> None:
        self.failures = []
        self.warnings = []
        self.skips = []
        self.ok = []

    def status(self):
        if self.failures:
            return "fail"
        if self.skips and not self.ok:
            return "skip"
        return "pass"

    def emit(self, as_json):
        if as_json:
            print(json.dumps({
                "check": CHECK,
                "status": self.status(),
                "failures": self.failures,
                "warnings": self.warnings,
                "skips": self.skips,
                "ok": self.ok,
            }, indent=2, sort_keys=True))
            return 1 if self.failures else 0
        for message in self.failures:
            print("[FAIL] " + CHECK + ": " + message)
        for message in self.warnings:
            print("[WARN] " + CHECK + ": " + message)
        for message in self.skips:
            print("[SKIP] " + CHECK + ": " + message)
        for message in self.ok:
            print("[OK] " + CHECK + ": " + message)
        print(
            "[SUMMARY] check=" + CHECK + " status=" + self.status()
            + " failures=" + str(len(self.failures))
            + " warnings=" + str(len(self.warnings))
            + " skipped=" + str(len(self.skips))
        )
        return 1 if self.failures else 0


def read(relative, report, what):
    path = ROOT / relative
    if not path.is_file():
        report.skips.append(
            str(relative) + " is missing, so the " + what + " could not be read."
            " NOT a pass: the contract is unverified, not satisfied."
        )
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def strip_comments(text):
    """Drop '#' comments so a documented example is not read as live config."""
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            out.append("")
        else:
            out.append(line)
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description="check the /api -> /server routing contract")
    parser.add_argument("--json", action="store_true", help="emit a single JSON object")
    args = parser.parse_args()
    report = Report()

    # --- 1. the browser-facing prefix ------------------------------------
    api_prefix = None
    consts = read(FRONTEND_CONSTS, report, "frontend base URL")
    if consts is not None:
        literals = sorted({match.group(1) for match in JS_API_LITERAL.finditer(consts)})
        plain = sorted({value for value in literals if value.startswith("/")})
        if not plain:
            report.failures.append(
                str(FRONTEND_CONSTS) + " declares no same-origin '/api/' literal."
                " The gateway routes /api/ and nothing else, so a frontend that"
                " builds URLs from window.location.origin without the /api/"
                " segment bypasses the proxy and lands on the SPA fallback"
            )
        else:
            api_prefix = plain[0]
            if len(plain) > 1:
                report.warnings.append(
                    str(FRONTEND_CONSTS) + " declares several same-origin prefixes: "
                    + ", ".join(plain) + ". Only one of them can be routed"
                )
            report.ok.append(
                "frontend request prefix is " + api_prefix
                + " (" + str(FRONTEND_CONSTS) + ")"
            )

    # --- 2. the compose default must agree --------------------------------
    compose = read(COMPOSE_PROD, report, "compose REACT_APP_BASE_URL default")
    if compose is not None:
        values = []
        for match in COMPOSE_ARG.finditer(compose):
            raw = match.group("value") or match.group("plain") or ""
            # REACT_APP_BASE_URL:-/api/ -> the default after ':-'
            if ":-" in raw:
                raw = raw.split(":-", 1)[1]
            raw = raw.strip()
            if raw:
                values.append(raw)
        if not values:
            report.warnings.append(
                str(COMPOSE_PROD) + " passes no REACT_APP_BASE_URL build argument."
                " The bundle then falls back to window.location.origin + '/api/',"
                " which still routes - but only for non-loopback hosts, so prod"
                " works and a localhost smoke test silently does not"
            )
        else:
            unique = sorted(set(values))
            if api_prefix and api_prefix not in unique:
                report.failures.append(
                    str(COMPOSE_PROD) + " bakes REACT_APP_BASE_URL="
                    + ", ".join(unique) + " but the frontend falls back to "
                    + api_prefix + ". The build argument wins, so the bundle"
                    " requests a prefix the gateway does not route"
                )
            elif api_prefix:
                report.ok.append(
                    "compose build argument matches the frontend prefix: "
                    + ", ".join(unique)
                )

    # --- 3. backend mount --------------------------------------------------
    backend_mounts = []
    server = read(BACKEND_SERVER, report, "FastAPI mount table")
    if server is not None:
        backend_mounts = [match.group("path") for match in MOUNT.finditer(strip_comments(server))]
        if not backend_mounts:
            report.failures.append(
                str(BACKEND_SERVER) + " declares no app.mount(...) call, so the"
                " router has no public prefix for the gateway to target"
            )
        else:
            report.ok.append(
                "backend mounts: " + ", ".join(backend_mounts)
                + " (" + str(BACKEND_SERVER) + ")"
            )

    # --- 4. gateway rewrite ------------------------------------------------
    template = read(NGINX_TEMPLATE, report, "gateway routing table")
    if template is not None:
        live = strip_comments(template)
        rewrites = list(REWRITE.finditer(live))
        locations = [(m.group("mod").strip(), m.group("path")) for m in LOCATION.finditer(live)]

        variables = {
            match.group("name"): match.group("host")
            for match in UPSTREAM_VAR.finditer(live)
        }
        if not variables:
            report.failures.append(
                str(NGINX_TEMPLATE) + " proxies to no 'set $name http://host:port;'"
                " variable. A literal proxy_pass resolves the container IP once at"
                " start-up and keeps it after the container is re-created, so the"
                " gateway 502s until it is restarted by hand"
            )
        else:
            report.ok.append(
                "gateway upstreams are variables: "
                + ", ".join("$" + key + " -> " + value for key, value in sorted(variables.items()))
            )

        prefix = api_prefix or "/api/"
        api_rewrites = [
            match for match in rewrites
            if match.group("pattern").startswith("^" + prefix) or match.group("pattern").startswith(prefix)
        ]
        if not api_rewrites:
            report.failures.append(
                str(NGINX_TEMPLATE) + " contains no 'rewrite ^" + prefix
                + "(.*)$ ... break;'. Without it the gateway forwards " + prefix
                + "... to a backend that mounts " + (", ".join(backend_mounts) or "/<unknown>")
                + ", which answers 404 for every request"
            )
        for match in {match.group("target"): match for match in api_rewrites}.values():
            target = match.group("target")
            mount = target.split("$", 1)[0].rstrip("/") or "/"
            if backend_mounts and mount not in backend_mounts:
                report.failures.append(
                    str(NGINX_TEMPLATE) + " rewrites to " + target + " but the backend"
                    " mounts " + ", ".join(backend_mounts) + ". One of the two moved;"
                    " nginx would proxy to a path nothing serves"
                )
            else:
                report.ok.append(
                    "gateway rewrite " + match.group("pattern") + " -> " + target
                    + " targets a real backend mount"
                )

        # /api/docs and friends must survive the rewrite: FastAPI's docs page
        # names its own schema URL as an absolute path, so it must be routed too.
        doc_locations = [
            path for _mod, path in locations
            if "docs" in path or "openapi" in path or "redoc" in path
        ]
        if not doc_locations:
            report.failures.append(
                str(NGINX_TEMPLATE) + " routes " + prefix + " but never mentions"
                " docs/redoc/openapi.json. FastAPI's Swagger UI links its schema as"
                " an absolute URL, so documenting the endpoints under " + prefix
                + "docs without routing " + prefix + "openapi.json yields a docs"
                " page that cannot load its spec"
            )
        else:
            report.ok.append(
                "documentation is routed under the public prefix: "
                + ", ".join(doc_locations[:3])
            )

        # /data/ must be proxied unchanged: the backend mounts StaticFiles there.
        data_locations = [path for _mod, path in locations if path.startswith("/data")]
        data_rewrites = [m for m in rewrites if m.group("pattern").startswith("^/data")]
        if not data_locations:
            report.warnings.append(
                str(NGINX_TEMPLATE) + " does not route /data/. The backend mounts a"
                " StaticFiles app there (uploaded videos and .npy embeddings); if"
                " files are only ever read through " + prefix + " responses this is"
                " fine, but a direct URL lands on the SPA fallback"
            )
        elif data_rewrites:
            report.failures.append(
                str(NGINX_TEMPLATE) + " rewrites /data/ somewhere. The backend serves"
                " it as a static mount, so any prefix change turns every stored file"
                " URL into a 404"
            )
        else:
            report.ok.append("gateway proxies /data/ unchanged")

    # --- 5. healthcheck path points into a mounted prefix -----------------
    if compose is not None and backend_mounts:
        paths = sorted(set(HEALTHCHECK_SERVER_PATH.findall(compose)))
        if not paths:
            report.warnings.append(
                str(COMPOSE_PROD) + " has no /server/... probe path, so the backend"
                " healthcheck cannot be tied to the mount contract"
            )
        else:
            bad = [
                path for path in paths
                if ("/" + path.split("/")[1]) not in backend_mounts
            ]
            if bad:
                report.failures.append(
                    str(COMPOSE_PROD) + " probes " + ", ".join(bad) + " but the"
                    " backend mounts " + ", ".join(backend_mounts) + "; the container"
                    " would report unhealthy forever"
                )
            else:
                report.ok.append(
                    "compose probes inside a mounted prefix: " + ", ".join(paths)
                )

    return report.emit(args.json)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print("[FAIL] " + CHECK + ": unexpected error: " + repr(exc))
        sys.exit(2)
