from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "skatai.v2.iss-deployment-identity.v1"


def canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_identities(
    *,
    b0_manifest: Mapping[str, Any],
    b1_manifest: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    b0_body = {
        "schema": SCHEMA,
        "arm": "B0",
        "baseline_id": b0_manifest["baseline_id"],
        "upstream_commit": b0_manifest["upstream_commit"],
        "pretrained_models": dict(b0_manifest["pretrained_models"]),
        "inference_api_sha256": b0_manifest["implementation_hashes"]["inference_api_py"],
        "bidding": {
            "implementation": "frozen upstream api.py BID",
            "accuracy": 231,
            "bid_threshold": -5.0,
        },
        "downstream": "frozen SkatZero declaration/discard/cardplay",
    }
    b0_sha = canonical_sha256(b0_body)

    b1_model = b1_manifest["artifacts"]["model.pt"]["sha256"]
    b1_body = {
        "schema": SCHEMA,
        "arm": "B1",
        "candidate_id": b1_manifest["candidate_id"],
        "parent_baseline": b1_manifest["parent_baseline"],
        "bidding_model_sha256": b1_model,
        "bidding_threshold": 0.5,
        "frozen_downstream_identity_sha256": b0_sha,
        "upstream_commit": b0_manifest["upstream_commit"],
        "pretrained_models": dict(b0_manifest["pretrained_models"]),
        "treatment": b1_manifest["treatment"],
    }
    b1_sha = canonical_sha256(b1_body)

    return {
        "B0": {
            "arm": "B0",
            "release_id": f"ISS-GATE-B0-{b0_sha[:16]}",
            "deployment_identity_sha256": b0_sha,
            "body": b0_body,
        },
        "B1": {
            "arm": "B1",
            "release_id": f"ISS-GATE-B1-{b1_sha[:16]}",
            "deployment_identity_sha256": b1_sha,
            "body": b1_body,
        },
    }


def load_identities(repo_root: Path) -> dict[str, dict[str, Any]]:
    b0 = json.loads((repo_root / "provenance/B0_SKATZERO_BASELINE.json").read_text())
    b1 = json.loads(
        (repo_root / "provenance/B1_BIDDING_LINEARISH_FULL_V1.json").read_text()
    )
    return build_identities(b0_manifest=b0, b1_manifest=b1)
