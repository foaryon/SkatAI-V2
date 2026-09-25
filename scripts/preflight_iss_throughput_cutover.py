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
    p.add_argument("--output", type=Path)
    args = p.parse_args()

    result = evaluate(
        repo=args.repo,
        expected_commit=args.expected_commit,
        r9_runtime=args.r9_runtime,
        candidate_runtime=args.candidate_runtime,
        iss_password_file=args.iss_password_file,
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
