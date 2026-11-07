#!/bin/bash
# Sidecar profile flags and the WIRING they imply, in one place.
#
# Called by both build.sh and run.sh so the two entry points can never disagree
# about which optional services exist. Mirror of R:\GPTHub\docker\
# sidecar_profiles.sh:3-8, where EXTRA_PROFILES is accumulated from the MEMOS
# and LDR flags and then appended to the compose command line.
#
# The defaults encode risk, not taste: a sidecar that is cheap and stateless
# (redis cache) is ON by default because a developer who forgets --cache gets a
# slow but correct stack; a sidecar that needs a broker, a mail server or
# special hardware is OFF by default because a developer who forgets the flag
# gets a working stack instead of a build that fails on the last layer.
#
#   CACHE=1    -> --profile cache    (redis:7.2-alpine, ~30 MB RAM)
#   STORAGE=1  -> --profile storage  (minio, local object store for uploads)
#   CELERY=0   -> --profile celery   (three workers + beat; needs the redis broker)
#   MAIL=0     -> --profile mail     (mailpit: SMTP sink with a web inbox)
#
# GPU is NOT in that list, because it is not a question of WHICH CONTAINERS
# EXIST. The ml sidecar is part of every stack - the API has no in-process
# inference path left to fall back to. What is optional is the CARD, and that
# is a different kind of decision: it is expressed as an extra compose FILE
# (docker-compose.gpu.yaml, see configure_gpu_override below) merged on top of
# the stack when GPU=1, not as a --profile. A profile can only add services;
# the GPU override adds a device reservation to a service that always exists,
# and keeping it in a separate file is what stops a CPU-only host from getting
# a container that dies with a driver error at start.
#
# Env values are read with the "-" form so an unset variable never trips
# "set -u" in a caller that uses it (GPTHub sidecar_profiles.sh uses the same
# "-" default form for MEMOS/LDR).
#
# TWO DIFFERENT JOBS HAPPEN HERE, and separating them matters:
#
#   1. STARTING a sidecar      - the compose --profile flags below.
#   2. POINTING the API at it  - the exports at the bottom.
#
# They are deliberately the same switch. A flag that starts a container but
# leaves the application talking to its in-process fallback produces a stack
# where 'ps' shows a healthy redis while every request still misses the cache,
# and the operator has no way to tell without reading the code. The wiring is
# exported (not written into .env) because a shell export WINS over --env-file
# during compose interpolation: that is what lets a key in the manifest stay
# EMPTY and mean "let the flag decide", instead of every mode having to carry
# a value that silently overrides the flag.

configure_sidecar_profiles() {
    EXTRA_PROFILES=()

    # The broker decision comes FIRST because celery needs redis. Saying
    # CELERY=1 while CACHE=0 is a configuration that cannot work: the workers
    # would retry a broker connection that no container answers. Turning redis
    # on and saying so is more honest than starting a worker that can never
    # consume, and it is not silently ignored either - it is printed.
    if [[ "${CELERY:-0}" == "1" && "${CACHE:-1}" != "1" ]]; then
        printf '%s\n' '[warn] CELERY=1 requires the redis broker; enabling the cache profile too.' >&2
        CACHE=1
        export CACHE
    fi

    if [[ "${CACHE:-1}" == "1" ]]; then
        EXTRA_PROFILES+=("--profile" "cache")
    fi

    if [[ "${STORAGE:-1}" == "1" ]]; then
        EXTRA_PROFILES+=("--profile" "storage")
    fi

    if [[ "${CELERY:-0}" == "1" ]]; then
        EXTRA_PROFILES+=("--profile" "celery")
    fi

    if [[ "${MAIL:-0}" == "1" ]]; then
        EXTRA_PROFILES+=("--profile" "mail")
    fi

    # Exported so a nested script (build.sh calling run.sh, or a make target)
    # sees the same decision without re-deriving it.
    export EXTRA_PROFILES

    export_sidecar_wiring
    configure_gpu_override
    return 0
}

