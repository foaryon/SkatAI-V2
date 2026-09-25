from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts/launch_frozen_r9_supervisor.py"
    spec = importlib.util.spec_from_file_location("launch_frozen_r9_supervisor", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_root_bootstrap_passes_credentials_only_in_child_environment(monkeypatch):
    module = _module()
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.pwd, "getpwnam", lambda name: SimpleNamespace(
        pw_uid=999, pw_gid=996, pw_dir="/var/lib/sentinelx",
    ))
    original = module.Path.read_bytes
    monkeypatch.setattr(module.Path, "read_bytes", lambda path: (
        b"AWS_ACCESS_KEY_ID=private-id\0AWS_SECRET_ACCESS_KEY=private-key\0"
    ) if str(path) == "/proc/1/environ" else original(path))
    monkeypatch.setattr(module.os, "environ", {"HOME": "/root"})
    calls = []
    monkeypatch.setattr(module.os, "execvpe", lambda binary, argv, env: calls.append((binary, argv, env)))

    module.main()

    binary, argv, env = calls[0]
    assert binary == "setpriv"
    supervisor_index = argv.index("/usr/bin/python3") + 1
    assert argv[supervisor_index].endswith("supervise_frozen_r9.py")
    assert argv[supervisor_index + 1:] == [
        "--frozen-repo", "/workspace/skatai-v2-wt-r9-game-not-started",
        "--launcher", "/workspace/skatai-v2-runtime/iss/external-gate-r9/launch-r9-pinned.sh",
        "--env-file", "/workspace/skatai-v2-runtime/iss/iss-runtime.env",
    ]
    assert env["HOME"] == "/var/lib/sentinelx"
    assert env["XDG_CONFIG_HOME"] == "/var/lib/sentinelx/.config"
    assert env["AWS_ACCESS_KEY_ID"] == "private-id"
    assert env["AWS_SECRET_ACCESS_KEY"] == "private-key"


def test_root_bootstrap_fails_closed_without_s3_credentials(monkeypatch):
    module = _module()
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.pwd, "getpwnam", lambda name: SimpleNamespace(
        pw_uid=999, pw_gid=996, pw_dir="/var/lib/sentinelx",
    ))
    monkeypatch.setattr(module.Path, "read_bytes", lambda path: b"ISS_HOST=example\0")
    monkeypatch.setattr(module.os, "environ", {})
    with pytest.raises(SystemExit, match="S3_CREDENTIALS_UNAVAILABLE"):
        module.main()
