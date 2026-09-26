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


def test_process_scan_never_counts_the_supervisor_itself():
    mod = _load()
    assert mod.os.getpid() not in mod._processes_containing("test_supervise_frozen_r9.py")


@pytest.mark.parametrize("discovered", [False, True])
def test_supervisor_refreshes_live_child_and_records_exit(tmp_path, monkeypatch, discovered):
    mod = _load()
    runtime = _runtime(tmp_path)
    statuses = []
    original_write = mod._atomic_json

    def capture_status(path, payload):
        original_write(path, payload)
        statuses.append(json.loads(path.read_text(encoding="utf-8")))

    class FakeChild:
        pid = 4321
        polls = iter([None, None, 0])

        def poll(self):
            return next(self.polls)

        def wait(self):
            return 7

    launches = []

    def fake_launch(*args, **kwargs):
        launches.append((args, kwargs))
        return FakeChild()

    observations = iter(["SAFE_TO_RESTART"] + ["RUNNING" if discovered else "SAFE_TO_RESTART"] * 2)

    def fake_state(**kwargs):
        state = next(observations)
        return {
            "schema": mod.SCHEMA,
            "state": state,
            "worker_pids": [4321] if state == "RUNNING" else [],
            "launcher_pids": [],
        }

    def fake_sleep(_seconds):
        if len(statuses) == 4:
            raise StopIteration("stop after exit status")

    monkeypatch.setattr(mod, "evaluate_restart_state", fake_state)
    monkeypatch.setattr(mod, "_atomic_json", capture_status)
    monkeypatch.setattr(mod, "_verify_frozen_identity", lambda *args: None)
    monkeypatch.setattr(mod, "load_runtime_env", lambda *args: {})
    monkeypatch.setattr(mod.subprocess, "Popen", fake_launch)
    monkeypatch.setattr(mod.time, "sleep", fake_sleep)

    with pytest.raises(StopIteration, match="stop after exit status"):
        mod.supervise(runtime=runtime, frozen_repo=tmp_path, launcher=tmp_path / "launcher", env_file=tmp_path / "env")

    assert len(launches) == 1
    assert [item["state"] for item in statuses] == [
        "SAFE_TO_RESTART",
        "RUNNING" if discovered else "BOOTSTRAPPING",
        "RUNNING" if discovered else "BOOTSTRAPPING",
        "WORKER_EXITED",
    ]
    assert statuses[1]["worker_pids" if discovered else "launcher_pids"] == [4321]
    assert statuses[2]["captured_unix_ns"] >= statuses[1]["captured_unix_ns"]
    assert statuses[3]["exit_code"] == 7
    assert statuses[3]["failure_streak"] == 1


def test_supervisor_does_not_launch_while_reconciliation_blocked(tmp_path, monkeypatch):
    mod = _load()
    runtime = _runtime(tmp_path, active=1)
    monkeypatch.setattr(mod, "_pending_effects", lambda *args: 0)
    monkeypatch.setattr(mod, "_processes_containing", lambda *args: [])
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("unexpected worker launch"))
    recovery_calls = []
    def recover(argv, **kwargs):
        assert Path(argv[1]).name == "reconcile_r9_abandoned_game.py"
        assert kwargs["timeout"] == 180
        recovery_calls.append(argv)
    monkeypatch.setattr(mod.subprocess, "run", recover)
    def stop_blocked(*_args):
        raise RuntimeError("blocked-test-stop")
    monkeypatch.setattr(mod.time, "sleep", stop_blocked)
    with pytest.raises(RuntimeError, match="blocked-test-stop"):
        mod.supervise(runtime=runtime, frozen_repo=tmp_path, launcher=tmp_path / "launcher", env_file=tmp_path / "env")
    assert len(recovery_calls) == 1
    assert json.loads((runtime / "supervisor-status.json").read_text())["state"] == "BLOCKED_RECONCILIATION"


def test_second_supervisor_cannot_launch(tmp_path, monkeypatch):
    mod = _load()
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(mod.fcntl, "flock", lambda *args: (_ for _ in ()).throw(BlockingIOError()))
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("unexpected launch"))
    with pytest.raises(SystemExit, match="R9_SUPERVISOR_ALREADY_RUNNING"):
        mod.supervise(runtime=runtime, frozen_repo=tmp_path, launcher=tmp_path / "launcher", env_file=tmp_path / "env")
