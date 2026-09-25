from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Mapping
from datetime import date as calendar_date
from typing import Any

from skatai.game.bidding import replay

_BENCHMARK_BOT = re.compile(r"^(?:kermit|zoot|thecount)(?::?\d+)?$", re.IGNORECASE)


def contains_benchmark_bot(players: list[str] | tuple[str, ...]) -> bool:
    return any(_BENCHMARK_BOT.fullmatch(p) is not None for p in players)


def split_for_game(game: Mapping[str, Any]) -> str:
    """Frozen V2 bidding split v1.

    Benchmark-bot games never enter train/validation/test. Remaining games are
    split chronologically to measure forward generalization.
    """
    players = tuple(str(x) for x in game.get("players", ()))
    if contains_benchmark_bot(players):
        return "external_bot_holdout"

    date = str(game.get("date") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return "quarantine_date"
    try:
        calendar_date.fromisoformat(date)
    except ValueError:
        return "quarantine_date"
    if date < "2023-01-01":
        return "train"
    if date < "2024-01-01":
        return "validation"
    if date < "2025-01-01":
        return "test"
    return "future_holdout"


def replay_is_eligible(game: Mapping[str, Any]) -> bool:
    r = replay(game.get("bidding_history") or ())
    return (
        r.ok
        and r.winner == int(game["declarer"])
        and r.winning_bid == int(game["bid_level"])
    )


def iter_bidding_decisions(game: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield legal decision-time bidding observations and binary targets."""
    r = replay(game.get("bidding_history") or ())
    all_pass = bool(game.get("all_pass"))
    if all_pass:
        if not (r.ok and r.all_pass):
            return
    else:
        if not (
            r.ok
            and r.winner == int(game["declarer"])
            and r.winning_bid == int(game["bid_level"])
        ):
            return

    initial_hands = game["initial_hands"]
    split = split_for_game(game)
    public_prefix: list[str] = []

    for action in r.actions:
        actor = action["actor"]
        before = action["before"]
        yield {
            "schema": "skatai.v2.bidding-decision.v1",
            "source": game["source"],
            "game_identity": game["semantic_sha256"],
            "split": split,
            "ordinal": action["ordinal"],
            "actor": actor,
            "seat": actor,
            "hand": list(initial_hands[actor]),
            "public_bidding_prefix": list(public_prefix),
            "current_offer": before["current_offer"],
            "bid_index": before["bid_index"],
            "bidder": before["bidder"],
            "answerer": before["answerer"],
            "decision_role": before["decision_role"],
            "legal_native_actions": before["legal_native_actions"],
            "target": action["target"],
            "native_observed_action": action["native_action"],
        }
        public_prefix.extend((str(actor), action["native_action"]))
