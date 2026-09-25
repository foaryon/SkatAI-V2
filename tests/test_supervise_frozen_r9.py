from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "supervise_frozen_r9.py"
    )
    spec = importlib.util.spec_from_file_location("supervise_frozen_r9", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _runtime(tmp_path: Path, *, active: int = 0, target=300) -> Path:
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "active-games.json").write_text(
        json.dumps({"games": [{"table_id": f"T{i}"} for i in range(active)]}),
        encoding="utf-8",
    )
    (runtime / "status.json").write_text(
        json.dumps({"next_per_arm_target": target}),
        encoding="utf-8",
    )
    return runtime


def test_restart_state_running_takes_precedence(tmp_path):
    mod = _load()
    runtime = _runtime(tmp_path, active=1)
    state = mod.evaluate_restart_state(
        runtime=runtime,
        frozen_repo=tmp_path,
        worker_pids=[123],
        launcher_pids=[],
        pending_effects=1,
    )
    assert state["state"] == "RUNNING"


def test_restart_state_blocks_unknown_external_authority(tmp_path):
    mod = _load()
    runtime = _runtime(tmp_path, active=1)
    state = mod.evaluate_restart_state(
        runtime=runtime,
        frozen_repo=tmp_path,
        worker_pids=[],
        launcher_pids=[],
        pending_effects=0,
    )
    assert state["state"] == "BLOCKED_RECONCILIATION"

    runtime = _runtime(tmp_path / "second", active=0)
    state = mod.evaluate_restart_state(
        runtime=runtime,
        frozen_repo=tmp_path,
        worker_pids=[],
        launcher_pids=[],
        pending_effects=1,
    )
    assert state["state"] == "BLOCKED_RECONCILIATION"


def test_restart_state_allows_only_clean_boundary(tmp_path):
    mod = _load()
    runtime = _runtime(tmp_path, active=0, target=300)
    state = mod.evaluate_restart_state(
        runtime=runtime,
        frozen_repo=tmp_path,
        worker_pids=[],
        launcher_pids=[],
        pending_effects=0,
    )
    assert state["state"] == "SAFE_TO_RESTART"


def test_restart_state_stops_after_campaign_complete(tmp_path):
    mod = _load()
    runtime = _runtime(tmp_path, active=0, target=None)
    state = mod.evaluate_restart_state(
        runtime=runtime,
        frozen_repo=tmp_path,
        worker_pids=[],
        launcher_pids=[],
        pending_effects=0,
    )
    assert state["state"] == "CAMPAIGN_COMPLETE"


def test_manual_hold_blocks_restart(tmp_path):
    mod = _load()
    runtime = _runtime(tmp_path, active=0)
    (runtime / ".supervisor-disable").touch()
    state = mod.evaluate_restart_state(
        runtime=runtime,
        frozen_repo=tmp_path,
        worker_pids=[],
        launcher_pids=[],
        pending_effects=0,
    )
    assert state["state"] == "MANUAL_HOLD"


def test_runtime_env_accepts_only_nonsecret_connection_metadata(tmp_path):
    mod = _load()
    secret = tmp_path / "iss_password"
    secret.write_text("x", encoding="utf-8")
    env = tmp_path / "iss-runtime.env"
    env.write_text(
        "export ISS_HOST=example.invalid\n"
        "export ISS_PORT=7000\n"
        "export ISS_CLIENT_ID=SkatAI\n"
        f"export ISS_PASSWORD_FILE={secret}\n",
        encoding="utf-8",
    )
    loaded = mod.load_runtime_env(env)
    assert loaded["ISS_PASSWORD_FILE"] == str(secret)
    assert "ISS_PASSWORD" not in loaded


def test_runtime_env_rejects_secret_value_key(tmp_path):
    mod = _load()
    env = tmp_path / "iss-runtime.env"
    env.write_text("export ISS_PASSWORD=secret\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="UNEXPECTED_RUNTIME_ENV_KEY"):
        mod.load_runtime_env(env)
