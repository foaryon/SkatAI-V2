from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from skatai.data.bidding import split_for_game
from skatai.game.bidding import replay

BENCHMARK_SCHEMA = "skatai.v2.b0-cardplay-benchmark.v1"


def normalized_gametype(game_type: str) -> str:
    if game_type in {"CLUBS", "SPADES", "HEARTS", "DIAMONDS"}:
        return "D"
    if game_type == "GRAND":
        return "G"
    if game_type == "NULL":
        return "N"
    raise ValueError(f"UNSUPPORTED_GAME_TYPE:{game_type}")


def actor_max_accepted_bids(game: Mapping[str, Any]) -> list[int]:
    r = replay(game.get("bidding_history") or ())
    if not r.ok:
        raise ValueError(f"INVALID_BIDDING:{r.error}")
    out = [0, 0, 0]
    for action in r.actions:
        if action["target"] == "CONTINUE":
            actor = int(action["actor"])
            out[actor] = max(out[actor], int(action["before"]["current_offer"]))
    return out


def dealer_spec(game: Mapping[str, Any]) -> dict[str, Any]:
    gt = normalized_gametype(str(game["game_type"]))
    declarer = int(game["declarer"])
    hands = [list(x) for x in game["initial_hands"]]
    skat = list(game["skat_initial"])

    if bool(game["is_hand"]):
        solo = list(hands[declarer])
        skat_out = skat
    else:
        discards = list(game.get("discards") or ())
        if len(discards) != 2:
            raise ValueError("PICKUP_GAME_WITHOUT_TWO_DISCARDS")
        solo = list(hands[declarer]) + skat
        for card in discards:
            try:
                solo.remove(card)
            except ValueError as exc:
                raise ValueError(f"DISCARD_NOT_OWNED:{card}") from exc
        skat_out = discards

    deck = (
        solo
        + list(hands[(declarer + 1) % 3])
        + list(hands[(declarer + 2) % 3])
        + skat_out
    )
    if len(deck) != 32 or len(set(deck)) != 32:
        raise ValueError("INVALID_RECONSTRUCTED_DECK")

    max_bids = actor_max_accepted_bids(game)
    return {
        "game_identity": str(game["semantic_sha256"]),
        "source": str(game["source"]),
        "source_game_id": game.get("game_id"),
        "date": str(game.get("date") or ""),
        "normalized_gametype": gt,
        "actual_game_type": str(game["game_type"]),
        "declarer_original_seat": declarer,
        "starting_player": (3 - declarer) % 3,
        "deck": deck,
        "relative_max_bids": [
            max_bids[declarer],
            max_bids[(declarer + 1) % 3],
            max_bids[(declarer + 2) % 3],
        ],
        "blind_hand": bool(game["is_hand"]),
        "open_hand": bool(game["is_ouvert"]),
    }


def _selection_key(seed: int, game_identity: str) -> int:
    h = hashlib.sha256(f"{seed}:{game_identity}".encode("ascii")).digest()
    return int.from_bytes(h, "big")


def select_specs(
    canonical_path: Path,
    *,
    per_gametype: int = 12,
    seed: int = 20260923,
) -> list[dict[str, Any]]:
    heaps: dict[str, list[tuple[int, int, dict[str, Any]]]] = {"D": [], "G": [], "N": []}
    serial = 0
    with canonical_path.open("r", encoding="utf-8") as f:
        for line in f:
            game = json.loads(line)
            if split_for_game(game) != "test":
                continue
            if not game.get("cardplay_usable") or not game.get("play_complete"):
                continue
            try:
                spec = dealer_spec(game)
            except (KeyError, TypeError, ValueError):
                continue

            gt = spec["normalized_gametype"]
            key = _selection_key(seed, spec["game_identity"])
            entry = (-key, serial, spec)
            serial += 1
            heap = heaps[gt]
            if len(heap) < per_gametype:
                heapq.heappush(heap, entry)
            elif entry > heap[0]:
                heapq.heapreplace(heap, entry)

    result: list[dict[str, Any]] = []
    for gt in ("D", "G", "N"):
        selected = [(-neg_key, spec) for neg_key, _, spec in heaps[gt]]
        selected.sort(key=lambda x: x[0])
        if len(selected) != per_gametype:
            raise ValueError(f"INSUFFICIENT_BENCHMARK_DEALS:{gt}:{len(selected)}")
        for rank, (key, spec) in enumerate(selected):
            spec = dict(spec)
            spec["selection_rank"] = rank
            spec["selection_key_sha256_int"] = str(key)
            result.append(spec)
    return result


