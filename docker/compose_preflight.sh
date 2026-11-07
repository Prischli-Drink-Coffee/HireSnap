#!/bin/bash
# Shared read-only preflight for the HireSnap compose stack.
#
# Mirrors R:\GPTHub\docker\compose_preflight.sh in intent:
#
#   * "docker compose config" echoes the FULLY INTERPOLATED environment. That
#     output contains every dotenv value - database passwords, storage keys.
#     It must never reach a terminal, a log or a CI artefact, so it is captured
#     into a local variable and never printed, not even when parsing fails
#     (GPTHub compose_preflight.sh:1-3, :19-27).
#   * The caller gets a bounded diagnostic CODE instead of the parser's own
#     message. Docker's error strings embed variable names, file paths and
#     sometimes values; a whitelist of source-controlled key names is the only
#     thing allowed to cross this boundary (GPTHub compose_preflight.sh:5-17).
#     Anything unrecognised fails closed as "invalid_configuration".
#
# Sourced by run.sh and build.sh; not executed on its own.

# Keys a deployment must supply. Keep this list in sync with the ":?" markers in
# the compose files and with the "REQUIRED (:?)" comments in .env.example.
# scripts/check_env_parity.py compares the three env files and
# scripts/check_compose_parity.py the two compose files; neither can check this
# list (it is shell), so it is a review item. A key missing here is still
# classified, just generically
# (code=invalid_configuration) instead of by name.
#
# These are only ever PRINTED as names, never with their values: the list is a
# source-controlled whitelist, which is exactly what makes it safe to print.
HIRESNAP_REQUIRED_KEYS=(
    DB__USER
    DB__PASSWORD
    MYSQL_ROOT_PASSWORD
    MYSQL_PASSWORD
    PUBLIC_BASE_URL
    STORAGE__ACCESS_KEY
    STORAGE__SECRET_KEY
)

# Print a bounded diagnostic for a captured compose failure.
# Arguments: the captured stderr+stdout of the failing command.
compose_preflight_error() {
    local diagnostic="${1:-}"
    local key
    for key in "${HIRESNAP_REQUIRED_KEYS[@]}"; do
        # Docker's wording, matched exactly; it is stable across compose v2.
        if [[ "${diagnostic}" == *"required variable ${key} is missing a value"* ]]; then
            printf '[ERROR] Compose configuration validation failed; code=missing_required_variable variable=%s\n' "${key}" >&2
            return 0
        fi
    done
    printf '[ERROR] Compose configuration validation failed; code=invalid_configuration\n' >&2
}

# Run "<compose...> config --quiet" and classify a failure.
# Returns 0 when the configuration resolves, 1 otherwise (already reported).
compose_config_preflight() {
    local diagnostic
    # Capture both streams: even a partial parse can print interpolated values.
    if diagnostic=$("$@" config --quiet 2>&1); then
        return 0
    fi
    compose_preflight_error "${diagnostic}"
    return 1
}
