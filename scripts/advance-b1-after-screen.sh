#!/usr/bin/env bash
set -euo pipefail

SCREEN_JOB_DIR=/workspace/sentinelx-host/longjobs/b1-screen-20260923T201655Z-56018
STATUS_FILE=$SCREEN_JOB_DIR/status
CONFIRM_WRAPPER=/workspace/skatai-v2/scripts/run-b1-local-confirmation-from-screen.sh

while true; do
  status=$(cat "$STATUS_FILE" 2>/dev/null || echo UNKNOWN)
  case "$status" in
    SUCCEEDED)
      echo "screen_succeeded; applying clustered decision and conditional confirmation"
      exec "$CONFIRM_WRAPPER"
      ;;
    FAILED)
      echo "screen_failed; confirmation not started" >&2
      exit 1
      ;;
    RUNNING|STARTING|UNKNOWN)
      sleep 15
      ;;
    *)
      echo "unexpected screen status: $status" >&2
      exit 2
      ;;
  esac
done
