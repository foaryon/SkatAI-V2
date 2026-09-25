#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent
CONTROLLER = ROOT / "openai_platform_main_controller.py"
MANIFEST = ROOT / "MANIFEST.sha256"

if not MANIFEST.is_file():
    raise SystemExit("MAIN_PREFLIGHT_MANIFEST_MISSING")
check = subprocess.run(
    ["sha256sum", "-c", str(MANIFEST)],
    cwd=str(ROOT),
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
if check.returncode != 0:
    raise SystemExit("MAIN_PREFLIGHT_MANIFEST_MISMATCH")

spec = importlib.util.spec_from_file_location("skatai_main_controller_preflight", CONTROLLER)
if spec is None or spec.loader is None:
    raise SystemExit("MAIN_PREFLIGHT_IMPORT_FAILED")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

if mod.HARD_DISABLE.exists():
    raise SystemExit("MAIN_PREFLIGHT_HARD_DISABLED")
ok, reason = mod.reenable_approval_valid()
if not ok:
    raise SystemExit("MAIN_PREFLIGHT_APPROVAL_INVALID:" + str(reason))
governor = mod.load_governor()
if governor.get("policy_epoch") != mod.POLICY_EPOCH:
    raise SystemExit("MAIN_PREFLIGHT_POLICY_EPOCH_MISMATCH")
if not governor.get("enabled"):
    raise SystemExit("MAIN_PREFLIGHT_GOVERNOR_DISABLED")
lock = mod.load_execution_lock()
permit, permit_reason = mod.work_permit_valid()
if permit is None:
    raise SystemExit("MAIN_PREFLIGHT_WORK_PERMIT_INVALID:" + str(permit_reason))
model = mod.desired_model()
print(
    "MAIN_PREFLIGHT_READY"
    f" policy_epoch={mod.POLICY_EPOCH}"
    f" primary={lock['primary']['gate_id']}"
    f" goal_path={lock['primary']['goal_path_id']}"
    f" model={model}"
)
