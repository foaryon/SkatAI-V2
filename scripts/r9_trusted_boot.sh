#!/usr/bin/env bash
set -euo pipefail
umask 027

HERE="$(cd "$(dirname "$0")" && pwd)"
RUNTIME=/workspace/skatai-v2-runtime/iss/external-gate-r9
ENV_FILE=/workspace/skatai-v2-runtime/iss/iss-runtime.env
SUPERVISOR="$HERE/supervise_frozen_r9.py"
LAUNCH_BOOT="$HERE/launch_frozen_r9_supervisor.py"
SECRET_PREP="$HERE/prepare_runtime_secrets.py"
LOG="$RUNTIME/supervisor-boot.log"
EXPECTED_SOURCE_COMMIT=c77b401f3665ebd64c7c62491e122d8ccdfd22f1
EXPECTED_LAUNCHER_SHA256=a9e083770b37092e54efe2c495547658a4972479177ed76869774e8e52c143ac

mkdir -p "$RUNTIME"
log() { printf '%s %s
' "$(date -u +%FT%TZ)" "$*" >>"$LOG"; }

[ "$(id -u)" -eq 0 ] || { log "trusted-r9-boot requires root"; exit 0; }
python3 "$SECRET_PREP" >>"$LOG" 2>&1 || { log "secret-prep-failed"; exit 0; }

python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import os, pwd, shlex, sys
out=Path(sys.argv[1])
env={}
for item in Path("/proc/1/environ").read_bytes().split(b"\0"):
    if b"=" not in item:
        continue
    k,v=item.split(b"=",1)
    key=k.decode(errors="ignore")
    if key in {"ISS_HOST","ISS_PORT","ISS_CLIENT_ID"}:
        env[key]=v.decode(errors="strict")
required={"ISS_HOST","ISS_PORT","ISS_CLIENT_ID"}
if required-env.keys():
    raise SystemExit("missing ISS metadata")
secret=Path("/run/skatai-v2-secrets/iss_password")
if not secret.is_file() or secret.stat().st_size <= 0:
    raise SystemExit("ISS password unavailable")
g=pwd.getpwnam("sentinelx").pw_gid
text="".join(f"export {k}={shlex.quote(env[k])}\n" for k in sorted(required))
text+="export ISS_PASSWORD_FILE=/run/skatai-v2-secrets/iss_password\n"
tmp=out.with_suffix(".env.tmp")
tmp.write_text(text,encoding="utf-8")
os.chown(tmp,0,g)
os.chmod(tmp,0o640)
os.replace(tmp,out)
PY

if pgrep -f 'supervise_frozen_r9.py' >/dev/null 2>&1; then
  log "supervisor-already-running"
  exit 0
fi

export SKATAI_R9_EXPECTED_SOURCE_COMMIT="$EXPECTED_SOURCE_COMMIT"
export SKATAI_R9_EXPECTED_LAUNCHER_SHA256="$EXPECTED_LAUNCHER_SHA256"
export SKATAI_R9_SUPERVISOR_PATH="$SUPERVISOR"
setsid nohup /usr/bin/python3 "$LAUNCH_BOOT" >>"$LOG" 2>&1 </dev/null &
log "trusted-supervisor-started pid=$!"
