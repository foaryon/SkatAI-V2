#!/usr/bin/env bash
set -euo pipefail
umask 027

ROOT=/workspace/skatai-v2
RUNTIME=/workspace/skatai-v2-runtime/iss/external-gate-r9
ENV_FILE=/workspace/skatai-v2-runtime/iss/iss-runtime.env
SUPERVISOR="$ROOT/scripts/supervise_frozen_r9.py"
PREP=/workspace/openai-agent/prepare-runtime-secrets.py
LOG="$RUNTIME/supervisor-boot.log"

mkdir -p "$RUNTIME"

log() {
  printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >>"$LOG"
}

[ -f "$SUPERVISOR" ] || { log "skip missing-supervisor"; exit 0; }

# Materialize private runtime secrets from the pod's configured secret source.
if [ "$(id -u)" -eq 0 ] && [ -f "$PREP" ]; then
  python3 "$PREP" >>"$LOG" 2>&1 || {
    log "secret-prep-failed"
    exit 0
  }
fi

# Only root can reliably read PID 1's original environment. Persist only
# non-secret ISS connection metadata plus the private password file path.
if [ "$(id -u)" -eq 0 ]; then
  python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import os, shlex, sys
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
    raise SystemExit("ISS password file unavailable")
text="".join(
    f"export {key}={shlex.quote(env[key])}\n" for key in sorted(required)
)
text += "export ISS_PASSWORD_FILE=/run/skatai-v2-secrets/iss_password\n"
out.parent.mkdir(parents=True,exist_ok=True)
tmp=out.with_suffix(".env.tmp")
tmp.write_text(text,encoding="utf-8")
os.chown(tmp,999,996)
os.chmod(tmp,0o640)
os.replace(tmp,out)
PY
fi

[ -s "$ENV_FILE" ] || { log "skip iss-runtime-env-unavailable"; exit 0; }
[ -s /run/skatai-v2-secrets/iss_password ] || {
  log "skip iss-password-file-unavailable"
  exit 0
}

# A supervisor lock provides the final singleton guarantee. The process check
# avoids needless noisy duplicate starts at normal boot/re-entry.
if pgrep -f 'scripts/supervise_frozen_r9.py' >/dev/null 2>&1; then
  log "supervisor-already-running"
  exit 0
fi

if [ "$(id -u)" -eq 0 ]; then
  setsid nohup /usr/bin/python3 "$ROOT/scripts/launch_frozen_r9_supervisor.py" \
    >>"$LOG" 2>&1 </dev/null &
else
  setsid nohup /usr/bin/python3 "$SUPERVISOR" >>"$LOG" 2>&1 </dev/null &
fi
log "supervisor-started pid=$!"
