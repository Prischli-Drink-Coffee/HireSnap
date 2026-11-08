# =============================================================================
# HireSnap - superproject task runner
#
# WHY this file exists (mirror of R:\GPTHub\Makefile:1-24):
#   Every document in docs/ promises the same commands ("make env", "make
#   run-dev", "make run-prod", "make logs", "make ps", "make down") and the
#   quickstart repeats them. Without a Makefile those promises are prose: the
#   reader guesses at docker compose flags, forgets --env-file, forgets the
#   profile, and gets a stack that "started" but does not serve. This file is the
#   executable form of the runbook and the only place outside docker/run.sh where
#   the compose invocation is spelled out.
#
# WHY it delegates to docker/run.sh and docker/build.sh instead of calling
# docker compose itself:
#   run.sh already resolves the mode into a compose file, an env file, a project
#   name, the sidecar profiles and the preflight (docker/run.sh:1-24). A second,
#   independent invocation here would drift from it - the same reason
#   R:\GPTHub\Makefile:60-96 shells into build.sh and run.sh rather than
#   duplicating their argument handling.
#
# WHY every target is .PHONY:
#   The repository has no build artifact whose mtime can stand in for "up to
#   date", and a target that silently does nothing because a file of the same
#   name exists is worse than no target at all.
# =============================================================================

# --- shell ------------------------------------------------------------------
# GNU Make on Windows defaults to cmd.exe, which cannot run a single recipe in
# this file. Git for Windows ships bash at a path WITH SPACES ("C:/Program
# Files/Git/bin/bash.exe"), and an unquoted path with a space makes make report
# "process_begin: CreateProcess(NULL, pwd, ...) failed", so the 8.3 short form is
# used. On POSIX the default shell is kept.
ifeq ($(OS),Windows_NT)
  ifneq ($(wildcard C:/PROGRA~1/Git/bin/bash.exe),)
    SHELL := C:/PROGRA~1/Git/bin/bash.exe
  else ifneq ($(wildcard C:/PROGRA~2/Git/bin/bash.exe),)
    SHELL := C:/PROGRA~2/Git/bin/bash.exe
  else
    $(error Git Bash not found under C:/PROGRA~1/Git or C:/PROGRA~2/Git. Install Git for Windows, or run make from WSL.)
  endif
endif
# -e: a failing command fails the recipe (no half-done target reports success)
# -u: an unset variable is an error (a MODE typo must not expand to "")
# -o pipefail: "cmd | tail" reports cmd's status, not tail's
.SHELLFLAGS := -eu -o pipefail -c

# --- configuration ----------------------------------------------------------
# MODE selects the stack: dev publishes backend and frontend for debugging, prod
# publishes only the gateway. One variable rather than two code paths because
# that is exactly how docker/run.sh models it (--dev / --prod).
MODE ?= dev
ifeq ($(filter $(MODE),dev prod),)
  $(error MODE must be dev or prod, got "$(MODE)")
endif

ENV_FILE ?= docker/.env.$(MODE)
COMPOSE_FILE := $(if $(filter dev,$(MODE)),docker/docker-compose.dev.yaml,docker/docker-compose.yaml)
PROJECT_NAME ?= hiresnap-$(MODE)

# Non-secret knobs; the real values are read from the env file by the shell.
NGINX_PUBLISH_HTTP ?= 8088

# Python: the guards and docker/sync_env.py are standard-library scripts, so any
# interpreter >= 3.8 will do.
#
# ORDER MATTERS, and the versioned names are not decoration. On Windows, PATH
# usually contains C:\Users\<user>\AppData\Local\Microsoft\WindowsApps, whose
# python3.exe is the Store stub: `command -v python3` SUCCEEDS, so a probe that
# only asks "does the name resolve" picks an interpreter that exits 49 for every
# invocation. The version check below therefore runs the candidate instead of
# trusting its name, and the versioned spellings (python3.13 ... python3.9) are
# what actually resolve on a pyenv-win or python.org install, where the plain
# names are the stubs. `py -3` is the last resort: the Windows launcher is not on
# PATH inside Git Bash, so it only helps when it is.
# An empty PYTHON is not fatal at parse time - it is caught by _require-python,
# which names the override. A silent empty expansion would make every target look
# like it passed.
PYTHON ?= $(shell for c in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python; do if $$c -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then echo $$c; exit 0; fi; done; if py -3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then echo 'py -3'; fi)

