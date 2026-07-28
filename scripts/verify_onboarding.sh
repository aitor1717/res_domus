#!/usr/bin/env bash
# Walks the exact onboarding steps documented in README.md, from a truly
# fresh copy of the repo in an isolated temp directory - not the developer's
# own checkout, no pre-existing config.py, no pre-existing data/.
#
# This is the "does this actually work for someone who just downloaded the
# repo" check. It's slow (venv build, pip install, optionally a Docker
# build) so it's not part of the pytest suite - run it by hand before a
# release/portfolio update:
#
#   scripts/verify_onboarding.sh            # both paths (default)
#   scripts/verify_onboarding.sh docker      # Docker path only
#   scripts/verify_onboarding.sh pip         # non-Docker path only
#
# Each path gets its own fresh snapshot/temp dir - a real user only ever
# takes one path, and sharing a data/ dir between two runs in the same
# invocation would let one path's state (e.g. a saved API key) leak into
# the other's "fresh install" checks.
#
# Requires: python3, and for the docker path, `docker compose`.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-both}"
WORKDIRS=()

log() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

cleanup() {
    log "Cleaning up"
    for wd in "${WORKDIRS[@]:-}"; do
        [ -z "$wd" ] && continue
        if [ -f "$wd/docker-compose.yml" ]; then
            # --rmi local only removes images this compose project built
            # itself, not the pulled python:3.12-slim base image.
            (cd "$wd" && docker compose down --rmi local >/dev/null 2>&1 || true)
        fi
        rm -rf "$wd"
    done
    if [ -n "${FLASK_PID:-}" ] && kill -0 "$FLASK_PID" 2>/dev/null; then
        kill "$FLASK_PID" 2>/dev/null || true
        wait "$FLASK_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

make_snapshot() {
    local wd
    wd="$(mktemp -d /tmp/res_domus_onboarding_XXXXXX)"
    WORKDIRS+=("$wd")
    # git ls-files (tracked + untracked-but-not-gitignored) is exactly what a
    # fresh `git clone` + this session's uncommitted edits would contain - a
    # closer approximation of "what ships next" than `git clone` alone, which
    # would only see the last commit.
    (cd "$REPO_ROOT" && git ls-files --cached --others --exclude-standard -z) \
        | tar --null -C "$REPO_ROOT" -T - -cf - \
        | tar -xf - -C "$wd"
    echo "$wd"
}

wait_for_http() {
    local url="$1" timeout="${2:-60}" waited=0
    while ! curl -sf -o /dev/null "$url" 2>/dev/null; do
        sleep 1
        waited=$((waited + 1))
        if [ "$waited" -ge "$timeout" ]; then
            echo "FAIL: $url never became ready after ${timeout}s"
            return 1
        fi
    done
}

check_api_key_roundtrip() {
    local base_url="$1"
    log "Checking Settings -> AI Manager key round-trip (no real Anthropic call)"
    local before after
    before=$(curl -s "$base_url/api/settings/api-key")
    echo "  before: $before"
    echo "$before" | grep -q '"configured":false' \
        || { echo "  FAIL: fresh install should report AI features as not configured, got: $before"; return 1; }
    curl -sf -X POST "$base_url/api/settings/api-key" \
        -H "Content-Type: application/json" \
        -d '{"api_key":"sk-ant-onboarding-verification-dummy"}' >/dev/null
    after=$(curl -s "$base_url/api/settings/api-key")
    echo "  after:  $after"
    echo "$after" | grep -q '"configured":true' \
        && echo "  OK: key saved via Settings UI activates without a restart" \
        || { echo "  FAIL: key did not register as configured"; return 1; }
}

run_pip_path() {
    local wd; wd="$(make_snapshot)"
    log "[pip path] Snapshot at $wd - cp config.example.py, venv, pip install"
    cp "$wd/app/config.example.py" "$wd/app/config.py"
    python3 -m venv "$wd/.venv-onboarding"
    "$wd/.venv-onboarding/bin/pip" install -q -r "$wd/app/requirements.txt"

    log "[pip path] python3 app/scripts/init_db.py"
    (cd "$wd" && python3 app/scripts/init_db.py)
    [ -f "$wd/data/res_domus.db" ] || { echo "FAIL: data/res_domus.db not created"; return 1; }

    log "[pip path] flask --app app run --debug"
    (cd "$wd/app" && env -u ANTHROPIC_API_KEY -u BASIC_AUTH_USER -u BASIC_AUTH_PASS -u NTFY_TOPIC -u DEMO_MODE \
        "$wd/.venv-onboarding/bin/python3" -m flask --app app run --port 47500 \
        > "$wd/flask_onboarding.log" 2>&1 &)
    FLASK_PID=$(pgrep -f "port 47500" | head -1)

    wait_for_http "http://127.0.0.1:47500/" 30 || { cat "$wd/flask_onboarding.log"; return 1; }
    echo "  OK: server responds on first run with no data and a blank API key"

    check_api_key_roundtrip "http://127.0.0.1:47500"

    kill "$FLASK_PID" 2>/dev/null || true
    wait "$FLASK_PID" 2>/dev/null || true
    unset FLASK_PID
    echo "PIP PATH: PASS"
}

run_docker_path() {
    local wd; wd="$(make_snapshot)"
    log "[docker path] Snapshot at $wd - cp config.example.py, python3 app/scripts/init_db.py"
    cp "$wd/app/config.example.py" "$wd/app/config.py"
    (cd "$wd" && python3 app/scripts/init_db.py)

    log "[docker path] docker compose up --build -d"
    (cd "$wd" && env -u ANTHROPIC_API_KEY -u BASIC_AUTH_USER -u BASIC_AUTH_PASS -u NTFY_TOPIC \
        docker compose up --build -d)

    wait_for_http "http://localhost:5000/" 180 || {
        (cd "$wd" && docker compose logs)
        return 1
    }
    echo "  OK: container serves the app on first boot"

    check_api_key_roundtrip "http://localhost:5000"

    (cd "$wd" && docker compose down --rmi local)
    echo "DOCKER PATH: PASS"
}

case "$MODE" in
    pip)    run_pip_path ;;
    docker) run_docker_path ;;
    both)   run_pip_path; run_docker_path ;;
    *) echo "Usage: $0 [docker|pip|both]"; exit 2 ;;
esac

log "Onboarding verification complete"
