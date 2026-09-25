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
    root = path.parents[1]
    mod.PROMPT = root / "configs/control/MAIN_AGENT_INSTRUCTIONS.md"
    mod.CONTINUE = root / "configs/control/MAIN_CONTINUE_EXECUTION_POLICY.txt"
    mod.GOVERNOR = root / "configs/control/MAIN_EXECUTION_GOVERNOR.json"
    mod.GOAL_POLICY = root / "configs/control/MAIN_GOAL_POLICY.json"
    mod.MASTER_PROMPT = root / "SKATAI_V2_MASTER_CONTINUE_MERGED.md"
    mod.FOUNDING_SPEC = root / "SKATAI_V2_FOUNDING_SPECIFICATION.md"
    mod.WORK_PROMPT = root / "SKATAI_V2_WORK_PROMPT.md"
    mod.REPO_EXECUTION_LOCK = root / "provenance/MAIN_EXECUTION_LOCK.json"
    mod.EXECUTION_LOCK = mod.REPO_EXECUTION_LOCK
    return mod


def _governor():
    return {
        "schema": "skatai.v2.main-execution-governor.v2",
        "policy_epoch": "goal-cost-discipline-v3-20260925",
        "enabled": True,
        "hard_total_budget": {
            "max_model_submits_per_utc_day": 10,
            "max_total_tokens_per_utc_day": 2_500_000,
            "token_reservation_per_turn": 250_000,
            "max_estimated_cost_usd_per_utc_day": 7.0,
            "incident_estimated_cost_per_submit_usd": 0.7,
            "user_input_may_bypass_min_interval": True,
            "user_input_may_bypass_hard_total": False,
        },
        "autonomous_budget": {
            "max_submits_per_utc_day": 8,
            "max_submits_per_session": 2,
            "max_estimated_cost_usd_per_utc_day": 5.0,
            "incident_estimated_cost_per_submit_usd": 0.7,
            "min_seconds_between_submits": 120,
            "max_turn_seconds": 1200,
            "max_consecutive_no_material_progress": 0,
            "max_same_gate_followups_without_terminal": 3,
        },
        "tool_budget": {
            "max_tool_calls_per_turn": 40,
            "max_side_effect_calls_per_turn": 8,
            "max_identical_tool_calls_per_turn": 3,
            "max_tool_output_bytes": 60000,
        },
        "executor_security": {
            "user": "skatai-main-agent",
            "raw_cloud_credentials_to_model_forbidden": True,
            "permitted_capabilities": ["ISS_RUNTIME"],
            "permitted_material_effects": [
                "READ_ONLY_INSPECTION",
                "LOCAL_DETERMINISTIC_ANALYSIS",
                "PROVENANCE_WRITE",
                "LOCAL_GIT_CHANGE",
            ],
            "goal_effects": {
                "G1_TRUSTED_EVIDENCE": [
                    "READ_ONLY_INSPECTION",
                    "LOCAL_DETERMINISTIC_ANALYSIS",
                    "PROVENANCE_WRITE",
                    "LOCAL_GIT_CHANGE",
                ]
            },
            "goal_capabilities": {
                "G1_TRUSTED_EVIDENCE": []
            },
        },
        "model_policy": {
            "default_tier": "VERIFIED_CURRENT",
            "default_model": "gpt-6-sol",
            "monitoring_model": None,
            "unverified_model_switching_forbidden": True,
        },
        "progress": {
            "allowed_progress_kinds": [
                "GATE_DECISION",
                "BLOCKER_REMOVED",
                "DECISION_BOUNDARY_ADVANCED",
                "REQUIRED_CAPABILITY_ACCEPTED",
                "UNKNOWN_RECONCILED",
                "EXTERNAL_EVENT",
            ]
        },
    }


