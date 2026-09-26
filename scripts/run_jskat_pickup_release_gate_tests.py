#!/usr/bin/env python3
"""Focused V2-owned tests for the JSkat pickup-plan source-bound release gate."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


def main() -> int:
    repo = Path("/workspace/skatai-v2")
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(repo / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": "/tmp",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "UV_CACHE_DIR": "/workspace/.cache/uv",
        "UV_NO_PROGRESS": "1",
    }
    argv = [
        "/usr/bin/uv", "run", "--offline", "--isolated", "--no-project",
        "--python", "/usr/bin/python3.11", "--with", "pytest==8.4.2",
        "python", "-m", "pytest", "-q",
        "tests/test_release_package.py",
        "tests/test_release_loader.py",
        "tests/test_host_service.py",
    ]
    cp = subprocess.run(argv, cwd=repo, env=env, timeout=180)
    return int(cp.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