.DEFAULT_GOAL := help

.PHONY: help env build run run-dev run-prod stop down logs ps rebuild config test lint hooks guards structure db-shell backend-shell ml-shell ml-test smoke _require-docker _require-python _require-openssl

# =============================================================================
# help
# =============================================================================
help:
	@printf '%s\n' "" \
	  "HireSnap - superproject targets (MODE=$(MODE), env $(ENV_FILE))" \
	  "" \
	  "  make env            create $(ENV_FILE) from docker/.env.example + generate the JWT key pair" \
	  "  make build          build the $(MODE) images (docker/build.sh --$(MODE))" \
	  "  make run            start the $(MODE) stack (alias of run-$(MODE))" \
	  "  make run-dev        start hiresnap-dev  (gateway :$(NGINX_PUBLISH_HTTP), api :8080, spa :3000)" \
	  "  make run-prod       start hiresnap-prod (gateway :$(NGINX_PUBLISH_HTTP) only)" \
	  "  make down           stop the stack, keep volumes and host data (alias: stop)" \
	  "  make logs           follow all logs (SERVICE=backend for one service)" \
	  "  make ps             container status including health" \
	  "  make rebuild        build --no-cache, then up" \
	  "  make config         validate BOTH compose files (config -q; secrets are never printed)" \
	  "  make test           backend pytest + frontend jest, with an explicit SKIP when absent" \
	  "  make lint           pre-commit run --all-files (fails on any finding)" \
	  "  make hooks          install the git hooks" \
	  "  make guards         run every scripts/check_*.py guard" \
	  "  make structure      the backend layout + ML boundary gate only (fast, no docker)" \
	  "  make db-shell       mysql client inside the mysql container" \
	  "  make backend-shell  bash inside the backend container" \
	  "  make ml-shell       bash inside the ml sidecar container" \
	  "  make coverage       backend branch coverage against the floor in backend/pyproject.toml" \
	  "  make ml-test        the ml contract tests only (no torch, no weights, no GPU)" \
	  "  make smoke          curl the gateway /health and the API chain, assert JSON" \
	  "" \
	  "  Variables: MODE=dev|prod  ENV_FILE=...  NGINX_PUBLISH_HTTP=8088  PYTHON=...  SERVICE=..." \
	  ""

# =============================================================================
# preconditions - one message per missing tool, and a non-zero exit
# =============================================================================
_require-docker:
	@command -v docker >/dev/null 2>&1 || { printf '%s\n' "ERROR: docker is not on PATH; the stack targets need Docker Desktop (or dockerd) running." >&2; exit 2; }

_require-python:
	@[ -n "$(PYTHON)" ] || { printf '%s\n' "ERROR: no Python >= 3.8 found (tried python3, python, py -3)." "       The guards and docker/sync_env.py are stdlib-only Python scripts." "       Point the Makefile at an interpreter:  make $(MAKECMDGOALS) PYTHON=/path/to/python" >&2; exit 2; }
	@$(PYTHON) -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' || { printf '%s\n' "ERROR: $(PYTHON) is older than 3.8." >&2; exit 2; }

_require-openssl:
	@command -v openssl >/dev/null 2>&1 || { printf '%s\n' "ERROR: openssl is not on PATH; it is required to generate the JWT RSA key pair." >&2; exit 2; }

