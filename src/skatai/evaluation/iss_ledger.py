from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

LEDGER_SCHEMA = "skatai.v2.external-iss-game-ledger.v1"
VALID_ARMS = {"B0", "B1"}
VALID_STATUSES = {"SCORED", "INFRA_FAILURE", "PROTOCOL_FAILURE", "MODEL_FAILURE"}


@dataclass(frozen=True)
class ISSGateLedgerRecord:
    schema: str
    recorded_unix_ns: int
    game_id: str
    arm: str
    opponent: str
    seat: int
    status: str
    score: float | None
    declarer: bool | None
    contract: str | None
    winning_bid: int | None
    overbid: bool | None
    latency_ms_p50: float | None
    latency_ms_p95: float | None
    raw_sgf_sha256: str | None
    journal_sha256: str | None
    model_sha256: str
    source_commit: str
    failure_reason: str | None = None

    @classmethod
    def create(
        cls,
        *,
        game_id: str,
        arm: str,
        opponent: str,
        seat: int,
        status: str,
        model_sha256: str,
        source_commit: str,
        score: float | None = None,
        declarer: bool | None = None,
        contract: str | None = None,
        winning_bid: int | None = None,
        overbid: bool | None = None,
        latency_ms_p50: float | None = None,
        latency_ms_p95: float | None = None,
        raw_sgf_sha256: str | None = None,
        journal_sha256: str | None = None,
        failure_reason: str | None = None,
        recorded_unix_ns: int | None = None,
    ) -> "ISSGateLedgerRecord":
        arm = str(arm)
        status = str(status)
        opponent = str(opponent).strip()
        game_id = str(game_id).strip()
        model_sha256 = str(model_sha256).strip().lower()
        source_commit = str(source_commit).strip().lower()
        if arm not in VALID_ARMS:
            raise ValueError(f"BAD_ARM:{arm}")
        if status not in VALID_STATUSES:
            raise ValueError(f"BAD_STATUS:{status}")
        if seat not in (0, 1, 2):
            raise ValueError(f"BAD_SEAT:{seat}")
        if not opponent or not game_id:
            raise ValueError("EMPTY_GAME_OR_OPPONENT")
        for name, value in (("model_sha256", model_sha256),):
            if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError(f"BAD_{name.upper()}")
        if len(source_commit) < 7 or any(c not in "0123456789abcdef" for c in source_commit):
            raise ValueError("BAD_SOURCE_COMMIT")
        if status == "SCORED":
            if score is None or not math.isfinite(float(score)):
                raise ValueError("SCORED_REQUIRES_FINITE_SCORE")
            if failure_reason is not None:
                raise ValueError("SCORED_CANNOT_HAVE_FAILURE_REASON")
        else:
            if score is not None:
                raise ValueError("FAILURE_RECORD_MUST_NOT_HAVE_SCORE")
            if not failure_reason:
                raise ValueError("FAILURE_RECORD_REQUIRES_REASON")
        for name, value in (
            ("latency_ms_p50", latency_ms_p50),
            ("latency_ms_p95", latency_ms_p95),
        ):
            if value is not None and (not math.isfinite(float(value)) or float(value) < 0):
                raise ValueError(f"BAD_{name.upper()}")
        if latency_ms_p50 is not None and latency_ms_p95 is not None:
            if float(latency_ms_p95) < float(latency_ms_p50):
                raise ValueError("P95_LT_P50")
        for name, value in (
            ("raw_sgf_sha256", raw_sgf_sha256),
            ("journal_sha256", journal_sha256),
        ):
            if value is not None:
                v = str(value).lower()
                if len(v) != 64 or any(c not in "0123456789abcdef" for c in v):
                    raise ValueError(f"BAD_{name.upper()}")
        return cls(
            schema=LEDGER_SCHEMA,
            recorded_unix_ns=time.time_ns() if recorded_unix_ns is None else int(recorded_unix_ns),
            game_id=game_id,
            arm=arm,
            opponent=opponent,
            seat=int(seat),
            status=status,
            score=None if score is None else float(score),
            declarer=None if declarer is None else bool(declarer),
            contract=None if contract is None else str(contract),
            winning_bid=None if winning_bid is None else int(winning_bid),
            overbid=None if overbid is None else bool(overbid),
            latency_ms_p50=None if latency_ms_p50 is None else float(latency_ms_p50),
            latency_ms_p95=None if latency_ms_p95 is None else float(latency_ms_p95),
            raw_sgf_sha256=None if raw_sgf_sha256 is None else str(raw_sgf_sha256).lower(),
            journal_sha256=None if journal_sha256 is None else str(journal_sha256).lower(),
            model_sha256=model_sha256,
            source_commit=source_commit,
            failure_reason=None if failure_reason is None else str(failure_reason),
        )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb", buffering=1024 * 1024) as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


class ISSGateLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = path.with_suffix(path.suffix + ".lock")

    def records(self) -> list[ISSGateLedgerRecord]:
        out = []
        if not self.path.exists():
            return out
        seen: set[str] = set()
        with self.path.open("r", encoding="utf-8") as f:
            for line_number, line in enumerate(f, 1):
                if not line.strip():
                    continue
                raw = json.loads(line)
                if raw.get("schema") != LEDGER_SCHEMA:
                    raise ValueError(f"LEDGER_SCHEMA_MISMATCH_LINE:{line_number}")
                rec = ISSGateLedgerRecord(**raw)
                if rec.game_id in seen:
                    raise ValueError(f"DUPLICATE_GAME_ID_IN_LEDGER:{rec.game_id}")
                seen.add(rec.game_id)
                out.append(rec)
        return out

    def append(self, record: ISSGateLedgerRecord) -> None:
        lock_fd = os.open(self.lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            existing = {x.game_id for x in self.records()}
            if record.game_id in existing:
                raise ValueError(f"DUPLICATE_GAME_ID:{record.game_id}")
            encoded = (
                json.dumps(asdict(record), sort_keys=True, separators=(",", ":"))
                + "\n"
            )
            fd = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            try:
                os.write(fd, encoded.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def scored_for_gate(self) -> list[dict[str, Any]]:
        return [
            {
                "arm": r.arm,
                "opponent": r.opponent,
                "seat": r.seat,
                "score": r.score,
                "game_id": r.game_id,
            }
            for r in self.records()
            if r.status == "SCORED"
        ]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("ledger", type=Path)
    p.add_argument("--summary", action="store_true")
    args = p.parse_args()
    ledger = ISSGateLedger(args.ledger)
    records = ledger.records()
    if args.summary:
        by_status: dict[str, int] = {}
        by_arm: dict[str, int] = {}
        for r in records:
            by_status[r.status] = by_status.get(r.status, 0) + 1
            by_arm[r.arm] = by_arm.get(r.arm, 0) + 1
        print(json.dumps({
            "schema": LEDGER_SCHEMA,
            "records": len(records),
            "by_status": by_status,
            "by_arm": by_arm,
            "ledger_sha256": sha256_file(args.ledger) if args.ledger.exists() else None,
        }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
