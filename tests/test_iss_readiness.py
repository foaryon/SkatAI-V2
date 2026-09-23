import json
from pathlib import Path

from skatai.evaluation.iss_readiness import assess_iss_gate_readiness


def _write(path: Path, data: bytes):
    path.write_bytes(data)
    return path


def test_readiness_requires_external_authorization_and_credentials(tmp_path):
    model = _write(tmp_path / "model.pt", b"model")
    import hashlib
    model_sha = hashlib.sha256(b"model").hexdigest()

    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps({
        "status": "LOCAL_GATE_NOT_REGRESSING",
        "next_action": "REQUIRE_EXTERNAL_DEPLOYMENT_VALID_EVALUATION",
    }))

    protocols = {
        "protocol": _write(tmp_path / "protocol.json", b"{}"),
        "decision_rule": _write(tmp_path / "rule.json", b"{}"),
        "ledger": _write(tmp_path / "ledger.json", b"{}"),
        "quotas": _write(tmp_path / "quotas.json", b"{}"),
    }
    r = assess_iss_gate_readiness(
        local_confirmation_decision=decision,
        b1_model=model,
        expected_b1_sha256=model_sha,
        protocol_files=protocols,
        environ={"ISS_HOST": "h", "ISS_CLIENT_ID": "id", "ISS_PASSWORD": "secret"},
    )
    assert r["ready"] is True
    assert r["blockers"] == []
    assert r["checks"]["iss_credentials"]["secret_values_exposed"] is False


def test_readiness_refuses_to_start_before_local_confirmation(tmp_path):
    model = _write(tmp_path / "model.pt", b"model")
    import hashlib
    r = assess_iss_gate_readiness(
        local_confirmation_decision=tmp_path / "missing.json",
        b1_model=model,
        expected_b1_sha256=hashlib.sha256(b"model").hexdigest(),
        protocol_files={},
        environ={"ISS_HOST": "h", "ISS_CLIENT_ID": "id", "ISS_PASSWORD": "secret"},
    )
    assert r["ready"] is False
    assert "local_confirmation" in r["blockers"]


def test_readiness_reports_missing_credentials_without_values(tmp_path):
    model = _write(tmp_path / "model.pt", b"model")
    import hashlib
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps({
        "status": "LOCAL_GATE_NOT_REGRESSING",
        "next_action": "REQUIRE_EXTERNAL_DEPLOYMENT_VALID_EVALUATION",
    }))
    r = assess_iss_gate_readiness(
        local_confirmation_decision=decision,
        b1_model=model,
        expected_b1_sha256=hashlib.sha256(b"model").hexdigest(),
        protocol_files={},
        environ={},
    )
    assert r["ready"] is False
    assert r["checks"]["iss_credentials"]["present"] == {
        "ISS_HOST": False,
        "ISS_CLIENT_ID": False,
        "ISS_PASSWORD": False,
        "ISS_PASSWORD_FILE": False,
    }


def test_readiness_accepts_password_file_indicator(tmp_path):
    model = _write(tmp_path / "model.pt", b"model")
    import hashlib
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps({
        "status": "LOCAL_GATE_NOT_REGRESSING",
        "next_action": "REQUIRE_EXTERNAL_DEPLOYMENT_VALID_EVALUATION",
    }))
    r = assess_iss_gate_readiness(
        local_confirmation_decision=decision,
        b1_model=model,
        expected_b1_sha256=hashlib.sha256(b"model").hexdigest(),
        protocol_files={},
        environ={
            "ISS_HOST": "skatgame.net",
            "ISS_CLIENT_ID": "SkatAI",
            "ISS_PASSWORD_FILE": "/run/secrets/iss-password",
        },
    )
    assert r["checks"]["iss_credentials"]["ok"] is True
    assert r["checks"]["iss_credentials"]["secret_values_exposed"] is False
