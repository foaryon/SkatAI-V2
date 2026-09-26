#!/usr/bin/env python3
"""Bounded deterministic validation for MAIN turn closure and budget accounting."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


def pytest_argv(*tests: str) -> list[str]:
    return [
        "/usr/bin/uv", "run", "--offline", "--isolated", "--no-project",
        "--python", "/usr/bin/python3.11", "--with", "pytest==8.4.2",
        "python", "-m", "pytest", "-q", *tests,
    ]


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
    commands = [
        pytest_argv("tests/test_openai_platform_main_controller.py"),
        ["/usr/bin/python3", "scripts/validate_main_controller_logic.py"],
    ]
    for argv in commands:
        cp = subprocess.run(argv, cwd=repo, env=env, timeout=180)
        if cp.returncode:
            return int(cp.returncode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
