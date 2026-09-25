#!/usr/bin/env python3
"""Offline bounded test runner for the bidding Parquet schema gate."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def main() -> int:
    repo = Path("/workspace/skatai-v2")
    uv = "/usr/bin/uv"
    py = "/usr/bin/python3.11"
    pytest_cache = "/workspace/.cache/uv"
    if not Path(uv).is_file() or not Path(py).is_file() or not Path(pytest_cache).is_dir():
        raise SystemExit("BOUNDED_TEST_RUNTIME_UNAVAILABLE")
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(repo / "src"),
        "UV_CACHE_DIR": pytest_cache,
        "UV_OFFLINE": "1",
        "HOME": "/tmp",
        "LC_ALL": "C.UTF-8",
    }
    root = Path(tempfile.mkdtemp(prefix="skatai-v2-bidding-gate-", dir="/tmp"))
    try:
        venv = root / "venv"
        subprocess.run(
            [uv, "venv", "--python", py, str(venv)],
            check=True, cwd=repo, env=env, timeout=60,
        )
        subprocess.run(
            [uv, "pip", "install", "--offline", "--python", str(venv / "bin/python"),
             "pytest", "pyarrow==18.1.0"],
            check=True, cwd=repo, env=env, timeout=120,
        )
        cp = subprocess.run(
            [str(venv / "bin/python"), "-m", "pytest", "-q",
             "tests/test_bidding_parquet.py", "tests/test_sgf.py"],
            cwd=repo, env=env, timeout=180,
        )
        return int(cp.returncode)
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
