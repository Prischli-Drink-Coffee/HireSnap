#!/bin/bash
# ============================================================================
# HireSnap - build the images. The only supported build entry point.
#
#   ./docker/build.sh --dev             # backend + frontend + ml, dev
#   ./docker/build.sh --prod            # backend + frontend + ml, prod
#   ./docker/build.sh --prod --celery   # backend image reused by the workers
#   ./docker/build.sh --prod --mail     # pull the mailpit image
#   ./docker/build.sh --prod --no-cache # full rebuild
#
# The ml sidecar is ALWAYS built: it is a core service of every stack, not an
# optional one, and the API no longer carries an in-process inference path to
# fall back to. It is listed last because it is the slow one (CUDA base image,
# torch cu116, several GB) and a failure in the cheap images is worth seeing
# first. Use 'compose build backend frontend' directly when iterating on the
# API and the sidecar image is already current.
#
# Mirrors R:\GPTHub\docker\build.sh:
#   * PROJECT_NAME must be the same value run.sh uses, otherwise the images are
#     built into a project that no "up" will ever look at (GPTHub build.sh
#     validates this before building);
#   * the build is preceded by the same preflight as run.sh, so a broken compose
#     file fails before ten minutes of pip install rather than after it.
#
# The sidecar flags come from sidecar_profiles.sh - the same source run.sh reads
# - so "--celery" cannot mean two different things in the two scripts. GPU is
# read as well, because a GPU build and a CPU build differ only in what happens
# at RUNTIME: the image is the same CUDA image either way, and the flag decides
# whether the container is allowed to ask for the card.
# ============================================================================
set -e

cd "$(dirname "$0")" || exit 1
source ./compose_preflight.sh
source ./sidecar_profiles.sh

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1" >&2; }
log_step()    { echo -e "${CYAN}[STEP]${NC} $1"; }

show_help() {
    sed -n '2,/^# ====/{/^# ====/d;p;}' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

MODE="prod"
NO_CACHE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dev)      MODE="dev"; shift ;;
        --prod)     MODE="prod"; shift ;;
        --no-cache) NO_CACHE="--no-cache"; shift ;;
        --cache)    CACHE=1; shift ;;
        --storage)  STORAGE=1; shift ;;
        --celery)   CELERY=1; shift ;;
        --mail)     MAIL=1; shift ;;
        -h|--help)  show_help ;;
        *)
            # An unknown flag is a typo, and a typo must not silently build the
            # other mode.
            log_error "unknown option: $1"
            log_info  "run '$0 --help' for usage"
            exit 2 ;;
    esac
done

if [[ "$MODE" == "dev" ]]; then
    COMPOSE_FILE="docker-compose.dev.yaml"
else
    COMPOSE_FILE="docker-compose.yaml"
fi
ENV_FILE=".env.${MODE}"

if [[ ! -f "$ENV_FILE" ]]; then
    log_error "environment file not found: docker/$ENV_FILE"
    log_info  "create it without leaking values: python docker/sync_env.py --mode $MODE"
    exit 1
fi

# One key reader, used only for NON-SECRET keys (same rule as run.sh: it prints
# the value, so it must never be called on a password).
env_value() {
    awk -F= -v key="$1" '
        $0 ~ "^[[:space:]]*" key "=" {
            value = substr($0, index($0, "=") + 1)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
            gsub(/^"|"$/, "", value)
            print value
            exit
        }
    ' "$ENV_FILE"
}

PROJECT_NAME="$(env_value PROJECT_NAME)"
PROJECT_NAME="${PROJECT_NAME:-hiresnap-$MODE}"

# Sidecar flags from the file, not from the caller's shell (see run.sh).
export CACHE="$(env_value CACHE)";     CACHE="${CACHE:-1}";     export CACHE
export STORAGE="$(env_value STORAGE)"; STORAGE="${STORAGE:-1}"; export STORAGE
export GPU="$(env_value GPU)";          GPU="${GPU:-0}";         export GPU
export CELERY="$(env_value CELERY)";   CELERY="${CELERY:-0}";   export CELERY
export MAIL="$(env_value MAIL)";       MAIL="${MAIL:-0}";       export MAIL
configure_sidecar_profiles

# Same file order as run.sh: the base stack first, then the GPU override, so a
# build validates against the exact service set that will run.
COMPOSE_BUILD_CMD=(docker compose --env-file "$ENV_FILE" -p "$PROJECT_NAME" -f "$COMPOSE_FILE" "${GPU_OVERRIDE_FILES[@]}" --profile "$MODE" "${EXTRA_PROFILES[@]}")

