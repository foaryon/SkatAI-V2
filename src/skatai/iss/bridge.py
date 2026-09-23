from __future__ import annotations

from typing import Protocol

from skatai.game.bidding import BiddingState
from skatai.iss.protocol import DealView, WireMove
from skatai.iss.session import TableSession
from skatai.runtime.interface import BiddingObservation

BRIDGE_SCHEMA = "skatai.v2.iss-product-bridge.v1"


class ISSBridgeError(ValueError):
    pass


class BiddingDecisionEngine(Protocol):
    def decide_bid(self, observation: BiddingObservation) -> str: ...


def player_view_from_deal(deal: DealView) -> tuple[int, tuple[str, ...]]:
    known = []
    for seat, hand in enumerate(deal.hands):
        if all(card != "??" for card in hand):
            known.append((seat, hand))
    if len(known) != 1:
        raise ISSBridgeError(f"EXPECTED_ONE_KNOWN_PLAYER_HAND:{len(known)}")
    return known[0]


def _initial_deal(moves: list[WireMove]) -> DealView | None:
    for move in moves:
        if move.kind == "initial_deal":
            if not isinstance(move.payload, DealView):
                raise ISSBridgeError("INITIAL_DEAL_PAYLOAD_TYPE")
            return move.payload
    return None


def _replay_public_auction(moves: list[WireMove]) -> BiddingState:
    state = BiddingState()
    for move in moves:
        if move.kind == "initial_deal":
            continue
        if move.kind in {"bid", "answer_yes", "pass"}:
            if move.actor not in {"0", "1", "2"}:
                raise ISSBridgeError("NONPLAYER_BIDDING_ACTOR")
            try:
                state.apply(int(move.actor), move.action)
            except (ValueError, IndexError) as exc:
                raise ISSBridgeError(f"BIDDING_REPLAY_FAILED:{exc}") from exc
            continue
        # Any later phase means bidding is no longer a decision surface.
        if move.kind in {"skat_request", "skat_delivery", "declaration", "cardplay"}:
            break
    return state


def next_bidding_action(
    table: TableSession,
    engine: BiddingDecisionEngine,
) -> str | None:
    deal = _initial_deal(table.moves)
    if deal is None:
        return None
    seat, hand = player_view_from_deal(deal)
    state = _replay_public_auction(table.moves)

    if state.finished or state.expected_actor != seat:
        return None

    obs = BiddingObservation.create(
        hand,
        actor=seat,
        bidder=state.bidder,
        answerer=state.answerer,
        bid_index=state.bid_index,
        decision_role=state.decision_role,
    )
    decision = engine.decide_bid(obs)
    if decision == "PASS":
        return "p"
    if decision != "CONTINUE":
        raise ISSBridgeError(f"UNKNOWN_BIDDING_DECISION:{decision}")

    legal = state.legal_native_actions()
    return legal[0]
