# ===========================================================================
# HireSnap backend image - FastAPI + PyMySQL. No CUDA, no torch, no models.
# ===========================================================================
#
# INFERENCE IS NOT IN THIS IMAGE. The embedding, text-embedding and
# transcription models live in the ml sidecar (ml/), and the backend reaches
# them over HTTP through src/services/ml_client.py (default
# http://127.0.0.1:8080, override with ML_SERVICE_URL). That is why there is no
# torch layer, no requirements_external.txt install and no
# cosmos_tokenizer/ copy here any more: this image used to carry ~4 GB of CUDA
# wheels for a code path it no longer has.
#
# BUILD CONTEXT IS THE SUPERPROJECT ROOT, not ./backend:
#     docker build -f backend/docker/Dockerfile .
# which is exactly what docker/docker-compose.yaml declares (context: ..,
# dockerfile: ./backend/docker/Dockerfile). This Dockerfile therefore copies
# paths of the form backend/... .
# Mirror of R:\GPTHub\backend\docker\Dockerfile:27-38, which does the same and
# states the reason: the image needs files from more than one top-level
# directory, and a ../backend context would turn every such path into
# ../backend/../backend/... .
#
# CONSEQUENCE FOR .dockerignore: because the context root is the superproject
# root, Docker reads <context>/.dockerignore (the root one, owned by the infra
# layer) and NOT backend/.dockerignore. The ignore list this image actually
# uses therefore lives in backend/docker/Dockerfile.dockerignore (BuildKit
# reads <Dockerfile>.dockerignore); backend/.dockerignore covers a standalone
# 'docker build backend/' and is kept in sync by hand.
#
# WHY python:3.9-slim and not 3.11/3.12: Python 3.9 is the version the backend
# targets (AGENTS.md) and every pin in requirements.txt has a cp39 wheel, so the
# image needs no compiler. Moving the interpreter is a dependency upgrade, not a
# base-image tweak. The tag is floating (no digest); pin the digest once CI
# builds the image, because ':3.9-slim' is the same class of moving target that
# the GPTHub stack pins every third-party image against.
# ===========================================================================

FROM python:3.9-slim

# --- build arguments -------------------------------------------------------
# Index for pip. A mirror is a deployment decision, so it comes from the env
# manifest (section 0) rather than being hardcoded. Mirror of
# R:\GPTHub\backend\docker\Dockerfile:3, :25 (NEXUS_PYPI_INDEX_URL).
ARG PIP_INDEX_URL=https://pypi.org/simple
# Which requirement list to install, as a BASENAME of the two files staged in
# /tmp/requirements below (they are copied with --chown and their basenames
# preserved). The default is the repository's own requirements.txt;
# requirements.lock.txt is the pinned SNAPSHOT described in its own header.
#     --build-arg REQUIREMENTS_FILE=requirements.lock.txt
# Keeping the switch as an ARG means the reproducible and the as-authored
# install paths are the same code with one build argument.
ARG REQUIREMENTS_FILE=requirements.txt

# --- environment -----------------------------------------------------------
# Unbuffered stdout: without it a container that dies mid-inference loses the
# last log lines, which are the only evidence of why it died.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
# UTF-8 everywhere. The base image is POSIX/C by default and the application is
# Russian-language: it reads Cyrillic labels and a Cyrillic SQL dump, and
# writes Cyrillic to MySQL. Under the C locale Python 3.9 opens files as
# ASCII/ANSI_X3.4-1968 and the first Russian string either mojibakes or raises
# UnicodeDecodeError deep inside a request. Setting it here is why the image
# does not reproduce the developers' Windows locale.
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC
# PYTHONPATH: /app FIRST, then /app/src.
# The brief says "/app/src", but that alone does not start the server:
# 'python src/pipeline/server.py' puts the SCRIPT's directory
# (/app/src/pipeline) on sys.path, never the working directory, so
# 'from env import Env' (backend/src/pipeline/server.py:11) and
# 'from create_sql import CreateSQL' (:1092) fail - both resolve only against
# /app. Verified by running the real entry-point layout with
# PYTHONPATH=<root>/src -> ModuleNotFoundError: No module named 'env', and with
# PYTHONPATH=<root>:<root>/src -> imports OK.
# /app/src is kept as well because backend/main.sh:14-15 exports exactly
# PROJECT_PATH=$(pwd)/src into PYTHONPATH; keeping both means an import that
# works on a developer machine also works in the image.
ENV PYTHONPATH=/app:/app/src
# HuggingFace cache. The backend no longer reads it (no model is loaded in this
# process any more - see the header), but docker-compose.yaml still mounts the
# hf_cache volume here for the sidecar-era layout, and a named volume inherits
# the ownership of the image directory it covers on first use. Keeping the
# variable and the directory keeps that mount writable; removing them would
# change nothing for inference and would hand a future ML caller a root-owned
# directory. It is the one ML-era setting this image keeps on purpose.
ENV HF_HOME=/app/.cache/huggingface
ENV HOME=/home/app

