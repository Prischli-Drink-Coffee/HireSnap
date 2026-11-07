# HireSnap ML sidecar image.
#
# BUILD CONTEXT IS ml/ (docker/docker-compose.yaml: context: ../ml, dockerfile:
# docker/Dockerfile). Nothing from ../backend is copied, because the sidecar OWNS the
# pipeline modules (ml/service/*.py) and imports nothing outside this directory -
# docs/ADR/0006-ml-sidecar-owns-inference.md. The first version of this image copied
# the backend tree and put it on sys.path; that made the "business logic / ML logic"
# split a naming convention instead of an architectural one.
#
# WHY THE CUDA RUNTIME BASE AND NOT nvidia/cuda:*-devel: inference needs the cuDNN and
# CUDA runtime libraries, not nvcc. The devel image is ~6 GB larger and we never
# compile a kernel here. 11.6.2 is the toolkit line the pinned torch 1.12.1+cu116
# wheels were built against.
FROM nvidia/cuda:11.6.2-cudnn8-runtime-ubuntu20.04

# 20.04 ships Python 3.8, and the application targets 3.9 (AGENTS.md). deadsnakes is
# the smallest way to get 3.9 without building CPython; the alternative - keeping 3.8
# - would mean the sidecar runs a different minor version from the backend and from
# every test run, which is exactly the drift this rewrite removed.
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      software-properties-common \
      ca-certificates \
      curl \
      ffmpeg \
      libsndfile1 \
      # libgl1 + libglib2.0-0 are dlopen-ed by opencv, which moviepy pulls in for
      # frame reading. Without them the video path dies at import with a bare
      # "libGL.so.1: cannot open shared object file".
      libgl1 \
      libglib2.0-0 \
      # X11/audio stubs that imageio-ffmpeg and the Qt-less moviepy writer probe
      # for at import; absent they only warn, but the warning is a red herring in
      # every support ticket, so they are cheap to include.
      libsm6 \
      libxext6 \
 && add-apt-repository -y ppa:deadsnakes/ppa \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      python3.9 \
      python3.9-dev \
      python3.9-distutils \
      python3.9-venv \
      build-essential \
 && rm -rf /var/lib/apt/lists/*

# python3.9 is not the default interpreter on 20.04; make it the default so that
# `python3`, `pip3` and a bare `pip` inside an exec into the container all agree with
# the CMD below. Relying on an explicit python3.9 in three places is how an operator
# ends up installing a package into the 3.8 site-packages.
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.9 1 \
 && update-alternatives --install /usr/bin/python python /usr/bin/python3.9 1 \
 && curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py \
 && python3.9 /tmp/get-pip.py --no-cache-dir \
 && rm -f /tmp/get-pip.py \
 && pip install --no-cache-dir "pip<24" "setuptools<60" wheel

# setuptools<60 is deliberate: torch 1.12 imports distutils at import time, and
# setuptools 60+ removes the bundled copy that supplies it on 3.9. pip<24 keeps the
# legacy resolver behaviour those old wheels were published for.

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # C.UTF-8 so that a filename with non-ASCII characters does not raise in ffmpeg or
    # in a tempfile path; the default POSIX locale cannot decode them.
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    # THE SYS.PATH CONTRACT: the modules are flat (service/app.py, service/engine.py).
    # There is no `service` package and no __init__.py, so this directory - not its
    # parent - is what has to be importable. Same value pyproject.toml gives pytest.
    PYTHONPATH=/app/service \
    # Model cache on a mounted volume: without it every recreate re-downloads
    # multi-hundred-MB weights into the writable layer and loses them.
    HF_HOME=/app/.cache/huggingface \
    HOME=/home/app \
    # Tells the NVIDIA container runtime this process wants the driver libraries and
    # the GPU; with the runtime absent these are inert, which is why a CPU-only host
    # still starts and answers /health as degraded.
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

# Non-root, fixed uid/gid 999: a bind-mounted models directory is chowned on the host
# by the same number, and an arbitrary uid would make every mounted checkpoint
# unreadable. 999 is the convention the other sidecars in this stack use.
RUN groupadd --gid 999 app \
 && useradd --uid 999 --gid 999 --create-home --shell /bin/bash app

# Code first, then dependencies: a code edit must not invalidate the (multi-GB) torch
# layer, and requirements.txt changes on a completely different cadence.
COPY service/ /app/service/
COPY docker/requirements.txt /tmp/requirements.txt

RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 && rm -f /tmp/requirements.txt

# The three directories the process writes or reads through, created and owned here so
# that a bind mount over an EMPTY directory still resolves: Docker creates a missing
# mount target as root-owned, and the sidecar runs as app. `mkdir -p` on an existing
# mount is a no-op, so this stays correct after the mount is applied.
#
# /app/models/weights and /app/models/pretrained_ckpts are the paths settings.py
# DEFAULTS to (WEIGHTS_DIR, PRETRAINED_CKPT_DIR) - compose mounts onto exactly these,
# so an operator who unsets either variable still reads the right directory instead of
# an empty one. /app/data is the DATA_PATH default and /app/ml-scratch the explicit
# value compose passes; both are created because either may be in effect.
RUN mkdir -p /app/models/weights /app/models/pretrained_ckpts /app/data /app/ml-scratch \
 && chown -R app:app /app /home/app

WORKDIR /app/service
USER app

EXPOSE 8080

# The healthcheck is the same endpoint the compose service healthcheck uses; carrying
# it in the image means `docker run` without compose is still monitored. --start-period
# is generous because the first request may pull a checkpoint from a slow mount, and a
# container that is merely cold must not be killed.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
    CMD curl -fsS "http://127.0.0.1:${ML_PORT:-8080}/health" -o /dev/null || exit 1

# `python3 app.py`, NOT `uvicorn app:app`: app.main() passes the already-built app
# object to uvicorn, so the module is imported exactly once. With the module-string form
# uvicorn imports app a second time in a fresh process context, which constructs a
# second MLEngine and a second idle-unload watchdog - two threads fighting over one
# GPU, and MODEL_IDLE_UNLOAD_SECONDS quietly unloading the models the other one is
# using.
CMD ["python3", "app.py"]