# =============================================================================
# env - the only target that writes secrets, and it only writes what is absent
# =============================================================================
# WHY it does not simply copy .env.example: the template ships placeholders, and
# a copied file would start the stack with those placeholders as real passwords
# while satisfying Compose's ":?must be set" guards with the literal word
# "change-me". docker/sync_env.py documents the append-only, never-rotate
# contract this target relies on (docker/sync_env.py:1-27).
#
# WHY the JWT key pair belongs here: the backend resolves the key files AT IMPORT
# TIME (backend/service/models/auth_models.py, the certs path is derived from
# __file__) and RS256 is hardcoded, so
# without the files every authenticated route fails at startup. "The environment
# exists" therefore includes the keys. They are generated ONLY when missing:
# rotating a live pair invalidates every issued token (infra/certs/README.md).
env: _require-python _require-openssl
	@if [ -f "$(ENV_FILE)" ]; then printf '%s\n' "env     : $(ENV_FILE) already exists - left untouched (values are never rotated)"; else printf '%s\n' "env     : creating $(ENV_FILE) from docker/.env.example"; $(PYTHON) docker/sync_env.py --mode $(MODE) && printf '%s\n' "env     : written"; fi
	@$(PYTHON) docker/sync_env.py --check
	@if [ -f infra/certs/jwt-private.pem ] && [ -f infra/certs/jwt-public.pem ]; then printf '%s\n' "jwt     : infra/certs/jwt-*.pem already present - left untouched"; else printf '%s\n' "jwt     : generating infra/certs/jwt-{private,public}.pem (2048-bit RSA, RS256)"; mkdir -p infra/certs; openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out infra/certs/jwt-private.pem && openssl rsa -pubout -in infra/certs/jwt-private.pem -out infra/certs/jwt-public.pem 2>/dev/null && chmod 600 infra/certs/jwt-private.pem && chmod 644 infra/certs/jwt-public.pem && printf '%s\n' "jwt     : done (both files are gitignored by .gitignore:52)"; fi

# =============================================================================
# stack lifecycle
# =============================================================================
build: env _require-docker
	@./docker/build.sh --$(MODE)

run: env _require-docker
	@./docker/run.sh --$(MODE) up

run-dev:
	@$(MAKE) --no-print-directory run MODE=dev

run-prod:
	@$(MAKE) --no-print-directory run MODE=prod

# WHAT IT DESTROYS: nothing on the host. Named volumes (mysql_data, redis_data,
# minio_data, hf_cache) survive "down" because compose removes them only with -v,
# and this target deliberately does not pass -v. Re-initialising the database is
# a separate, explicit act documented in infra/mysql/init/README.md.
down: env _require-docker
	@./docker/run.sh --$(MODE) down

stop: down

logs: env _require-docker
	@./docker/run.sh --$(MODE) logs $(SERVICE)

ps: env _require-docker
	@./docker/run.sh --$(MODE) ps

rebuild: env _require-docker
	@./docker/build.sh --$(MODE) --no-cache
	@./docker/run.sh --$(MODE) up

# =============================================================================
# config - validate both compose files, print nothing that is secret
# =============================================================================
# WHY never "docker compose config" without -q: the rendered document contains
# every interpolated value, DB password and MinIO keys included. On a terminal
# that is a habit; in a CI log or a pasted bug report it is a credential leak.
# -q parses, validates and exits, which is the whole point of the check.
#
# WHICH PROFILES ARE NAMED, AND WHY EXACTLY THESE: the stack has one PRIMARY
# profile per mode (dev, prod) that owns the five core services (mysql, backend,
# frontend, gateway, ml) plus four optional ones - cache, storage, celery, mail -
# gated by CACHE/STORAGE/CELERY/MAIL in the env file. `config -q` only validates
# what it is asked to render, so every optional profile is requested here and a
# typo in a service that is off by default still fails the check.
#
# WHAT IS DELIBERATELY NOT A PROFILE ANY MORE: ml was `--profile ml` until the
# sidecar moved into its own submodule. It is a CORE service now, declared under
# the primary profile, because the API cannot answer an upload without it -
# leaving it behind a profile meant the default stack started with no embedding
# path at all (docker/docker-compose.yaml ml: profiles [prod];
# scripts/check_compose_parity.py asserts it).
#
# GPU IS NOT A PROFILE EITHER: GPU=1 layers docker/docker-compose.gpu.yaml on top
# of the base file (docker/sidecar_profiles.sh), because a device reservation is a
# HOST requirement - on a machine without the NVIDIA runtime the container fails
# to start rather than starting degraded. It adds no service, so it is not part
# of a profile list; `make config` validates the base stack, which is what every
# host runs.
config: env _require-docker
	@if [ ! -f docker/.env.dev ]; then printf '%s\n' "config  : SKIP docker/docker-compose.dev.yaml - docker/.env.dev is absent (run: make env MODE=dev)"; else docker compose --env-file docker/.env.dev -f docker/docker-compose.dev.yaml --profile dev --profile cache --profile storage --profile celery --profile mail config -q && printf '%s\n' "config  : OK   docker/docker-compose.dev.yaml" || { printf '%s\n' "config  : FAIL docker/docker-compose.dev.yaml" >&2; exit 1; }; fi
	@if [ ! -f docker/.env.prod ]; then printf '%s\n' "config  : SKIP docker/docker-compose.yaml - docker/.env.prod is absent (run: make env MODE=prod)"; else docker compose --env-file docker/.env.prod -f docker/docker-compose.yaml --profile prod --profile cache --profile storage --profile celery --profile mail config -q && printf '%s\n' "config  : OK   docker/docker-compose.yaml" || { printf '%s\n' "config  : FAIL docker/docker-compose.yaml" >&2; exit 1; }; fi

