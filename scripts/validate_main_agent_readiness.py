#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import tempfile
import time

TRUST = Path("/opt/skatai-main-controller/current")
CONTROL = Path("/var/lib/skatai-main-controller")
EXECUTOR_USER = "skatai-main-agent"


def require(condition: bool, code: str) -> None:
    if not condition:
        raise SystemExit(code)


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def main() -> int:
    require(os.geteuid() == 0, "READINESS_REQUIRES_ROOT")
    require(TRUST.is_dir(), "TRUSTED_RUNTIME_MISSING")
    require((TRUST / "MANIFEST.sha256").is_file(), "TRUSTED_MANIFEST_MISSING")
    check = subprocess.run(
        ["sha256sum", "-c", "MANIFEST.sha256"],
        cwd=str(TRUST),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    require(check.returncode == 0, "TRUSTED_MANIFEST_MISMATCH")

    require(CONTROL.is_dir(), "CONTROL_ROOT_MISSING")
    require(CONTROL.stat().st_uid == 0 and mode(CONTROL) == 0o700, "CONTROL_ROOT_PERMISSIONS")
    require((CONTROL / "AGENT_HARD_DISABLED").is_file(), "HARD_DISABLE_REQUIRED_DURING_READINESS")
    require(not (CONTROL / "AGENT_REENABLE_APPROVED").exists(), "REENABLE_APPROVAL_MUST_BE_ABSENT")
    require(not (CONTROL / "ACTIVE_WORK_PERMIT.json").exists(), "WORK_PERMIT_MUST_BE_ABSENT")

    spec = importlib.util.spec_from_file_location(
        "skatai_main_readiness_controller", TRUST / "openai_platform_main_controller.py"
    )
    require(spec is not None and spec.loader is not None, "CONTROLLER_IMPORT_SPEC")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    governor = mod.load_governor()
    require(governor.get("enabled") is True, "GOVERNOR_NOT_ENABLED")
    prompt_bytes = len((TRUST / "config/MAIN_AGENT_INSTRUCTIONS.md").read_bytes())
    require(prompt_bytes < 8_000, "AGENT_INSTRUCTIONS_TOO_LARGE")
    hard_budget = governor["hard_total_budget"]
    require(int(hard_budget["max_model_submits_per_utc_day"]) <= 10, "HARD_TOTAL_SUBMIT_CAP_TOO_HIGH")
    require(int(hard_budget["max_total_tokens_per_utc_day"]) <= 2_500_000, "HARD_TOTAL_TOKEN_CAP_TOO_HIGH")
    require(1 <= int(hard_budget["token_reservation_per_turn"]) <= 250_000, "TURN_TOKEN_RESERVATION_INVALID")
    require(float(hard_budget["max_estimated_cost_usd_per_utc_day"]) <= 7.0, "HARD_TOTAL_ESTIMATED_COST_CAP_TOO_HIGH")
    require(hard_budget.get("user_input_may_bypass_hard_total") is False, "USER_CAN_BYPASS_HARD_TOTAL")
    budget = governor["autonomous_budget"]
    require(1 <= int(budget["max_submits_per_session"]) <= 2, "SESSION_SUBMIT_CAP_OUT_OF_RANGE")
    require(int(budget["max_submits_per_utc_day"]) <= 8, "DAILY_SUBMIT_CAP_TOO_HIGH")
    require(float(budget["max_estimated_cost_usd_per_utc_day"]) <= 5.0, "DAILY_COST_CAP_TOO_HIGH")
    require(int(budget["min_seconds_between_submits"]) >= 120, "SUBMIT_INTERVAL_TOO_LOW")
    require(int(budget["max_consecutive_no_material_progress"]) == 0, "NO_PROGRESS_BUDGET_NONZERO")

    active_lock = CONTROL / "EXECUTION_LOCK.json"
    trusted_lock = TRUST / "config/INITIAL_EXECUTION_LOCK.json"
    require(active_lock.is_file(), "ACTIVE_EXECUTION_LOCK_MISSING")
    require(active_lock.stat().st_uid == 0 and mode(active_lock) == 0o600, "ACTIVE_EXECUTION_LOCK_PERMISSIONS")
    require(trusted_lock.is_file(), "TRUSTED_EXECUTION_LOCK_MISSING")
    require(mod.sha256_file(active_lock) == mod.sha256_file(trusted_lock), "ACTIVE_EXECUTION_LOCK_NOT_TRUSTED")
    lock = mod.load_execution_lock()
    require(lock["secondary"] is None or lock["primary"]["status"] in mod.EXTERNAL_WAIT_CLASSIFICATIONS, "WIP_LOCK_INVALID")
    require(bool(lock["primary"].get("end_state_contribution")), "END_STATE_CONTRIBUTION_MISSING")
    require(bool(lock["primary"].get("selection_evidence")), "SELECTION_EVIDENCE_MISSING")
    require(isinstance(lock["primary"].get("writable_files"), list), "WRITABLE_FILES_MISSING")

    # MAIN itself has no self-hosted shell/environment. The unprivileged
    # executor account exists only for pre-authorized deterministic command
    # wrappers run by the root-owned gateway.
    account = pwd.getpwnam(EXECUTOR_USER)
    require(account.pw_uid != 0, "EXECUTOR_IS_ROOT")
    sudo_info = subprocess.run(
        ["sudo", "-n", "-l", "-U", EXECUTOR_USER],
        text=True,
        capture_output=True,
    )
    sudo_text = (sudo_info.stdout + sudo_info.stderr).lower()
    require(
        "nopasswd" not in sudo_text and "may run the following commands" not in sudo_text,
        "EXECUTOR_HAS_SUDO",
    )

    gateway_path = TRUST / "main_tool_gateway.py"
    require(gateway_path.is_file() and gateway_path.stat().st_uid == 0, "TOOL_GATEWAY_NOT_TRUSTED")
    require(mode(gateway_path) & 0o022 == 0, "TOOL_GATEWAY_WRITABLE_BY_NONROOT")
    tools = mod.function_tools()
    tool_names = {row.get("name") for row in tools}
    require(
        tool_names == {
            "get_active_lease", "read_text", "search_text", "list_paths",
            "git_query", "apply_patch", "run_authorized_command",
            "record_turn_outcome",
        },
        "FUNCTION_TOOL_SET_MISMATCH",
    )
    require(all(row.get("type") == "function" for row in tools), "NON_FUNCTION_TOOL_EXPOSED")

    controller_text = (TRUST / "openai_platform_main_controller.py").read_text(encoding="utf-8")
    require('"environment": {"type": "none"}' in controller_text, "FUNCTION_ONLY_ENVIRONMENT_NOT_ENFORCED")
    require('"multi_agent": {"enabled": False}' in controller_text, "MULTI_AGENT_NOT_DISABLED")
    require("start_executor(state" not in controller_text, "SELF_HOSTED_EXECUTOR_STILL_IN_MAIN_LOOP")

    with tempfile.TemporaryDirectory(prefix="skatai-main-readiness-", dir="/var/lib") as td:
        temp = Path(td)
        permit_path = temp / "permit.json"
        permit_id = "readiness-permit-0000000000000001"
        permit_path.write_text(
            json.dumps({
                "schema": mod.WORK_PERMIT_SCHEMA,
                "mode": "BOUNDED_GATE",
                "approved": True,
                "permit_id": permit_id,
                "expires_at_epoch": int(time.time() + 300),
                "execution_lock_sha256": mod.sha256_file(active_lock),
                "primary_gate_id": lock["primary"]["gate_id"],
                "goal_path_id": lock["primary"]["goal_path_id"],
            }) + "\n",
            encoding="utf-8",
        )
        gateway = mod.ToolGateway(
            repo_root=Path("/workspace/skatai-v2"),
            runtime_root=Path("/workspace/skatai-v2-runtime"),
            control_root=temp,
            execution_lock=active_lock,
            work_permit=permit_path,
            governor=TRUST / "config/MAIN_EXECUTION_GOVERNOR.json",
            turn_outcome=temp / "turn-outcome.json",
            executor_user=EXECUTOR_USER,
        )
        tool_state = {
            "work_permit_id": permit_id,
            "turn_primary_gate_id": lock["primary"]["gate_id"],
        }
        try:
            gateway.dispatch(
                "apply_patch",
                {"patch": "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-x\n+y\n"},
                tool_state,
            )
        except RuntimeError as exc:
            require("PATCH_PATH_NOT_AUTHORIZED" in str(exc), "UNAUTHORIZED_PATCH_WRONG_REJECTION")
        else:
            raise SystemExit("UNAUTHORIZED_PATCH_ACCEPTED")

        try:
            gateway.dispatch(
                "run_authorized_command",
                {"command_id": "definitely-not-authorized", "arguments": {}},
                tool_state,
            )
        except RuntimeError as exc:
            require("COMMAND_NOT_AUTHORIZED" in str(exc), "UNAUTHORIZED_COMMAND_WRONG_REJECTION")
        else:
            raise SystemExit("UNAUTHORIZED_COMMAND_ACCEPTED")

        founding = gateway.dispatch(
            "read_text",
            {"path": "SKATAI_V2_FOUNDING_SPECIFICATION.md", "start_line": 1, "end_line": 3},
            tool_state,
        )
        require(bool(founding.get("text")), "GATEWAY_READ_FAILED")

    source_commit = (TRUST / "SOURCE_COMMIT").read_text(encoding="utf-8").strip()
    require(
        len(source_commit) == 40
        and all(c in "0123456789abcdef" for c in source_commit),
        "SOURCE_COMMIT_INVALID",
    )

    legacy = Path("/workspace/openai-agent/platform-controller")
    for name in ("openai_agents_keywrap.pem", "openai_agents_keywrap.pub.pem", "public_jwk.json"):
        require(not (legacy / name).exists(), "LEGACY_KEYWRAP_STILL_SHARED:" + name)

    pre_start = Path("/pre_start.sh")
    require(pre_start.is_file() and pre_start.stat().st_uid == 0, "PRE_START_NOT_ROOT_OWNED")
    require("/opt/skatai-main-controller/current" in pre_start.read_text(encoding="utf-8"), "PRE_START_NOT_TRUSTED")

    ps = subprocess.check_output(["ps", "-eo", "args="], text=True)
    forbidden = ("codex exec-server", "openai_platform_main_controller.py")
    require(not any(token in ps for token in forbidden), "MAIN_PROCESS_RUNNING_DURING_READINESS")

    secret_dir = Path("/run/skatai-v2-secrets")
    if secret_dir.is_dir():
        for name in ("openai_agents_api_key", "openai_executor_api_key", "runpod_deploy_api_key"):
            p = secret_dir / name
            if p.exists():
                require(p.stat().st_uid == 0 and mode(p) == 0o600, "CONTROLLER_SECRET_PERMISSIONS:" + name)
                read_probe = subprocess.run(
                    ["runuser", "-u", EXECUTOR_USER, "--", "test", "-r", str(p)]
                )
                require(read_probe.returncode != 0, "EXECUTOR_CAN_READ_CONTROLLER_SECRET:" + name)

    result = {
        "schema": "skatai.v2.main-agent-readiness.v1",
        "status": "PASS",
        "policy_epoch": mod.POLICY_EPOCH,
        "primary_gate_id": lock["primary"]["gate_id"],
        "goal_path_id": lock["primary"]["goal_path_id"],
        "daily_autonomous_submit_cap": int(budget["max_submits_per_utc_day"]),
        "daily_autonomous_estimated_cost_cap_usd": float(budget["max_estimated_cost_usd_per_utc_day"]),
        "hard_total_submit_cap": int(hard_budget["max_model_submits_per_utc_day"]),
        "hard_total_token_cap": int(hard_budget["max_total_tokens_per_utc_day"]),
        "turn_token_reservation": int(hard_budget["token_reservation_per_turn"]),
        "hard_total_estimated_cost_cap_usd": float(hard_budget["max_estimated_cost_usd_per_utc_day"]),
        "cost_cap_basis": "token reservation plus conservative incident cost proxy",
        "session_submit_cap": int(budget["max_submits_per_session"]),
        "agent_instruction_bytes": prompt_bytes,
        "source_commit": source_commit,
        "idle_triggers_submit": bool(governor["progress"]["idle_alone_triggers_submit"]),
        "execution_boundary": "FUNCTION_GATEWAY_ENVIRONMENT_NONE",
        "executor_user_for_authorized_commands_only": EXECUTOR_USER,
        "hard_disabled": True,
        "reenable_approval_present": False,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
