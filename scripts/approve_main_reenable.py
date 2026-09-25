#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

TRUST = Path("/opt/skatai-main-controller/current")
CONTROL = Path("/var/lib/skatai-main-controller")
READINESS = TRUST / "validate_main_agent_readiness.py"
APPROVAL = CONTROL / "AGENT_REENABLE_APPROVED"
PERMIT = CONTROL / "ACTIVE_WORK_PERMIT.json"
HARD_DISABLE = CONTROL / "AGENT_HARD_DISABLED"


def atomic_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("MAIN_REENABLE_REQUIRES_ROOT")
    p = argparse.ArgumentParser()
    p.add_argument("--reason", required=True)
    p.add_argument("--activate", action="store_true")
    p.add_argument("--max-total", type=int, default=4)
    p.add_argument("--max-autonomous", type=int, default=4)
    p.add_argument("--max-total-tokens", type=int, default=1_000_000)
    p.add_argument("--ttl-minutes", type=int, default=240)
    p.add_argument("--max-gate-rotations", type=int, default=2)
    p.add_argument("--allow-user-input", action="store_true")
    p.add_argument("--allow-external-events", action="store_true")
    args = p.parse_args()
    if not (1 <= args.max_total <= 10):
        raise SystemExit("MAIN_PERMIT_MAX_TOTAL_INVALID")
    if not (0 <= args.max_autonomous <= min(8, args.max_total)):
        raise SystemExit("MAIN_PERMIT_MAX_AUTONOMOUS_INVALID")
    if not (250_000 <= args.max_total_tokens <= 2_500_000):
        raise SystemExit("MAIN_PERMIT_MAX_TOTAL_TOKENS_INVALID")
    if not (5 <= args.ttl_minutes <= 720):
        raise SystemExit("MAIN_PERMIT_TTL_INVALID")
    if not (0 <= args.max_gate_rotations <= 15):
        raise SystemExit("MAIN_PERMIT_GATE_ROTATIONS_INVALID")

    check = subprocess.run(
        ["python3", str(READINESS)],
        text=True,
        capture_output=True,
    )
    if check.returncode != 0:
        raise SystemExit("MAIN_READINESS_FAILED:" + (check.stderr.strip() or check.stdout.strip()))
    if not args.activate:
        print("MAIN_READY_FOR_EXPLICIT_ACTIVATION_NO_STATE_CHANGED")
        return 0

    controller = TRUST / "openai_platform_main_controller.py"
    spec = importlib.util.spec_from_file_location("skatai_main_approval_controller", controller)
    if spec is None or spec.loader is None:
        raise SystemExit("MAIN_APPROVAL_IMPORT_FAILED")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    now = time.time()
    policy_hashes = mod.approval_policy_hashes()
    lock = mod.load_execution_lock()
    queue = mod.load_gate_queue()
    active_entry = mod.gate_queue_entry_for_lock(queue)
    if active_entry is None:
        raise SystemExit("MAIN_ACTIVE_LOCK_NOT_IN_TRUSTED_GATE_QUEUE")
    start_index = int(active_entry["index"])
    end_index = min(len(queue["entries"]) - 1, start_index + args.max_gate_rotations)
    authorized_gates = [
        {k: entry[k] for k in ("index", "gate_id", "goal_path_id", "lock_sha256")}
        for entry in queue["entries"][start_index : end_index + 1]
    ]
    actual_rotations = max(0, len(authorized_gates) - 1)
    approval = {
        "schema": mod.REENABLE_APPROVAL_SCHEMA,
        "policy_epoch": mod.POLICY_EPOCH,
        "policy_hashes": policy_hashes,
        "approved": True,
        "approved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "reason": args.reason,
    }
    triggers = ["PRIMARY_NEXT_STEP"]
    if args.allow_user_input:
        triggers.append("USER_DIRECTIVE")
    if args.allow_external_events:
        triggers.append("EXTERNAL_EVENT")
    permit = {
        "schema": mod.WORK_PERMIT_SCHEMA,
        "policy_epoch": mod.POLICY_EPOCH,
        "policy_hashes": policy_hashes,
        "permit_id": secrets.token_hex(16),
        "mode": "BOUNDED_GATE",
        "approved": True,
        "approved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "expires_at_epoch": int(now + args.ttl_minutes * 60),
        "max_model_submits_total": args.max_total,
        "max_autonomous_submits_total": args.max_autonomous,
        "max_total_tokens": args.max_total_tokens,
        "allowed_trigger_types": triggers,
        "gate_queue_sha256": mod.sha256_file(mod.GATE_QUEUE),
        "gate_queue_start_index": start_index,
        "max_gate_rotations": actual_rotations,
        "authorized_gates": authorized_gates,
        "reason": args.reason,
    }
    atomic_json(APPROVAL, approval)
    atomic_json(PERMIT, permit)

    HARD_DISABLE.unlink(missing_ok=True)
    ok, reason = mod.reenable_approval_valid()
    checked_permit, permit_reason = mod.work_permit_valid()
    if not ok or checked_permit is None:
        HARD_DISABLE.write_text(
            "HARD_DISABLED=1\nreason=Post-approval validation failed.\n",
            encoding="utf-8",
        )
        os.chmod(HARD_DISABLE, 0o600)
        raise SystemExit(
            "MAIN_APPROVAL_POSTCHECK_FAILED:"
            + str(reason if not ok else permit_reason)
        )
    print(
        "MAIN_REENABLE_APPROVED_AND_ACTIVATED "
        f"permit_id={permit['permit_id']} "
        f"max_total={args.max_total} max_autonomous={args.max_autonomous} "
        f"gate_rotations={actual_rotations} ttl_minutes={args.ttl_minutes}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
