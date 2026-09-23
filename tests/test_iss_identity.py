import json
from pathlib import Path

from skatai.evaluation.iss_identity import build_identities, load_identities


def test_deployment_identities_bind_full_treatment_stack():
    root = Path(__file__).resolve().parents[1]
    ids = load_identities(root)
    assert set(ids) == {"B0", "B1"}
    for arm in ("B0", "B1"):
        assert len(ids[arm]["deployment_identity_sha256"]) == 64
        assert ids[arm]["release_id"].startswith("ISS-GATE-" + arm + "-")
    assert ids["B0"]["deployment_identity_sha256"] != ids["B1"]["deployment_identity_sha256"]
    assert (
        ids["B1"]["body"]["frozen_downstream_identity_sha256"]
        == ids["B0"]["deployment_identity_sha256"]
    )


def test_b1_identity_changes_if_bidding_model_changes():
    b0 = {
        "baseline_id": "V2-B0",
        "upstream_commit": "a" * 40,
        "pretrained_models": {"D_0.pth": "1" * 64},
        "implementation_hashes": {"inference_api_py": "2" * 64},
    }
    b1 = {
        "candidate_id": "B1",
        "parent_baseline": "V2-B0",
        "artifacts": {"model.pt": {"sha256": "3" * 64}},
        "treatment": "bidding only",
    }
    a = build_identities(b0_manifest=b0, b1_manifest=b1)
    b1["artifacts"]["model.pt"]["sha256"] = "4" * 64
    b = build_identities(b0_manifest=b0, b1_manifest=b1)
    assert a["B0"] == b["B0"]
    assert a["B1"]["deployment_identity_sha256"] != b["B1"]["deployment_identity_sha256"]
