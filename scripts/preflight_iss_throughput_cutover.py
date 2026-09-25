from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from skatai.iss.effects import ISSEffectJournal


def _gate_worker_processes() -> list[int]:
    out: list[int] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return out
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", "replace"
            )
        except OSError:
            continue
        if "skatai.iss.gate_worker" in cmd:
            out.append(int(entry.name))
    return sorted(out)


def _active_games(runtime: Path) -> int:
    path = runtime / "active-games.json"
    if not path.is_file():
        return 0
    raw = json.loads(path.read_text(encoding="utf-8"))
    games = raw.get("games")
    if not isinstance(games, list):
        raise RuntimeError("ACTIVE_GAMES_NOT_LIST")
    return len(games)


def _pending_effects(runtime: Path) -> int:
    path = runtime / "effects.jsonl"
    if not path.exists():
        return 0
    return len(ISSEffectJournal(path).pending())


def _scored_arm_counts(runtime: Path) -> dict[str, int]:
    path = runtime / "gate-ledger.jsonl"
    counts = {"B0": 0, "B1": 0}
    if not path.is_file():
        return counts
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            status = row.get("status") or row.get("classification")
            arm = row.get("arm")
            if status == "SCORED" and arm in counts:
                counts[str(arm)] += 1
    return counts


EPOCH_SCHEMA = "skatai.v2.iss-throughput-deployment-epoch.v1"
EPOCH_FILE = "deployment-epoch.json"
MATERIAL_RUNTIME_FILES = (
    "gate-ledger.jsonl",
    "active-games.json",
    "effects.jsonl",
    "table-slots.json",
    "service.jsonl",
)


def _candidate_epoch_state(
    runtime: Path,
    *,
    expected_commit: str,
    tables: int,
    workers: int,
) -> tuple[dict[str, Any] | None, list[str]]:
    reasons: list[str] = []
    epoch_path = runtime / EPOCH_FILE
    material = [
        name for name in MATERIAL_RUNTIME_FILES
        if (runtime / name).exists() and (runtime / name).stat().st_size > 0
    ]
    if not epoch_path.is_file():
        if material:
            reasons.append(
                "CANDIDATE_MATERIAL_STATE_WITHOUT_EPOCH:"
                + ",".join(sorted(material))
            )
        return None, reasons

    try:
        epoch = json.loads(epoch_path.read_text(encoding="utf-8"))
    except Exception:
        return None, ["CANDIDATE_EPOCH_UNREADABLE"]
    if epoch.get("schema") != EPOCH_SCHEMA:
        reasons.append("CANDIDATE_EPOCH_SCHEMA_MISMATCH")
    if epoch.get("source_commit") != expected_commit:
        reasons.append(
            "CANDIDATE_EPOCH_COMMIT_MISMATCH:"
            + str(epoch.get("source_commit"))
        )
    if int(epoch.get("tables", -1)) != int(tables):
        reasons.append(
            "CANDIDATE_EPOCH_TABLES_MISMATCH:"
            + str(epoch.get("tables"))
        )
    if int(epoch.get("inference_workers", -1)) != int(workers):
        reasons.append(
            "CANDIDATE_EPOCH_WORKERS_MISMATCH:"
            + str(epoch.get("inference_workers"))
        )
    return epoch, reasons


def initialize_candidate_epoch(
    runtime: Path,
    *,
    expected_commit: str,
    tables: int,
    workers: int,
    parent_r9_runtime: Path,
) -> dict[str, Any]:
    epoch, reasons = _candidate_epoch_state(
        runtime,
        expected_commit=expected_commit,
        tables=tables,
        workers=workers,
    )
    if reasons:
        raise RuntimeError("EPOCH_INIT_BLOCKED:" + "|".join(reasons))
    if epoch is not None:
        return epoch

    from datetime import datetime, timezone

    payload = {
        "schema": EPOCH_SCHEMA,
        "source_commit": expected_commit,
        "tables": int(tables),
        "inference_workers": int(workers),
        "parent_frozen_r9_runtime": str(parent_r9_runtime),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": (
            "post-R9 deployment/throughput epoch; "
            "never merge with frozen R9 ledger"
        ),
    }
    runtime.mkdir(parents=True, exist_ok=True)
    path = runtime / EPOCH_FILE
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return payload


