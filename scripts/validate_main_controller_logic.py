#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import time

REPO = Path("/workspace/skatai-v2")
RUNTIME = Path("/workspace/skatai-v2-runtime")
CONTROLLER = REPO / "scripts/openai_platform_main_controller.py"


def require(cond: bool, code: str) -> None:
    if not cond:
        raise SystemExit(code)


def load_controller():
    spec = importlib.util.spec_from_file_location("skatai_main_logic_validation", CONTROLLER)
    require(spec is not None and spec.loader is not None, "IMPORT_SPEC_FAILED")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.PROMPT = REPO / "configs/control/MAIN_AGENT_INSTRUCTIONS.md"
    mod.CONTINUE = REPO / "configs/control/MAIN_CONTINUE_EXECUTION_POLICY.txt"
    mod.GOVERNOR = REPO / "configs/control/MAIN_EXECUTION_GOVERNOR.json"
    mod.GOAL_POLICY = REPO / "configs/control/MAIN_GOAL_POLICY.json"
    mod.GATE_QUEUE = REPO / "configs/control/MAIN_GATE_QUEUE.json"
    mod.GATE_DIR = REPO / "configs/control/main_gates"
    mod.MASTER_PROMPT = REPO / "SKATAI_V2_MASTER_CONTINUE_MERGED.md"
    mod.FOUNDING_SPEC = REPO / "SKATAI_V2_FOUNDING_SPECIFICATION.md"
    mod.WORK_PROMPT = REPO / "SKATAI_V2_WORK_PROMPT.md"
    mod.REPO_EXECUTION_LOCK = REPO / "provenance/MAIN_EXECUTION_LOCK.json"
    mod.EXECUTION_LOCK = mod.REPO_EXECUTION_LOCK
    return mod