# --- non-root user ---------------------------------------------------------
# uid 999 mirrors R:\GPTHub\backend\docker\Dockerfile:8-10: a fixed numeric
# uid keeps bind-mounted host directories (infra/storage, infra/logs) writable
# without a chmod 777. The container never needs root: the app binds 8080, the
# mounts are prepared on the host.
RUN groupadd --gid 999 app \
    && useradd --uid 999 --gid app --create-home --shell /bin/bash app \
    && mkdir -p /app \
    && chown app:app /app

# --- system packages -------------------------------------------------------
# ONE package, deliberately: curl. It exists for the HEALTHCHECK below; a probe
# that may be absent from the base image is a healthcheck that fails for the
# wrong cause (mirror of R:\GPTHub\backend\docker\Dockerfile:16).
#
# WHAT WENT AND WHY, because the removal is the point of this change:
#   * ffmpeg, libsndfile1          - were for moviepy/librosa in
#                                    src/modelling/*, which is gone from this
#                                    stack. The pipeline that still shells out
#                                    to ffmpeg runs in the ml image.
#   * libgl1, libglib2.0-0, libsm6, libxext6 - were for opencv-python, which no
#                                    module reachable from server.py imports.
#   * build-essential, gcc         - existed only because webrtcvad and parts of
#                                    textattack/nltk have no cp39 wheel. Every
#                                    pin in requirements.txt now ships one, so
#                                    the pip layer needs no compiler at all.
# They are not purged-but-kept any more: the compiler is simply not installed,
# which removes ~250 MB from the layer and, more importantly, makes it
# impossible to reintroduce a source-only dependency without deciding to.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
# The last two lines mirror R:\GPTHub\backend\docker\Dockerfile:16-23: apt's
# lists are the single biggest removable chunk of a Debian layer (~40 MB).

WORKDIR /app

# --- layer 1: python requirements -----------------------------------------
# THE FIRST AND ONLY LAYER THAT DOWNLOADS ANYTHING. Dependencies are copied and
# installed before the source is copied, so this layer is reused until the
# dependency files themselves change (mirror of R:\GPTHub\backend\docker\
# Dockerfile:27-31).
#
# There used to be a torch layer before this one: it downloaded ~4 GB of cu116
# wheels for a code path that has moved to the ml image. It is gone, so a cold
# build of this image is now a couple of minutes and a few hundred megabytes.
#
# Every entry in requirements.txt is pinned, so the "two builds a month apart
# differ" gap (docs/KNOWN_GAPS.md) is closed; requirements.lock.txt is kept as
# the identical snapshot for the REQUIREMENTS_FILE switch.
COPY --chown=app:app backend/requirements.txt backend/docker/requirements.lock.txt /tmp/requirements/
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" \
    -r "/tmp/requirements/${REQUIREMENTS_FILE}"
# /tmp/requirements/ is staging only: the two files are referenced by ARG, and
# a COPY of both into a directory keeps this layer's cache key identical for
# both install paths.

