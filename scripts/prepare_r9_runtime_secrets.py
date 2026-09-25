#!/usr/bin/env python3
"""Materialize only the independent R9 ISS credential.

This helper is root-only. It reads the pod's configured ISS secret from PID 1,
writes no secret value to logs, and creates the runtime file as the unprivileged
R9 account with owner-only permissions required by the ISS client.
"""

from __future__ import annotations

import os
from pathlib import Path
import pwd

OUT = Path("/run/skatai-v2-secrets")
SOURCE_KEYS = (
    "RUNPOD_SECRET_skatai_iss_password",
    "skatai_iss_password",
    "ISS_PASSWORD",
)


def _pid1_environment() -> dict[str, str]:
    env: dict[str, str] = {}
    for item in Path("/proc/1/environ").read_bytes().split(b"\0"):
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        env[key.decode("utf-8", "replace")] = value.decode(
            "utf-8", "surrogateescape"
        )
    return env


def materialize(env: dict[str, str], *, out: Path = OUT) -> Path:
    value = next((env.get(key) for key in SOURCE_KEYS if env.get(key)), None)
    if not value:
        raise RuntimeError("R9_ISS_SECRET_SOURCE_UNAVAILABLE")
    account = pwd.getpwnam("sentinelx")
    out.mkdir(parents=True, exist_ok=True)
    os.chown(out, 0, 0)
    os.chmod(out, 0o755)
    path = out / "iss_password"
    tmp = out / ".iss_password.r9.tmp"
    try:
        with tmp.open("w", encoding="utf-8") as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
        os.chown(tmp, account.pw_uid, account.pw_gid)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    st = path.stat()
    if st.st_uid != account.pw_uid or st.st_gid != account.pw_gid:
        raise RuntimeError("R9_ISS_SECRET_OWNER_INVALID")
    if st.st_mode & 0o077:
        raise RuntimeError("R9_ISS_SECRET_PERMISSIONS_TOO_OPEN")
    if st.st_size <= 0 or st.st_size > 4096:
        raise RuntimeError("R9_ISS_SECRET_SIZE_INVALID")
    return path


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("R9_SECRET_PREP_REQUIRES_ROOT")
    materialize(_pid1_environment())
    print("r9_iss_password=present_owner_only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
