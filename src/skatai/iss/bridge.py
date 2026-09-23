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


class FullSkatDecisionEngine(BiddingDecisionEngine, Protocol):
    def choose_contract(self, observation): ...
    def choose_discard(self, observation): ...
    def play_card(self, observation): ...


def _hand_actions(winning_bid: int) -> tuple[str, ...]:
    out = ["PICKUP", "CH", "SH", "HH", "DH", "GH"]
    if winning_bid <= 35:
        out.append("NH")
    if winning_bid <= 59:
        out.append("NHO")
    return tuple(out)


def _pickup_contracts(winning_bid: int) -> tuple[str, ...]:
    out = ["C", "S", "H", "D", "G"]
    if winning_bid <= 23:
        out.append("N")
    if winning_bid <= 46:
        out.append("NO")
    return tuple(out)


def next_action(
    table: TableSession,
    engine: FullSkatDecisionEngine,
) -> str | None:
    """Return one ISS move string for the current player view, or None.

    Bidding reuses the already-verified bidding bridge. Later phases are
    reconstructed from the official ISS move stream into the stable V2
    product observations.
    """
    from skatai.iss.gameview import ISSPhase, replay_player_view
    from skatai.runtime.interface import (
        CardplayObservation,
        DeclarationObservation,
        DiscardObservation,
    )

    state = replay_player_view(table.moves)
    if state.to_move != state.viewer_seat:
        return None

    if state.phase in {ISSPhase.BID, ISSPhase.ANSWER}:
        return next_bidding_action(table, engine)

    if state.declarer is None or state.winning_bid is None:
        return None

    if state.phase == ISSPhase.SKAT_OR_HAND_DECL:
        if state.viewer_seat != state.declarer:
            return None
        obs = DeclarationObservation.create(
            state.hand,
            seat=state.viewer_seat,
            winning_bid=state.winning_bid,
            picked_up_skat=False,
            legal_contracts=_hand_actions(state.winning_bid),
            max_accepted_bids_by_seat=state.max_accepted_bids_by_seat,
        )
        action = engine.choose_contract(obs)
        if action == "PICKUP":
            return "s"
        if "O" in action:
            return action + "." + ".".join(state.hand)
        return action

    if state.phase == ISSPhase.DISCARD_AND_DECL:
        if state.viewer_seat != state.declarer or len(state.hand) != 12:
            return None
        decl = DeclarationObservation.create(
            state.hand,
            seat=state.viewer_seat,
            winning_bid=state.winning_bid,
            picked_up_skat=True,
            legal_contracts=_pickup_contracts(state.winning_bid),
            max_accepted_bids_by_seat=state.max_accepted_bids_by_seat,
        )
        contract = engine.choose_contract(decl)
        discard = DiscardObservation.create(
            state.hand,
            seat=state.viewer_seat,
            winning_bid=state.winning_bid,
            max_accepted_bids_by_seat=state.max_accepted_bids_by_seat,
        )
        d1, d2 = engine.choose_discard(discard)
        remaining = tuple(c for c in state.hand if c not in {d1, d2})
        action = f"{contract}.{d1}.{d2}"
        if "O" in contract:
            action += "." + ".".join(remaining)
        return action

    if state.phase == ISSPhase.CARDPLAY:
        if not state.legal_cards:
            return None
        skat_cards = (
            state.discarded_cards
            if state.viewer_seat == state.declarer
            else ()
        )
        obs = CardplayObservation.create(
            state.hand,
            seat=state.viewer_seat,
            declarer=state.declarer,
            contract=state.contract or "",
            winning_bid=state.winning_bid,
            current_trick=state.current_trick,
            played_cards=state.played_cards,
            legal_cards=state.legal_cards,
            points_self=state.declarer_visible_points,
            points_other=state.defender_points,
            max_accepted_bids_by_seat=state.max_accepted_bids_by_seat,
            skat_cards=skat_cards,
            blind_hand=not state.picked_up_skat,
            open_hand_cards=state.open_hand_cards,
        )
        return engine.play_card(obs)

    return None