# --- layer 2: platform dependencies (celery, redis, boto3) -----------------
# WHY the SAME image also carries the platform stack instead of a second
# Dockerfile for the workers: the worker tasks import src.workers.tasks, which
# imports src.services.jobs_services and src.services.ml_client - i.e. the worker
# is the API image plus a different entry point, and now that both reach
# inference over HTTP they need exactly the same dependencies. R:GPTHub solves
# this the same way (celery-worker-* services in docker/docker-compose.yaml:
# 636-733 reuse the backend image and only change the command); a separate worker
# image drifts from requirements.lock.txt within a week.
#
# WHY its own layer AFTER requirements: the platform file changes far less often
# than requirements.txt, and keeping it separate means bumping a business
# dependency does not re-download celery/redis/boto3, and vice versa.
#
# WHY it is installed unconditionally rather than behind a build ARG: the code
# imports all three LAZILY, so an image that carries them still starts and still
# serves exactly as before when every flag is off (backend/requirements_platform.txt
# :1-13 states the same trade from the other side). A conditional layer would
# produce two image variants whose behaviour differs only in an ImportError that
# appears on a deploy night, and 'docker compose config' could not tell them apart.
COPY --chown=app:app backend/requirements_platform.txt /tmp/requirements/requirements_platform.txt
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" \
    -r /tmp/requirements/requirements_platform.txt

# --- layer 3: application code --------------------------------------------
# src/          the FastAPI app, config.yaml, logging.yaml and the certs/ and
#               weights/ mount points that compose fills in.
# env.py        imported as a TOP-LEVEL module ('from env import Env'), so it
#               must sit at /app/env.py, not inside src/ (server.py:11).
# create_sql.py imported as a top-level module too, and it is the DEBUG-gated
#               schema loader (server.py:1091-1094).
# HireSnap.sql  create_sql.py:18 resolves '<DB>.sql' NEXT TO ITSELF, i.e.
#               /app/HireSnap.sql, so the dump has to be in the image for a
#               DEBUG=TRUE start to create its schema. It is the one *.sql file
#               that is not excluded by the ignore lists (18 KB, already
#               committed, no secrets).
# setup/        NOT copied: it holds the interactive developer bootstrap
#               (check_local_modules.py, setup_windows.py) and nothing at
#               runtime imports it.
# cosmos_tokenizer/ NOT copied: it was a vendored package for
#               src/modelling/*.py, which is no longer part of this image. The
#               ml sidecar mounts/copies it on its own side.
COPY --chown=app:app backend/env.py backend/create_sql.py backend/HireSnap.sql /app/
COPY --chown=app:app backend/src /app/src

# Mount points, created with the right owner in the image. A bind mount hides
# the directory but the named volume below does NOT: Docker seeds a fresh named
# volume from the image's directory, ownership included. Without this, the
# non-root user cannot write the HF cache and every model download fails with
# 'Permission denied' only on the SECOND start (the first one ran as root).
RUN mkdir -p /app/data /app/logs /app/src/certs /app/src/weights \
        /app/pretrained_ckpts /app/.cache/huggingface \
    && chown -R app:app /app/data /app/logs /app/src/certs /app/src/weights \
        /app/pretrained_ckpts /app/.cache/huggingface

USER app
# 8080 is the default of SERVER_PORT in the env manifest; EXPOSE is
# documentation only - the actual bind address comes from HOST/SERVER_PORT
# (server.py:1077-1080) and compose publishes nothing in prod.
EXPOSE 8080

# --- healthcheck -----------------------------------------------------------
# There is NO /health route in the application (server.py mounts /server,
# /public and /data only) and adding one is not an allowed source patch.
# /server/openapi.json is the closest honest liveness probe: it answers 200
# only after uvicorn is serving AND the app object - including the
# StaticFiles(DATA_PATH) mount - was constructed. Compose overrides this with
# the same command plus a 180s start_period; the Dockerfile value is the
# default for a plain 'docker run'.
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=5 \
    CMD curl -fsS "http://127.0.0.1:${SERVER_PORT:-8080}/server/openapi.json" -o /dev/null || exit 1

# The entry point the repository itself documents (AGENTS.md:
# 'python3 src/pipeline/server.py'). Not uvicorn directly: server.py:1077-1090
# reads DEBUG to decide on --reload and configures uvicorn's log_config from
# src/logging.yaml, so bypassing it would silently change logging and hot
# reload behaviour.
CMD ["python", "src/pipeline/server.py"]