# =============================================================================
# quality gates
# =============================================================================
# WHY guards is separate from lint: these are the same scripts/check_*.py that
# pre-commit and CI run, and a developer must be able to run them without a full
# pre-commit environment (on a machine with no Node, for instance). A guard that
# cannot run prints [SKIP] with a reason and exits 0; a guard that finds a
# violation exits non-zero and this target propagates it.
#
# WHY it also runs the SUBMODULES' own guards: the superproject can see a submodule
# only as one commit hash, so every check here is blind to what is inside it. The
# layout rules that matter after a move - does the backend still have its
# vertical slices, does the sidecar still keep its modules flat - are shipped by
# the owners of those trees (backend/scripts/check_backend_layout.py,
# ml/scripts/check_ml_service_layout.py) precisely because they know the shape
# better than a cross-repo guard can. Running them from here means ONE command
# answers "is this whole checkout consistent" instead of three.
# WHY they go through scripts/run_guard.sh instead of $(PYTHON): that script is
# the repository's single answer to "which interpreter actually starts" (a
# Microsoft Store python3 name resolves and then exits 49 - Makefile:66-78), and a
# second copy of that probe here would drift from the one pre-commit uses. It also
# gives the [SKIP]-on-no-python behaviour for free: a missing interpreter is a
# missing local prerequisite, not a broken tree.
# WHY a missing guard is a SKIP and not a failure: these files live in the
# submodules, and a checkout made without --recursive legitimately does not have
# them. The SKIP says so out loud; a guard that IS present and fails still fails.
guards: _require-python
	@set -e; failed=""; ran=0; for script in scripts/check_*.py; do [ -f "$$script" ] || continue; ran=$$((ran + 1)); printf '%s\n' "---- $$script"; $(PYTHON) "$$script" || failed="$$failed $$script"; done; if [ "$$ran" -eq 0 ]; then printf '%s\n' "guards  : ERROR no scripts/check_*.py found" >&2; exit 1; fi; if [ -n "$$failed" ]; then printf '%s\n' "guards  : FAILED$$failed" >&2; exit 1; fi; printf '%s\n' "guards  : all $$ran guard(s) passed"
	@set -e; failed=""; ran=0; for script in backend/scripts/check_backend_layout.py ml/scripts/check_ml_service_layout.py; do if [ ! -f "$$script" ]; then printf '%s\n' "guards  : SKIP $$script - the submodule guard is not in this checkout (nothing was checked)"; continue; fi; ran=$$((ran + 1)); printf '%s\n' "---- $$script (submodule guard)"; bash scripts/run_guard.sh "$$script" || failed="$$failed $$script"; done; if [ -n "$$failed" ]; then printf '%s\n' "guards  : FAILED (submodule guards)$$failed" >&2; exit 1; fi; printf '%s\n' "guards  : $$ran submodule guard(s) passed"

