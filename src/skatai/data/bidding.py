from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Mapping
from typing import Any

_BID_VALUE = re.compile(r"^\d+$")
_BENCHMARK_BOT = re.compile(r"^(?:kermit|zoot|thecount)(?::?\d+)?$", re.IGNORECASE)


def contains_benchmark_bot(players: list[str] | tuple[str, ...]) -> bool:
    return any(_BENCHMARK_BOT.fullmatch(p) is not None for p in players)


def split_for_game(game: Mapping[str, Any]) -> str:
    """Deterministic V2 split from immutable game identity.

    Games containing Kermit, Zoot, or theCount are never training data.
    Remaining games use a 90/5/5 hash split.
    """
    players = tuple(str(x) for x in game.get("players", ()))
    if contains_benchmark_bot(players):
        return "external_bot_holdout"

    identity = str(game["semantic_sha256"])
    bucket = int(hashlib.sha256(identity.encode("ascii")).hexdigest()[:8], 16) % 10_000
    if bucket < 9_000:
        return "train"
    if bucket < 9_500:
        return "validation"
    return "test"


def iter_bidding_decisions(game: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield supervised bidding decisions using legal decision-time information only.

    The canonical history is actor/action pairs. Bidding terminates before the
    post-bid skat/declaration tokens (s/w/etc.). Future contract/outcome fields
    are intentionally excluded from model inputs.
    """
    history = list(game.get("bidding_history") or ())
    initial_hands = game["initial_hands"]
    split = split_for_game(game)
    prefix: list[str] = []

    for i in range(0, len(history) - 1, 2):
        actor_token = str(history[i])
        action = str(history[i + 1])
        if actor_token not in {"0", "1", "2"}:
            break
        if not (action in {"p", "y"} or _BID_VALUE.fullmatch(action)):
            break

        actor = int(actor_token)
        yield {
            "schema": "skatai.v2.bidding-decision.v1",
            "source": game["source"],
            "game_identity": game["semantic_sha256"],
            "split": split,
            "actor": actor,
            "seat": actor,
            "hand": list(initial_hands[actor]),
            "history_before": list(prefix),
            "target_action": action,
        }
        prefix.extend((actor_token, action))