def specs_identity(specs: Iterable[Mapping[str, Any]]) -> str:
    encoded = json.dumps(
        list(specs), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _install_skatzero_import(skatzero_root: Path) -> None:
    root = str(skatzero_root.resolve())
    if root not in sys.path:
        sys.path.insert(0, root)


def _build_dealer(spec: Mapping[str, Any]):
    from skatzero.evaluation.utils import swap_bids, swap_colors
    from skatzero.game.dealer import Dealer

    dealer = Dealer(None)
    dealer.starting_player = int(spec["starting_player"])
    dealer.deck = list(spec["deck"])
    dealer.max_bids = [int(x) for x in spec["relative_max_bids"]]
    dealer.parse_and_set_bid(1)
    dealer.parse_and_set_bid(2)

    if spec["normalized_gametype"] == "D":
        suit = {
            "CLUBS": "C",
            "SPADES": "S",
            "HEARTS": "H",
            "DIAMONDS": "D",
        }[str(spec["actual_game_type"])]
        if suit != "D":
            dealer.deck = swap_colors(dealer.deck, "D", suit)
            dealer.bids[1] = swap_bids(dealer.bids[1], "D", suit)
            dealer.bids[2] = swap_bids(dealer.bids[2], "D", suit)

    dealer.blind_hand = bool(spec["blind_hand"])
    dealer.open_hand = bool(spec["open_hand"])
    return dealer


def run_specs(
    skatzero_root: Path,
    model_root: Path,
    specs: list[dict[str, Any]],
    *,
    seed: int = 20260923,
) -> dict[str, Any]:
    _install_skatzero_import(skatzero_root)
    from skatzero.evaluation.eval_env import EvalEnv
    from skatzero.evaluation.simulation import load_model

    grouped = {"D": [], "G": [], "N": []}
    for spec in specs:
        grouped[str(spec["normalized_gametype"])].append(spec)

    output: dict[str, Any] = {
        "schema": BENCHMARK_SCHEMA,
        "selection_identity_sha256": specs_identity(specs),
        "seed": seed,
        "gametypes": {},
    }

    for gt in ("D", "G", "N"):
        group = grouped[gt]
        dealers = [_build_dealer(spec) for spec in group]
        agents = [load_model(str(model_root / f"{gt}_{i}.pth")) for i in range(3)]
        env = EvalEnv(seed=seed, gametype=gt, dealers=dealers)
        env.set_agents(agents)

        records = []
        start = time.perf_counter()
        for spec in group:
            _, rewards = env.run(is_training=False)
            records.append(
                {
                    "game_identity": spec["game_identity"],
                    "rewards": [float(x) for x in rewards],
                }
            )
        elapsed = time.perf_counter() - start
        means = [
            sum(r["rewards"][seat] for r in records) / len(records)
            for seat in range(3)
        ]
        output["gametypes"][gt] = {
            "games": len(records),
            "elapsed_s": elapsed,
            "mean_rewards": means,
            "records": records,
        }
    return output


def result_identity(result: Mapping[str, Any]) -> str:
    stable = {
        "schema": result["schema"],
        "selection_identity_sha256": result["selection_identity_sha256"],
        "seed": result["seed"],
        "gametypes": {
            gt: {
                "records": result["gametypes"][gt]["records"],
                "mean_rewards": result["gametypes"][gt]["mean_rewards"],
            }
            for gt in ("D", "G", "N")
        },
    }
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--canonical", type=Path, required=True)
    p.add_argument("--skatzero-root", type=Path, required=True)
    p.add_argument("--model-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--per-gametype", type=int, default=12)
    p.add_argument("--selection-seed", type=int, default=20260923)
    p.add_argument("--run-seed", type=int, default=20260923)
    args = p.parse_args()

    specs = select_specs(
        args.canonical, per_gametype=args.per_gametype, seed=args.selection_seed
    )
    first = run_specs(
        args.skatzero_root, args.model_root, specs, seed=args.run_seed
    )
    second = run_specs(
        args.skatzero_root, args.model_root, specs, seed=args.run_seed
    )
    first_id = result_identity(first)
    second_id = result_identity(second)
    if first_id != second_id:
        raise RuntimeError(f"NONDETERMINISTIC_B0:{first_id}!={second_id}")

    result = {
        "schema": BENCHMARK_SCHEMA,
        "selection_seed": args.selection_seed,
        "run_seed": args.run_seed,
        "per_gametype": args.per_gametype,
        "selection_identity_sha256": specs_identity(specs),
        "result_identity_sha256": first_id,
        "specs": specs,
        "run": first,
        "determinism_recheck": "PASS",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "selection_identity_sha256": result["selection_identity_sha256"],
        "result_identity_sha256": result["result_identity_sha256"],
        "determinism_recheck": "PASS",
        "gametypes": {
            gt: {
                k: v for k, v in first["gametypes"][gt].items()
                if k != "records"
            }
            for gt in ("D", "G", "N")
        },
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
