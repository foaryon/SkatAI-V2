#!/usr/bin/env bash
set -euo pipefail
TRUST=/opt/skatai-orchestrator/current
CONTROL=/workspace/skatai-v2-runtime/orchestrator/control
mkdir -p "$CONTROL"
chmod 700 "$CONTROL"
[ -d "$TRUST" ] || exit 0
(
  cd "$TRUST"
  sha256sum -c MANIFEST.sha256 >"$CONTROL/trusted-manifest-check.log" 2>&1
)
if pgrep -f '^bash /opt/skatai-orchestrator/current/orchestrator_supervisor\.sh$' >/dev/null 2>&1; then
  exit 0
fi
setsid nohup bash "$TRUST/orchestrator_supervisor.sh" >>"$CONTROL/supervisor-boot.log" 2>&1 </dev/null &
echo $! >"$CONTROL/supervisor-boot.pid"
chmod 600 "$CONTROL/supervisor-boot.pid"
