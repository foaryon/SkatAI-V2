#!/usr/bin/env python3
from __future__ import annotations
import os
from pathlib import Path

OUT = Path("/run/skatai-v2-secrets")
OUT.mkdir(parents=True, exist_ok=True)
os.chmod(OUT, 0o755)

env = {}
for item in Path("/proc/1/environ").read_bytes().split(b"\0"):
    if b"=" in item:
        k, v = item.split(b"=", 1)
        env[k.decode("utf-8", "replace")] = v.decode("utf-8", "surrogateescape")

def write_secret(name: str, value: str | None) -> None:
    if not value:
        return
    p = OUT / name
    tmp = OUT / ("." + name + ".tmp")
    tmp.write_text(value, encoding="utf-8")
    os.chown(tmp, 0, 0)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)

write_secret("openai_agents_api_key",
    env.get("RUNPOD_SECRET_openai_agents_api_key")
    or env.get("openai_agents_api_key")
    or env.get("OPENAI_AGENTS_API_KEY"))
write_secret("runpod_deploy_api_key",
    env.get("RUNPOD_DEPLOY_API_KEY")
    or env.get("RUNPOD_SECRET_SkatAI-V2-Deploy")
    or env.get("RUNPOD_SECRET_SkatAI_V2_Deploy")
    or env.get("SkatAI-V2-Deploy"))
print("openai_agents_api_key=" + ("present" if (OUT / "openai_agents_api_key").is_file() else "absent"))
print("runpod_deploy_api_key=" + ("present" if (OUT / "runpod_deploy_api_key").is_file() else "absent"))
