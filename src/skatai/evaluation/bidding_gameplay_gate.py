from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from skatai.data.bidding import split_for_game
from skatai.evaluation.skatzero_bidding_baseline import (
    frozen_b0_discard_and_decl,
    frozen_b0_max_bid,
    frozen_b0_skat_or_hand,
)
from skatai.game.bidding import BID_VALUES
from skatai.gameplay.bidding import (
    AuctionResult,
    NeuralBiddingPolicy,
    simulate_auction,
    simulate_max_bid_auction,
)
from skatai.gameplay.skatzero_bidding import max_accepted_bids_by_seat

SCHEMA = "skatai.v2.b0-vs-b1-local-paired-gameplay.v1"


@dataclass(frozen=True)
class Deal:
    game_identity: str
    hands: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]
    skat: tuple[str, str]


@dataclass(frozen=True)
class DeclaredGame:
    winner_seat: int
    winning_bid: int
    actual_contract: str
    normalized_gametype: str
    blind_hand: bool
    open_hand: bool
    discards: tuple[str, str] | None
    opponent_bids_relative: tuple[int, int]
    max_accepted_bids_by_seat: tuple[int, int, int]
    declaration_trace: tuple[str, ...]
    declaration_elapsed_s: float


def _seed(*parts: object) -> int:
    digest = hashlib.sha256(":".join(str(x) for x in parts).encode()).digest()
    return int.from_bytes(digest[:4], "big")


