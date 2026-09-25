#!/usr/bin/env python3
"""Bounded deterministic tests for frozen-R9 supervisor observability."""

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
        raise SystemExit("R9_SUPERVISOR_TEST_RUNTIME_UNAVAILABLE")
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(repo / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": "/tmp",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    }
    cp = subprocess.run(
        [str(py), "-m", "pytest", "-q", "tests/test_supervise_frozen_r9.py"],
        cwd=repo,
        env=env,
        timeout=120,
    )
    return int(cp.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
