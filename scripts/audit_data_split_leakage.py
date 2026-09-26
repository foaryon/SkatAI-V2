#!/usr/bin/env python3
"""Deterministic evidence audit for frozen split/leakage controls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path("/workspace/skatai-v2")
P = ROOT / "provenance"


def load(name: str):
    return json.loads((P / name).read_text(encoding="utf-8"))


def sha(name: str) -> str:
    return hashlib.sha256((P / name).read_bytes()).hexdigest()


def main() -> int:
    split = load("BIDDING_SPLIT_V1.json")
    d3 = load("BIDDING_D3_SPLIT_INTEGRITY_CORRECTION_20260925.json")
    registry = load("DATA_REGISTRY.json")
    card = load("CARDPLAY_DIAGNOSTIC_SPLIT_GUARD_20260925.json")
    v5 = load("V5_LEARNER_AUCTION_ROLE_GATE_20260925.json")

    holdouts = registry.get("holdout_exposures") or []
    if split.get("status") != "FROZEN":
        raise SystemExit("SPLIT_NOT_FROZEN")
    if d3.get("classification") != "PASS_FALSE_POSITIVE_RECONCILED":
        raise SystemExit("D3_SPLIT_AUDIT_NOT_RECONCILED")
    verification = d3.get("verification") or {}
    report_text = str(verification.get("corrected_exact_manifest_audit") or "")
    if "72 manifest-listed shards" not in report_text or "split mismatches all 0" not in report_text:
        raise SystemExit("D3_SPLIT_ZERO_MISMATCH_EVIDENCE_MISSING")
    if card.get("classification") != "CONCLUDE" or "Frozen V2 bidding split v1" not in str(card.get("input_boundary")):
        raise SystemExit("CARDPLAY_SPLIT_GUARD_NOT_BOUND")
    if v5.get("classification") != "PASS_D1_EXPLORATORY_ONLY":
        raise SystemExit("V5_SPLIT_STATUS_UNEXPECTED")
    if len(holdouts) < 3:
        raise SystemExit("HOLDOUT_EXPOSURE_REGISTRY_INCOMPLETE")
    for row in holdouts:
        rule = str(row.get("rule") or "").lower()
        if "preserve frozen split membership" not in rule:
            raise SystemExit("HOLDOUT_RULE_DOES_NOT_PRESERVE_SPLIT")
    active_assets = [
        {
            "asset_id": row.get("asset_id"),
            "active_training_use": row.get("active_training_use"),
            "trust": row.get("trust"),
            "status": row.get("status"),
        }
        for row in registry.get("entries") or []
        if row.get("active_training_use") not in (False, None)
    ]

    out = {
        "schema": "skatai.v2.data-split-leakage-audit.v1",
        "status": "PASS_STRUCTURAL_CONTROLS",
        "split_status": split.get("status"),
        "split_sha256": sha("BIDDING_SPLIT_V1.json"),
        "d3_classification": d3.get("classification"),
        "d3_sha256": sha("BIDDING_D3_SPLIT_INTEGRITY_CORRECTION_20260925.json"),
        "cardplay_guard_classification": card.get("classification"),
        "cardplay_guard_sha256": sha("CARDPLAY_DIAGNOSTIC_SPLIT_GUARD_20260925.json"),
        "v5_classification": v5.get("classification"),
        "v5_sha256": sha("V5_LEARNER_AUCTION_ROLE_GATE_20260925.json"),
        "holdout_exposure_count": len(holdouts),
        "holdout_incidents": [row.get("incident_id") for row in holdouts],
        "active_registry_assets": active_assets,
        "registry_sha256": sha("DATA_REGISTRY.json"),
        "scope_limit": (
            "This audit verifies registered frozen split identities, known holdout-exposure quarantine rules, "
            "D3 exact split integrity, cardplay train-only guard binding, and V5 exploratory split status. "
            "It does not infer leakage freedom for unregistered future datasets or experiments."
        ),
    }
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
