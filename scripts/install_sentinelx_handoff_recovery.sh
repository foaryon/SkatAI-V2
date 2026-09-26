#!/usr/bin/env bash
# Preserve the verified one-time old-Pod handoff across RunPod API outages.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo ROOT_REQUIRED >&2; exit 1; }
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone

base = Path(os.environ.get("SKATAI_SENTINELX_TEST_BASE", "/workspace/sentinelx-host"))
entry = base / "runpod-upgrade-entrypoint.sh"
log = base / "logs/runpod-upgrade-entrypoint.log"
control = Path("/workspace/skatai-v2-runtime/orchestrator/control")
control.mkdir(parents=True, exist_ok=True)
control.chmod(0o700)
old_id = "u72p5u7y65boia"
lines = log.read_text().splitlines()
seen_start = False
authorized_line = None
for line in lines:
    if "upgrade-entrypoint-start" in line:
        seen_start = f"waiting_for_old={old_id}" in line
    elif seen_start and "old-pod-absent handoff=authorized" in line:
        authorized_line = line
        break
if authorized_line is None:
    raise SystemExit("VERIFIED_OLD_POD_ABSENCE_MISSING")
marker = base / "handoff-authorized.json"
record = {"old_pod_id": old_id, "observation": "old-pod-absent handoff=authorized",
          "observed_at": authorized_line.split()[0],
          "log_sha256_at_install": hashlib.sha256(log.read_bytes()).hexdigest()}
if marker.exists():
    prior = json.loads(marker.read_text())
    if any(prior.get(key) != record[key] for key in ("old_pod_id", "observation", "observed_at")):
        raise SystemExit("HANDOFF_MARKER_CONFLICT")
else:
    fd, tmp = tempfile.mkstemp(prefix=".handoff-authorized.", dir=base)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(record, out, sort_keys=True)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, marker)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
source = entry.read_text()
flag = "# SKATAI_V2_VERIFIED_HANDOFF_V1"
block = '''# SKATAI_V2_VERIFIED_HANDOFF_V1
HANDOFF_MARKER="$BASE/handoff-authorized.json"
if [ -f "$HANDOFF_MARKER" ] && python3 - "$HANDOFF_MARKER" "$OLD_POD_ID" <<'CHECK'
import json, sys
try:
    record = json.load(open(sys.argv[1]))
    assert record["old_pod_id"] == sys.argv[2]
    assert record["observation"] == "old-pod-absent handoff=authorized"
except (OSError, ValueError, KeyError, AssertionError):
    raise SystemExit(1)
CHECK
then
  log "verified-prior-handoff old=$OLD_POD_ID starting-sentinelx"
  exec "$BASE/runpod-entrypoint.sh"
fi
# END_SKATAI_V2_VERIFIED_HANDOFF_V1

'''
anchor = 'while true; do\n'
if source.count(flag) == 1 and source.count("# END_SKATAI_V2_VERIFIED_HANDOFF_V1") == 1:
    print("HANDOFF_HOOK_ALREADY_INSTALLED")
    raise SystemExit(0)
if flag in source or source.count(anchor) != 1 or 'exec "$BASE/runpod-entrypoint.sh"' not in source:
    raise SystemExit("SENTINELX_UPGRADE_ENTRYPOINT_SHAPE_UNEXPECTED")
backup = control / ("sentinelx-upgrade.before-handoff." +
                    datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".sh")
backup.write_text(source)
backup.chmod(0o600)
st = entry.stat()
fd, tmp = tempfile.mkstemp(prefix=".runpod-upgrade-entrypoint.", dir=base)
try:
    with os.fdopen(fd, "w") as out:
        out.write(source.replace(anchor, block + anchor, 1))
        out.flush()
        os.fsync(out.fileno())
    os.chown(tmp, st.st_uid, st.st_gid)
    os.chmod(tmp, 0o755)
    os.replace(tmp, entry)
finally:
    if os.path.exists(tmp):
        os.unlink(tmp)
print(f"HANDOFF_HOOK_INSTALLED backup={backup}")
PY
bash -n "${SKATAI_SENTINELX_TEST_BASE:-/workspace/sentinelx-host}/runpod-upgrade-entrypoint.sh"
