"""Paired cardplay-only comparison on fixed, basic-contract positions.

Factories must produce fresh deterministic policy instances. The position,
declaration, bid and opponent policy family are held fixed. This module does
not select deals or authorize promotion from a small local comparison.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from skatai.selfplay.cardplay import CardplayEpisode, CardplayPolicy, make_deal, run_cardplay
from skatai.selfplay.scoring import DEFAULT_BASIC_CONTRACTS, score_basic_game
from skatai.selfplay.declaration import HAND_CONTRACTS, PICKUP_CONTRACTS

PolicyFactory = Callable[[int], CardplayPolicy]


@dataclass(frozen=True)
class CardplayPosition:
    deal_seed: int
    declarer: int
    contract: str
    winning_bid: int
    final_hand: tuple[str, ...] | None = None
    final_skat: tuple[str, str] | None = None


def _signed_score(position: CardplayPosition, episode: CardplayEpisode) -> int:
    deal = make_deal(position.deal_seed)
    cards = (
        (*position.final_hand, *position.final_skat)
        if position.final_hand is not None and position.final_skat is not None
        else (*deal.hands[position.declarer], *deal.skat)
    )
    score = score_basic_game(
        contract=position.contract,
        winning_bid=position.winning_bid,
        declarer_cards=cards,
        declarer_points=episode.declarer_final_points,
        declarer_tricks=sum(w == position.declarer for w in episode.trick_winners),
    )
    return score.signed_game_value


def evaluate_cardplay_position(
    position: CardplayPosition,
    *,
    control_factory: PolicyFactory,
    candidate_factory: PolicyFactory,
) -> dict:
    """Compare candidate at each seat against a shared all-control game."""
    if position.declarer not in (0, 1, 2):
        raise ValueError("BAD_CARDPLAY_EVALUATION_DECLARER")
    if position.contract not in DEFAULT_BASIC_CONTRACTS:
        raise ValueError("UNSUPPORTED_CARDPLAY_EVALUATION_CONTRACT")
    if (position.final_hand is None) != (position.final_skat is None):
        raise ValueError("INCOMPLETE_CARDPLAY_EVALUATION_DECLARATION")
    pickup = position.final_hand is not None
    if position.contract not in (PICKUP_CONTRACTS if pickup else HAND_CONTRACTS):
        raise ValueError("CARDPLAY_EVALUATION_CONTRACT_MODE_MISMATCH")
    deal = make_deal(position.deal_seed)
    fixed = dict(
        contract=position.contract,
        declarer=position.declarer,
        final_hand=position.final_hand,
        final_skat=position.final_skat,
        winning_bid=position.winning_bid,
    )
    baseline = run_cardplay(
        deal, policies=[control_factory(seat) for seat in range(3)], **fixed,
    )
    baseline_score = _signed_score(position, baseline)
    rows = []
    for candidate_seat in range(3):
        policies = [
            candidate_factory(seat) if seat == candidate_seat else control_factory(seat)
            for seat in range(3)
        ]
        treatment = run_cardplay(deal, policies=policies, **fixed)
        treatment_score = _signed_score(position, treatment)
        # Declarer gains when the signed declarer score rises; either defender
        # gains when it falls. Both defenders share the same game outcome.
        delta = (treatment_score - baseline_score) * (
            1 if candidate_seat == position.declarer else -1
        )
        rows.append({
            "candidate_seat": candidate_seat,
            "role": "DECLARER" if candidate_seat == position.declarer else "DEFENDER",
            "control_signed_declarer_score": baseline_score,
            "treatment_signed_declarer_score": treatment_score,
            "candidate_delta": delta,
            "control_play_count": len(baseline.plays),
            "treatment_play_count": len(treatment.plays),
        })
    return {
        "schema": "skatai.v2.cardplay-fixed-position-paired.v1",
        "deal_sha256": deal.identity_sha256,
        "deal_seed": position.deal_seed,
        "declarer": position.declarer,
        "contract": position.contract,
        "winning_bid": position.winning_bid,
        "picked_up_skat": position.final_hand is not None,
        "rows": rows,
        "position_cluster_mean_delta": sum(row["candidate_delta"] for row in rows) / 3,
    }
