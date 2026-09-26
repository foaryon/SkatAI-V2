#!/usr/bin/env python3
"""Focused verification for the deterministic JSkat adapter source port."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


def main() -> int:
    repo = Path("/workspace/skatai-v2")
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONPATH": str(repo / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": "/tmp",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "UV_CACHE_DIR": "/workspace/.cache/uv",
        "UV_NO_PROGRESS": "1",
    }
    commands = [
        ["/usr/bin/python3", "scripts/port_jskat_adapter_source.py", "--mode", "verify"],
        [
            os.environ.get("SKATAI_V2_TEST_PYTHON", "/tmp/skatai-v2-b0-venv/bin/python"),
            "-m", "pytest", "-q",
            "tests/test_host_service.py",
            "tests/test_jskat_cardplay_source_parity.py",
        ],
        ["git", "diff", "--check", "--",
         "integrations/jskat-adapter",
         "src/skatai/runtime/host_service.py",
         "tests/test_host_service.py",
         "provenance/JSKAT_INSTALLED_RUNTIME_WHEEL_GATE_20260925.json",
         "provenance/JSKAT_RUNTIME_INTEGRATION_V1_20260925.json"],
    ]
    test_python = Path(commands[1][0])
    if not test_python.is_file():
        raise SystemExit("JSKAT_TEST_RUNTIME_UNAVAILABLE:" + str(test_python))
    for argv in commands:
        cp = subprocess.run(argv, cwd=repo, env=env, timeout=180)
        if cp.returncode:
            return int(cp.returncode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
