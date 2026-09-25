#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import pwd
import shlex

OUT = Path("/run/skatai-v2-secrets")
OUT.mkdir(parents=True, exist_ok=True)
os.chown(OUT, 0, 0)
os.chmod(OUT, 0o755)

raw = Path("/proc/1/environ").read_bytes().split(b"\0")
env = {}
for item in raw:
    if b"=" in item:
        k, v = item.split(b"=", 1)
        env[k.decode("utf-8", "replace")] = v.decode("utf-8", "surrogateescape")

sentinel = pwd.getpwnam("sentinelx")


def write_secret(name: str, value: str | None, uid: int, gid: int, mode: int = 0o600) -> None:
    if not value:
        return
    path = OUT / name
    tmp = OUT / ("." + name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(value)
        f.flush()
        os.fsync(f.fileno())
    os.chown(tmp, uid, gid)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


# R9 uses the ISS password independently. MAIN gets no access to this master
# file; the controller materializes a short-lived per-turn copy only when an
# explicitly authorized ISS_RUNTIME capability is active.
iss = (
    env.get("RUNPOD_SECRET_skatai_iss_password")
    or env.get("skatai_iss_password")
    or env.get("ISS_PASSWORD")
)
write_secret("iss_password", iss, 0, sentinel.pw_gid, 0o640)

# Controller-only keys. The least-privilege model executor cannot read these.
executor_key = env.get("CODEX_API_KEY") or env.get("OPENAI_EXECUTOR_API_KEY")
write_secret("openai_executor_api_key", executor_key, 0, 0)

agents_key = (
    env.get("RUNPOD_SECRET_openai_agents_api_key")
    or env.get("openai_agents_api_key")
    or env.get("OPENAI_AGENTS_API_KEY")
)
write_secret("openai_agents_api_key", agents_key, 0, 0)

runpod_deploy_key = (
    env.get("RUNPOD_DEPLOY_API_KEY")
    or env.get("RUNPOD_SECRET_SkatAI-V2-Deploy")
    or env.get("RUNPOD_SECRET_SkatAI_V2_Deploy")
    or env.get("SkatAI-V2-Deploy")
)
write_secret("runpod_deploy_api_key", runpod_deploy_key, 0, 0)

public = {}
for key in ("ISS_HOST", "ISS_PORT", "ISS_CLIENT_ID"):
    if env.get(key):
        public[key] = env[key]

public_env = OUT / "public.env"
tmp = OUT / ".public.env.tmp"
with tmp.open("w", encoding="utf-8") as f:
    for key, value in public.items():
        f.write(f"export {key}={shlex.quote(value)}\n")
    f.flush()
    os.fsync(f.fileno())
os.chown(tmp, 0, sentinel.pw_gid)
os.chmod(tmp, 0o640)
os.replace(tmp, public_env)

print("iss_password=" + ("present" if iss else "absent"))
print("executor_key=" + ("present" if executor_key else "absent"))
print("agents_api_key=" + ("present" if agents_key else "absent"))
print("runpod_deploy_api_key=" + ("present" if runpod_deploy_key else "absent"))