# WHY a separate target for the layout gate: 'make guards' runs seven checks, five
# of which want docker (compose parity, healthcontracts) and a populated env file,
# so a developer who only moved a module waits for all of them to answer a question
# about the tree. check_ml_boundary.py is the guard that owns the layout - it fails
# on an ML library inside the API, on a modelling/ or cosmos_tokenizer/ directory
# anywhere under backend/, and on a loose .py/.sh/.bat/.sql/.txt file at the
# backend ROOT - and that is exactly the set a refactor needs to be told about.
#
# WHY it also globs scripts/check_*structure*.py: the layout rules will grow (the
# refactor created backend/database/, backend/notebooks/ and backend/docs/ at
# once), and a target with a hard-coded file list would silently stop covering a
# guard that was added later. The glob means a new structure guard runs the day it
# lands, with no Makefile edit and no chance to forget one.
#
# WHY the two SUBMODULE layout guards are named here and not globbed: they live
# outside this repository (backend/ and ml/ are separate checkouts), so a glob over
# scripts/ can never see them, and the names are a contract the submodules owe this
# target. backend/scripts/check_backend_layout.py asserts the vertical-slice shape
# (service/services/<domain>/{domain,application,persistence,presentation}) and
# ml/scripts/check_ml_service_layout.py asserts the sidecar's flat layout; both are
# stdlib-only by design, so this target still needs no docker, no env file and no
# service dependencies - that is what makes it the right command to run after a
# module move. A guard that is absent (a checkout without --recursive) prints SKIP
# and does not fail the target; one that is present and fails does.
#
# WHY they run through scripts/run_guard.sh and not $(PYTHON): the same reason the
# guards target does - that script is the repository's one answer to "which
# interpreter actually starts", it is what pre-commit calls, and a second copy of
# the probe here would let `make structure` and the hook disagree about whether the
# guard ran at all.
# WHY every shell variable is written "$$name" and not "$name": make expands a
# single "$" ITSELF, and a bare "$script" is read as the make variable "$s"
# followed by the literal text "cript". The recipe below therefore used to run
# "[ -f cript ]" and "ran=)" - a raw shell syntax error that made this target
# unusable, while "make guards" (whose recipe was already escaped) passed. Same
# reason the arithmetic is "$$((ran + 1))": make would otherwise eat "$((".
structure: _require-python
	@set -e; ran=0; failed=""; for script in scripts/check_ml_boundary.py scripts/check_*structure*.py; do [ -f "$$script" ] || continue; ran=$$((ran + 1)); printf '%s\n' "---- $$script"; $(PYTHON) "$$script" || failed="$$failed $$script"; done; if [ "$$ran" -eq 0 ]; then printf '%s\n' "structure: ERROR no layout guard found (scripts/check_ml_boundary.py is missing)" >&2; exit 1; fi; if [ -n "$$failed" ]; then printf '%s\n' "structure: FAILED$$failed" >&2; exit 1; fi; printf '%s\n' "structure: all $$ran layout guard(s) passed"
	@set -e; ran=0; failed=""; for script in backend/scripts/check_backend_layout.py ml/scripts/check_ml_service_layout.py; do if [ ! -f "$$script" ]; then printf '%s\n' "structure: SKIP $$script - the submodule layout guard is not in this checkout"; continue; fi; ran=$$((ran + 1)); printf '%s\n' "---- $$script"; bash scripts/run_guard.sh "$$script" || failed="$$failed $$script"; done; if [ -n "$$failed" ]; then printf '%s\n' "structure: FAILED (submodule layout guards)$$failed" >&2; exit 1; fi; printf '%s\n' "structure: $$ran submodule layout guard(s) passed"

# WHY no "|| true": a lint that cannot fail holds nothing (R:\GPTHub\Makefile:337-340
# makes the same argument). When pre-commit is missing this target FAILS with the
# install command instead of reporting a success it never verified.
lint: _require-python
	@command -v pre-commit >/dev/null 2>&1 || { printf '%s\n' "ERROR: pre-commit is not installed, so lint cannot run." "       Install it:  $(PYTHON) -m pip install pre-commit" >&2; exit 2; }
	@pre-commit run --all-files

# WHY hooks installs into the SUBMODULES TOO, not just here: backend/, frontend/
# and ml/ are independent git repositories, so a hook installed only in the
# superproject never runs on a commit made inside one of them - and that is where
# the code changes. R:\GPTHub\Makefile:344-372 makes the same point from the
# other direction: its per-submodule loop was written because gitleaks had never
# run in frontend/ at all, and eyeballing the tree cannot detect it (a submodule's
# .git is not where the superproject's is).
#
# WHY the loop is driven by the CONFIG FILE and not by a list of names: a submodule
# without .pre-commit-config.yaml has no policy of its own to install, and saying so
# out loud is better than pretending it was hooked. A submodule that GAINS a config
# is picked up on the next run without editing this target.
#
# WHY pre-push as well: the commit hook can be bypassed deliberately (--no-verify),
# and a full-history secret scan is too slow to want on every commit, so the same
# gates get a second chance before anything leaves the machine.
hooks:
	@command -v pre-commit >/dev/null 2>&1 || { printf '%s\n' "ERROR: pre-commit is not installed; run: $(PYTHON) -m pip install pre-commit" >&2; exit 2; }
	@pre-commit install
	@pre-commit install --hook-type pre-push
	@printf '%s\n' "hooks   : installed in the superproject (commit + pre-push)"
	@set -e; for m in backend frontend ml; do \
	  if [ ! -f "$$m/.pre-commit-config.yaml" ]; then \
	    printf '%s\n' "hooks   : $$m has no .pre-commit-config.yaml - skipped"; \
	  else \
	    ( cd "$$m" && pre-commit install >/dev/null && pre-commit install --hook-type pre-push >/dev/null ) \
	      && printf '%s\n' "hooks   : installed in $$m (commit + pre-push)"; \
	  fi; \
	done

