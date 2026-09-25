"""Local-only gate for bounded declarer self-play learner artifacts.

The caller supplies exact local files. This module does not fetch from object
storage or read privileged seed/replay files, and does not approve training.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from skatai.game.bidding import BID_VALUES
from skatai.game.rules import card_points, game_type_from_contract, legal_cards, replay_tricks
from skatai.runtime.interface import (
    BiddingObservation, CardplayObservation, DeclarationObservation,
    DiscardObservation,
)
from skatai.selfplay.trajectory import LEARNER_SCHEMA, PHASES

MANIFEST_SCHEMA = "skatai.v2.selfplay.learner-seat-pilot-manifest.v1"
MAX_PILOT_BYTES = 32 * 1024 * 1024
RECORD_KEYS = {
    "schema", "source_commit", "seat", "policy_family_ids", "threshold",
    "legal_contracts", "decisions", "contract", "signed_basic_value",
}
DECISION_KEYS = {"ordinal", "phase", "seat", "observation", "action", "native_bid_action"}


def _normalized(value):
    return json.loads(json.dumps(value, sort_keys=True))


def _validate_decision(item: dict, seat: int, contract: str, threshold: float) -> None:
    if set(item) != DECISION_KEYS or item["seat"] != seat:
        raise ValueError("LEARNER_DECISION_KEYS_OR_SEAT")
    phase = item["phase"]
    view = item["observation"]
    if not isinstance(view, dict):
        raise ValueError("LEARNER_OBSERVATION_NOT_OBJECT")
    if phase == "BID":
        expected = BiddingObservation.create(
            view["hand"], actor=view["actor"], bidder=view["bidder"],
            answerer=view["answerer"], bid_index=view["bid_index"],
            decision_role=view["decision_role"],
        )
        action = float(item["action"])
        if (expected.actor != seat or expected.bid_index >= len(BID_VALUES)
                or not 0 <= action <= 1):
            raise ValueError("LEARNER_BID_INVALID")
        native = (
            str(BID_VALUES[expected.bid_index]) if expected.decision_role == "BIDDER"
            else "y"
        ) if action >= threshold else "p"
        if item["native_bid_action"] != native:
            raise ValueError("LEARNER_BID_NATIVE_ACTION_MISMATCH")
    elif phase == "DECLARATION":
        expected = DeclarationObservation.create(
            view["cards"], seat=view["seat"], winning_bid=view["winning_bid"],
            picked_up_skat=view["picked_up_skat"],
            legal_contracts=view["legal_contracts"],
            max_accepted_bids_by_seat=view["max_accepted_bids_by_seat"],
        )
        if item["action"] not in expected.legal_contracts:
            raise ValueError("LEARNER_DECLARATION_ACTION_ILLEGAL")
    elif phase == "DISCARD":
        expected = DiscardObservation.create(
            view["hand12"], seat=view["seat"], winning_bid=view["winning_bid"],
            max_accepted_bids_by_seat=view["max_accepted_bids_by_seat"],
        )
        cards = tuple(item["action"])
        if len(cards) != 2 or len(set(cards)) != 2 or not set(cards).issubset(set(expected.hand12)):
            raise ValueError("LEARNER_DISCARD_ACTION_ILLEGAL")
    elif phase == "CARDPLAY":
        expected = CardplayObservation.create(**view)
        if expected.contract != contract or item["action"] not in expected.legal_cards:
            raise ValueError("LEARNER_CARDPLAY_ACTION_ILLEGAL")
        played = expected.played_cards
        played_cards = {card for _, card in played}
        if (len(played) >= 30 or len(played_cards) != len(played)
                or set(expected.hand) & played_cards
                or set(expected.skat_cards) & played_cards
                or set(expected.hand) & set(expected.skat_cards)):
            raise ValueError("LEARNER_CARDPLAY_CARD_STATE_MISMATCH")
        replayed = replay_tricks(
            played,
            game_type=game_type_from_contract(expected.contract),
            declarer=expected.declarer,
        )
        if (expected.seat != replayed["expected_actor"]
                or expected.current_trick != replayed["current_trick"]
                or set(expected.legal_cards) != set(legal_cards(
                    expected.hand, expected.current_trick,
                    game_type_from_contract(expected.contract),
                ))):
            raise ValueError("LEARNER_CARDPLAY_HISTORY_OR_LEGAL_MISMATCH")
        declarer_points = int(replayed["declarer_trick_points"])
        if expected.seat == expected.declarer and not expected.blind_hand:
            declarer_points += sum(card_points(card) for card in expected.skat_cards)
        if (expected.points_self != declarer_points
                or expected.points_other != replayed["defender_trick_points"]):
            raise ValueError("LEARNER_CARDPLAY_POINT_STATE_MISMATCH")
    else:
        raise ValueError("LEARNER_PHASE_UNSUPPORTED")
    observed_seat = expected.actor if phase == "BID" else expected.seat
    if observed_seat != seat:
        raise ValueError("LEARNER_OBSERVATION_SEAT_MISMATCH")
    if item["native_bid_action"] is not None and phase != "BID":
        raise ValueError("LEARNER_NONBID_NATIVE_ACTION")
    if _normalized(asdict(expected)) != view:
        raise ValueError("LEARNER_OBSERVATION_FIELDS_MISMATCH")


def load_bounded_learner_pilot(manifest_path: Path, data_path: Path) -> list[dict]:
    """Verify the entire bounded artifact before returning any record."""
    manifest = json.loads(Path(manifest_path).read_text())
    if (manifest.get("schema") != MANIFEST_SCHEMA
            or set(manifest) != {"schema", "source_commit", "count", "learner_schema",
                                 "learner_file", "learner_sha256", "trust_level", "restrictions"}
            or manifest.get("learner_schema") != LEARNER_SCHEMA
            or manifest.get("trust_level") != "D1_EXPLORATORY_ONLY"
            or manifest.get("learner_file") != Path(data_path).name):
        raise ValueError("LEARNER_MANIFEST_INVALID_OR_PRIVATE_REFERENCE")
    path = Path(data_path)
    if path.stat().st_size > MAX_PILOT_BYTES:
        raise ValueError("LEARNER_PILOT_TOO_LARGE")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != manifest["learner_sha256"]:
        raise ValueError("LEARNER_DATA_HASH_MISMATCH")
    rows = [json.loads(line) for line in data.splitlines()]
    if len(rows) != manifest["count"]:
        raise ValueError("LEARNER_RECORD_COUNT_MISMATCH")
    for row in rows:
        if (set(row) != RECORD_KEYS or row["schema"] != LEARNER_SCHEMA
                or row["source_commit"] != manifest["source_commit"]
                or row["seat"] not in (0, 1, 2)
                or set(row["policy_family_ids"]) != set(PHASES)
                or not isinstance(row["threshold"], (int, float))
                or not 0 <= row["threshold"] <= 1
                or row["contract"] not in row["legal_contracts"]
                or not row["decisions"] or not isinstance(row["signed_basic_value"], int)
                or row["signed_basic_value"] == 0):
            raise ValueError("LEARNER_RECORD_INVALID")
        phases = [decision["phase"] for decision in row["decisions"]]
        declarations = [decision for decision in row["decisions"] if decision["phase"] == "DECLARATION"]
        pickup = bool(declarations and declarations[0]["action"] == "PICKUP")
        terminal_phases = (["DECLARATION", "DISCARD", "DECLARATION"] if pickup else ["DECLARATION"])
        if (phases != ["BID"] * phases.count("BID") + terminal_phases + ["CARDPLAY"] * 10
                or not declarations or declarations[-1]["action"] != row["contract"]
                or any(d["observation"]["picked_up_skat"] != (pickup and i == 1)
                       for i, d in enumerate(declarations))):
            raise ValueError("LEARNER_COMPLETED_DECLARER_SEQUENCE_REQUIRED")
        previous = -1
        previous_play = None
        cardplay_count = 0
        discard_actions = [d["action"] for d in row["decisions"] if d["phase"] == "DISCARD"]
        expected_skat = set(discard_actions[0]) if pickup else set()
        declared_cards = (
            set(next(d["observation"]["hand12"] for d in row["decisions"]
                     if d["phase"] == "DISCARD")) - expected_skat
            if pickup else set(declarations[-1]["observation"]["cards"])
        )
        for decision in row["decisions"]:
            if not isinstance(decision["ordinal"], int) or decision["ordinal"] <= previous:
                raise ValueError("LEARNER_DECISION_ORDER_INVALID")
            previous = decision["ordinal"]
            if (decision["phase"] == "CARDPLAY"
                    and decision["observation"]["declarer"] != row["seat"]):
                raise ValueError("LEARNER_CARDPLAY_NOT_DECLARER")
            _validate_decision(decision, row["seat"], row["contract"], row["threshold"])
            if decision["phase"] == "CARDPLAY":
                view = decision["observation"]
                played = tuple(tuple(move) for move in view["played_cards"])
                hand = set(view["hand"])
                if (len(hand) != 10 - cardplay_count
                        or set(view["skat_cards"]) != expected_skat
                        or view["blind_hand"] != (not pickup)):
                    raise ValueError("LEARNER_CARDPLAY_DECLARATION_STATE_MISMATCH")
                if previous_play is None and hand != declared_cards:
                    raise ValueError("LEARNER_CARDPLAY_DECLARATION_STATE_MISMATCH")
                if previous_play is not None:
                    prior_history, prior_hand, prior_action = previous_play
                    if (len(played) <= len(prior_history)
                            or played[:len(prior_history) + 1]
                            != prior_history + ((row["seat"], prior_action),)
                            or hand != prior_hand - {prior_action}):
                        raise ValueError("LEARNER_CARDPLAY_SEQUENCE_MISMATCH")
                previous_play = (played, hand, decision["action"])
                cardplay_count += 1
    return rows
