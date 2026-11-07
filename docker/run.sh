#!/bin/bash
# ============================================================================
# HireSnap - the only supported way to run the stack.
#
#   ./docker/run.sh --dev  up            # build-free start of the dev stack
#   ./docker/run.sh --prod up            # start the prod stack
#   ./docker/run.sh --prod down          # stop it (add --volumes to drop data)
#   ./docker/run.sh --dev  logs [SVC]    # follow logs (all services or one)
#   ./docker/run.sh --dev  ps            # container status
#   ./docker/run.sh --dev  exec backend bash   # shell into a service
#
# A bare "docker compose up" is NOT equivalent: it skips --env-file, the
# profiles, the sidecar flags and the preflight below, and it silently starts
# whatever the current shell happens to have exported.
#
# GPU=1 in the env file (or GPU=1 in the shell) merges docker-compose.gpu.yaml
# on top of the stack, which is what adds the NVIDIA device reservation to the
# ml sidecar. The sidecar itself is not optional and has no flag: every mode
# runs it, and GPU only decides whether it may use the card.
#
# Mirrors R:\GPTHub\docker\run.sh:
#   * cd to the script directory, so every relative path (env files, compose
#     files, ../infra mounts) is independent of the caller's CWD (GPTHub :49);
#   * one compose command array built once and reused for every action, so no
#     action can accidentally run against a different project or env file
#     (GPTHub :240);
#   * the nginx template fingerprint + force-recreate (GPTHub :94-103, :335-363);
#   * the env drift check as a WARNING, never a failure (GPTHub :109-135).
# ============================================================================
set -e

