"""Privileged semantic replay of a captured V2 self-play trajectory.

This audit consumes private deal provenance. It must never be imported by a
learner process or receive only the learner-facing record.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json

from skatai.selfplay.learner_dataset import _validate_decision
from skatai.selfplay.cardplay import make_deal
from skatai.selfplay.game import run_game
from skatai.selfplay.scoring import score_basic_episode
from skatai.selfplay.trajectory import SCHEMA


def _normalized(value):
    return json.loads(json.dumps(value, sort_keys=True))


def audit_captured_record(record: dict, *, seed: int) -> dict:
    if record.get("schema") != SCHEMA or record.get("deal_seed") != seed:
        raise ValueError("REPLAY_SOURCE_IDENTITY_MISMATCH")
    decisions = record["decisions"]
    trace = hashlib.sha256(json.dumps(
        decisions, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    if trace != record["decision_trace_sha256"]:
        raise ValueError("REPLAY_DECISION_TRACE_HASH_MISMATCH")
    # Replay against the producer alone can reproduce the producer's semantic
    # mistake. Check all-seat public point views independently first.
    contract = record.get("contract")
    open_hand = None
    if contract is not None and "O" in contract:
        declarer = record["declarer"]
        if declarer not in (0, 1, 2):
            raise ValueError("REPLAY_OUVERT_DECLARER_INVALID")
        deal = make_deal(seed)
        original = (*deal.hands[declarer], *deal.skat)
        discards = [
            tuple(item["action"]) for item in decisions
            if item["phase"] == "DISCARD" and item["seat"] == declarer
        ]
        if len(discards) > 1:
            raise ValueError("REPLAY_OUVERT_DISCARD_COUNT_INVALID")
        open_hand = (
            tuple(card for card in original if card not in discards[0])
            if discards else deal.hands[declarer]
        )
        if len(open_hand) != 10:
            raise ValueError("REPLAY_OUVERT_HAND_INVALID")
    for item in decisions:
        if item["phase"] == "CARDPLAY":
            _validate_decision(
                item, item["seat"], record["contract"], record["threshold"],
            )
            if open_hand is not None:
                view = item["observation"]
                played = {
                    card for actor, card in view["played_cards"]
                    if actor == record["declarer"]
                }
                expected = {card for card in open_hand if card not in played}
                if set(view["open_hand_cards"]) != expected:
                    raise ValueError("REPLAY_OUVERT_PUBLIC_HAND_MISMATCH")

    class Cursor:
        def __init__(self) -> None:
            self.index = 0

        def take(self, phase: str, seat: int, observation):
            if self.index >= len(decisions):
                raise ValueError("REPLAY_DECISION_TRUNCATED")
            item = decisions[self.index]
            if (item["ordinal"] != self.index or item["phase"] != phase
                    or item["seat"] != seat
                    or item["observation"] != _normalized(asdict(observation))):
                raise ValueError(f"REPLAY_DECISION_OR_VIEW_MISMATCH:{self.index}")
            self.index += 1
            return item["action"]

    cursor = Cursor()

    class Policy:
        def __init__(self, seat: int) -> None:
            self.seat = seat

        def probability_continue(self, observation):
            return cursor.take("BID", self.seat, observation)

        def choose_contract(self, observation):
            return cursor.take("DECLARATION", self.seat, observation)

        def choose_discard(self, observation):
            return tuple(cursor.take("DISCARD", self.seat, observation))

        def play_card(self, observation):
            return cursor.take("CARDPLAY", self.seat, observation)

    policies = [Policy(seat) for seat in range(3)]
    episode = run_game(
        seed, bidding_policies=policies, declaration_policies=policies,
        discard_policies=policies, cardplay_policies=policies,
        legal_contracts=record["legal_contracts"], threshold=record["threshold"],
    )
    if cursor.index != len(decisions):
        raise ValueError("REPLAY_EXTRA_DECISIONS")
    recorded_native = [x["native_bid_action"] for x in decisions if x["phase"] == "BID"]
    actual_native = [action for _, action in episode.bidding.actions]
    if recorded_native != actual_native:
        raise ValueError("REPLAY_NATIVE_AUCTION_MISMATCH")
    if (record["deal_sha256"] != episode.deal_sha256
            or record["all_pass"] != episode.bidding.all_pass
            or record["declarer"] != episode.bidding.winner
            or record["contract"] != (None if episode.declaration is None else episode.declaration.contract)):
        raise ValueError("REPLAY_TERMINAL_IDENTITY_MISMATCH")
    scored = None if episode.bidding.all_pass else score_basic_episode(episode)
    value = None if scored is None else scored.signed_game_value
    if record["signed_basic_value"] != value:
        raise ValueError("REPLAY_TERMINAL_VALUE_MISMATCH")
    return {
        "deal_sha256": episode.deal_sha256,
        "decisions": cursor.index,
        "contract": record["contract"],
        "signed_basic_value": value,
    }
