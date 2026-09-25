#!/usr/bin/env bash
set -euo pipefail
USER_NAME=skatai-main-agent
NEW_HOME=/var/lib/skatai-main-agent/codex-home

if ! getent passwd "$USER_NAME" >/dev/null 2>&1; then
  if [ "$(id -u)" -eq 0 ]; then
    useradd --system --no-create-home --home-dir "$NEW_HOME" --shell /usr/sbin/nologin "$USER_NAME"
  else
    sudo -n useradd --system --no-create-home --home-dir "$NEW_HOME" --shell /usr/sbin/nologin "$USER_NAME"
  fi
else
  if [ "$(id -u)" -eq 0 ]; then
    usermod --home "$NEW_HOME" "$USER_NAME"
  else
    sudo -n usermod --home "$NEW_HOME" "$USER_NAME"
  fi
fi

UID_NUM="$(id -u "$USER_NAME")"
GID_NUM="$(id -g "$USER_NAME")"
BASE_DIR="$(dirname "$NEW_HOME")"
OUTBOX="$BASE_DIR/outbox"
TMP_DIR="$BASE_DIR/tmp"
WORK_DIR="$BASE_DIR/work"
if [ "$(id -u)" -eq 0 ]; then
  install -d -m 700 -o "$UID_NUM" -g "$GID_NUM" "$BASE_DIR"
  install -d -m 700 -o "$UID_NUM" -g "$GID_NUM" "$NEW_HOME"
  install -d -m 700 -o "$UID_NUM" -g "$GID_NUM" "$OUTBOX"
  install -d -m 700 -o "$UID_NUM" -g "$GID_NUM" "$TMP_DIR"
  install -d -m 700 -o "$UID_NUM" -g "$GID_NUM" "$WORK_DIR"
else
  sudo -n install -d -m 700 -o "$UID_NUM" -g "$GID_NUM" "$BASE_DIR"
  sudo -n install -d -m 700 -o "$UID_NUM" -g "$GID_NUM" "$NEW_HOME"
  sudo -n install -d -m 700 -o "$UID_NUM" -g "$GID_NUM" "$OUTBOX"
  sudo -n install -d -m 700 -o "$UID_NUM" -g "$GID_NUM" "$TMP_DIR"
  sudo -n install -d -m 700 -o "$UID_NUM" -g "$GID_NUM" "$WORK_DIR"
fi

SUDO_INFO="$(sudo -n -l -U "$USER_NAME" 2>&1 || true)"
if printf '%s\n' "$SUDO_INFO" | grep -Eq '\(ALL\).*NOPASSWD|may run the following commands'; then
  echo "EXECUTOR_USER_HAS_SUDO" >&2
  exit 1
fi

for forbidden in auth.json .runpod .aws; do
  if [ -e "$NEW_HOME/$forbidden" ]; then
    echo "EXECUTOR_HOME_FORBIDDEN_STATE:$forbidden" >&2
    exit 1
  fi
done

MODE="$(stat -c '%a' "$NEW_HOME")"
OWNER="$(stat -c '%u:%g' "$NEW_HOME")"
if [ "$MODE" != "700" ] || [ "$OWNER" != "$UID_NUM:$GID_NUM" ]; then
  echo "EXECUTOR_HOME_PERMISSIONS_INVALID:$MODE:$OWNER" >&2
  exit 1
fi

getent passwd "$USER_NAME"
