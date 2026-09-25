#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
CONTROL_ROOT=/var/lib/skatai-main-controller
PREFLIGHT="$HERE/check_main_startup.py"
GUARDIAN="$HERE/openai_platform_guardian.sh"
PREP_EXECUTOR_USER="$HERE/prepare_main_executor_user.sh"
SECRET_PREP="$HERE/prepare_runtime_secrets.py"

[ "$(id -u)" -eq 0 ] || exit 0
[ -d "$CONTROL_ROOT" ] || exit 0

if ! bash "$PREP_EXECUTOR_USER" >>"$CONTROL_ROOT/boot.log" 2>&1; then
  exit 0
fi

if ! python3 "$PREFLIGHT" >>"$CONTROL_ROOT/boot.log" 2>&1; then
  exit 0
fi

python3 "$SECRET_PREP" >>"$CONTROL_ROOT/secret-prep.log" 2>&1 || exit 0

nohup "$GUARDIAN" </dev/null >>"$CONTROL_ROOT/guardian-boot.log" 2>&1 &
echo $! >"$CONTROL_ROOT/guardian-boot.pid"
chmod 600 "$CONTROL_ROOT/guardian-boot.pid"
exit 0
