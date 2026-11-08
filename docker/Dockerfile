# ===========================================================================
# HireSnap backend image - FastAPI + PyMySQL. No CUDA, no torch, no models.
# ===========================================================================
#
# INFERENCE IS NOT IN THIS IMAGE. The embedding, text-embedding and
# transcription models live in the ml sidecar (ml/), and the backend reaches
# them over HTTP through service/services/ml_client.py (default
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
# root, Docker reads <context>/.dockerignore - the ROOT one, owned by the infra
# layer - and NOT backend/.dockerignore. That root file is therefore the only
# ignore list this image actually uses. backend/.dockerignore is not dead
# weight: it covers the other build path, "docker build backend/" while
# iterating on the service alone, and it is what the standalone backend checkout
# gets. If a future refactor ever needs rules that differ between the two paths,
# BuildKit also reads <Dockerfile>.dockerignore - here that would be
# backend/docker/Dockerfile.dockerignore - but no such file exists today
# (backend/docker/ holds exactly Dockerfile and requirements.lock.txt), so the
# root .dockerignore is the whole story.
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
# PYTHONPATH: /app ONLY.
# The package is 'service' (backend/service/ -> /app/service), so /app is the one
# directory that has to be importable. There used to be a second entry, /app/src,
# because the application lived in a directory called 'src' and half of the modules
# were imported as TOP-LEVEL names ('from env import Env'). Both of those are gone:
# the package has a name, every import is absolute ('from service.configuration.env
# import Env'), and there is no top-level module left to find. A second PYTHONPATH
# entry would only invite a new top-level module to appear beside the package.
ENV PYTHONPATH=/app
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
#                                    the old modelling tree, which is gone from this
#                                    stack. The pipeline that still shells out
#                                    to ffmpeg runs in the ml image.
#   * libgl1, libglib2.0-0, libsm6, libxext6 - were for opencv-python, which no
#                                    module reachable from service/api/app.py imports.
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
# Dockerfile for the workers: the worker tasks import
# service.infrastructure.messaging.tasks, which imports service.services.jobs_services
# and service.services.ml_client - i.e. the worker
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
# serves exactly as before when every flag is off (backend/requirements-platform.txt
# :1-13 states the same trade from the other side). A conditional layer would
# produce two image variants whose behaviour differs only in an ImportError that
# appears on a deploy night, and 'docker compose config' could not tell them apart.
COPY --chown=app:app backend/requirements-platform.txt /tmp/requirements/requirements-platform.txt
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" \
    -r /tmp/requirements/requirements-platform.txt

# --- layer 3: application code --------------------------------------------
# THREE things are copied, and each is here because something imports or opens it
# by a path derived from the package, not from the working directory:
#
# service/       the whole application package. /app/service/ is the package, so
#                'from service.x import y' resolves and 'python -m service.main'
#                runs. configuration/logging.yaml travels with it: it is code that
#                configures the process, so it lives beside the code that reads it
#                (service/configuration/paths.py:33-34) instead of at /app.
# database/      the schema dump. The DEBUG-gated loader
#                (service/infrastructure/database/schema.py) resolves
#                <database>/<DB>.sql from BACKEND_ROOT/database, i.e.
#                /app/database/HireSnap.sql, so a DEBUG=TRUE start can create its
#                own schema. 18 KB, already committed, no secrets.
# docker/        NOT copied (it is build context for this file only), and neither
#                are scripts/, tests/, notebooks/ or docs/: the interactive
#                developer bootstrap and the manual suites are not runtime code,
#                and shipping them would put a second copy of every path in the
#                image where a stale one could be imported.
COPY --chown=app:app backend/service /app/service
COPY --chown=app:app backend/database /app/database

# Mount points, created with the right owner in the image. Bind mounts hide these
# directories, but a named volume does NOT: Docker seeds a fresh named volume from
# the image's directory, ownership included, so the directory has to exist with the
# right owner or the non-root user cannot write into it.
#
# /app/data      DATA_PATH bind mount (uploads and .npy embeddings)
# /app/logs      app.log, written by service/configuration/logging.yaml
# /app/secrets   ../infra/certs is mounted at /app/secrets/certs, read-only; the
#                directory is created so the mount point is stable and the packaged
#                fallback at /app/service/certs stays available
# /app/service/certs  the fallback location for the JWT pair when the variables are
#                unset (service/domain/models.py:_key_path)
RUN mkdir -p /app/data /app/logs /app/secrets/certs /app/service/certs \
    && chown -R app:app /app/data /app/logs /app/secrets /app/service/certs

USER app
# 8080 is the default of SERVER_PORT in the env manifest; EXPOSE is
# documentation only - the actual bind address comes from HOST/SERVER_PORT
# (service/main.py, run_server) and compose publishes nothing in prod.
EXPOSE 8080

# --- healthcheck -----------------------------------------------------------
# GET /server/health answers {"status": "ok", "service": "backend", "version": ...}
# (service/api/app.py). It is deliberately a route of its own rather than
# /server/openapi.json, which was the previous probe: the OpenAPI document is built
# from every router and costs real work on each call, while a liveness probe is run
# every 30 seconds for the life of the container. The shape mirrors the ml sidecar's
# /health (docs/SERVICE_CONTRACTS.md 1.1) so one script can probe both.
# Compose overrides this with the same command plus a 180s start_period; the
# Dockerfile value is the default for a plain 'docker run'.
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=5 \
    CMD curl -fsS "http://127.0.0.1:${SERVER_PORT:-8080}/server/health" -o /dev/null || exit 1

# The entry point is the package, run as a module: 'python -m service.main'. Not
# uvicorn directly, because service/main.py reads DEBUG to decide on --reload and
# configures uvicorn's log_config from service/configuration/logging.yaml, so
# bypassing it would silently change logging and hot-reload behaviour. Not
# 'python service/main.py' either: that puts service/ on sys.path instead of /app,
# and 'from service.x import y' would then fail.
CMD ["python", "-m", "service.main"]
