"""Frozen holdout records cannot enter cardplay weakness selection."""

from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "diagnose-b0-cardplay-agreement.py"
    spec = importlib.util.spec_from_file_location("cardplay_diagnostic_script", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_cardplay_diagnostic_filters_split_before_reconstruction(tmp_path, monkeypatch):
    module = load_script()

    records = [
        {"date": "2022-06-01", "players": ["A", "B", "C"], "cardplay_usable": True},
        {"date": "2023-06-01", "players": ["A", "B", "C"], "cardplay_usable": True},
        {"date": "2022-06-01", "players": ["Kermit", "B", "C"], "cardplay_usable": True},
        {"date": "2024-06-01", "players": ["A", "B", "C"], "cardplay_usable": True},
        {"date": "", "players": ["A", "B", "C"], "cardplay_usable": True},
    ]
    source = tmp_path / "games.jsonl"
    source.write_text("".join(json.dumps(row) + "\n" for row in records))
    reconstructed = []

    def record_reconstruction(row):
        reconstructed.append(row)
        return (row,)

    monkeypatch.setattr(module, "reconstruct_cardplay_events", record_reconstruction)
    events, report = module.load_events(source, max_rows=5)

    assert events == [records[0]]
    assert reconstructed == [records[0]]
    assert report["counts"] == {
        "candidate_games": 1,
        "excluded_external_bot_holdout": 1,
        "excluded_quarantine_date": 1,
        "excluded_test": 1,
        "excluded_validation": 1,
        "reconstruction_ok": 1,
        "rows_seen": 5,
    }


def test_cardplay_diagnostic_choice_filter_excludes_forced_actions(tmp_path, monkeypatch):
    module = load_script()
    source = tmp_path / "games.jsonl"
    source.write_text(json.dumps({
        "date": "2022-06-01", "players": ["A", "B", "C"],
        "cardplay_usable": True,
    }) + "\n")
    forced = SimpleNamespace(observation=SimpleNamespace(legal_cards=("CA",)))
    choice = SimpleNamespace(observation=SimpleNamespace(legal_cards=("CA", "CK")))
    monkeypatch.setattr(module, "reconstruct_cardplay_events", lambda _row: (forced, choice))
    events, report = module.load_events(source, max_rows=1, choices_only=True)
    assert events == [choice]
    assert report["counts"]["forced_events_excluded"] == 1


def test_cardplay_diagnostic_rejects_wrong_input_before_loading(tmp_path, monkeypatch):
    module = load_script()
    source = tmp_path / "sample.jsonl"
    source.write_text("{}\n")
    monkeypatch.setattr(
        module, "load_events", lambda *_args, **_kwargs: pytest.fail("loaded unverified input")
    )
    with pytest.raises(ValueError, match="INPUT_SHA256_MISMATCH"):
        module.run_diagnostic(
            input_path=source,
            expected_input_sha256="0" * 64,
            source_asset_id="legacy-v1-canonical-corpus:bounded-sample",
            expected_selection_sha256="0" * 64,
            max_rows=1,
            per_stratum=1,
            seed=1,
            skatzero_root=tmp_path,
            skatzero_python=tmp_path / "python",
        )


def test_cardplay_diagnostic_rejects_changed_selection_before_b0(tmp_path, monkeypatch):
    module = load_script()
    source = tmp_path / "sample.jsonl"
    source.write_bytes(b"sample\n")
    observation = SimpleNamespace(seat=0, declarer=0, contract="C", current_trick=())
    event = SimpleNamespace(
        game_id="game-1", source_semantic_sha256="semantic", raw_sha256="raw",
        play_ordinal=0, observation=observation,
    )
    monkeypatch.setattr(module, "load_events", lambda *_args, **_kwargs: ([event], {}))
    monkeypatch.setattr(module, "deterministic_balanced_sample", lambda *_args, **_kwargs: (event,))
    monkeypatch.setattr(module, "FrozenB0CardplayPolicy", lambda *_args: pytest.fail("B0 started"))
    with pytest.raises(ValueError, match="SELECTION_SHA256_MISMATCH"):
        module.run_diagnostic(
            input_path=source,
            expected_input_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            source_asset_id="registered-sample",
            expected_selection_sha256="0" * 64,
            max_rows=1, per_stratum=1, seed=1,
            skatzero_root=tmp_path, skatzero_python=tmp_path / "python",
        )
