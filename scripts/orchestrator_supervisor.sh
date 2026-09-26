#!/usr/bin/env bash
set -u
TRUST=/opt/skatai-orchestrator/current
CONTROL=/var/lib/skatai-orchestrator
LOCK=/run/lock/skatai-v2-orchestrator-supervisor.lock
mkdir -p "$CONTROL" /run/lock
exec 9>"$LOCK" || exit 1
flock -n 9 || exit 0
trap 'exit 0' INT TERM
backoff=2
while true; do
  if pgrep -f '^python3 /opt/skatai-orchestrator/current/control_plane\.py$' >/dev/null 2>&1; then
    sleep 10
    backoff=2
    continue
  fi
  /usr/bin/python3 /opt/skatai-orchestrator/current/prepare_shared_runtime_secrets.py >>"$CONTROL/secret-prep.log" 2>&1 || true
  if [ ! -s /run/skatai-v2-secrets/openai_agents_api_key ]; then
    printf '%s openai agents key unavailable\n' "$(date -u +%FT%TZ)" >>"$CONTROL/supervisor.log"
    sleep 30
    continue
  fi
  : >"$CONTROL/controller-boot.log"
  SKATAI_ORCHESTRATOR_TRUSTED_ROOT="$TRUST" PYTHONPATH="$TRUST"     /usr/bin/python3 "$TRUST/control_plane.py" >>"$CONTROL/controller-boot.log" 2>&1
  rc=$?
  if grep -q 'OpenAI HTTP 400:' "$CONTROL/controller-boot.log"; then
    printf '%s non-retriable agent configuration error; supervisor stopped\n' "$(date -u +%FT%TZ)" >>"$CONTROL/supervisor.log"
    exit 1
  fi
  printf '%s controller exit rc=%s restart_s=%s\n' "$(date -u +%FT%TZ)" "$rc" "$backoff" >>"$CONTROL/supervisor.log"
  sleep "$backoff"
  if [ "$backoff" -lt 60 ]; then backoff=$((backoff*2)); [ "$backoff" -gt 60 ] && backoff=60; fi
done