# Turn GPU=1 into the -f argument that merges docker-compose.gpu.yaml into the
# stack.
#
# Kept out of EXTRA_PROFILES on purpose: --profile changes the SET of services,
# while this changes the RESOURCES of a service that is always present. Mixing
# the two would mean the sidecar block had to exist in two shapes (with and
# without the reservation) and the two would drift. One override file, applied
# or not, is the same trick the compose docs use for dev-only mounts.
#
# The file name is relative because run.sh and build.sh both cd to docker/
# before calling this (the same reason the compose file is named bare).
configure_gpu_override() {
    GPU_OVERRIDE_FILES=()

    if [[ "${GPU:-0}" == "1" ]]; then
        GPU_OVERRIDE_FILES+=("-f" "docker-compose.gpu.yaml")
    fi

    export GPU_OVERRIDE_FILES
    return 0
}

# One word for the run.sh banner. GPU is reported even when it is off, because
# 'is this stack using the card?' must be answerable from the startup output
# alone - the difference between a 40-second embed and a 4-minute one is not
# visible in 'ps'.
describe_gpu_state() {
    if [[ "${GPU:-0}" == "1" ]]; then
        printf 'gpu'
    else
        printf 'cpu'
    fi
}

# Export the keys the application reads, for every sidecar that is ON.
#
# Only the ON case exports anything: an OFF sidecar must leave the manifest
# value alone, and the manifest's default is the in-process/file/inline
# fallback. Exporting an empty string would be worse than not exporting - it
# would override a deliberate value an operator put in .env to point at an
# external service (a managed redis, a real SMTP relay).
export_sidecar_wiring() {
    if [[ "${CACHE:-1}" == "1" ]]; then
        # redis, not memory: with more than one container reading the cache, a
        # per-process LRU is a cache in name only.
        export CACHE__BACKEND=redis
        export CACHE__REDIS_URL="${CACHE__REDIS_URL:-redis://cache:6379/1}"
    fi

    if [[ "${CELERY:-0}" == "1" ]]; then
        # jobs_services.enqueue() runs tasks INLINE unless this is true, so a
        # worker container with this unset would sit idle while the API did the
        # work in the request thread.
        export CELERY__ENABLED=true
        export CELERY__BROKER_URL="${CELERY__BROKER_URL:-redis://cache:6379/1}"
        export CELERY__RESULT_BACKEND="${CELERY__RESULT_BACKEND:-redis://cache:6379/1}"
    fi

    if [[ "${MAIL:-0}" == "1" ]]; then
        # mailpit listens on 1025 with no auth and no TLS, so the smtp backend
        # runs plain. STARTTLS=false is not an oversight: asking for STARTTLS
        # against a server that does not offer it fails the handshake, which is
        # how "the mail never arrives" becomes "the mail catcher is misconfigured"
        # instead of "the SMTP code is broken".
        export MAIL__ENABLED=true
        export MAIL__BACKEND=smtp
        export MAIL__HOST="${MAIL__HOST:-mailpit}"
        export MAIL__PORT="${MAIL__PORT:-1025}"
        export MAIL__STARTTLS=false
        export MAIL__SSL=false
    fi

    return 0
}

# Human-readable summary for the "which optional services are on" line that
# run.sh prints. Kept next to the decision so the two cannot drift.
describe_sidecar_profiles() {
    local enabled=()
    [[ "${CACHE:-1}"  == "1" ]] && enabled+=("cache")
    [[ "${STORAGE:-1}" == "1" ]] && enabled+=("storage")
    [[ "${CELERY:-0}" == "1" ]] && enabled+=("celery")
    [[ "${MAIL:-0}"   == "1" ]] && enabled+=("mail")
    if (( ${#enabled[@]} == 0 )); then
        printf 'none'
    else
        local joined
        joined=$(IFS=,; printf '%s' "${enabled[*]}")
        printf '%s' "${joined}"
    fi
}
