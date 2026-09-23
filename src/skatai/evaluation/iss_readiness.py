from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Mapping

READINESS_SCHEMA = "skatai.v2.external-iss-readiness.v1"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb", buffering=1024 * 1024) as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def assess_iss_gate_readiness(
    *,
    local_confirmation_decision: Path,
    b1_model: Path,
    expected_b1_sha256: str,
    protocol_files: Mapping[str, Path],
    environ: Mapping[str, str] | None = None,
) -> dict:
    env = os.environ if environ is None else environ
    checks: dict[str, dict] = {}

    if local_confirmation_decision.exists():
        decision = json.loads(local_confirmation_decision.read_text(encoding="utf-8"))
        next_action = decision.get("next_action")
        authorized = next_action == "REQUIRE_EXTERNAL_DEPLOYMENT_VALID_EVALUATION"
        checks["local_confirmation"] = {
            "ok": authorized,
            "next_action": next_action,
            "status": decision.get("status"),
        }
    else:
        checks["local_confirmation"] = {
            "ok": False,
            "reason": "decision_missing",
        }

    model_ok = b1_model.exists() and sha256_file(b1_model) == expected_b1_sha256
    checks["b1_model"] = {
        "ok": model_ok,
        "path": str(b1_model),
        "expected_sha256": expected_b1_sha256,
        "actual_sha256": sha256_file(b1_model) if b1_model.exists() else None,
    }

    files = {}
    for name, path in sorted(protocol_files.items()):
        files[name] = {
            "ok": path.exists(),
            "path": str(path),
            "sha256": sha256_file(path) if path.exists() else None,
        }
    checks["frozen_protocol_artifacts"] = {
        "ok": all(x["ok"] for x in files.values()),
        "files": files,
    }

    present = {
        "ISS_HOST": bool(env.get("ISS_HOST")),
        "ISS_CLIENT_ID": bool(env.get("ISS_CLIENT_ID")),
        "ISS_PASSWORD": bool(env.get("ISS_PASSWORD")),
        "ISS_PASSWORD_FILE": bool(env.get("ISS_PASSWORD_FILE")),
    }
    has_secret = present["ISS_PASSWORD"] or present["ISS_PASSWORD_FILE"]
    checks["iss_credentials"] = {
        "ok": present["ISS_HOST"] and present["ISS_CLIENT_ID"] and has_secret,
        "present": present,
        "accepted_secret_inputs": ["ISS_PASSWORD", "ISS_PASSWORD_FILE"],
        "secret_values_exposed": False,
    }

    ready = all(c["ok"] for c in checks.values())
    blockers = [name for name, c in checks.items() if not c["ok"]]
    return {
        "schema": READINESS_SCHEMA,
        "ready": ready,
        "blockers": blockers,
        "checks": checks,
    }