# WHY the SKIP branches: a test target that reports success while executing zero
# tests is a lie. backend/tests/ and ml/tests/ exist today, but pytest may not be
# installed for the interpreter this Makefile picked, and frontend/node_modules only
# exists after npm ci, so every case is stated out loud instead of passing silently.
#
# WHY the ml suite is a separate interpreter call and not a second path argument to
# the same pytest run: backend's conftest.py must not leak fixtures into the
# sidecar's contract tests, and the sidecar's import root comes from its own
# pyproject.toml - two facts that would otherwise be silently merged.
test: _require-python
	@if [ ! -d backend/tests ]; then printf '%s\n' "test    : SKIP backend - backend/tests/ does not exist"; elif ! $(PYTHON) -c 'import pytest' >/dev/null 2>&1; then printf '%s\n' "test    : SKIP backend - pytest is not importable by $(PYTHON)" "          install it:  $(PYTHON) -m pip install -r backend/requirements.txt"; else printf '%s\n' "test    : backend pytest"; ( cd backend && $(PYTHON) -m pytest -q ) || exit 1; fi
	@$(MAKE) --no-print-directory ml-test
	@if [ -d frontend/node_modules ]; then printf '%s\n' "test    : frontend jest (CI mode, no watch)"; ( cd frontend && CI=true npm test -- --watchAll=false ) || exit 1; else printf '%s\n' "test    : SKIP frontend - frontend/node_modules is absent (run: cd frontend and npm ci)"; fi

# WHY a coverage target at all, and why it is NOT the test target: 'make test' is
# the fast loop a developer runs while working, and a coverage run is measurably
# slower (branch tracing on every line). More importantly the two answer different
# questions - 'test' answers "does it pass", 'coverage' answers "how much of this
# tree has any test at all" - and folding the second into the first makes the
# first slower for everyone in order to inform one person.
#
# WHY 'coverage run' AND NOT 'pytest --cov': measured on this host, pytest-cov
# leaves a SECOND, statement-shaped data file behind when it cannot find its
# configuration (it reads .coveragerc by default and ignores pyproject.toml), and
# the next invocation that does read the branch setting dies with
# "Can't combine statement coverage data with branch data" - a crash that names a
# plugin, not a test, and that only reproduces when the two runs interleave. The
# coverage CLI has one entry point, reads --rcfile when told to, and can be run
# twice in a row without the second run inheriting the first one's format.
#
# WHY the threshold is not passed here: it lives in pyproject.toml
# ([tool.coverage.report] fail_under = 65), so this target, CI and a developer's
# own command all read ONE number instead of three that drift. Measured: 65.32%
# on Python 3.9 (the CI interpreter) with branch coverage on.
coverage: _require-python
	@if [ ! -d backend/tests ]; then printf '%s\n' "coverage: SKIP backend - backend/tests/ does not exist"; elif ! $(PYTHON) -c 'import coverage' >/dev/null 2>&1; then printf '%s\n' "coverage: SKIP backend - coverage is not importable by $(PYTHON)" "          install it:  $(PYTHON) -m pip install coverage"; else printf '%s\n' "coverage: backend branch coverage (floor from backend/pyproject.toml)"; ( cd backend && $(PYTHON) -m coverage run --branch --source=service --rcfile=pyproject.toml -m pytest -q && $(PYTHON) -m coverage report --rcfile=pyproject.toml ) || exit 1; fi