def _lock():
    return {
        "schema": "skatai.v2.main-execution-lock.v2",
        "primary": {
            "gate_id": "G1",
            "status": "EXECUTABLE",
            "goal_path_id": "G1_TRUSTED_EVIDENCE",
            "end_state_contribution": "remove a verified blocker on the shortest valid test goal path",
            "selection_basis": "verified test blocker is the highest-value executable test gate",
            "selection_evidence": ["tests/test_openai_platform_main_controller.py"],
            "model_tier": "VERIFIED_CURRENT",
            "model_reason": "use only the live controller model verified by policy for this test",
            "evidence": ["tests/test_openai_platform_main_controller.py"],
            "allowed_material_effects": [
                "READ_ONLY_INSPECTION",
                "LOCAL_DETERMINISTIC_ANALYSIS",
                "PROVENANCE_WRITE",
            ],
            "executor_capabilities": [],
            "writable_files": ["tests/test_openai_platform_main_controller.py"],
            "progress_contract": {
                "kind": "FILE_CONTENT_CHANGE",
                "path": "tests/test_openai_platform_main_controller.py",
            },
            "completion": ["done"],
        },
        "secondary": None,
        "external_dependencies": [],
    }


def test_saved_agent_prompt_is_compact_and_points_to_binding_authority():
    mod = _load_controller()
    text = mod.PROMPT.read_text(encoding="utf-8")
    assert len(text.encode("utf-8")) < 8_000
    assert "SKATAI_V2_FOUNDING_SPECIFICATION.md" in text
    assert "SKATAI_V2_WORK_PROMPT.md" in text
    assert "get_active_lease" in text
    assert "record_turn_outcome" in text


def test_progress_fingerprint_ignores_git_only_activity(tmp_path, monkeypatch):
    mod = _load_controller()
    lock_path = tmp_path / "lock.json"
    state_path = tmp_path / "state.json"
    lock_path.write_text(json.dumps(_lock()), encoding="utf-8")
    base = {
        "captured_at": "t1",
        "git": {"head": "aaa"},
        "completed_independent_gate": {"task_id": "x", "classification": "CONCLUDE"},
        "data_integrity": {"bidding": "BLOCKED"},
        "open_blockers": [{"blocker_id": "b", "status": "OPEN"}],
        "staged_release_gate": {"status": "PENDING"},
    }
    state_path.write_text(json.dumps(base), encoding="utf-8")
    monkeypatch.setattr(mod, "EXECUTION_LOCK", lock_path)
    monkeypatch.setattr(mod, "CURRENT_STATE", state_path)
    first = mod.progress_fingerprint()

    git_only = dict(base)
    git_only["captured_at"] = "t2"
    git_only["git"] = {"head": "bbb"}
    state_path.write_text(json.dumps(git_only), encoding="utf-8")
    assert mod.progress_fingerprint() == first

    material = dict(git_only)
    material["open_blockers"] = [{"blocker_id": "b", "status": "RESOLVED"}]
    state_path.write_text(json.dumps(material), encoding="utf-8")
    assert mod.progress_fingerprint() != first


def test_execution_lock_requires_exactly_one_primary(tmp_path, monkeypatch):
    mod = _load_controller()
    p = tmp_path / "lock.json"
    monkeypatch.setattr(mod, "EXECUTION_LOCK", p)
    p.write_text(json.dumps(_lock()), encoding="utf-8")
    assert mod.load_execution_lock()["primary"]["gate_id"] == "G1"

    bad = _lock()
    bad["primary"] = None
    p.write_text(json.dumps(bad), encoding="utf-8")
    try:
        mod.load_execution_lock()
    except RuntimeError as exc:
        assert "PRIMARY_INVALID" in str(exc)
    else:
        raise AssertionError("invalid lock accepted")


def test_autonomous_budget_caps_cost_count_and_frequency():
    mod = _load_controller()
    gov = _governor()
    state = {}
    now = 1_790_000_000
    ok, reason = mod.autonomous_budget_status(state, gov, now)
    assert ok and reason is None

    for i in range(7):
        mod.record_model_submit(state, gov, "autonomous", now + i * 601)
    ok, reason = mod.autonomous_budget_status(state, gov, now + 7 * 601)
    assert not ok
    assert reason == "daily_estimated_cost_cap"
    assert state["autonomous_submits_today"] == 7
    assert state["estimated_autonomous_cost_usd_today"] == 4.9