def _selection_key(seed: int, identity: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{seed}:{identity}".encode()).digest(), "big")


def select_deals(
    canonical_path: Path,
    *,
    count: int,
    seed: int,
) -> list[Deal]:
    heap: list[tuple[int, int, Deal]] = []
    serial = 0
    with canonical_path.open("r", encoding="utf-8") as f:
        for line in f:
            game = json.loads(line)
            if split_for_game(game) != "test":
                continue
            hands = tuple(tuple(str(c) for c in hand) for hand in game["initial_hands"])
            skat = tuple(str(c) for c in game["skat_initial"])
            flat = [c for hand in hands for c in hand] + list(skat)
            if len(hands) != 3 or any(len(h) != 10 for h in hands):
                continue
            if len(skat) != 2 or len(flat) != 32 or len(set(flat)) != 32:
                continue
            identity = str(game["semantic_sha256"])
            deal = Deal(identity, hands, (skat[0], skat[1]))
            key = _selection_key(seed, identity)
            entry = (-key, serial, deal)
            serial += 1
            if len(heap) < count:
                heapq.heappush(heap, entry)
            elif entry > heap[0]:
                heapq.heapreplace(heap, entry)
    selected = [(-k, d) for k, _, d in heap]
    selected.sort(key=lambda x: x[0])
    if len(selected) != count:
        raise ValueError(f"INSUFFICIENT_DEALS:{len(selected)}<{count}")
    return [d for _, d in selected]


def _auction_stable(auction: AuctionResult) -> dict[str, Any]:
    return {
        "winner": auction.winner,
        "winning_bid": auction.winning_bid,
        "all_pass": auction.all_pass,
        "max_accepted_bids_by_seat": list(max_accepted_bids_by_seat(auction)),
        "decisions": [
            {
                "ordinal": d.ordinal,
                "actor": d.actor,
                "bidder": d.bidder,
                "answerer": d.answerer,
                "bid_index": d.bid_index,
                "current_offer": d.current_offer,
                "decision_role": d.decision_role,
                "probability_continue": d.probability_continue,
                "native_action": d.native_action,
            }
            for d in auction.decisions
        ],
    }


def _downstream_identity(auction: AuctionResult) -> tuple[Any, ...]:
    return (
        auction.winner,
        auction.winning_bid,
        max_accepted_bids_by_seat(auction),
    )


def mixed_auction(
    deal: Deal,
    *,
    candidate_seat: int,
    b0_max_bids: Sequence[int],
    b1: NeuralBiddingPolicy,
    threshold: float,
) -> AuctionResult:
    if candidate_seat not in (0, 1, 2):
        raise ValueError(f"BAD_CANDIDATE_SEAT:{candidate_seat}")

    def probability(
        hand: Sequence[str],
        actor: int,
        bidder: int,
        answerer: int,
        bid_index: int,
        decision_role: str,
    ) -> float:
        if actor == candidate_seat:
            return b1.probability_continue(
                hand, actor, bidder, answerer, bid_index, decision_role
            )
        return 1.0 if int(b0_max_bids[actor]) >= BID_VALUES[bid_index] else 0.0

    return simulate_auction(deal.hands, probability, threshold=threshold)


def _parse_hand_contract(text: str) -> tuple[str, bool, bool]:
    if text.startswith("NHO."):
        return "N", True, True
    if text in {"CH", "SH", "HH", "DH", "GH", "NH"}:
        return text[0], True, False
    raise ValueError(f"UNSUPPORTED_HAND_DECL:{text}")


def _parse_pickup_contract(
    text: str,
) -> tuple[str, bool, bool, tuple[str, str]]:
    parts = text.split(".")
    mode = parts[0]
    if mode not in {"C", "S", "H", "D", "G", "N", "NO"}:
        raise ValueError(f"UNSUPPORTED_PICKUP_DECL:{text}")
    if len(parts) < 3:
        raise ValueError(f"MISSING_DISCARDS:{text}")
    return (
        "N" if mode == "NO" else mode,
        False,
        mode == "NO",
        (parts[1], parts[2]),
    )


def declare_with_frozen_b0(
    skatzero_root: Path,
    deal: Deal,
    auction: AuctionResult,
    *,
    seed: int,
    accuracy: int,
    bid_threshold: float,
) -> DeclaredGame | None:
    if auction.winner is None:
        return None
    winner = int(auction.winner)
    winning_bid = int(auction.winning_bid or 0)
    accepted = max_accepted_bids_by_seat(auction)
    opp1 = int(accepted[(winner + 1) % 3])
    opp2 = int(accepted[(winner + 2) % 3])

    first = frozen_b0_skat_or_hand(
        skatzero_root,
        deal.hands[winner],
        winner,
        opp1,
        opp2,
        winning_bid,
        seed=seed,
        accuracy=accuracy,
        bid_threshold=bid_threshold,
    )
    elapsed = first.elapsed_s
    trace = [first.declaration]

    if first.declaration == "s":
        hand12 = list(deal.hands[winner]) + list(deal.skat)
        second = frozen_b0_discard_and_decl(
            skatzero_root,
            hand12,
            winner,
            opp1,
            opp2,
            winning_bid,
            seed=seed,
        )
        elapsed += second.elapsed_s
        trace.append(second.declaration)
        contract, blind, open_hand, discards = _parse_pickup_contract(second.declaration)
    else:
        contract, blind, open_hand = _parse_hand_contract(first.declaration)
        discards = None

    normalized = "D" if contract in {"C", "S", "H", "D"} else contract
    return DeclaredGame(
        winner_seat=winner,
        winning_bid=winning_bid,
        actual_contract=contract,
        normalized_gametype=normalized,
        blind_hand=blind,
        open_hand=open_hand,
        discards=discards,
        opponent_bids_relative=(opp1, opp2),
        max_accepted_bids_by_seat=accepted,
        declaration_trace=tuple(trace),
        declaration_elapsed_s=elapsed,
    )


def _install_skatzero_import(skatzero_root: Path) -> None:
    root = str(skatzero_root.resolve())
    if root not in sys.path:
        sys.path.insert(0, root)


def build_dealer(
    skatzero_root: Path,
    deal: Deal,
    declared: DeclaredGame,
):
    _install_skatzero_import(skatzero_root)
    from skatzero.evaluation.utils import swap_bids, swap_colors
    from skatzero.game.dealer import Dealer

    winner = declared.winner_seat
    solo = list(deal.hands[winner])
    if declared.blind_hand:
        skat_out = list(deal.skat)
    else:
        solo += list(deal.skat)
        if declared.discards is None:
            raise ValueError("PICKUP_WITHOUT_DISCARDS")
        for card in declared.discards:
            try:
                solo.remove(card)
            except ValueError as exc:
                raise ValueError(f"DISCARD_NOT_OWNED:{card}") from exc
        skat_out = list(declared.discards)

    deck = (
        solo
        + list(deal.hands[(winner + 1) % 3])
        + list(deal.hands[(winner + 2) % 3])
        + skat_out
    )
    if len(deck) != 32 or len(set(deck)) != 32:
        raise ValueError("INVALID_DECLARED_DECK")

    dealer = Dealer(None)
    dealer.starting_player = (3 - winner) % 3
    dealer.deck = deck
    accepted = declared.max_accepted_bids_by_seat
    dealer.max_bids = [
        int(accepted[winner]),
        int(accepted[(winner + 1) % 3]),
        int(accepted[(winner + 2) % 3]),
    ]
    dealer.parse_and_set_bid(1)
    dealer.parse_and_set_bid(2)
    dealer.blind_hand = declared.blind_hand
    dealer.open_hand = declared.open_hand

    if declared.normalized_gametype == "D" and declared.actual_contract != "D":
        suit = declared.actual_contract
        dealer.deck = swap_colors(dealer.deck, "D", suit)
        dealer.bids[1] = swap_bids(dealer.bids[1], "D", suit)
        dealer.bids[2] = swap_bids(dealer.bids[2], "D", suit)
    return dealer


def run_frozen_b0_cardplay(
    skatzero_root: Path,
    model_root: Path,
    deal: Deal,
    declared: DeclaredGame | None,
    *,
    seed: int,
) -> tuple[tuple[float, float, float], float]:
    if declared is None:
        return (0.0, 0.0, 0.0), 0.0

    _install_skatzero_import(skatzero_root)
    from skatzero.evaluation.eval_env import EvalEnv
    from skatzero.evaluation.simulation import load_model

    gt = declared.normalized_gametype
    dealer = build_dealer(skatzero_root, deal, declared)
    agents = [load_model(str(model_root / f"{gt}_{i}.pth")) for i in range(3)]
    env = EvalEnv(seed=seed, gametype=gt, dealers=[dealer])
    env.set_agents(agents)

    start = time.perf_counter()
    _, rewards = env.run(is_training=False)
    elapsed = time.perf_counter() - start

    winner = declared.winner_seat
    mapped = [0.0, 0.0, 0.0]
    mapped[winner] = float(rewards[0])
    mapped[(winner + 1) % 3] = float(rewards[1])
    mapped[(winner + 2) % 3] = float(rewards[2])
    return (mapped[0], mapped[1], mapped[2]), elapsed


def evaluate_deal(
    skatzero_root: Path,
    model_root: Path,
    b1: NeuralBiddingPolicy,
    deal: Deal,
    *,
    threshold: float,
    accuracy: int,
    bid_threshold: float,
    master_seed: int,
) -> dict[str, Any]:
    b0_results = []
    for seat in range(3):
        r = frozen_b0_max_bid(
            skatzero_root,
            deal.hands[seat],
            seat,
            seed=_seed(master_seed, deal.game_identity, "b0-bid", seat),
            accuracy=accuracy,
            bid_threshold=bid_threshold,
        )
        b0_results.append(r)

    b0_max = tuple(x.max_bid for x in b0_results)
    baseline_auction = simulate_max_bid_auction(deal.hands, b0_max)
    downstream_seed = _seed(master_seed, deal.game_identity, "downstream")
    baseline_decl = declare_with_frozen_b0(
        skatzero_root,
        deal,
        baseline_auction,
        seed=downstream_seed,
        accuracy=accuracy,
        bid_threshold=bid_threshold,
    )
    baseline_rewards, baseline_cardplay_s = run_frozen_b0_cardplay(
        skatzero_root,
        model_root,
        deal,
        baseline_decl,
        seed=downstream_seed,
    )

    paired = []
    for candidate_seat in range(3):
        treatment_auction = mixed_auction(
            deal,
            candidate_seat=candidate_seat,
            b0_max_bids=b0_max,
            b1=b1,
            threshold=threshold,
        )
        if _downstream_identity(treatment_auction) == _downstream_identity(baseline_auction):
            treatment_decl = baseline_decl
            treatment_rewards = baseline_rewards
            treatment_cardplay_s = 0.0
            reused = True
        else:
            treatment_decl = declare_with_frozen_b0(
                skatzero_root,
                deal,
                treatment_auction,
                seed=downstream_seed,
                accuracy=accuracy,
                bid_threshold=bid_threshold,
            )
            treatment_rewards, treatment_cardplay_s = run_frozen_b0_cardplay(
                skatzero_root,
                model_root,
                deal,
                treatment_decl,
                seed=downstream_seed,
            )
            reused = False

        paired.append(
            {
                "candidate_seat": candidate_seat,
                "baseline_reward": baseline_rewards[candidate_seat],
                "treatment_reward": treatment_rewards[candidate_seat],
                "delta": treatment_rewards[candidate_seat] - baseline_rewards[candidate_seat],
                "treatment_downstream_reused": reused,
                "treatment_auction": _auction_stable(treatment_auction),
                "treatment_declaration": None if treatment_decl is None else asdict(treatment_decl),
                "treatment_cardplay_elapsed_s": treatment_cardplay_s,
            }
        )

    return {
        "deal_identity": deal.game_identity,
        "b0_max_bids": list(b0_max),
        "b0_bid_elapsed_s": [x.elapsed_s for x in b0_results],
        "baseline_auction": _auction_stable(baseline_auction),
        "baseline_declaration": None if baseline_decl is None else asdict(baseline_decl),
        "baseline_rewards_by_original_seat": list(baseline_rewards),
        "baseline_cardplay_elapsed_s": baseline_cardplay_s,
        "paired": paired,
    }



def paired_delta_stats(deltas: Sequence[float]) -> dict[str, float | int | None]:
    n = len(deltas)
    if n == 0:
        return {
            "n": 0,
            "mean": None,
            "sample_std": None,
            "standard_error": None,
            "ci95_low": None,
            "ci95_high": None,
        }
    mean = sum(float(x) for x in deltas) / n
    if n < 2:
        return {
            "n": n,
            "mean": mean,
            "sample_std": None,
            "standard_error": None,
            "ci95_low": None,
            "ci95_high": None,
        }
    variance = sum((float(x) - mean) ** 2 for x in deltas) / (n - 1)
    sample_std = variance ** 0.5
    se = sample_std / (n ** 0.5)
    return {
        "n": n,
        "mean": mean,
        "sample_std": sample_std,
        "standard_error": se,
        "ci95_low": mean - 1.96 * se,
        "ci95_high": mean + 1.96 * se,
    }

def run_gate(
    canonical_path: Path,
    skatzero_root: Path,
    model_root: Path,
    b1_model: Path,
    *,
    deal_count: int,
    selection_seed: int,
    master_seed: int,
    threshold: float,
    accuracy: int,
    bid_threshold: float,
) -> dict[str, Any]:
    deals = select_deals(canonical_path, count=deal_count, seed=selection_seed)
    b1 = NeuralBiddingPolicy.load(b1_model, device="cpu")
    records = []
    start = time.perf_counter()
    for deal in deals:
        records.append(
            evaluate_deal(
                skatzero_root,
                model_root,
                b1,
                deal,
                threshold=threshold,
                accuracy=accuracy,
                bid_threshold=bid_threshold,
                master_seed=master_seed,
            )
        )
    elapsed = time.perf_counter() - start

    deltas = [
        float(pair["delta"])
        for record in records
        for pair in record["paired"]
    ]
    changed_auctions = sum(
        pair["treatment_auction"] != record["baseline_auction"]
        for record in records
        for pair in record["paired"]
    )
    return {
        "schema": SCHEMA,
        "interpretation": {
            "causal_treatment": "B1 learned bidding on evaluated seat only",
            "fixed_opponents": "B0 bidding on two opponent seats",
            "fixed_downstream": "frozen B0 declaration/discard/cardplay",
            "promotion_evidence": False,
            "reason": "local SkatZero payoff pre-gate; ISS/deployment-valid evidence still required",
        },
        "configuration": {
            "deal_count": deal_count,
            "paired_seat_observations": len(deltas),
            "selection_seed": selection_seed,
            "master_seed": master_seed,
            "b1_threshold": threshold,
            "b0_accuracy": accuracy,
            "b0_bid_threshold": bid_threshold,
        },
        "summary": {
            "paired_delta_stats": paired_delta_stats(deltas),
            "changed_auction_pairs": int(changed_auctions),
            "elapsed_s": elapsed,
        },
        "records": records,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--canonical", type=Path, required=True)
    p.add_argument("--skatzero-root", type=Path, required=True)
    p.add_argument("--model-root", type=Path, required=True)
    p.add_argument("--b1-model", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--deal-count", type=int, default=1)
    p.add_argument("--selection-seed", type=int, default=20260923)
    p.add_argument("--master-seed", type=int, default=20260923)
    p.add_argument("--b1-threshold", type=float, default=0.5)
    p.add_argument("--b0-accuracy", type=int, default=231)
    p.add_argument("--b0-bid-threshold", type=float, default=-5.0)
    args = p.parse_args()

    result = run_gate(
        args.canonical,
        args.skatzero_root,
        args.model_root,
        args.b1_model,
        deal_count=args.deal_count,
        selection_seed=args.selection_seed,
        master_seed=args.master_seed,
        threshold=args.b1_threshold,
        accuracy=args.b0_accuracy,
        bid_threshold=args.b0_bid_threshold,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
