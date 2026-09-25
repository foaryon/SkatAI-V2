#!/usr/bin/env bash
set -u
umask 027

BASE=/workspace/sentinelx-host
REPO=/workspace/skatai-v2
HUNTER="$REPO/scripts/runpod_cpu_upgrade_hunter.py"
SECRET_PREP=/workspace/openai-agent/prepare-runtime-secrets.py
LOG="$BASE/logs/runpod-cpu-hunter-guardian.log"
LOCK=/run/lock/skatai-runpod-cpu-hunter-guardian.lock
POLL_SECONDS=60
TARGETS="16/32,8/16"
COMPLETE_MARKER="$BASE/runpod-cpu-upgrade-hunter/UPGRADE_COMPLETE.json"

mkdir -p "$BASE/logs" /run/lock "$BASE/runpod-cpu-upgrade-hunter" 2>/dev/null || true

log_event() {
  printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >>"$LOG" 2>/dev/null || true
}

exec 9>"$LOCK" || exit 1
if ! flock -n 9; then
  exit 0
fi

child=""
cleanup() {
  if [ -n "$child" ] && kill -0 "$child" 2>/dev/null; then
    kill -TERM "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
  fi
}
trap 'cleanup; exit 0' INT TERM

failures=0
while true; do
  # A successful handoff is terminal for this upgrade campaign. Do not keep
  # polling RunPod just to rediscover the pod we are already running on.
  # Removing the durable marker intentionally re-enables a future campaign.
  if [ -f "$COMPLETE_MARKER" ]; then
    sleep 3600
    continue
  fi

  if [ ! -f "$HUNTER" ]; then
    log_event "hunter-missing path=$HUNTER retry_s=30"
    sleep 30
    continue
  fi

  started="$(date +%s)"
  if [ -f "$SECRET_PREP" ]; then
    python3 "$SECRET_PREP" >/dev/null 2>&1 || true
  fi
  claim_args=()
  mode=watch-only
  AUTOCLAIM_MARKER="$BASE/runpod-cpu-upgrade-hunter/ENABLE_AUTOCLAIM"
  if [ -s /run/skatai-v2-secrets/runpod_deploy_api_key ] && [ -f "$AUTOCLAIM_MARKER" ]; then
    claim_args=(--claim)
    mode=claim-authorized
  fi
  log_event "hunter-start mode=$mode targets=$TARGETS poll_s=$POLL_SECONDS"
  python3 "$HUNTER" "${claim_args[@]}" --targets "$TARGETS" --poll-seconds "$POLL_SECONDS" &
  child=$!
  wait "$child"
  rc=$?
  child=""

  lived=$(( $(date +%s) - started ))
  if [ "$lived" -ge 300 ]; then
    failures=0
    delay=10
  else
    failures=$((failures + 1))
    delay=$((10 * failures))
    [ "$delay" -gt 120 ] && delay=120
  fi
  log_event "hunter-exit rc=$rc lived_s=$lived restart_s=$delay failures=$failures"
  sleep "$delay"
done
