from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_controller():
    path = Path(__file__).resolve().parents[1] / "scripts" / "openai_platform_main_controller.py"
    spec = importlib.util.spec_from_file_location("skatai_openai_platform_main_controller", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_progress_fingerprint_ignores_volatile_runtime_fields(tmp_path, monkeypatch):
    mod = _load_controller()
    runtime = tmp_path / "runtime"
    current = runtime / "continuation" / "CONTINUATION_STATE_CURRENT.json"
    current.parent.mkdir(parents=True)
    monkeypatch.setattr(mod, "RUNTIME", runtime)
    monkeypatch.setattr(mod.subprocess, "check_output", lambda *a, **k: "deadbeef\n")

    base = {
        "captured_at": "t1",
        "current_highest_value_action": "advance gate",
        "open_blockers": [{"id": "b", "status": "OPEN"}],
        "nearest_consequential_gates": ["g1"],
        "champion": {"release_id": "B0"},
        "candidates": [],
        "evaluations": [],
        "releases": [],
        "git": {"main_head": "deadbeef"},
        "iss": {"active_games": [{"table": "A"}], "observation_freshness": "t1"},
        "resource_state": {"cpu": 8},
    }
    current.write_text(json.dumps(base), encoding="utf-8")
    first = mod.progress_fingerprint()

    volatile = dict(base)
    volatile["captured_at"] = "t2"
    volatile["iss"] = {"active_games": [{"table": "B"}], "observation_freshness": "t2"}
    volatile["resource_state"] = {"cpu": 8, "load": 99}
    current.write_text(json.dumps(volatile), encoding="utf-8")
    assert mod.progress_fingerprint() == first

    material = dict(volatile)
    material["open_blockers"] = [{"id": "b", "status": "RESOLVED"}]
    current.write_text(json.dumps(material), encoding="utf-8")
    assert mod.progress_fingerprint() != first


def test_send_message_uses_http_idempotency_header(monkeypatch):
    mod = _load_controller()
    calls = []

    def fake_api(method, path, body=None, extra_headers=None):
        calls.append((method, path, body, extra_headers))
        return {}

    monkeypatch.setattr(mod, "api", fake_api)
    mod.send_message("sess_test", "continue")
    assert len(calls) == 1
    method, path, body, headers = calls[0]
    assert method == "POST"
    assert path.endswith("/agents/sessions/sess_test/events")
    assert headers and headers.get("Idempotency-Key")
    assert "idempotency_key" not in body


def test_no_progress_backoff_becomes_cheap_but_bounded():
    mod = _load_controller()
    assert mod.backoff_seconds({"no_progress": 0}) == 5
    assert mod.backoff_seconds({"no_progress": 1}) == 60
    assert mod.backoff_seconds({"no_progress": 2}) == 120
    assert mod.backoff_seconds({"no_progress": 7}) == 3600
    assert mod.backoff_seconds({"no_progress": 100}) == 3600


def test_session_rotation_budget_is_bounded_not_hyperactive():
    mod = _load_controller()
    assert mod.SESSION_SUBMIT_BUDGET == 4


def test_executor_uses_persistent_codex_home(tmp_path, monkeypatch):
    mod = _load_controller()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setattr(mod, "CODEX_HOME", codex_home)
    env = mod.child_env("executor-key")
    assert env["HOME"] == str(codex_home)
    assert env["CODEX_HOME"] == str(codex_home)
    assert env["CODEX_API_KEY"] == "executor-key"


def test_logical_submit_key_is_stable_across_retry_and_changes_after_confirmed_sequence():
    mod = _load_controller()
    state = {"session_submit_count": 2}
    first = mod.logical_submit_key("sess", state, "same payload")
    retry = mod.logical_submit_key("sess", state, "same payload")
    assert retry == first

    state["session_submit_count"] = 3
    assert mod.logical_submit_key("sess", state, "same payload") != first
