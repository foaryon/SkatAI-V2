from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "collect_iss_throughput_metrics.py"
    )
    spec = importlib.util.spec_from_file_location("iss_throughput_metrics", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_collect_reports_rate_integrity_and_mirror_lag(tmp_path):
    mod = _load()
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    rows = [
        {"status": "SCORED", "arm": "B0", "game_id": "g1", "recorded_unix_ns": 0},
        {
            "status": "SCORED",
            "arm": "B1",
            "game_id": "g2",
            "recorded_unix_ns": 100_000_000_000,
        },
        {
            "status": "SCORED",
            "arm": "B0",
            "game_id": "g3",
            "recorded_unix_ns": 200_000_000_000,
        },
        {
            "status": "PROTOCOL_FAILURE",
            "arm": "B0",
            "game_id": "g3",
            "recorded_unix_ns": 210_000_000_000,
        },
    ]
    (runtime / "gate-ledger.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in rows),
        encoding="utf-8",
    )
    (runtime / "active-games.json").write_text(
        json.dumps({"games": [{"table_id": "T1"}]}),
        encoding="utf-8",
    )
    (runtime / "table-slots.json").write_text(
        json.dumps({"slots": [{"table_id": "T1"}, {"table_id": "T2"}]}),
        encoding="utf-8",
    )
    receipts = runtime / "mirror-receipts"
    receipts.mkdir()
    for game_id, lag in (("g1", 2.0), ("g2", 4.0)):
        (receipts / f"{game_id}.json").write_text(
            json.dumps({"game_id": game_id, "lag_s": lag}),
            encoding="utf-8",
        )
    (runtime / "connection-events.jsonl").write_text(
        json.dumps({"event": "CONNECTED"}) + "\n"
        + json.dumps({"event": "CONNECTED"}) + "\n"
        + json.dumps({"event": "CONNECTION_CLOSED"}) + "\n",
        encoding="utf-8",
    )

    result = mod.collect(runtime, recent_games=3)

    assert result["games"]["scored"] == 3
    assert result["games"]["scored_by_arm"] == {"B0": 2, "B1": 1}
    assert result["games"]["duplicate_game_ids"] == 1
    assert result["games"]["recent_games_per_hour"] == 36.0
    assert result["games"]["recent_completion_gap_s"]["median"] == 100.0
    assert result["effects"]["chain_valid"] is True
    assert result["effects"]["pending"] == 0
    assert result["authority"] == {"active_games": 1, "table_slots": 2}
    assert result["mirror"]["receipts"] == 2
    assert result["mirror"]["lag_s"]["median"] == 3.0
    assert result["mirror"]["scored_games_without_receipt"] == 1
    assert result["connection_events"] == {
        "CONNECTED": 2,
        "CONNECTION_CLOSED": 1,
    }


def test_summary_empty_and_percentiles():
    mod = _load()
    assert mod._summary([])["p95"] is None
    summary = mod._summary([1, 2, 3, 4, 5])
    assert summary["median"] == 3.0
    assert summary["min"] == 1.0
    assert summary["max"] == 5.0
    assert 4.0 < summary["p95"] <= 5.0


def test_collect_does_not_call_legacy_runtime_unmirrored_without_receipt_schema(tmp_path):
    mod = _load()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "gate-ledger.jsonl").write_text(
        json.dumps(
            {
                "status": "SCORED",
                "arm": "B0",
                "game_id": "g1",
                "recorded_unix_ns": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = mod.collect(runtime)

    assert result["mirror"]["receipt_tracking_present"] is False
    assert result["mirror"]["receipts"] == 0
    assert result["mirror"]["scored_games_without_receipt"] is None
