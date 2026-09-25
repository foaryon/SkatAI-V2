from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "preflight_iss_throughput_cutover.py"
    )
    spec = importlib.util.spec_from_file_location("iss_cutover_preflight", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_gate(runtime: Path, *, b0: int, b1: int, active: int = 0):
    runtime.mkdir(parents=True, exist_ok=True)
    rows = []
    for arm, count in (("B0", b0), ("B1", b1)):
        for index in range(count):
            rows.append(
                json.dumps(
                    {
                        "status": "SCORED",
                        "arm": arm,
                        "game_id": f"{arm}:{index}",
                    }
                )
            )
    (runtime / "gate-ledger.jsonl").write_text(
        "\n".join(rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )
    games = [
        {
            "table_id": f"T{i}",
            "game_sequence": i,
            "assignment": {
                "arm": "B0",
                "stack": "kermit+zoot",
                "seat": 0,
                "per_arm_target": 300,
                "primary": True,
            },
            "protocol_offset": 0,
            "effect_offset": 0,
        }
        for i in range(active)
    ]
    (runtime / "active-games.json").write_text(
        json.dumps({"games": games}), encoding="utf-8"
    )


def test_ready_only_after_clean_completed_r9(monkeypatch, tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    repo.mkdir()
    r9 = tmp_path / "r9"
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    secret = tmp_path / "iss_password"
    secret.write_text("x", encoding="utf-8")
    _write_gate(r9, b0=300, b1=300)

    monkeypatch.setattr(mod, "_repo_state", lambda repo: ("abc", False))
    monkeypatch.setattr(mod, "_gate_worker_processes", lambda: [])
    monkeypatch.setattr(mod, "_pending_effects", lambda runtime: 0)

    result = mod.evaluate(
        repo=repo,
        expected_commit="abc",
        r9_runtime=r9,
        candidate_runtime=candidate,
        iss_password_file=secret,
    )
    assert result["ready"] is True
    assert result["reasons"] == []


def test_running_worker_active_game_and_open_gate_all_block(monkeypatch, tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    repo.mkdir()
    r9 = tmp_path / "r9"
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    secret = tmp_path / "iss_password"
    secret.write_text("x", encoding="utf-8")
    _write_gate(r9, b0=299, b1=298, active=1)

    monkeypatch.setattr(mod, "_repo_state", lambda repo: ("abc", False))
    monkeypatch.setattr(mod, "_gate_worker_processes", lambda: [123])
    monkeypatch.setattr(mod, "_pending_effects", lambda runtime: 0)

    result = mod.evaluate(
        repo=repo,
        expected_commit="abc",
        r9_runtime=r9,
        candidate_runtime=candidate,
        iss_password_file=secret,
    )
    assert result["ready"] is False
    assert "GATE_WORKER_ALREADY_RUNNING" in result["reasons"]
    assert "R9_ACTIVE_GAMES:1" in result["reasons"]
    assert "R9_GATE_NOT_COMPLETE:B0:299/300" in result["reasons"]
    assert "R9_GATE_NOT_COMPLETE:B1:298/300" in result["reasons"]


def test_pending_effect_or_dirty_candidate_blocks(monkeypatch, tmp_path):
    mod = _load()
    repo = tmp_path / "repo"
    repo.mkdir()
    r9 = tmp_path / "r9"
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    secret = tmp_path / "iss_password"
    secret.write_text("x", encoding="utf-8")
    _write_gate(r9, b0=300, b1=300)

    monkeypatch.setattr(mod, "_repo_state", lambda repo: ("wrong", True))
    monkeypatch.setattr(mod, "_gate_worker_processes", lambda: [])
    monkeypatch.setattr(
        mod,
        "_pending_effects",
        lambda runtime: 1 if runtime == r9 else 2,
    )

    result = mod.evaluate(
        repo=repo,
        expected_commit="expected",
        r9_runtime=r9,
        candidate_runtime=candidate,
        iss_password_file=secret,
    )
    assert result["ready"] is False
    assert any(x.startswith("CANDIDATE_HEAD_MISMATCH:") for x in result["reasons"])
    assert "CANDIDATE_WORKTREE_DIRTY" in result["reasons"]
    assert "R9_PENDING_EFFECTS:1" in result["reasons"]
    assert "CANDIDATE_PENDING_EFFECTS:2" in result["reasons"]