log_info "============================================"
log_info "HireSnap image build"
log_info "  mode     : $MODE"
log_info "  project  : $PROJECT_NAME"
log_info "  compose  : $COMPOSE_FILE"
log_info "  sidecars : $(describe_sidecar_profiles)"
log_info "  compute  : $(describe_gpu_state)"
log_info "============================================"

# 1. Validate the configuration before spending build minutes on it. The same
#    function run.sh uses, so a missing required variable is reported with the
#    same bounded diary code in both scripts.
log_step "validating compose configuration"
if ! compose_config_preflight "${COMPOSE_BUILD_CMD[@]}"; then
    exit 1
fi

# 2. The project name must match what run.sh will use. "compose config"
#    resolves the project name AFTER reading the "name:" field, the -p flag and
#    COMPOSE_PROJECT_NAME from the environment - so this also catches a
#    COMPOSE_PROJECT_NAME exported in the operator's shell that would silently
#    override both.
RESOLVED_PROJECT=$("${COMPOSE_BUILD_CMD[@]}" config --environment 2>/dev/null | awk -F= '$1 == "COMPOSE_PROJECT_NAME" {print $2}')
if [[ -n "$RESOLVED_PROJECT" && "$RESOLVED_PROJECT" != "$PROJECT_NAME" ]]; then
    log_error "compose resolves project '$RESOLVED_PROJECT' but run.sh expects '$PROJECT_NAME'"
    log_info  "unset COMPOSE_PROJECT_NAME in your shell, or align PROJECT_NAME in $ENV_FILE"
    exit 1
fi

# 3. Build through compose, not through "docker build": compose walks the same
#    service definitions the later "up" will use, so the build args - above all
#    REACT_APP_BASE_URL, which is baked into the bundle - are by construction the
#    ones that will run. A direct docker build lets the two diverge silently.
#
#    Only services with a `build:` block can be built. cache (redis) and storage
#    (minio) are pinned third-party images, so they are PULLED below instead of
#    built - passing them to `compose build` is a silent no-op ("No services to
#    build", exit 0), which looks like success and leaves the operator with an
#    un-pulled image discovered only at `up` time.
SERVICES=(backend frontend ml)
if [[ "${CELERY:-0}" == "1" ]]; then
    # The three workers and beat reuse the backend image (same Dockerfile, same
    # context), so "compose build" resolves them as cache hits on the SAME image.
    # They are listed anyway so the log shows what will actually run, and so a
    # per-service build failure is attributed to the right name instead of
    # surfacing later as "image not found" during up.
    SERVICES+=(celery-worker-agents celery-worker-maintenance celery-worker-ml celery-beat)
fi
# ml is NOT in this conditional list: it has its own Dockerfile (nvidia/cuda
# base, python3.9, torch cu116), so its build is a real one and never a cache
# hit on the backend image, and it is part of every stack. It sits last in the
# array above because everything before it is fast: all services are built in
# ONE "compose build" invocation, so the order only decides which failure the
# operator sees first.

log_step "building: ${SERVICES[*]}"
# --pull is deliberately NOT passed: it re-downloads the python/node base images
# on every build, which is minutes of network for a layer that changes twice a
# year. Use --no-cache when a stale base is actually the suspect.
"${COMPOSE_BUILD_CMD[@]}" build $NO_CACHE "${SERVICES[@]}"

# 4. Pre-pull the image-only sidecars that were switched on, so `up` does not
#    have to reach the registry while the operator is watching for a healthcheck
#    that is really a download. The tags are pinned in the compose files, so this
#    fetches exactly the version that will run (R:\GPTHub pins every third-party
#    image for the same reason: an unpinned tag makes two hosts run two stacks).
PULL_SERVICES=()
[[ "${CACHE:-1}"   == "1" ]] && PULL_SERVICES+=(cache)
[[ "${STORAGE:-1}" == "1" ]] && PULL_SERVICES+=(storage)
[[ "${MAIL:-0}"    == "1" ]] && PULL_SERVICES+=(mailpit)
if [[ "${#PULL_SERVICES[@]}" -gt 0 ]]; then
    log_step "pulling sidecar images: ${PULL_SERVICES[*]}"
    "${COMPOSE_BUILD_CMD[@]}" pull --quiet "${PULL_SERVICES[@]}" || \
        log_warning "sidecar image pull failed; 'up' will retry it"
fi

log_success "images built for project $PROJECT_NAME"
log_info  "start the stack: ./docker/run.sh --$MODE up"
