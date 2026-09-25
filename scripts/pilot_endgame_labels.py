#!/usr/bin/env python3
"""Deterministic synthetic integration pilot for offline endgame labels."""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from skatai.evaluation.endgame_labels import label_endgame_observation
from skatai.selfplay.cardplay import make_deal, run_cardplay

CONTRACTS = ("C", "S", "H", "D", "G", "N")


class FirstLegal:
    def __init__(self, views: list):
        self.views = views

    def play_card(self, observation):
        self.views.append(observation)
        return observation.legal_cards[0]


def run_pilot() -> dict:
    digest = hashlib.sha256()
    counts: Counter[str] = Counter()
    for ordinal in range(36):
        contract = CONTRACTS[ordinal % 6]
        declarer = (ordinal // 6) % 3
        pickup = bool((ordinal // 18) % 2)
        deal = make_deal(20260925 + ordinal)
        final_hands = list(deal.hands)
        final_hand = None
        final_skat = None
        if pickup:
            cards12 = (*deal.hands[declarer], *deal.skat)
            final_skat = cards12[:2]
            final_hand = cards12[2:]
            final_hands[declarer] = final_hand
        views: list = []
        run_cardplay(
            deal, contract=contract, declarer=declarer,
            final_hand=final_hand, final_skat=final_skat,
            policies=[FirstLegal(views) for _ in range(3)],
        )
        for view in views:
            if len(view.played_cards) < 21:
                continue
            row = label_endgame_observation(
                view, post_declaration_hands=final_hands,
            )
            digest.update(json.dumps(
                row, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode() + b"\n")
            counts["labels"] += 1
            counts[f"contract:{contract}"] += 1
            counts[f"mode:{'pickup' if pickup else 'hand'}"] += 1
            if len(view.legal_cards) > 1:
                counts["choice_labels"] += 1
            if row["target_card"] != view.legal_cards[0]:
                counts["teacher_first_legal_disagreements"] += 1
        counts["games"] += 1
    return {
        "schema": "skatai.v2.synthetic-endgame-label-pilot.v1",
        "seed_range": [20260925, 20260960],
        "policy": "first legal card for all seats",
        "source_scope": "synthetic deterministic D1 integration pilot only",
        "counts": dict(sorted(counts.items())),
        "learner_rows_sha256": digest.hexdigest(),
        "training_approved": False,
        "strength_claim_authorized": False,
    }


if __name__ == "__main__":
    print(json.dumps(run_pilot(), sort_keys=True, indent=2))
