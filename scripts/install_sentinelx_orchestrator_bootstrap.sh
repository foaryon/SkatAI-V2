#!/usr/bin/env bash
# Install one persistent, idempotent hook behind the existing SentinelX entrypoint.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo ROOT_REQUIRED >&2; exit 1; }
entry=${SKATAI_SENTINELX_ENTRYPOINT_PATH:-/workspace/sentinelx-host/runpod-entrypoint.sh}
control=/workspace/skatai-v2-runtime/orchestrator/control
mkdir -p "$control"
chmod 700 "$control"
python3 - "$entry" "$control" <<'PY'
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timezone

path, control = map(pathlib.Path, sys.argv[1:])
source = path.read_text()
anchor = 'if [ -x /start.sh ]; then\n'
marker = '# SKATAI_V2_ORCHESTRATOR_BOOTSTRAP_V1'
block = '''# SKATAI_V2_ORCHESTRATOR_BOOTSTRAP_V1
ORCH_BOOT=/workspace/skatai-v2/scripts/volume_bootstrap.sh
if [ -f "$ORCH_BOOT" ]; then
  log_local "starting-skatai-v2-volume-bootstrap path=$ORCH_BOOT"
  if SKATAI_BOOTSTRAP_DEFER_START=1 /bin/bash "$ORCH_BOOT" >/var/log/skatai-v2-volume-bootstrap.log 2>&1; then
    log_local "skatai-v2-volume-bootstrap-ok"
  else
    log_local "skatai-v2-volume-bootstrap-failed; continuing pod startup for repair"
  fi
fi
# END_SKATAI_V2_ORCHESTRATOR_BOOTSTRAP_V1

'''
if source.count(marker) == 1 and source.count('# END_SKATAI_V2_ORCHESTRATOR_BOOTSTRAP_V1') == 1:
    print('HOOK_ALREADY_INSTALLED')
    raise SystemExit(0)
if marker in source or source.count(anchor) != 1 or 'starting-sentinelx-supervisor' not in source:
    raise SystemExit('SENTINELX_ENTRYPOINT_SHAPE_UNEXPECTED')
backup = control / ('sentinelx-entrypoint.before-orchestrator.' +
                    datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.sh')
backup.write_text(source)
backup.chmod(0o600)
stat = path.stat()
fd, tmp = tempfile.mkstemp(prefix='.runpod-entrypoint.', dir=path.parent)
try:
    with os.fdopen(fd, 'w') as out:
        out.write(source.replace(anchor, block + anchor, 1))
        out.flush()
        os.fsync(out.fileno())
    os.chown(tmp, stat.st_uid, stat.st_gid)
    os.chmod(tmp, 0o755)
    os.replace(tmp, path)
finally:
    if os.path.exists(tmp):
        os.unlink(tmp)
print(f'HOOK_INSTALLED backup={backup}')
PY
bash -n "$entry"