def _repo_state(repo: Path) -> tuple[str, bool]:
    head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        text=True,
        timeout=10,
    ).strip()
    dirty = bool(
        subprocess.check_output(
            ["git", "-C", str(repo), "status", "--porcelain"],
            text=True,
            timeout=10,
        ).strip()
    )
    return head, dirty


def evaluate(
    *,
    repo: Path,
    expected_commit: str,
    r9_runtime: Path,
    candidate_runtime: Path,
    iss_password_file: Path,
    tables: int,
    workers: int,
) -> dict[str, Any]:
    reasons: list[str] = []

    head, dirty = _repo_state(repo)
    if head != expected_commit:
        reasons.append(f"CANDIDATE_HEAD_MISMATCH:{head}")
    if dirty:
        reasons.append("CANDIDATE_WORKTREE_DIRTY")

    worker_pids = _gate_worker_processes()
    if worker_pids:
        reasons.append("GATE_WORKER_ALREADY_RUNNING")

    r9_active = _active_games(r9_runtime)
    if r9_active:
        reasons.append(f"R9_ACTIVE_GAMES:{r9_active}")

    r9_pending = _pending_effects(r9_runtime)
    if r9_pending:
        reasons.append(f"R9_PENDING_EFFECTS:{r9_pending}")

    scored = _scored_arm_counts(r9_runtime)
    for arm in ("B0", "B1"):
        if scored[arm] < 300:
            reasons.append(f"R9_GATE_NOT_COMPLETE:{arm}:{scored[arm]}/300")

    candidate_active = _active_games(candidate_runtime)
    if candidate_active:
        reasons.append(f"CANDIDATE_ACTIVE_GAMES:{candidate_active}")

    candidate_pending = _pending_effects(candidate_runtime)
    if candidate_pending:
        reasons.append(f"CANDIDATE_PENDING_EFFECTS:{candidate_pending}")

    epoch, epoch_reasons = _candidate_epoch_state(
        candidate_runtime,
        expected_commit=expected_commit,
        tables=tables,
        workers=workers,
    )
    reasons.extend(epoch_reasons)

    secret_ok = (
        iss_password_file.is_file()
        and os.access(iss_password_file, os.R_OK)
        and iss_password_file.stat().st_size > 0
    )
    if not secret_ok:
        reasons.append("ISS_PASSWORD_FILE_UNAVAILABLE")

    return {
        "schema": "skatai.v2.iss-throughput-cutover-preflight.v1",
        "ready": not reasons,
        "reasons": reasons,
        "candidate": {
            "repo": str(repo),
            "head": head,
            "expected_commit": expected_commit,
            "dirty": dirty,
            "runtime": str(candidate_runtime),
        },
        "r9": {
            "runtime": str(r9_runtime),
            "active_games": r9_active,
            "pending_effects": r9_pending,
            "scored_by_arm": scored,
        },
        "candidate_runtime_state": {
            "active_games": candidate_active,
            "pending_effects": candidate_pending,
            "epoch": epoch,
            "tables": int(tables),
            "inference_workers": int(workers),
        },
        "gate_worker_pids": worker_pids,
        "iss_password_file_ready": secret_ok,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--expected-commit", required=True)
    p.add_argument("--r9-runtime", type=Path, required=True)
    p.add_argument("--candidate-runtime", type=Path, required=True)
    p.add_argument("--iss-password-file", type=Path, required=True)
    p.add_argument("--tables", type=int, required=True)
    p.add_argument("--workers", type=int, required=True)
    p.add_argument("--initialize-epoch", action="store_true")
    p.add_argument("--output", type=Path)
    args = p.parse_args()

    result = evaluate(
        repo=args.repo,
        expected_commit=args.expected_commit,
        r9_runtime=args.r9_runtime,
        candidate_runtime=args.candidate_runtime,
        iss_password_file=args.iss_password_file,
        tables=args.tables,
        workers=args.workers,
    )
    if result["ready"] and args.initialize_epoch:
        result["candidate_runtime_state"]["epoch"] = (
            initialize_candidate_epoch(
                args.candidate_runtime,
                expected_commit=args.expected_commit,
                tables=args.tables,
                workers=args.workers,
                parent_r9_runtime=args.r9_runtime,
            )
        )

    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        tmp = args.output.with_suffix(args.output.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, args.output)
    print(text, end="")
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
