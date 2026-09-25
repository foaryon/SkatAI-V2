#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
CONTROL_ROOT=/var/lib/skatai-main-controller
CONTROLLER="$HERE/openai_platform_main_controller.py"
PREFLIGHT="$HERE/check_main_startup.py"
SECRET_PREP="$HERE/prepare_runtime_secrets.py"
PIDFILE="$CONTROL_ROOT/controller.pid"
LOG="$CONTROL_ROOT/guardian.log"
LOCK=/run/lock/skatai-openai-platform-guardian.lock
mkdir -p /run/lock
umask 077
exec 9>"$LOCK" || exit 1
if ! flock -n 9; then exit 0; fi
trap 'exit 0' INT TERM
log() { printf '%s %s\n' "$(date -Is)" "$*" >>"$LOG"; }
alive() {
  local p="" a=""
  [ -r "$PIDFILE" ] || return 1
  p="$(cat "$PIDFILE" 2>/dev/null || true)"
  [ -n "$p" ] || return 1
  kill -0 "$p" 2>/dev/null || return 1
  a="$(ps -p "$p" -o args= 2>/dev/null || true)"
  case "$a" in *"$CONTROLLER"*) return 0 ;; *) return 1 ;; esac
}
backoff=2
log "trusted platform guardian start"
while true; do
  if ! python3 "$PREFLIGHT" >>"$CONTROL_ROOT/boot.log" 2>&1; then
    log "MAIN preflight not ready; guardian exiting fail-closed"
    exit 0
  fi
  if alive; then backoff=2; sleep 10; continue; fi
  python3 "$SECRET_PREP" >>"$CONTROL_ROOT/secret-prep.log" 2>&1 || {
    log "runtime secret preparation failed"
    sleep 30
    continue
  }
  if [ ! -s /run/skatai-v2-secrets/openai_agents_api_key ]; then log "application API key unavailable"; sleep 30; continue; fi
  if [ ! -s /run/skatai-v2-secrets/openai_executor_api_key ]; then log "executor key unavailable"; sleep 30; continue; fi
  rm -f "$PIDFILE" 2>/dev/null || true
  nohup python3 "$CONTROLLER" </dev/null >>"$CONTROL_ROOT/boot.log" 2>&1 &
  log "platform controller launch requested"
  sleep "$backoff"
  if alive; then log "platform controller verified pid=$(cat "$PIDFILE")"; backoff=2; continue; fi
  if [ "$backoff" -lt 60 ]; then backoff=$((backoff*2)); [ "$backoff" -gt 60 ] && backoff=60; fi
done
