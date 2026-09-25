#!/usr/bin/env python3
"""Bounded deterministic validation for MAIN turn closure and budget accounting."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


def main() -> int:
    repo = Path("/workspace/skatai-v2")
    py = Path(
        "/workspace/skatai/ops/skatai-autonomy-platform-v1/"
        "engineering/master-order-continuous-improvement-v1/test-venv/bin/python"
    )
    if not py.is_file():
        raise SystemExit("MAIN_RECOVERY_TEST_RUNTIME_UNAVAILABLE")
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(repo / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": "/tmp",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    }
    commands = [
        [str(py), "-m", "pytest", "-q", "tests/test_openai_platform_main_controller.py"],
        ["/usr/bin/python3", "scripts/validate_main_controller_logic.py"],
    ]
    for argv in commands:
        cp = subprocess.run(argv, cwd=repo, env=env, timeout=180)
        if cp.returncode:
            return int(cp.returncode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