def main() -> int:
    m = load_controller()
    results: dict[str, object] = {}

    prompt_bytes = len(m.PROMPT.read_bytes())
    require(prompt_bytes < 8_000, "PROMPT_TOO_LARGE")
    results["prompt_bytes"] = prompt_bytes

    governor = m.load_governor()
    lock = m.load_execution_lock()
    queue = m.load_gate_queue()
    active_queue_entry = m.gate_queue_entry_for_lock(queue)
    require(active_queue_entry is not None and int(active_queue_entry["index"]) == 0, "INITIAL_GATE_QUEUE_BINDING_INVALID")
    results["gate_queue_validation"] = "PASS"
    goal_policy = json.loads(m.GOAL_POLICY.read_text(encoding="utf-8"))
    valid_goal_ids = {row["id"] for row in goal_policy["goal_path"]}
    require(lock["primary"]["goal_path_id"] in valid_goal_ids, "UNEXPECTED_PRIMARY_GOAL")
    results["lock_validation"] = "PASS"

    # Hard total budget covers user-triggered as well as autonomous model turns.
    state: dict[str, object] = {}
    now = 1_790_000_000
    for i in range(10):
        ok, reason = m.hard_total_budget_status(state, governor, now + i)
        require(ok and reason is None, "HARD_BUDGET_EARLY_BLOCK")
        m.record_model_submit(state, governor, "user", now + i)
    ok, reason = m.hard_total_budget_status(state, governor, now + 11)
    require(not ok and reason == "hard_daily_model_submit_cap", "HARD_BUDGET_USER_BYPASS")
    require(state["total_model_submits_today"] == 10, "HARD_BUDGET_COUNT")
    results["hard_total_budget"] = "PASS"

    # Autonomous cadence/cost limiter is independently stricter.
    state = {}
    for i in range(7):
        m.record_model_submit(state, governor, "autonomous", now + i * 601)
    ok, reason = m.autonomous_budget_status(state, governor, now + 7 * 601)
    require(not ok and reason == "daily_estimated_cost_cap", "AUTONOMOUS_COST_CAP")
    results["autonomous_budget"] = "PASS"

    # UTC rollover resets both total and autonomous counters.
    m.refresh_budget_state(state, governor, now + 86_400)
    require(state["total_model_submits_today"] == 0, "TOTAL_BUDGET_NO_UTC_RESET")
    require(state["autonomous_submits_today"] == 0, "AUTO_BUDGET_NO_UTC_RESET")
    results["budget_rollover"] = "PASS"

    scratch = RUNTIME / "controller-validation"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        progress = scratch / "progress.json"
        progress.write_text(json.dumps({"status": "UNKNOWN"}) + "\n", encoding="utf-8")
        contract_lock = json.loads(json.dumps(lock))
        contract_lock["primary"]["progress_contract"] = {
            "kind": "JSON_FIELD_TRANSITION",
            "path": str(progress),
            "field": "status",
            "before_prefix": "UNKNOWN",
            "after_forbid_prefix": "UNKNOWN",
        }
        baseline = m.capture_progress_contract(contract_lock)
        unchanged_ok, unchanged_reason = m.progress_contract_status(
            {"turn_progress_contract": baseline}
        )
        require(
            not unchanged_ok and unchanged_reason == "progress_contract_no_field_transition",
            "UNCHANGED_PROGRESS_ACCEPTED",
        )
        progress.write_text(json.dumps({"status": "RECONCILED"}) + "\n", encoding="utf-8")
        changed_ok, changed_reason = m.progress_contract_status(
            {"turn_progress_contract": baseline}
        )
        require(changed_ok and changed_reason is None, "VALID_PROGRESS_REJECTED")
        results["progress_contract"] = "PASS"

        outcome = {
            "schema": m.TURN_OUTCOME_SCHEMA,
            "turn_nonce": "nonce-validation",
            "primary_gate_id": lock["primary"]["gate_id"],
            "goal_path_id": lock["primary"]["goal_path_id"],
            "trigger_type": "PRIMARY_NEXT_STEP",
            "event_key": "controller-event-validation",
            "classification": "CONTINUE",
            "material_progress": True,
            "progress_kind": "UNKNOWN_RECONCILED",
            "evidence": ["provenance/BIDDING_D3_SPLIT_INTEGRITY_CORRECTION_20260925.json"],
            "next_action": "continue bounded gate work",
            "request_followup": True,
        }
        follow_state = {
            "turn_trigger_type": "PRIMARY_NEXT_STEP",
            "turn_event_key": "controller-event-validation",
            "turn_primary_gate_id": lock["primary"]["gate_id"],
            "turn_goal_path_id": lock["primary"]["goal_path_id"],
            "turn_allowed_material_effects": list(lock["primary"]["allowed_material_effects"]),
            "turn_executor_capabilities": list(lock["primary"]["executor_capabilities"]),
            "turn_progress_contract": baseline,
        }
        allowed, why = m.outcome_allows_followup(outcome, lock, governor, follow_state)
        require(allowed and why is None, "VALID_FOLLOWUP_REJECTED:" + str(why))
        sig = outcome["_controller_followup_signature"]
        follow_state["last_followup_signature"] = sig
        follow_state["last_followup_gate"] = lock["primary"]["gate_id"]
        follow_state["same_gate_followups"] = 1
        repeated = dict(outcome)
        allowed, why = m.outcome_allows_followup(repeated, lock, governor, follow_state)
        require(not allowed and why == "repeated_followup_signature", "LOOP_NOT_REJECTED")

        mismatch = dict(outcome)
        mismatch["event_key"] = "agent-invented-event"
        follow_state.pop("last_followup_signature", None)
        allowed, why = m.outcome_allows_followup(mismatch, lock, governor, follow_state)
        require(not allowed and why == "turn_outcome_event_mismatch", "EVENT_ID_SELF_CERTIFIED")
        results["followup_and_loop_guards"] = "PASS"

        dup = scratch / "dup.json"
        dup.write_text('{"x":1,"x":2}\n', encoding="utf-8")
        try:
            m.strict_json_load(dup)
        except RuntimeError as exc:
            require("DUPLICATE_JSON_KEY" in str(exc), "DUPLICATE_JSON_WRONG_ERROR")
        else:
            raise SystemExit("DUPLICATE_JSON_ACCEPTED")
        results["strict_json"] = "PASS"

        # A finite permit is mandatory, bound to the exact active lock/policy,
        # and has a non-resetting per-permit submit budget.
        m.HARD_DISABLE = scratch / "hard-disable"
        m.WORK_PERMIT = scratch / "permit.json"
        m.approval_policy_hashes = lambda: {"logic": "validation"}
        permit = {
            "schema": m.WORK_PERMIT_SCHEMA,
            "policy_epoch": m.POLICY_EPOCH,
            "policy_hashes": {"logic": "validation"},
            "permit_id": "0123456789abcdef0123456789abcdef",
            "mode": "BOUNDED_GATE",
            "approved": True,
            "expires_at_epoch": int(time.time() + 3600),
            "max_model_submits_total": 2,
            "max_autonomous_submits_total": 2,
            "max_total_tokens": 500000,
            "allowed_trigger_types": ["PRIMARY_NEXT_STEP"],
            "gate_queue_sha256": m.sha256_file(m.GATE_QUEUE),
            "gate_queue_start_index": 0,
            "max_gate_rotations": len(queue["entries"]) - 1,
            "authorized_gates": [
                {k: entry[k] for k in ("index", "gate_id", "goal_path_id", "lock_sha256")}
                for entry in queue["entries"]
            ],
        }
        m.WORK_PERMIT.write_text(json.dumps(permit) + "\n", encoding="utf-8")
        checked, why = m.work_permit_valid()
        require(checked is not None and why is None, "VALID_WORK_PERMIT_REJECTED:" + str(why))
        permit_state = {"work_permit_id": permit["permit_id"]}
        ok, why = m.work_permit_budget_status(permit_state, permit, "autonomous")
        require(ok and why is None, "WORK_PERMIT_EARLY_BLOCK")
        m.record_model_submit(permit_state, governor, "autonomous", now)
        m.record_model_submit(permit_state, governor, "autonomous", now + 601)
        ok, why = m.work_permit_budget_status(permit_state, permit, "autonomous")
        require(not ok and why == "work_permit_total_submit_cap", "WORK_PERMIT_TOTAL_CAP_FAILED")
        results["finite_work_permit"] = "PASS"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    # Token reservations are external to the model, cannot be reset by
    # session rotation, and must settle before another turn can be purchased.
    token_permit = {
        "max_total_tokens": 500000,
        "permit_id": "logic-validation-permit",
    }
    token_state = {"budget_day": m.utc_day_key(now)}
    ok, why = m.reserve_turn_tokens(token_state, governor, token_permit, now)
    require(ok and why is None, "TOKEN_RESERVATION_REJECTED:" + str(why))
    reserve = int(governor["hard_total_budget"]["token_reservation_per_turn"])
    require(token_state["turn_token_reservation"] == reserve, "TOKEN_RESERVATION_WRONG_AMOUNT")
    ok, why = m.token_reservation_status(token_state, governor, token_permit, now + 1)
    require(not ok and why == "outstanding_token_reservation", "TOKEN_DOUBLE_RESERVATION_ALLOWED")

    session = {"usage": {"total_tokens": 12345}}
    ok, why = m.settle_session_usage(token_state, session, governor, token_permit, now + 10)
    require(ok and why is None, "TOKEN_SETTLEMENT_FAILED:" + str(why))
    require(token_state["turn_token_reservation"] == 0, "TOKEN_RESERVATION_NOT_RELEASED")
    require(token_state["actual_total_tokens_today"] == 12345, "TOKEN_ACTUAL_NOT_ACCOUNTED")

    # Missing usage never becomes zero-cost. After the grace period the whole
    # reservation is conservatively charged.
    ok, why = m.reserve_turn_tokens(token_state, governor, token_permit, now + 20)
    require(ok and why is None, "SECOND_TOKEN_RESERVATION_REJECTED")
    token_state["usage_pending_since"] = now + 20
    ok, why = m.settle_session_usage(token_state, {}, governor, token_permit, now + 30)
    require(not ok and why == "session_usage_pending", "MISSING_USAGE_NOT_HELD")
    ok, why = m.settle_session_usage(token_state, {}, governor, token_permit, now + 200)
    require(ok and why is None, "CONSERVATIVE_SETTLEMENT_FAILED")
    require(
        token_state["actual_total_tokens_today"] == 12345 + reserve,
        "CONSERVATIVE_RESERVATION_NOT_CHARGED",
    )
    results["token_reservation_and_settlement"] = "PASS"

    # Function-only execution boundary: no self-hosted shell, exact tool set,
    # and side effects are denied unless the immutable lock authorizes them.
    names = {row["name"] for row in m.function_tools()}
    require(
        names == {
            "get_active_lease", "read_text", "search_text", "list_paths",
            "git_query", "apply_patch", "run_authorized_command",
            "record_turn_outcome",
        },
        "FUNCTION_TOOL_SET_MISMATCH",
    )
    controller_text = CONTROLLER.read_text(encoding="utf-8")
    require('"environment": {"type": "none"}' in controller_text, "ENVIRONMENT_NONE_MISSING")
    require('"multi_agent": {"enabled": False}' in controller_text, "MULTI_AGENT_DISABLE_MISSING")
    require("start_executor(state" not in controller_text, "SELF_HOSTED_EXECUTOR_STILL_USED")

    gateway_scratch = RUNTIME / "controller-validation-gateway"
    shutil.rmtree(gateway_scratch, ignore_errors=True)
    gateway_scratch.mkdir(parents=True, exist_ok=True)
    try:
        gateway_permit = gateway_scratch / "permit.json"
        gateway_permit_id = "gateway-validation-permit-00000001"
        gateway_permit.write_text(
            json.dumps({
                "schema": m.WORK_PERMIT_SCHEMA,
                "mode": "BOUNDED_GATE",
                "approved": True,
                "permit_id": gateway_permit_id,
                "expires_at_epoch": int(time.time() + 300),
                "gate_queue_sha256": m.sha256_file(m.GATE_QUEUE),
                "gate_queue_start_index": 0,
                "max_gate_rotations": len(queue["entries"]) - 1,
                "authorized_gates": [
                    {k: entry[k] for k in ("index", "gate_id", "goal_path_id", "lock_sha256")}
                    for entry in queue["entries"]
                ],
            }) + "\n",
            encoding="utf-8",
        )
        gateway = m.ToolGateway(
            repo_root=REPO,
            runtime_root=RUNTIME,
            control_root=gateway_scratch,
            execution_lock=m.EXECUTION_LOCK,
            work_permit=gateway_permit,
            governor=m.GOVERNOR,
            turn_outcome=gateway_scratch / "turn-outcome.json",
            executor_user="nobody",
        )
        gateway_state = {
            "work_permit_id": gateway_permit_id,
            "turn_primary_gate_id": lock["primary"]["gate_id"],
        }
        try:
            gateway.dispatch(
                "apply_patch",
                {"patch": "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-x\n+y\n"},
                gateway_state,
            )
        except RuntimeError as exc:
            require("PATCH_PATH_NOT_AUTHORIZED" in str(exc), "UNAUTHORIZED_PATCH_WRONG_ERROR")
        else:
            raise SystemExit("UNAUTHORIZED_PATCH_ACCEPTED")

        try:
            gateway.dispatch(
                "run_authorized_command",
                {"command_id": "not-authorized", "arguments": {}},
                gateway_state,
            )
        except RuntimeError as exc:
            require("COMMAND_NOT_AUTHORIZED" in str(exc), "UNAUTHORIZED_COMMAND_WRONG_ERROR")
        else:
            raise SystemExit("UNAUTHORIZED_COMMAND_ACCEPTED")

        # Expiry is checked at the side-effect boundary, independently of the
        # scheduler loop.
        expired = json.loads(gateway_permit.read_text(encoding="utf-8"))
        expired["expires_at_epoch"] = int(time.time() - 1)
        gateway_permit.write_text(json.dumps(expired) + "\n", encoding="utf-8")
        try:
            gateway.dispatch("get_active_lease", {}, gateway_state)
        except RuntimeError as exc:
            require("TOOL_PERMIT_EXPIRED" in str(exc), "EXPIRED_TOOL_PERMIT_WRONG_ERROR")
        else:
            raise SystemExit("EXPIRED_TOOL_PERMIT_ACCEPTED")
        results["function_gateway_boundary"] = "PASS"
    finally:
        shutil.rmtree(gateway_scratch, ignore_errors=True)

    require(str(m.CONTROL_ROOT).startswith("/var/lib/"), "CONTROL_ROOT_NOT_LOCAL_PROTECTED")
    require(str(m.TRUSTED_ROOT).startswith("/opt/"), "TRUSTED_ROOT_NOT_LOCAL_PROTECTED")
    results["trust_roots"] = "PASS"

    results["status"] = "PASS"
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
