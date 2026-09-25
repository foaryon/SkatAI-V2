"""Root-only bootstrap for the independent frozen R9 supervisor.

Pass pod S3 credentials from PID 1 directly to the unprivileged process.
Never print or persist credential values.
"""

from __future__ import annotations

import os
from pathlib import Path
import pwd


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("R9_SUPERVISOR_BOOTSTRAP_REQUIRES_ROOT")
    account = pwd.getpwnam("sentinelx")
    environment = os.environ.copy()
    for item in Path("/proc/1/environ").read_bytes().split(b"\0"):
        if item.startswith((b"AWS_ACCESS_KEY_ID=", b"AWS_SECRET_ACCESS_KEY=", b"AWS_SESSION_TOKEN=")):
            key, value = item.split(b"=", 1)
            environment[key.decode("ascii")] = value.decode("ascii")
    if not all(environment.get(key) for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")):
        raise SystemExit("R9_SUPERVISOR_S3_CREDENTIALS_UNAVAILABLE")
    environment["HOME"] = account.pw_dir
    environment["XDG_CONFIG_HOME"] = str(Path(account.pw_dir) / ".config")
    supervisor = os.environ.get(
        "SKATAI_R9_SUPERVISOR_PATH",
        str(Path(__file__).resolve().with_name("supervise_frozen_r9.py")),
    )
    frozen_repo = os.environ.get(
        "SKATAI_R9_FROZEN_REPO",
        "/workspace/skatai-v2-wt-r9-game-not-started",
    )
    launcher = os.environ.get(
        "SKATAI_R9_LAUNCHER",
        "/workspace/skatai-v2-runtime/iss/external-gate-r9/launch-r9-pinned.sh",
    )
    env_file = os.environ.get(
        "SKATAI_R9_ENV_FILE",
        "/workspace/skatai-v2-runtime/iss/iss-runtime.env",
    )
    os.execvpe(
        "setpriv",
        [
            "setpriv", f"--reuid={account.pw_uid}", f"--regid={account.pw_gid}",
            "--init-groups", "--", "/usr/bin/python3", supervisor,
            "--frozen-repo", frozen_repo,
            "--launcher", launcher,
            "--env-file", env_file,
        ],
        environment,
    )


if __name__ == "__main__":
    main()
