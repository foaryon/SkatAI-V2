"""Stream a frozen public archive for bounded, previously unused DHS rules cases.

This is rules research only. It never adds records to training or opens an ISS
strength look. The plan fixes selection before any candidate is scored.
"""

from __future__ import annotations

import argparse
import bz2
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.request

from skatai.data.sgf import SGFParseError, parse_properties, parse_sgf_line
from scripts.validate_announced_hs_corpus import check_record


class HashedReader:
    def __init__(self, source):
        self.source = source
        self.digest = hashlib.sha256()
        self.count = 0

    def read(self, size=-1):
        data = self.source.read(size)
        self.digest.update(data)
        self.count += len(data)
        return data


def scan(plan: dict) -> dict:
    if plan["schema"] != "skatai.v2.hs-public-archive-heldout-plan.v1":
        raise ValueError("BAD_PLAN_SCHEMA")
    limit = int(plan["per_outcome_limit"])
    if not 1 <= limit <= 16:
        raise ValueError("BAD_SELECTION_LIMIT")
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()
    if commit != plan["source_commit"]:
        raise ValueError("SOURCE_COMMIT_MISMATCH")
    scorer_path = Path(__file__).with_name("validate_announced_hs_corpus.py")
    for path, key in ((Path(__file__), "validator_sha256"),
                      (scorer_path, "scorer_sha256")):
        if hashlib.sha256(path.read_bytes()).hexdigest() != plan[key]:
            raise ValueError(f"SOURCE_FILE_MISMATCH:{key}")
    excluded_raw = set(plan["excluded_raw_sha256"])
    excluded_semantic = set(plan["excluded_semantic_sha256"])
    selected: dict[str, list[tuple[str, int, bytes, dict]]] = {
        "win": [], "loss": []
    }
    counts = {"raw_lines": 0, "dhs_marker_lines": 0, "eligible_win": 0,
              "eligible_loss": 0, "excluded": 0, "parse_invalid": 0}
    req = urllib.request.Request(
        plan["url"], headers={"User-Agent": "SkatAI-V2-rules-research/1"}
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        reader = HashedReader(response)
        with bz2.BZ2File(reader, "rb") as archive:
            for ordinal, raw in enumerate(archive):
                counts["raw_lines"] += 1
                if ordinal < int(plan["exclude_first_lines"]) or b"DHS" not in raw:
                    continue
                counts["dhs_marker_lines"] += 1
                try:
                    record = parse_sgf_line("published-skatgame-2024-07", raw)
                except (SGFParseError, ValueError):
                    counts["parse_invalid"] += 1
                    continue
                source_outcomes = set(
                    parse_properties(raw.decode("utf-8")).get("R", "").split()
                ) & {"win", "loss"}
                if (len(source_outcomes) != 1
                        or record.get("classification") != "PARSED_PLAYED_GAME"
                        or record.get("announcement") != "DHS"
                        or record.get("play_count") != 30
                        or record.get("game_value") is None
                        or record.get("card_points") is None
                        or record.get("matadors") is None):
                    continue
                raw_hash = record["raw_sha256"]
                if (raw_hash in excluded_raw
                        or record["semantic_sha256"] in excluded_semantic):
                    counts["excluded"] += 1
                    continue
                outcome = "win" if record["won"] else "loss"
                counts[f"eligible_{outcome}"] += 1
                bucket = selected[outcome]
                bucket.append((raw_hash, ordinal, raw.strip(), record))
                bucket.sort(key=lambda item: item[0])
                del bucket[limit:]

        source_bytes = reader.count
        source_hash = reader.digest.hexdigest()
    if (source_bytes != int(plan["source_bytes"])
            or source_hash != plan["source_sha256"]):
        raise ValueError("PUBLISHED_ARCHIVE_IDENTITY_MISMATCH")

    results = []
    for outcome in ("loss", "win"):
        for raw_hash, ordinal, raw, record in selected[outcome]:
            try:
                check = check_record(record)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                check = {"status": "INVALID", "reason": str(exc)}
            results.append({
                "outcome_stratum": outcome, "raw_ordinal": ordinal,
                "raw_sha256": raw_hash,
                "semantic_sha256": record["semantic_sha256"],
                "raw_sgf": raw.decode("utf-8"),
                "oracle": check,
            })
    observed = [item["oracle"]["status"] for item in results]
    if any(status != "MATCH" for status in observed):
        decision = "FAIL"
    elif len(selected["loss"]) >= limit and len(selected["win"]) >= limit:
        decision = "PASS_BOUNDED_DHS_RULES_COVERAGE"
    else:
        decision = "INCONCLUSIVE_MISSING_TARGET_CELLS"
    return {
        "schema": "skatai.v2.hs-public-archive-heldout-result.v1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": commit,
        "plan_sha256": plan["plan_sha256"],
        "source_sha256_verified": source_hash,
        "source_bytes_verified": source_bytes,
        "counts": counts,
        "results": results,
        "decision": decision,
        "restriction": "Rules research only; archive may overlap historical corpus. No policy training, release acceptance, or frozen ISS strength look.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.plan.read_bytes()
    plan = json.loads(raw)
    expected = plan.pop("plan_sha256")
    canonical = (json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if hashlib.sha256(canonical).hexdigest() != expected:
        raise ValueError("PLAN_HASH_MISMATCH")
    plan["plan_sha256"] = expected
    result = scan(plan)
    data = (json.dumps(result, sort_keys=True, indent=2) + "\n").encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + args.output.name,
                                    dir=args.output.parent)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, args.output)
    print(json.dumps({"decision": result["decision"], "counts": result["counts"],
                      "selected": len(result["results"])}, sort_keys=True))


if __name__ == "__main__":
    main()