# WHY ml-test is its own target and not three lines inside test: the sidecar is a
# submodule with its own test contract (ml/README.md is canonical), and a change
# to ml/ alone should be provable without waiting for backend and jest. 'make
# test' still runs it - the separate target is for the short loop, not instead of
# the full one.
#
# WHY the tests run from the repository ROOT and not "cd ml": pytest infers
# rootdir from the path argument (ml/tests -> ml) and reads the sidecar's own
# [tool.pytest.ini_options] pythonpath from ml/pyproject.toml, so the module
# directory is declared ONCE, by the submodule that owns it - by the same
# declaration the CI job and the developer's shell read. Running from ml/ would
# give the same result and a different command than CI, which is how the two
# drift. (The sidecar imports nothing from backend/ any more; the sibling
# checkout is not what makes this work.) A missing pytest is reported, never
# swallowed: a test target that reports success while running zero tests is a lie.
ml-test: _require-python
	@if [ ! -d ml/tests ]; then printf '%s\n' "ml-test : SKIP ml - ml/tests/ does not exist"; elif ! $(PYTHON) -c 'import pytest' >/dev/null 2>&1; then printf '%s\n' "ml-test : SKIP ml - pytest is not importable by $(PYTHON)" "          install it:  $(PYTHON) -m pip install pytest fastapi python-multipart"; else printf '%s\n' "ml-test : contract tests (no torch, no weights, no GPU)"; $(PYTHON) -m pytest ml/tests -q || exit 1; fi

# =============================================================================
# service shells
# =============================================================================
# WHY exec through run.sh rather than docker compose exec: run.sh already resolved
# the mode into one compose command array (docker/run.sh:150), so the container
# reached here is guaranteed to belong to the project this Makefile thinks it is
# talking to.
db-shell: env _require-docker
	@./docker/run.sh --$(MODE) exec mysql sh -c 'exec mysql -u"$$MYSQL_USER" -p"$$MYSQL_PASSWORD" "$$MYSQL_DATABASE"'

backend-shell: env _require-docker
	@./docker/run.sh --$(MODE) exec backend bash

# WHY a shell into the sidecar is worth a target: ml/ is a separate submodule with
# its own image, so "the model did not load" is debugged inside a DIFFERENT
# container than the one backend-shell opens. Without this target the answer is
# either a memorised compose invocation or an exec into the wrong container -
# which looks like an empty /app and a missing model at the same time.
ml-shell: env _require-docker
	@./docker/run.sh --$(MODE) exec ml bash

# =============================================================================
# smoke - is the stack serving through the same path a browser uses?
# =============================================================================
# WHY not "docker ps": ps reports the container HEALTHCHECK, and the gateway
# answers its own /health. That proves nginx started; it does not prove the proxy
# routes. These requests go through the published gateway port, so they catch a
# broken upstream variable, a missing rewrite and a dead backend - the three
# failures that look like "the site is down" and are invisible in ps.
#
# WHY not /api/health: the FastAPI app mounts routers at /server, /public and a
# static /data (backend/service/api/app.py) and declares no health route
# anywhere, so /api/health 404s through a perfect proxy. The API chain is proven
# with /api/openapi.json, which answers only if the gateway rewrote /api/ into
# /server/ AND the backend is serving.
smoke: env _require-docker
	@base="$${SMOKE_BASE:-http://localhost:$(NGINX_PUBLISH_HTTP)}"; printf '%s\n' "smoke   : gateway $$base"; body=$$(curl -fsS --max-time 10 "$$base/health") || { printf '%s\n' "smoke   : FAIL gateway /health is unreachable" >&2; exit 1; }; case "$$body" in *ok*) printf '%s\n' "smoke   : OK   /health -> $$body" ;; *) printf '%s\n' "smoke   : FAIL /health did not return the expected JSON: $$body" >&2; exit 1 ;; esac; spec=$$(curl -fsS --max-time 30 "$$base/api/openapi.json") || { printf '%s\n' "smoke   : FAIL /api/openapi.json - the gateway is up but /api/ does not reach the backend" >&2; exit 1; }; case "$$spec" in *openapi*) printf '%s\n' "smoke   : OK   /api/ -> /server/ rewrite reaches FastAPI (schema served)" ;; *) printf '%s\n' "smoke   : FAIL /api/openapi.json is not an OpenAPI document" >&2; exit 1 ;; esac; code=$$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "$$base/") || true; case "$$code" in 200) printf '%s\n' "smoke   : OK   / serves the SPA (HTTP 200)" ;; *) printf '%s\n' "smoke   : WARN / returned HTTP $$code - the frontend container may still be starting" ;; esac; printf '%s\n' "smoke   : done"