def test_hard_total_budget_includes_user_submissions():
    mod = _load_controller()
    gov = _governor()
    state = {}
    now = 1_790_000_000
    for i in range(10):
        ok, reason = mod.hard_total_budget_status(state, gov, now + i)
        assert ok and reason is None
        mod.record_model_submit(state, gov, "user", now + i)
    ok, reason = mod.hard_total_budget_status(state, gov, now + 11)
    assert not ok
    assert reason == "hard_daily_model_submit_cap"
    assert state["total_model_submits_today"] == 10
    assert state["autonomous_submits_today"] == 0
    assert state["estimated_total_cost_usd_today"] == 7.0


def test_budget_resets_at_utc_day_boundary():
    mod = _load_controller()
    gov = _governor()
    state = {}
    day1 = 1_790_000_000
    mod.record_model_submit(state, gov, "autonomous", day1)
    assert state["autonomous_submits_today"] == 1
    day2 = day1 + 86_400
    ok, reason = mod.autonomous_budget_status(state, gov, day2)
    assert ok and reason is None
    assert state["autonomous_submits_today"] == 0
    assert state["estimated_autonomous_cost_usd_today"] == 0.0


def test_turn_outcome_must_be_nonce_bound_and_material(tmp_path, monkeypatch):
    mod = _load_controller()
    outcome_path = tmp_path / "outcome.json"
    monkeypatch.setattr(mod, "TURN_OUTCOME", outcome_path)
    gov = _governor()
    lock = _lock()
    progress = tmp_path / "progress.txt"
    progress.write_text("before", encoding="utf-8")
    state = {
        "turn_trigger_type": "PRIMARY_NEXT_STEP",
        "turn_event_key": "test-event-0001",
        "turn_primary_gate_id": "G1",
        "turn_goal_path_id": "G1_TRUSTED_EVIDENCE",
        "turn_allowed_material_effects": list(lock["primary"]["allowed_material_effects"]),
        "turn_executor_capabilities": list(lock["primary"]["executor_capabilities"]),
        "turn_progress_contract": {
            "contract": {
                "kind": "FILE_CONTENT_CHANGE",
                "path": str(progress),
            },
            "path": str(progress),
            "exists": True,
            "sha256": mod.sha256_file(progress),
        },
    }
    progress.write_text("after", encoding="utf-8")
    good = {
        "schema": "skatai.v2.main-turn-outcome.v2",
        "turn_nonce": "nonce1",
        "primary_gate_id": "G1",
        "goal_path_id": "G1_TRUSTED_EVIDENCE",
        "trigger_type": "PRIMARY_NEXT_STEP",
        "event_key": "test-event-0001",
        "classification": "CONTINUE",
        "material_progress": True,
        "progress_kind": "DECISION_BOUNDARY_ADVANCED",
        "evidence": ["tests/test_openai_platform_main_controller.py"],
        "next_action": "run bounded evaluation",
        "request_followup": True,
    }
    outcome_path.write_text(json.dumps(good), encoding="utf-8")
    outcome, error = mod.read_turn_outcome("nonce1", gov)
    assert error is None
    allowed, reason = mod.outcome_allows_followup(outcome, lock, gov, state)
    assert allowed and reason is None

    stale, error = mod.read_turn_outcome("different", gov)
    assert stale is None
    assert error == "turn_outcome_nonce_mismatch"

    good["material_progress"] = False
    good["progress_kind"] = "NONE"
    good["request_followup"] = True
    outcome_path.write_text(json.dumps(good), encoding="utf-8")
    outcome, error = mod.read_turn_outcome("nonce1", gov)
    assert error is None
    allowed, reason = mod.outcome_allows_followup(outcome, lock, gov, state)
    assert not allowed
    assert reason == "no_material_progress"


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


def test_function_only_boundary_and_token_reservation():
    mod = _load_controller()
    text = Path(mod.__file__).read_text(encoding="utf-8")
    assert '"environment": {"type": "none"}' in text
    assert '"multi_agent": {"enabled": False}' in text
    assert "start_executor(state" not in text
    assert {row["name"] for row in mod.function_tools()} == {
        "get_active_lease",
        "read_text",
        "search_text",
        "list_paths",
        "git_query",
        "apply_patch",
        "run_authorized_command",
        "record_turn_outcome",
    }

    gov = _governor()
    permit = {"max_total_tokens": 500_000}
    state = {}
    ok, reason = mod.reserve_turn_tokens(state, gov, permit, 1_790_000_000)
    assert ok and reason is None
    assert state["turn_token_reservation"] == 250_000
    ok, reason = mod.token_reservation_status(state, gov, permit, 1_790_000_001)
    assert not ok and reason == "outstanding_token_reservation"