# WHY the absolute path is captured BEFORE the cd: show_help() reads the header
# comment out of the running file, and after "cd $(dirname $0)" a relative
# invocation path like ./docker/run.sh no longer resolves - sed then reports
# "can't read ./docker/run.sh" instead of printing the usage. Every documented
# invocation is relative (./docker/run.sh --dev up), so the failing case is the
# common one and the working case is the rare one.
SCRIPT_PATH="$0"
case "$SCRIPT_PATH" in
    /*) ;;                                        # already absolute
    *) SCRIPT_PATH="$(pwd)/$SCRIPT_PATH" ;;       # make it absolute first
esac

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
    # Print the header comment block only: it is the documentation, and keeping
    # the help text inside the file that implements it means the two cannot
    # drift apart.
    sed -n '2,/^# ====/{/^# ====/d;p;}' "$SCRIPT_PATH" | sed 's/^# \{0,1\}//'
    exit 0
}

MODE="dev"
MODE_EXPLICIT=false
ACTION="up"
STATE_DIR=".run-state"
NGINX_TEMPLATES_DIR="../infra/nginx/templates"
GATEWAY_SERVICE="gateway"

# --- argument parsing -------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dev)  MODE="dev";  MODE_EXPLICIT=true; shift ;;
        --prod) MODE="prod"; MODE_EXPLICIT=true; shift ;;
        -h|--help) show_help ;;
        up|down|logs|ps|exec)
            ACTION="$1"; shift ;;
        --)
            shift; break ;;
        -*)
            # An unknown flag is a typo, and a typo must not silently start a
            # stack with default settings.
            log_error "unknown option: $1"
            log_info  "run '$0 --help' for usage"
            exit 2 ;;
        *)
            break ;;
    esac
done
# Everything left over belongs to the compose subcommand (a service name for
# logs, a service + command for exec).
ACTION_ARGS=("$@")

# --- resolve the mode into concrete files -----------------------------------
if [[ "$MODE" == "dev" ]]; then
    COMPOSE_FILE="docker-compose.dev.yaml"
else
    COMPOSE_FILE="docker-compose.yaml"
fi
ENV_FILE=".env.${MODE}"

if [[ ! -f "$COMPOSE_FILE" ]]; then
    log_error "compose file not found: docker/$COMPOSE_FILE"
    exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
    log_error "environment file not found: docker/$ENV_FILE"
    log_info  "create it without leaking values: python docker/sync_env.py --mode $MODE"
    exit 1
fi

# Read ONE non-secret key from the env file.
#   - The sidecar flags and the project name must come from the FILE that
#     Compose is about to interpolate, not from whatever the caller's shell
#     exported. A flag that looks set in .env.dev but is silently ignored is a
#     worse bug than a flag that does not exist.
#   - This helper is only ever called for non-secret keys; it prints the value,
#     so calling it on a password would leak it to the terminal and the shell
#     history.
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
if [[ -z "$PROJECT_NAME" ]]; then
    PROJECT_NAME="hiresnap-$MODE"
    log_warning "PROJECT_NAME is not set in $ENV_FILE; using $PROJECT_NAME"
fi

# Cross-check the mode recorded in the file against the mode that was asked for.
# A .env.prod carried into a dev start would put DEBUG=FALSE and INIT_DB=false
# into a stack where the developer expects a reloading server - the failure
# looks like "my code changes are ignored", not like a config error.
COMPOSE_MODE="$(env_value COMPOSE_MODE)"
if [[ -n "$COMPOSE_MODE" && "$COMPOSE_MODE" != "$MODE" ]]; then
    log_warning "$ENV_FILE declares COMPOSE_MODE=$COMPOSE_MODE but --$MODE was requested"
fi

APP_DOMAIN="$(env_value APP_DOMAIN)"
APP_DOMAIN="${APP_DOMAIN:-localhost}"
NGINX_PUBLISH_HTTP="$(env_value NGINX_PUBLISH_HTTP)"
NGINX_PUBLISH_HTTP="${NGINX_PUBLISH_HTTP:-8088}"
BACKEND_PUBLISH_PORT="$(env_value BACKEND_PUBLISH_PORT)"
BACKEND_PUBLISH_PORT="${BACKEND_PUBLISH_PORT:-8080}"
FRONTEND_PUBLISH_PORT="$(env_value FRONTEND_PUBLISH_PORT)"
FRONTEND_PUBLISH_PORT="${FRONTEND_PUBLISH_PORT:-3000}"

# Sidecar flags, for the reason above: from the file, not from the shell.
export CACHE="$(env_value CACHE)";     CACHE="${CACHE:-1}";     export CACHE
export STORAGE="$(env_value STORAGE)"; STORAGE="${STORAGE:-1}"; export STORAGE
export CELERY="$(env_value CELERY)";   CELERY="${CELERY:-0}";   export CELERY
export MAIL="$(env_value MAIL)";       MAIL="${MAIL:-0}";       export MAIL
# GPU is a flag but NOT a profile: it decides whether the always-present ml
# sidecar gets the device reservation, via an override file merged below. The
# default is 0 for the same reason as the other expensive switches - a host
# without the NVIDIA runtime cannot start a container that asks for a GPU.
export GPU="$(env_value GPU)";          GPU="${GPU:-0}";         export GPU
# Ports printed in the dev summary; read from the file for the same reason as
# everything above (a shell value would disagree with what compose publishes).
# There is no ML_PUBLISH_PORT: the sidecar has no host port in any mode, so a
# dev URL for it would be a lie.
MAILPIT_PUBLISH_PORT="$(env_value MAILPIT_PUBLISH_PORT)"; export MAILPIT_PUBLISH_PORT
# Also exports the WIRING (CACHE__BACKEND, CELERY__ENABLED, ML__SERVICE_URL,
# MAIL__HOST, ...) for every sidecar that is on. A shell export beats --env-file
# in compose interpolation, which is why the manifest keys are empty by default:
# empty means "let this function decide", so a flag and its wiring cannot
# contradict each other (see sidecar_profiles.sh).
configure_sidecar_profiles

# The single compose invocation every action is derived from. --profile "$MODE"
# activates the base services (they declare profiles: ["dev"] / ["prod"], so
# without it "up" would start nothing at all).
#
# The GPU override is spliced in BEFORE the profiles, because compose reads -f
# in order and the later files win: docker-compose.yaml defines the ml service,
# docker-compose.gpu.yaml adds a device reservation to it. Applying them the
# other way round would define the service after the override and drop the
# reservation without a word.
COMPOSE_CMD=(docker compose --env-file "$ENV_FILE" -p "$PROJECT_NAME" -f "$COMPOSE_FILE" "${GPU_OVERRIDE_FILES[@]}" --profile "$MODE" "${EXTRA_PROFILES[@]}")

# --- preflight helpers ------------------------------------------------------
# Fingerprint of infra/nginx/templates/*, so a changed vhost is actually served.
# The nginx template directory is a BIND MOUNT: editing a template changes a
# file the running container can already see, but nginx only re-reads
# /etc/nginx/conf.d at start, so without this the stack happily serves the OLD
# vhost until someone thinks to restart it. Hashing the file list (not just the
# contents) also catches a rename. GPTHub run.sh:94-103 computes it the same way.
compute_templates_fingerprint() {
    if [[ ! -d "$NGINX_TEMPLATES_DIR" ]]; then
        echo ""
        return 0
    fi
    find "$NGINX_TEMPLATES_DIR" -type f | sort | while IFS= read -r file_path; do
        sha256sum "$file_path"
    done | sha256sum | awk '{print $1}'
}

gateway_service_exists() {
    "${COMPOSE_CMD[@]}" config --services 2>/dev/null | grep -qx "$GATEWAY_SERVICE"
}

# Compare the KEY SETS of .env.example and .env.<mode>.
#
# A key added to the template but not carried into the deployment file makes the
# service fall back to a hardcoded default - silently, and only for the people
# whose deployment file is behind. This warns instead of failing because an
# extra key in the template is not a reason to refuse to run a stack.
#
# It cannot live in CI: .env.dev/.env.prod are gitignored, so the only machine
# that has them is the deployment host (the same conclusion as GPTHub
# run.sh:109-135, which also explains why the check is here rather than in a
# Makefile target nobody remembers to call).
check_env_drift() {
    local example=".env.example"
    [[ -f "$example" ]] || return 0

    local missing
    missing=$(comm -23 \
        <(grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' "$example" | tr -d '=' | sort -u) \
        <(grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE" | tr -d '=' | sort -u))

    if [[ -n "$missing" ]]; then
        log_warning "$ENV_FILE is missing keys declared in $example; those settings will fall back to code defaults:"
        echo "$missing" | sed 's/^/    /'
        log_warning "fix with: python docker/sync_env.py --mode $MODE   (appends only, never rotates)"
    fi
}

# --- actions ----------------------------------------------------------------
action_up() {
    log_info "============================================"
    log_info "HireSnap start"
    log_info "  mode      : $MODE"
    log_info "  project   : $PROJECT_NAME"
    log_info "  compose   : $COMPOSE_FILE"
    log_info "  env file  : $ENV_FILE"
    log_info "  sidecars  : $(describe_sidecar_profiles)"
    log_info "  compute   : $(describe_gpu_state)"
    if [[ "${GPU_OVERRIDE_FILES+set}" == "set" && "${#GPU_OVERRIDE_FILES[@]}" -gt 0 ]]; then
        log_info "  gpu file  : docker-compose.gpu.yaml"
    fi
    log_info "============================================"

    check_env_drift

    # Preflight BEFORE any container is created: a missing required variable or
    # a malformed compose file must fail here, with a bounded diagnostic, not
    # halfway through a rollout.
    log_step "validating compose configuration"
    if ! compose_config_preflight "${COMPOSE_CMD[@]}"; then
        exit 1
    fi

    mkdir -p "$STATE_DIR"
    local fingerprint state_file force_recreate=false
    fingerprint=$(compute_templates_fingerprint)
    state_file="$STATE_DIR/nginx-templates-$MODE.sha256"

    if [[ -n "$fingerprint" ]] && gateway_service_exists; then
        if [[ -f "$state_file" ]]; then
            if [[ "$(cat "$state_file")" != "$fingerprint" ]]; then
                force_recreate=true
                log_warning "infra/nginx/templates/* changed; recreating $GATEWAY_SERVICE so the new vhost is loaded"
            fi
        elif "${COMPOSE_CMD[@]}" ps -q "$GATEWAY_SERVICE" 2>/dev/null | grep -q .; then
            # No state file but a running gateway: the fingerprint was never
            # recorded for this mode, so the running container may predate an
            # edit. Recreate once, then the state file keeps it honest.
            force_recreate=true
            log_warning "no template fingerprint recorded for $MODE but $GATEWAY_SERVICE is running; resynchronising"
        fi
    fi

    if [[ "$force_recreate" == true ]]; then
        # --no-deps so recreating the gateway does not bounce backend/frontend.
        "${COMPOSE_CMD[@]}" up -d --force-recreate --no-deps "$GATEWAY_SERVICE"
    fi

    log_step "starting containers"
    "${COMPOSE_CMD[@]}" up -d "${ACTION_ARGS[@]}"

    if [[ -n "$fingerprint" ]]; then
        echo "$fingerprint" > "$state_file"
    fi

    print_urls
    print_schema_init_hint
}

action_down() {
    log_info "stopping $PROJECT_NAME"
    # Volumes are NOT removed by default, and the two kinds are different on
    # purpose: the DATABASE lives in the named volume mysql_data
    # (mysql_data_dev in dev), not in infra/mysql_data - a Windows bind-mounted
    # datadir is an InnoDB corruption risk, so it is a volume. infra/storage
    # holds uploads as a bind mount because a human must be able to see them.
    # 'down -v' therefore destroys the database; dropping it is a deliberate
    # act, hence the explicit passthrough.
    "${COMPOSE_CMD[@]}" down "${ACTION_ARGS[@]}"
    log_success "stopped"
}

action_logs() {
    if [[ "${#ACTION_ARGS[@]}" -gt 0 ]]; then
        log_info "logs: ${ACTION_ARGS[0]} (Ctrl+C to stop following)"
    else
        log_info "logs: all services (Ctrl+C to stop following)"
    fi
    "${COMPOSE_CMD[@]}" logs -f "${ACTION_ARGS[@]}"
}

action_ps() {
    "${COMPOSE_CMD[@]}" ps
}

action_exec() {
    if [[ "${#ACTION_ARGS[@]}" -eq 0 ]]; then
        log_error "exec needs a service name, e.g. $0 --$MODE exec backend bash"
        exit 2
    fi
    "${COMPOSE_CMD[@]}" exec "${ACTION_ARGS[@]}"
}

# The schema loader in infra/mysql/init/00-init.sh runs from
# /docker-entrypoint-initdb.d, and the mysql image executes that directory ONLY
# when the data directory is EMPTY - i.e. on the very first start of a fresh
# volume. A developer who edits HireSnap.sql and re-runs 'up' therefore sees no
# change and concludes the loader is broken. Say it out loud instead.
print_schema_init_hint() {
    log_info "schema : loaded from backend/HireSnap.sql ONLY on a fresh MySQL volume"
    log_info "         (existing volume => loader is a documented no-op; see docs/RUNBOOK.md)"
    log_info "         re-initialise: dump first, then '$0 --$MODE down -v' and 'up'"
}

print_urls() {
    echo ""
    log_success "compose up finished; container health was NOT waited on"
    log_info  "gateway : http://${APP_DOMAIN}:${NGINX_PUBLISH_HTTP}"
    if [[ "$MODE" == "dev" ]]; then
        log_info "api     : http://localhost:${BACKEND_PUBLISH_PORT}   (dev only)"
        log_info "api docs: http://localhost:${BACKEND_PUBLISH_PORT}/server/docs"
        log_info "spa     : http://localhost:${FRONTEND_PUBLISH_PORT}   (dev only, CRA)"
        # No ml URL: the sidecar publishes no host port in any mode, so the only
        # honest way to reach it is from inside the project.
        log_info "ml      : no host port; probe with '$0 --$MODE exec ml curl -fsS http://127.0.0.1:8080/health'"
        [[ "${MAIL:-0}" == "1" ]] && log_info "mail    : http://localhost:${MAILPIT_PUBLISH_PORT:-8025}   (dev only, Mailpit inbox)"
    fi
    log_info  "health  : docker compose -f docker/$COMPOSE_FILE ps"
    # A changed flag only takes effect for containers created AFTER it, and the
    # wiring is part of the container environment. Saying so beats an operator
    # wondering why CACHE=0 changed nothing. compose recreates a container whose
    # environment changed, but only the ones the current invocation selects -
    # which is why the flags live in the compose command line rather than in a
    # restart instruction.
    if [[ "${CELERY:-0}" != "1" ]]; then
        log_info "jobs    : inline in the API process (CELERY=0); set CELERY=1 for workers"
    fi
    echo ""
    log_info "follow logs : $0 --$MODE logs"
    log_info "stop        : $0 --$MODE down"
}

case "$ACTION" in
    up)    action_up ;;
    down)  action_down ;;
    logs)  action_logs ;;
    ps)    action_ps ;;
    exec)  action_exec ;;
    *)     log_error "unknown action: $ACTION"; exit 2 ;;
esac
