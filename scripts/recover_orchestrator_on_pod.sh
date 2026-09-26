#!/usr/bin/env bash
# Run from the persistent repository after a container/pod replacement.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo ROOT_REQUIRED >&2; exit 1; }
repo=/workspace/skatai-v2
control=/workspace/skatai-v2-runtime/orchestrator/control
cd "$repo"
# Never install a controller from edited source; untracked scientific files are preserved.
git diff --quiet HEAD -- src/skatai/orchestration scripts/install_orchestrator_runtime.sh scripts/orchestrator_boot.sh scripts/orchestrator_supervisor.sh scripts/pre_start_orchestrator.sh configs/orchestration SKATAI_V2_FOUNDING_SPECIFICATION.md SKATAI_V2_WORK_PROMPT.md || { echo TRACKED_CONTROL_SOURCE_DIRTY >&2; exit 1; }
git diff --cached --quiet -- src/skatai/orchestration scripts/install_orchestrator_runtime.sh scripts/orchestrator_boot.sh scripts/orchestrator_supervisor.sh scripts/pre_start_orchestrator.sh configs/orchestration SKATAI_V2_FOUNDING_SPECIFICATION.md SKATAI_V2_WORK_PROMPT.md || { echo STAGED_CONTROL_SOURCE_DIRTY >&2; exit 1; }
if pgrep -f '^(/usr/bin/)?python3 /opt/skatai-main-controller/current/openai_platform_main_controller\.py$' >/dev/null; then
  echo LEGACY_MAIN_ACTIVE >&2
  exit 1
fi
mkdir -p "$control"
chmod 700 "$control"
if [ -f /pre_start.sh ] && ! cmp -s /pre_start.sh scripts/pre_start_orchestrator.sh; then
  install -m 600 /pre_start.sh "$control/pre_start.before_recovery.$(date -u +%Y%m%dT%H%M%SZ).sh"
fi
bash scripts/install_orchestrator_runtime.sh >"$control/recovery-install.log"
install -o root -g root -m 755 scripts/pre_start_orchestrator.sh /pre_start.sh
bash /pre_start.sh
printf 'RECOVERY_BOOTSTRAP_OK head=%s\n' "$(git rev-parse --short HEAD)"