def test_logical_submit_key_is_stable_across_retry_and_sequence_changes():
    mod = _load_controller()
    state = {"session_submit_count": 2}
    first = mod.logical_submit_key("sess", state, "same payload")
    assert mod.logical_submit_key("sess", state, "same payload") == first
    state["session_submit_count"] = 3
    assert mod.logical_submit_key("sess", state, "same payload") != first


def test_followup_rejects_nonexistent_evidence_and_repeated_signature(tmp_path):
    mod = _load_controller()
    gov = _governor()
    lock = _lock()
    base = {
        "schema": "skatai.v2.main-turn-outcome.v2",
        "turn_nonce": "nonce1",
        "primary_gate_id": "G1",
        "goal_path_id": "G1_TRUSTED_EVIDENCE",
        "trigger_type": "PRIMARY_NEXT_STEP",
        "event_key": "event-verified-0001",
        "classification": "CONTINUE",
        "material_progress": True,
        "progress_kind": "DECISION_BOUNDARY_ADVANCED",
        "evidence": ["provenance/THIS_FILE_DOES_NOT_EXIST.json"],
        "next_action": "continue exact gate",
        "request_followup": True,
    }
    state = {
        "turn_trigger_type": "PRIMARY_NEXT_STEP",
        "turn_primary_gate_id": "G1",
        "turn_goal_path_id": "G1_TRUSTED_EVIDENCE",
        "turn_allowed_material_effects": list(lock["primary"]["allowed_material_effects"]),
        "turn_executor_capabilities": list(lock["primary"]["executor_capabilities"]),
    }
    allowed, reason = mod.outcome_allows_followup(dict(base), lock, gov, state)
    assert not allowed
    assert reason == "material_progress_without_fresh_verifiable_evidence"

    progress = tmp_path / "progress.txt"
    progress.write_text("before", encoding="utf-8")
    state["turn_event_key"] = "event-verified-0001"
    state["turn_progress_contract"] = {
        "contract": {
            "kind": "FILE_CONTENT_CHANGE",
            "path": str(progress),
        },
        "path": str(progress),
        "exists": True,
        "sha256": mod.sha256_file(progress),
    }
    progress.write_text("after", encoding="utf-8")
    base["evidence"] = ["tests/test_openai_platform_main_controller.py"]
    first = dict(base)
    allowed, reason = mod.outcome_allows_followup(first, lock, gov, state)
    assert allowed and reason is None
    state["last_followup_signature"] = first["_controller_followup_signature"]
    state["last_followup_gate"] = "G1"
    state["same_gate_followups"] = 1
    repeated = dict(base)
    allowed, reason = mod.outcome_allows_followup(repeated, lock, gov, state)
    assert not allowed
    assert reason == "repeated_followup_signature"


def test_background_event_is_quiet_until_meaningful_change(tmp_path, monkeypatch):
    mod = _load_controller()
    status = tmp_path / "status.json"
    supervisor = tmp_path / "supervisor.json"
    status.write_text(json.dumps({
        "gate": None,
        "next_per_arm_target": 300,
        "source_commit": "abc",
    }), encoding="utf-8")
    supervisor.write_text(json.dumps({"state": "RUNNING"}), encoding="utf-8")
    lock = _lock()
    lock["external_dependencies"] = [{
        "gate_id": "R9",
        "goal_path_id": "G4_CONTROLLED_EVALUATION",
        "event_watch": {
            "type": "R9_GATE",
            "status_path": str(status),
            "supervisor_path": str(supervisor),
            "expected_next_per_arm_target": 300,
            "expected_source_commit": "abc",
        },
    }]
    state = {}
    assert mod.background_event(lock, state) is None
    assert mod.background_event(lock, state) is None

    status.write_text(json.dumps({
        "gate": "R9_DECISION_READY",
        "next_per_arm_target": 300,
        "source_commit": "abc",
    }), encoding="utf-8")
    event = mod.background_event(lock, state)
    assert event is not None
    assert event["trigger_type"] == "EXTERNAL_EVENT"
    assert event["gate_id"] == "R9"
