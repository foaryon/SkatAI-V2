from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from skatai.data.sgf import SGFParseError, parse_sgf_line

ISS_DECLARER_BONUS = 50
ISS_DEFENDER_LOSS_BONUS_3P = 40
ISS_PENALTY = 100
SCHEMA = "skatai.v2.iss-game-result.v1"


class ISSResultError(ValueError):
    pass


def _group(name: str) -> str:
    return str(name).split(":", 1)[0]


def player_seat(players: list[str] | tuple[str, ...], viewer_name: str) -> int:
    exact = [i for i, x in enumerate(players) if str(x) == str(viewer_name)]
    if len(exact) == 1:
        return exact[0]
    group = _group(viewer_name)
    grouped = [i for i, x in enumerate(players) if _group(x) == group]
    if len(grouped) == 1:
        return grouped[0]
    raise ISSResultError(
        f"VIEWER_SEAT_AMBIGUOUS_OR_MISSING:{viewer_name}:{list(players)}"
    )


def iss_score_for_seat(parsed: dict[str, Any], seat: int) -> float:
    if parsed.get("classification") == "VERIFIED_ALL_PASS":
        return 0.0
    if parsed.get("classification") != "PARSED_PLAYED_GAME":
        raise ISSResultError(
            f"NONSCORABLE_CLASSIFICATION:{parsed.get('classification')}"
        )
    declarer = int(parsed["declarer"])
    value = parsed.get("game_value")
    if value is None:
        raise ISSResultError("MISSING_GAME_VALUE")
    value = int(value)
    if value == 0:
        raise ISSResultError("PLAYED_GAME_ZERO_VALUE")
    if seat == declarer:
        return float(value + ISS_DECLARER_BONUS if value > 0 else value - ISS_DECLARER_BONUS)
    return float(ISS_DEFENDER_LOSS_BONUS_3P if value < 0 else 0)


def live_game_result(sgf: str, *, viewer_name: str) -> dict[str, Any]:
    try:
        parsed = parse_sgf_line("iss-live", sgf)
    except SGFParseError as exc:
        raise ISSResultError(f"SGF_PARSE_FAILED:{exc}") from exc

    players = tuple(str(x) for x in parsed.get("players") or ())
    if len(players) != 3:
        raise ISSResultError(f"EXPECTED_THREE_PLAYERS:{len(players)}")
    seat = player_seat(players, viewer_name)
    raw_hash = hashlib.sha256(sgf.encode("utf-8")).hexdigest()

    penalties = tuple(int(x) for x in (parsed.get("penalties") or (0, 0, 0)))
    timeout_seat = parsed.get("timeout_seat")
    left_seat = parsed.get("left_seat")
    failure_reason = None
    if seat < len(penalties) and penalties[seat] > 0:
        failure_reason = f"ISS_PENALTY:{penalties[seat]}"
    if timeout_seat == seat:
        failure_reason = "ISS_TIMEOUT"
    if left_seat == seat:
        failure_reason = "ISS_DISCONNECT_OR_LEAVE"

    score = None if failure_reason else iss_score_for_seat(parsed, seat)
    return {
        "schema": SCHEMA,
        "game_id": str(parsed.get("semantic_sha256") or raw_hash),
        "source_game_id": parsed.get("game_id"),
        "raw_sgf_sha256": raw_hash,
        "players": players,
        "seat": seat,
        "score": score,
        "failure_reason": failure_reason,
        "classification": parsed.get("classification"),
        "declarer": parsed.get("declarer"),
        "declarer_name": (
            None
            if parsed.get("declarer") is None
            else players[int(parsed["declarer"])]
        ),
        "winning_bid": parsed.get("bid_level"),
        "contract": parsed.get("announcement"),
        "game_type": parsed.get("game_type"),
        "overbid": None,
        "won": parsed.get("won"),
        "game_value": parsed.get("game_value"),
        "penalties": penalties,
        "timeout_seat": timeout_seat,
        "left_seat": left_seat,
    }
