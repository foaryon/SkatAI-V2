#!/usr/bin/env bash
set -euo pipefail

SOURCE=
REMOTE=
STATUS_FILE=
INTERVAL=60

while [ "$#" -gt 0 ]; do
  case "$1" in
    --source) SOURCE=$2; shift 2 ;;
    --remote) REMOTE=$2; shift 2 ;;
    --status-file) STATUS_FILE=$2; shift 2 ;;
    --interval) INTERVAL=$2; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -n "$SOURCE" ] && [ -n "$REMOTE" ] && [ -n "$STATUS_FILE" ] || {
  echo "source, remote, and status-file are required" >&2
  exit 2
}

LAST_SHA=
sync_once() {
  [ -f "$SOURCE" ] || return 0
  local sha
  sha=$(sha256sum "$SOURCE" | cut -d' ' -f1)
  [ "$sha" != "$LAST_SHA" ] || return 0
  rclone copyto "$SOURCE" "$REMOTE" \
    --s3-provider Other --s3-env-auth \
    --s3-endpoint https://fsn1.your-objectstorage.com --s3-region fsn1
  local remote_sha
  remote_sha=$(rclone cat "$REMOTE" \
    --s3-provider Other --s3-env-auth \
    --s3-endpoint https://fsn1.your-objectstorage.com --s3-region fsn1 \
    | sha256sum | cut -d' ' -f1)
  test "$sha" = "$remote_sha"
  LAST_SHA=$sha
  printf '%s checkpoint_mirrored sha256=%s\n' "$(date -u +%FT%TZ)" "$sha"
}

while true; do
  sync_once
  status=$(cat "$STATUS_FILE" 2>/dev/null || echo UNKNOWN)
  case "$status" in
    RUNNING|STARTING|UNKNOWN) sleep "$INTERVAL" ;;
    *)
      sync_once
      printf '%s watcher_exit status=%s\n' "$(date -u +%FT%TZ)" "$status"
      exit 0
      ;;
  esac
done
