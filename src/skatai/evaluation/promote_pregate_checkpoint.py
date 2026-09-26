from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from skatai.evaluation.bidding_gameplay_gate import (
    CHECKPOINT_SCHEMA,
    _atomic_write_json,
    _checkpoint_configuration,
    _sha256_file,
    load_deal_set,
)

SCHEMA = "skatai.v2.pregate-checkpoint-promotion.v1"


def promote_screen_to_confirmation(
    *,
    screen_result_path: Path,
    screen_deal_set_path: Path,
    confirmation_deal_set_path: Path,
    skatzero_root: Path,
    model_root: Path,
    b1_model: Path,
    output_checkpoint: Path,
    selection_seed: int = 20260923,
    master_seed: int = 20260923,
    threshold: float = 0.5,
    accuracy: int = 231,
    bid_threshold: float = -5.0,
    expected_screen_deals: int = 30,
    expected_confirmation_deals: int = 100,
) -> dict[str, Any]:
    screen_deals = load_deal_set(screen_deal_set_path)
    confirmation_deals = load_deal_set(confirmation_deal_set_path)
    if len(screen_deals) != expected_screen_deals:
        raise ValueError(
            f"SCREEN_DEAL_COUNT_MISMATCH:{len(screen_deals)}!={expected_screen_deals}"
        )
    if len(confirmation_deals) != expected_confirmation_deals:
        raise ValueError(
            "CONFIRMATION_DEAL_COUNT_MISMATCH:"
            f"{len(confirmation_deals)}!={expected_confirmation_deals}"
        )

    screen_ids = [d.game_identity for d in screen_deals]
    confirmation_ids = [d.game_identity for d in confirmation_deals]
    if screen_ids != confirmation_ids[:expected_screen_deals]:
        raise ValueError("SCREEN_NOT_CONFIRMATION_PREFIX")

    result = json.loads(screen_result_path.read_text(encoding="utf-8"))
    config = result.get("configuration") or {}
    if int(config.get("deal_count") or 0) != expected_screen_deals:
        raise ValueError("SCREEN_RESULT_DEAL_COUNT_MISMATCH")
    if int(config.get("paired_seat_observations") or 0) != expected_screen_deals * 3:
        raise ValueError("SCREEN_RESULT_PAIR_COUNT_MISMATCH")

    records = result.get("records") or []
    record_ids = [str(r["deal_identity"]) for r in records]
    if record_ids != screen_ids:
        raise ValueError("SCREEN_RESULT_RECORD_ORDER_MISMATCH")

    deal_source = {
        "kind": "deal_set",
        "path": str(confirmation_deal_set_path.resolve()),
        "sha256": _sha256_file(confirmation_deal_set_path),
    }
    configuration = _checkpoint_configuration(
        deal_source=deal_source,
        skatzero_root=skatzero_root,
        model_root=model_root,
        b1_model=b1_model,
        deal_count=expected_confirmation_deals,
        selection_seed=selection_seed,
        master_seed=master_seed,
        threshold=threshold,
        accuracy=accuracy,
        bid_threshold=bid_threshold,
    )

    elapsed = float((result.get("summary") or {}).get("elapsed_s") or 0.0)
    checkpoint = {
        "schema": CHECKPOINT_SCHEMA,
        "complete": False,
        "configuration": configuration,
        "selected_deal_identities": confirmation_ids,
        "completed_deals": expected_screen_deals,
        "elapsed_s": elapsed,
        "records": records,
        "promotion": {
            "schema": SCHEMA,
            "source_screen_result": str(screen_result_path.resolve()),
            "source_screen_result_sha256": _sha256_file(screen_result_path),
            "source_screen_deal_set_sha256": _sha256_file(screen_deal_set_path),
            "target_confirmation_deal_set_sha256": _sha256_file(
                confirmation_deal_set_path
            ),
            "reused_deals": expected_screen_deals,
            "reused_deal_identities": screen_ids,
            "overlap_provenance": {
                "count": expected_screen_deals,
                "relationship": "source screen record identities equal the prefix of target confirmation deal identities",
                "target_confirmation_prefix_identities": confirmation_ids[:expected_screen_deals],
            },
            "rule": "same-seed deterministic prefix; identical per-deal treatment configuration",
        },
    }
    _atomic_write_json(output_checkpoint, checkpoint)
    return checkpoint


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--screen-result", type=Path, required=True)
    p.add_argument("--screen-deal-set", type=Path, required=True)
    p.add_argument("--confirmation-deal-set", type=Path, required=True)
    p.add_argument("--skatzero-root", type=Path, required=True)
    p.add_argument("--model-root", type=Path, required=True)
    p.add_argument("--b1-model", type=Path, required=True)
    p.add_argument("--output-checkpoint", type=Path, required=True)
    p.add_argument("--selection-seed", type=int, default=20260923)
    p.add_argument("--master-seed", type=int, default=20260923)
    p.add_argument("--b1-threshold", type=float, default=0.5)
    p.add_argument("--b0-accuracy", type=int, default=231)
    p.add_argument("--b0-bid-threshold", type=float, default=-5.0)
    args = p.parse_args()

    x = promote_screen_to_confirmation(
        screen_result_path=args.screen_result,
        screen_deal_set_path=args.screen_deal_set,
        confirmation_deal_set_path=args.confirmation_deal_set,
        skatzero_root=args.skatzero_root,
        model_root=args.model_root,
        b1_model=args.b1_model,
        output_checkpoint=args.output_checkpoint,
        selection_seed=args.selection_seed,
        master_seed=args.master_seed,
        threshold=args.b1_threshold,
        accuracy=args.b0_accuracy,
        bid_threshold=args.b0_bid_threshold,
    )
    print(
        json.dumps(
            {
                "completed_deals": x["completed_deals"],
                "complete": x["complete"],
                "promotion": x["promotion"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
