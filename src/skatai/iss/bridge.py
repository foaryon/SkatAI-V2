from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from skatai.game.bidding import BiddingState
from skatai.game.rules import card_points, game_type_from_contract, legal_cards, replay_tricks
from skatai.iss.protocol import DealView, WireMove
from skatai.iss.session import TableSession
from skatai.runtime.interface import (
    BiddingObservation,
    CardplayObservation,
    DeclarationObservation,
    DiscardObservation,
)

BRIDGE_SCHEMA = "skatai.v2.iss-product-bridge.v2"
PLAYER_ACTORS = {"0", "1", "2"}


class ISSBridgeError(ValueError):
    pass


class BiddingDecisionEngine(Protocol):
    def decide_bid(self, observation: BiddingObservation) -> str: ...


class SkatAIDecisionEngine(BiddingDecisionEngine, Protocol):
    def choose_contract(self, observation: DeclarationObservation) -> str: ...
    def choose_discard(self, observation: DiscardObservation) -> tuple[str, str]: ...
    def play_card(self, observation: CardplayObservation) -> str: ...


@dataclass(frozen=True)
class AuctionContext:
    state: BiddingState
    max_accepted_bids_by_seat: tuple[int, int, int]


@dataclass(frozen=True)
class PostAuctionContext:
    picked_up_skat: bool
    skat_delivery: tuple[str, ...] | None
    declaration: WireMove | None
    discard_only: WireMove | None
    cardplays: tuple[tuple[int, str], ...]
    terminal: bool


def player_view_from_deal(deal: DealView) -> tuple[int, tuple[str, ...]]:
    known = []
    for seat, hand in enumerate(deal.hands):
        if all(card != "??" for card in hand):
            known.append((seat, hand))
    if len(known) != 1:
        raise ISSBridgeError(f"EXPECTED_ONE_KNOWN_PLAYER_HAND:{len(known)}")
    return known[0]


def _initial_deal(moves: Sequence[WireMove]) -> DealView | None:
    for move in moves:
        if move.kind == "initial_deal":
            if not isinstance(move.payload, DealView):
                raise ISSBridgeError("INITIAL_DEAL_PAYLOAD_TYPE")
            return move.payload
    return None


def _auction_context(moves: Sequence[WireMove]) -> AuctionContext:
    state = BiddingState()
    max_bids = [0, 0, 0]
    for move in moves:
        if move.kind == "initial_deal":
            continue
        if move.kind in {"bid", "answer_yes", "pass"}:
            if state.finished:
                raise ISSBridgeError("BIDDING_MOVE_AFTER_FINISH")
            if move.actor not in PLAYER_ACTORS:
                raise ISSBridgeError("NONPLAYER_BIDDING_ACTOR")
            actor = int(move.actor)
            try:
                before = state.apply(actor, move.action)
            except (ValueError, IndexError) as exc:
                raise ISSBridgeError(f"BIDDING_REPLAY_FAILED:{exc}") from exc
            if move.action != "p":
                max_bids[actor] = max(
                    max_bids[actor], int(before["current_offer"])
                )
            continue
        # Anything beyond bidding freezes the auction.
        if move.kind in {
            "skat_request",
            "skat_delivery",
            "declaration",
            "discard_only",
            "cardplay",
            "resign",
            "show_cards",
            "timeout",
            "leave",
        }:
            break
    return AuctionContext(state, tuple(max_bids))


def _replay_public_auction(moves: Sequence[WireMove]) -> BiddingState:
    return _auction_context(moves).state


def _post_auction_context(moves: Sequence[WireMove]) -> PostAuctionContext:
    picked_up = False
    skat_delivery: tuple[str, ...] | None = None
    declaration: WireMove | None = None
    discard_only: WireMove | None = None
    cardplays: list[tuple[int, str]] = []
    terminal = False

    for move in moves:
        if move.kind == "skat_request":
            picked_up = True
        elif move.kind == "skat_delivery":
            payload = tuple(str(x) for x in move.payload)
            if len(payload) != 2:
                raise ISSBridgeError("BAD_SKAT_DELIVERY_PAYLOAD")
            skat_delivery = payload
        elif move.kind == "declaration":
            if move.actor not in PLAYER_ACTORS:
                raise ISSBridgeError("NONPLAYER_DECLARATION")
            declaration = move
        elif move.kind == "discard_only":
            if move.actor not in PLAYER_ACTORS:
                raise ISSBridgeError("NONPLAYER_DISCARD")
            discard_only = move
        elif move.kind == "cardplay":
            if move.actor not in PLAYER_ACTORS:
                raise ISSBridgeError("NONPLAYER_CARDPLAY")
            cardplays.append((int(move.actor), str(move.payload)))
        elif move.kind in {"resign", "timeout", "leave"}:
            terminal = True

    return PostAuctionContext(
        picked_up_skat=picked_up,
        skat_delivery=skat_delivery,
        declaration=declaration,
        discard_only=discard_only,
        cardplays=tuple(cardplays),
        terminal=terminal,
    )


def _declaration_payload(move: WireMove | None) -> tuple[str | None, tuple[str, ...]]:
    if move is None:
        return None, ()
    if not isinstance(move.payload, dict):
        raise ISSBridgeError("BAD_DECLARATION_PAYLOAD")
    contract = str(move.payload.get("game_type") or "")
    cards = tuple(str(x) for x in (move.payload.get("cards") or ()))
    if not contract:
        raise ISSBridgeError("EMPTY_DECLARATION_CONTRACT")
    return contract, cards


def _discard_payload(move: WireMove | None) -> tuple[str, ...]:
    if move is None:
        return ()
    if not isinstance(move.payload, tuple):
        raise ISSBridgeError("BAD_DISCARD_PAYLOAD")
    return tuple(str(x) for x in move.payload)


def _is_known_cards(cards: Sequence[str]) -> bool:
    return bool(cards) and all(c != "??" for c in cards)


def _pickup_discards(post: PostAuctionContext) -> tuple[str, str] | None:
    if not post.picked_up_skat:
        return None
    _, declaration_cards = _declaration_payload(post.declaration)
    if len(declaration_cards) >= 2:
        return (declaration_cards[0], declaration_cards[1])
    discard = _discard_payload(post.discard_only)
    if len(discard) >= 2:
        return (discard[0], discard[1])
    return None


def _open_hand_from_wire(post: PostAuctionContext) -> tuple[str, ...]:
    contract, declaration_cards = _declaration_payload(post.declaration)
    if contract is None or "O" not in contract:
        return ()
    if not post.picked_up_skat and len(declaration_cards) == 10:
        return declaration_cards
    if post.picked_up_skat and len(declaration_cards) == 12:
        return declaration_cards[2:]
    discard = _discard_payload(post.discard_only)
    if len(discard) == 12:
        return discard[2:]
    return ()


def _declaration_complete(post: PostAuctionContext) -> bool:
    if post.declaration is None:
        return False
    if not post.picked_up_skat:
        return True
    _, declaration_cards = _declaration_payload(post.declaration)
    return len(declaration_cards) >= 2 or post.discard_only is not None


def _remove_owned(cards: list[str], card: str, *, error: str) -> None:
    try:
        cards.remove(card)
    except ValueError as exc:
        raise ISSBridgeError(f"{error}:{card}") from exc


def _current_player_hand(
    initial_hand: Sequence[str],
    *,
    player_seat: int,
    declarer: int,
    post: PostAuctionContext,
) -> tuple[str, ...]:
    hand = list(initial_hand)

    if post.picked_up_skat and player_seat == declarer:
        skat = post.skat_delivery or ()
        if len(skat) != 2 or not _is_known_cards(skat):
            raise ISSBridgeError("DECLARER_SKAT_NOT_VISIBLE")
        hand.extend(skat)
        discards = _pickup_discards(post)
        if discards is not None:
            if not _is_known_cards(discards):
                raise ISSBridgeError("DECLARER_DISCARDS_NOT_VISIBLE")
            for card in discards:
                _remove_owned(hand, card, error="DISCARD_NOT_OWNED")

    for actor, card in post.cardplays:
        if actor == player_seat:
            _remove_owned(hand, card, error="PLAY_NOT_OWNED")

    if len(hand) != len(set(hand)):
        raise ISSBridgeError("DUPLICATE_CARD_IN_CURRENT_HAND")
    return tuple(hand)


def _remaining_open_hand(
    original: Sequence[str],
    *,
    declarer: int,
    cardplays: Sequence[tuple[int, str]],
) -> tuple[str, ...]:
    cards = list(original)
    for actor, card in cardplays:
        if actor == declarer and card in cards:
            cards.remove(card)
    return tuple(cards)


def _legal_hand_contracts(winning_bid: int) -> tuple[str, ...]:
    contracts: list[str] = []
    for base in ("D", "H", "S", "C", "G"):
        contracts.extend((base + "H", base + "HO", base + "HS", base + "HZ"))
    if winning_bid <= 35:
        contracts.append("NH")
    if winning_bid <= 59:
        contracts.append("NHO")
    return tuple(contracts)


def _legal_pickup_contracts(winning_bid: int) -> tuple[str, ...]:
    contracts = ["D", "H", "S", "C", "G"]
    if winning_bid <= 23:
        contracts.append("N")
    if winning_bid <= 46:
        contracts.append("NO")
    return tuple(contracts)


def _format_hand_declaration(contract: str, hand: Sequence[str]) -> str:
    if "O" in contract:
        return contract + "." + ".".join(hand)
    return contract


def _format_pickup_declaration(
    contract: str,
    discards: Sequence[str],
    final_hand: Sequence[str],
) -> str:
    if len(discards) != 2:
        raise ISSBridgeError("DISCARD_COUNT_NOT_TWO")
    action = contract + "." + ".".join(discards)
    if "O" in contract:
        action += "." + ".".join(final_hand)
    return action


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

    return state.legal_native_actions()[0]


def next_skat_action(
    table: TableSession,
    engine: SkatAIDecisionEngine,
) -> str | None:
    if not table.in_progress or table.stopped:
        return None

    deal = _initial_deal(table.moves)
    if deal is None:
        return None
    seat, initial_hand = player_view_from_deal(deal)
    auction = _auction_context(table.moves)

    if not auction.state.finished:
        return next_bidding_action(table, engine)
    if auction.state.winner is None:
        return None

    declarer = int(auction.state.winner)
    winning_bid = int(auction.state.winning_bid or 0)
    post = _post_auction_context(table.moves)
    if post.terminal:
        return None

    contract, _ = _declaration_payload(post.declaration)

    # Declarer chooses pickup versus Hand.
    if seat == declarer and not post.picked_up_skat and post.declaration is None:
        legal = ("PICKUP",) + _legal_hand_contracts(winning_bid)
        obs = DeclarationObservation.create(
            initial_hand,
            seat=seat,
            winning_bid=winning_bid,
            picked_up_skat=False,
            legal_contracts=legal,
            max_accepted_bids_by_seat=auction.max_accepted_bids_by_seat,
        )
        chosen = engine.choose_contract(obs)
        if chosen == "PICKUP":
            return "s"
        return _format_hand_declaration(chosen, initial_hand)

    # After requesting skat, wait for the world delivery.
    if post.picked_up_skat and post.skat_delivery is None:
        return None

    # Declarer chooses a combined pickup declaration + two-card discard.
    if seat == declarer and post.picked_up_skat and post.declaration is None:
        hand12 = _current_player_hand(
            initial_hand,
            player_seat=seat,
            declarer=declarer,
            post=post,
        )
        legal = _legal_pickup_contracts(winning_bid)
        decl_obs = DeclarationObservation.create(
            hand12,
            seat=seat,
            winning_bid=winning_bid,
            picked_up_skat=True,
            legal_contracts=legal,
            max_accepted_bids_by_seat=auction.max_accepted_bids_by_seat,
        )
        chosen = engine.choose_contract(decl_obs)
        discard_obs = DiscardObservation.create(
            hand12,
            seat=seat,
            winning_bid=winning_bid,
            max_accepted_bids_by_seat=auction.max_accepted_bids_by_seat,
        )
        discards = engine.choose_discard(discard_obs)
        remaining = list(hand12)
        for card in discards:
            _remove_owned(remaining, card, error="AI_DISCARD_NOT_OWNED")
        return _format_pickup_declaration(chosen, discards, remaining)

    # Recovery path for a split declaration already sent before reconnect.
    if (
        seat == declarer
        and post.picked_up_skat
        and post.declaration is not None
        and not _declaration_complete(post)
    ):
        hand12 = _current_player_hand(
            initial_hand,
            player_seat=seat,
            declarer=declarer,
            post=post,
        )
        discard_obs = DiscardObservation.create(
            hand12,
            seat=seat,
            winning_bid=winning_bid,
            max_accepted_bids_by_seat=auction.max_accepted_bids_by_seat,
        )
        discards = engine.choose_discard(discard_obs)
        remaining = list(hand12)
        for card in discards:
            _remove_owned(remaining, card, error="AI_DISCARD_NOT_OWNED")
        action = ".".join(discards)
        if contract is not None and "O" in contract:
            action += "." + ".".join(remaining)
        return action

    if not _declaration_complete(post):
        return None
    if contract is None:
        raise ISSBridgeError("CARDPLAY_WITHOUT_CONTRACT")

    game_type = game_type_from_contract(contract)
    trick_state = replay_tricks(
        post.cardplays,
        game_type=game_type,
        declarer=declarer,
    )
    if int(trick_state["expected_actor"]) != seat:
        return None

    hand = _current_player_hand(
        initial_hand,
        player_seat=seat,
        declarer=declarer,
        post=post,
    )
    legal = legal_cards(hand, trick_state["current_trick"], game_type)
    if not legal:
        return None

    declarer_points = int(trick_state["declarer_trick_points"])
    defender_points = int(trick_state["defender_trick_points"])

    discards = _pickup_discards(post)
    visible_skat: tuple[str, ...] = ()
    if seat == declarer and post.picked_up_skat:
        if discards is None or not _is_known_cards(discards):
            raise ISSBridgeError("DECLARER_DISCARDS_NOT_VISIBLE_AT_CARDPLAY")
        visible_skat = tuple(discards)
        declarer_points += sum(card_points(c) for c in discards)

    open_original = _open_hand_from_wire(post)
    if "O" in contract:
        if seat == declarer and not open_original:
            # Our own final hand is necessarily known; reconstruct the original
            # open hand by adding back our already-played cards.
            current = list(hand)
            own_played = [c for a, c in post.cardplays if a == declarer]
            open_original = tuple(current + own_played)
        if not open_original:
            raise ISSBridgeError("OUVERT_HAND_NOT_VISIBLE")
        open_remaining = _remaining_open_hand(
            open_original,
            declarer=declarer,
            cardplays=post.cardplays,
        )
    else:
        open_remaining = ()

    obs = CardplayObservation.create(
        hand,
        seat=seat,
        declarer=declarer,
        contract=contract,
        winning_bid=winning_bid,
        current_trick=trick_state["current_trick"],
        played_cards=post.cardplays,
        legal_cards=legal,
        points_self=declarer_points,
        points_other=defender_points,
        max_accepted_bids_by_seat=auction.max_accepted_bids_by_seat,
        skat_cards=visible_skat,
        blind_hand=not post.picked_up_skat,
        open_hand_cards=open_remaining,
    )
    return engine.play_card(obs)


class ISSSkatAIMoveProvider:
    def __init__(self, engine: SkatAIDecisionEngine) -> None:
        self.engine = engine

    def next_action(self, table: TableSession) -> str | None:
        return next_skat_action(table, self.engine)


from dataclasses import dataclass

from skatai.runtime.decision import (
    DecisionRequest,
    DecisionResult,
    DecisionType,
    decide as decide_request,
)
from skatai.runtime.interface import SkatAI


@dataclass(frozen=True)
class ISSDecision:
    request: DecisionRequest
    result: DecisionResult
    wire_action: str


class ISSBiddingDecisionProvider:
    """Decision-aware ISS bidding adapter around the stable SkatAI product API."""

    def __init__(self, ai: SkatAI, *, release_id: str) -> None:
        if not release_id:
            raise ISSBridgeError("EMPTY_RELEASE_ID")
        self.ai = ai
        self.release_id = str(release_id)

    def next_decision(self, table: TableSession) -> ISSDecision | None:
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
        request = DecisionRequest.create(
            game_id=f"iss:{table.table_id}:{table.game_sequence}",
            sequence_no=len(table.moves),
            decision_type=DecisionType.BID,
            observation=obs,
            source="ISS",
            source_context={
                "table_id": table.table_id,
                "game_sequence": table.game_sequence,
                "move_count": len(table.moves),
            },
        )
        result = decide_request(
            self.ai,
            request,
            release_id=self.release_id,
            metadata={"adapter_schema": BRIDGE_SCHEMA},
        )
        if result.action == "PASS":
            wire_action = "p"
        elif result.action == "CONTINUE":
            legal = state.legal_native_actions()
            if not legal:
                raise ISSBridgeError("CONTINUE_WITHOUT_NATIVE_ACTION")
            wire_action = legal[0]
        else:
            raise ISSBridgeError(f"UNKNOWN_BIDDING_DECISION:{result.action}")
        return ISSDecision(request=request, result=result, wire_action=wire_action)


def next_action(table: TableSession, engine: SkatAIDecisionEngine) -> str | None:
    """Stable generic alias for the complete ISS-to-SkatAI move bridge."""
    return next_skat_action(table, engine)


def _cardplay_observation_from_context(
    *,
    seat: int,
    initial_hand: Sequence[str],
    auction: AuctionContext,
    post: PostAuctionContext,
    contract: str,
) -> CardplayObservation | None:
    if auction.state.winner is None:
        return None
    declarer = int(auction.state.winner)
    winning_bid = int(auction.state.winning_bid or 0)
    game_type = game_type_from_contract(contract)
    trick_state = replay_tricks(
        post.cardplays,
        game_type=game_type,
        declarer=declarer,
    )
    if int(trick_state["expected_actor"]) != seat:
        return None

    hand = _current_player_hand(
        initial_hand,
        player_seat=seat,
        declarer=declarer,
        post=post,
    )
    legal = legal_cards(hand, trick_state["current_trick"], game_type)
    if not legal:
        return None

    declarer_points = int(trick_state["declarer_trick_points"])
    defender_points = int(trick_state["defender_trick_points"])
    discards = _pickup_discards(post)
    visible_skat: tuple[str, ...] = ()
    if seat == declarer and post.picked_up_skat:
        if discards is None or not _is_known_cards(discards):
            raise ISSBridgeError("DECLARER_DISCARDS_NOT_VISIBLE_AT_CARDPLAY")
        visible_skat = tuple(discards)
        declarer_points += sum(card_points(c) for c in discards)

    open_original = _open_hand_from_wire(post)
    if "O" in contract:
        if seat == declarer and not open_original:
            current = list(hand)
            own_played = [c for a, c in post.cardplays if a == declarer]
            open_original = tuple(current + own_played)
        if not open_original:
            raise ISSBridgeError("OUVERT_HAND_NOT_VISIBLE")
        open_remaining = _remaining_open_hand(
            open_original,
            declarer=declarer,
            cardplays=post.cardplays,
        )
    else:
        open_remaining = ()

    return CardplayObservation.create(
        hand,
        seat=seat,
        declarer=declarer,
        contract=contract,
        winning_bid=winning_bid,
        current_trick=trick_state["current_trick"],
        played_cards=post.cardplays,
        legal_cards=legal,
        points_self=declarer_points,
        points_other=defender_points,
        max_accepted_bids_by_seat=auction.max_accepted_bids_by_seat,
        skat_cards=visible_skat,
        blind_hand=not post.picked_up_skat,
        open_hand_cards=open_remaining,
    )


class ISSSkatAIDecisionProvider:
    """All-phase decision/effect adapter for live ISS operation.

    Pickup declaration and discard are intentionally emitted as two official
    ISS half-moves so every network effect binds to exactly one DecisionResult.
    """

    def __init__(self, ai: SkatAI, *, release_id: str) -> None:
        if not release_id:
            raise ISSBridgeError("EMPTY_RELEASE_ID")
        self.ai = ai
        self.release_id = str(release_id)

    def _decide(
        self,
        table: TableSession,
        *,
        decision_type: DecisionType,
        observation,
        wire_from_result,
        phase: str,
    ) -> ISSDecision:
        request = DecisionRequest.create(
            game_id=f"iss:{table.table_id}:{table.game_sequence}",
            sequence_no=len(table.moves),
            decision_type=decision_type,
            observation=observation,
            source="ISS",
            source_context={
                "table_id": table.table_id,
                "game_sequence": table.game_sequence,
                "move_count": len(table.moves),
                "phase": phase,
            },
        )
        result = decide_request(
            self.ai,
            request,
            release_id=self.release_id,
            metadata={"adapter_schema": BRIDGE_SCHEMA, "phase": phase},
        )
        wire_action = str(wire_from_result(result))
        if not wire_action:
            raise ISSBridgeError("EMPTY_WIRE_ACTION")
        return ISSDecision(request=request, result=result, wire_action=wire_action)

    def next_decision(self, table: TableSession) -> ISSDecision | None:
        if not table.in_progress or table.stopped:
            return None
        deal = _initial_deal(table.moves)
        if deal is None:
            return None
        seat, initial_hand = player_view_from_deal(deal)
        auction = _auction_context(table.moves)

        if not auction.state.finished:
            state = auction.state
            if state.expected_actor != seat:
                return None
            obs = BiddingObservation.create(
                initial_hand,
                actor=seat,
                bidder=state.bidder,
                answerer=state.answerer,
                bid_index=state.bid_index,
                decision_role=state.decision_role,
            )

            def bid_wire(result: DecisionResult) -> str:
                if result.action == "PASS":
                    return "p"
                if result.action != "CONTINUE":
                    raise ISSBridgeError(
                        f"UNKNOWN_BIDDING_DECISION:{result.action}"
                    )
                return state.legal_native_actions()[0]

            return self._decide(
                table,
                decision_type=DecisionType.BID,
                observation=obs,
                wire_from_result=bid_wire,
                phase=state.decision_role,
            )

        if auction.state.winner is None:
            return None
        declarer = int(auction.state.winner)
        winning_bid = int(auction.state.winning_bid or 0)
        post = _post_auction_context(table.moves)
        if post.terminal:
            return None
        contract, _ = _declaration_payload(post.declaration)

        if seat == declarer and not post.picked_up_skat and post.declaration is None:
            obs = DeclarationObservation.create(
                initial_hand,
                seat=seat,
                winning_bid=winning_bid,
                picked_up_skat=False,
                legal_contracts=("PICKUP",) + _legal_hand_contracts(winning_bid),
                max_accepted_bids_by_seat=auction.max_accepted_bids_by_seat,
            )

            def hand_wire(result: DecisionResult) -> str:
                if result.action == "PICKUP":
                    return "s"
                return _format_hand_declaration(result.action, initial_hand)

            return self._decide(
                table,
                decision_type=DecisionType.DECLARATION,
                observation=obs,
                wire_from_result=hand_wire,
                phase="SKAT_OR_HAND_DECL",
            )

        if post.picked_up_skat and post.skat_delivery is None:
            return None

        # First half-move after pickup: contract only.
        if seat == declarer and post.picked_up_skat and post.declaration is None:
            hand12 = _current_player_hand(
                initial_hand,
                player_seat=seat,
                declarer=declarer,
                post=post,
            )
            obs = DeclarationObservation.create(
                hand12,
                seat=seat,
                winning_bid=winning_bid,
                picked_up_skat=True,
                legal_contracts=_legal_pickup_contracts(winning_bid),
                max_accepted_bids_by_seat=auction.max_accepted_bids_by_seat,
            )
            return self._decide(
                table,
                decision_type=DecisionType.DECLARATION,
                observation=obs,
                wire_from_result=lambda result: result.action,
                phase="DISCARD_AND_DECL_CONTRACT",
            )

        # Second half-move after the contract echo: exactly the discard.
        if (
            seat == declarer
            and post.picked_up_skat
            and post.declaration is not None
            and not _declaration_complete(post)
        ):
            if contract is None:
                raise ISSBridgeError("DISCARD_WITHOUT_CONTRACT")
            hand12 = _current_player_hand(
                initial_hand,
                player_seat=seat,
                declarer=declarer,
                post=post,
            )
            obs = DiscardObservation.create(
                hand12,
                seat=seat,
                winning_bid=winning_bid,
                max_accepted_bids_by_seat=auction.max_accepted_bids_by_seat,
            )

            def discard_wire(result: DecisionResult) -> str:
                parts = tuple(result.action.split("."))
                if len(parts) != 2:
                    raise ISSBridgeError("BAD_DISCARD_DECISION_ACTION")
                remaining = list(hand12)
                for card in parts:
                    _remove_owned(
                        remaining, card, error="AI_DISCARD_NOT_OWNED"
                    )
                action = result.action
                if "O" in contract:
                    action += "." + ".".join(remaining)
                return action

            return self._decide(
                table,
                decision_type=DecisionType.DISCARD,
                observation=obs,
                wire_from_result=discard_wire,
                phase="DISCARD_AND_DECL_DISCARD",
            )

        if not _declaration_complete(post) or contract is None:
            return None

        obs = _cardplay_observation_from_context(
            seat=seat,
            initial_hand=initial_hand,
            auction=auction,
            post=post,
            contract=contract,
        )
        if obs is None:
            return None
        return self._decide(
            table,
            decision_type=DecisionType.PLAY_CARD,
            observation=obs,
            wire_from_result=lambda result: result.action,
            phase="CARDPLAY",
        )



from skatai.game.rules import card_points, game_type_from_contract, legal_cards, replay_tricks
from skatai.runtime.interface import (
    CardplayObservation,
    DeclarationObservation,
    DiscardObservation,
)

HAND_DECLARATIONS = ("PICKUP", "CH", "SH", "HH", "DH", "GH", "NH", "NHO")
PICKUP_DECLARATIONS = ("C", "S", "H", "D", "G", "N", "NO")


@dataclass(frozen=True)
class ISSReconstructedState:
    seat: int
    initial_hand: tuple[str, ...]
    current_hand: tuple[str, ...]
    bidding: BiddingState
    max_accepted_bids_by_seat: tuple[int, int, int]
    declarer: int | None
    winning_bid: int | None
    picked_up_skat: bool
    known_skat_delivery: tuple[str, ...]
    declaration: str | None
    discards: tuple[str, ...]
    open_hand_cards: tuple[str, ...]
    cardplays: tuple[tuple[int, str], ...]


def _auction_summary(moves: list[WireMove]) -> tuple[BiddingState, tuple[int, int, int]]:
    state = BiddingState()
    max_bids = [0, 0, 0]
    for move in moves:
        if move.kind == "initial_deal":
            continue
        if move.kind in {"bid", "answer_yes", "pass"}:
            if move.actor not in {"0", "1", "2"}:
                raise ISSBridgeError("NONPLAYER_BIDDING_ACTOR")
            actor = int(move.actor)
            try:
                before = state.apply(actor, move.action)
            except (ValueError, IndexError) as exc:
                raise ISSBridgeError(f"BIDDING_REPLAY_FAILED:{exc}") from exc
            if move.kind != "pass":
                max_bids[actor] = max(max_bids[actor], int(before["current_offer"]))
            continue
        break
    return state, (max_bids[0], max_bids[1], max_bids[2])


def _known_cards(cards) -> tuple[str, ...]:
    return tuple(str(x) for x in cards if str(x) != "??")


def reconstruct_state(table: TableSession) -> ISSReconstructedState | None:
    deal = _initial_deal(table.moves)
    if deal is None:
        return None
    seat, initial_hand = player_view_from_deal(deal)
    bidding, max_bids = _auction_summary(table.moves)
    declarer = bidding.winner if bidding.finished else None
    winning_bid = bidding.winning_bid if bidding.finished else None

    picked_up = False
    skat_delivery: tuple[str, ...] = ()
    declaration: str | None = None
    discards: tuple[str, ...] = ()
    open_hand: tuple[str, ...] = ()
    cardplays: list[tuple[int, str]] = []

    for move in table.moves:
        if move.kind == "skat_request":
            picked_up = True
        elif move.kind == "skat_delivery":
            skat_delivery = _known_cards(move.payload)
        elif move.kind == "declaration":
            payload = move.payload
            if not isinstance(payload, dict):
                raise ISSBridgeError("DECLARATION_PAYLOAD_TYPE")
            declaration = str(payload["game_type"])
            cards = tuple(str(x) for x in payload.get("cards", ()))
            if picked_up and len(cards) >= 2:
                d = _known_cards(cards[:2])
                if len(d) == 2:
                    discards = d
                if len(cards) in (12,):
                    open_hand = _known_cards(cards[2:])
            elif "O" in declaration and cards:
                # Hand ouvert declarations append the 10-card open hand.
                if len(cards) == 10:
                    open_hand = _known_cards(cards)
        elif move.kind == "discard_only":
            cards = tuple(str(x) for x in move.payload)
            d = _known_cards(cards[:2])
            if len(d) == 2:
                discards = d
            if len(cards) >= 12:
                open_hand = _known_cards(cards[2:12])
        elif move.kind == "cardplay":
            if move.actor not in {"0", "1", "2"}:
                raise ISSBridgeError("NONPLAYER_CARDPLAY_ACTOR")
            cardplays.append((int(move.actor), str(move.payload)))

    hand = list(initial_hand)
    if picked_up and seat == declarer and len(skat_delivery) == 2:
        for card in skat_delivery:
            if card in hand:
                raise ISSBridgeError(f"SKAT_CARD_ALREADY_IN_HAND:{card}")
            hand.append(card)
    if seat == declarer and discards:
        for card in discards:
            if card not in hand:
                raise ISSBridgeError(f"KNOWN_DISCARD_NOT_OWNED:{card}")
            hand.remove(card)
    for actor, card in cardplays:
        if actor == seat:
            if card not in hand:
                raise ISSBridgeError(f"OWN_PLAY_NOT_IN_RECONSTRUCTED_HAND:{card}")
            hand.remove(card)

    return ISSReconstructedState(
        seat=seat,
        initial_hand=tuple(initial_hand),
        current_hand=tuple(hand),
        bidding=bidding,
        max_accepted_bids_by_seat=max_bids,
        declarer=declarer,
        winning_bid=winning_bid,
        picked_up_skat=picked_up,
        known_skat_delivery=skat_delivery,
        declaration=declaration,
        discards=discards,
        open_hand_cards=open_hand,
        cardplays=tuple(cardplays),
    )


def _request(
    table: TableSession,
    *,
    decision_type: DecisionType,
    observation,
) -> DecisionRequest:
    return DecisionRequest.create(
        game_id=f"iss:{table.table_id}:{table.game_sequence}",
        sequence_no=len(table.moves),
        decision_type=decision_type,
        observation=observation,
        source="ISS",
        source_context={
            "table_id": table.table_id,
            "game_sequence": table.game_sequence,
            "move_count": len(table.moves),
        },
    )


def _ouvert_wire(contract: str, hand: tuple[str, ...]) -> str:
    if "O" not in contract:
        return contract
    if len(hand) != 10:
        raise ISSBridgeError(f"OUVERT_REQUIRES_TEN_CARDS:{len(hand)}")
    return contract + "." + ".".join(hand)


class ISSFullDecisionProvider:
    """Translate ISS state into the same stable SkatAI product decisions.

    ISS contains no AI policy here. Every actual bidding/declaration/discard/
    cardplay choice is made through the V2 DecisionRequest -> DecisionResult API.
    """

    def __init__(self, ai: SkatAI, *, release_id: str) -> None:
        if not release_id:
            raise ISSBridgeError("EMPTY_RELEASE_ID")
        self.ai = ai
        self.release_id = str(release_id)

    def _result(
        self,
        request: DecisionRequest,
        *,
        phase: str,
    ) -> DecisionResult:
        return decide_request(
            self.ai,
            request,
            release_id=self.release_id,
            metadata={"adapter_schema": BRIDGE_SCHEMA, "iss_phase": phase},
        )

    def next_decision(self, table: TableSession) -> ISSDecision | None:
        state = reconstruct_state(table)
        if state is None:
            return None

        # Bidding.
        if not state.bidding.finished:
            if state.bidding.expected_actor != state.seat:
                return None
            obs = BiddingObservation.create(
                state.current_hand,
                actor=state.seat,
                bidder=state.bidding.bidder,
                answerer=state.bidding.answerer,
                bid_index=state.bidding.bid_index,
                decision_role=state.bidding.decision_role,
            )
            req = _request(table, decision_type=DecisionType.BID, observation=obs)
            res = self._result(req, phase="BIDDING")
            if res.action == "PASS":
                wire = "p"
            elif res.action == "CONTINUE":
                wire = state.bidding.legal_native_actions()[0]
            else:
                raise ISSBridgeError(f"UNKNOWN_BIDDING_DECISION:{res.action}")
            return ISSDecision(req, res, wire)

        # Passed-in game: no decision exists.
        if state.declarer is None or state.winning_bid is None:
            return None

        # Only the declarer chooses pickup/hand, declaration and discard.
        if state.seat == state.declarer:
            if not state.picked_up_skat and state.declaration is None:
                obs = DeclarationObservation.create(
                    state.current_hand,
                    seat=state.seat,
                    winning_bid=state.winning_bid,
                    picked_up_skat=False,
                    legal_contracts=HAND_DECLARATIONS,
                    max_accepted_bids_by_seat=state.max_accepted_bids_by_seat,
                )
                req = _request(
                    table, decision_type=DecisionType.DECLARATION, observation=obs
                )
                res = self._result(req, phase="HAND_OR_PICKUP")
                wire = "s" if res.action == "PICKUP" else _ouvert_wire(
                    res.action, state.current_hand
                )
                return ISSDecision(req, res, wire)

            if (
                state.picked_up_skat
                and len(state.known_skat_delivery) == 2
                and state.declaration is None
            ):
                if len(state.current_hand) != 12:
                    raise ISSBridgeError(
                        f"PICKUP_DECLARATION_REQUIRES_12_CARDS:{len(state.current_hand)}"
                    )
                obs = DeclarationObservation.create(
                    state.current_hand,
                    seat=state.seat,
                    winning_bid=state.winning_bid,
                    picked_up_skat=True,
                    legal_contracts=PICKUP_DECLARATIONS,
                    max_accepted_bids_by_seat=state.max_accepted_bids_by_seat,
                )
                req = _request(
                    table, decision_type=DecisionType.DECLARATION, observation=obs
                )
                res = self._result(req, phase="POST_PICKUP_DECLARATION")
                # Use the official split half-move: declaration first, discard next.
                return ISSDecision(req, res, res.action)

            if (
                state.picked_up_skat
                and state.declaration is not None
                and not state.discards
                and len(state.current_hand) == 12
            ):
                obs = DiscardObservation.create(
                    state.current_hand,
                    seat=state.seat,
                    winning_bid=state.winning_bid,
                    max_accepted_bids_by_seat=state.max_accepted_bids_by_seat,
                )
                req = _request(
                    table, decision_type=DecisionType.DISCARD, observation=obs
                )
                res = self._result(req, phase="DISCARD")
                cards = tuple(res.action.split("."))
                if len(cards) != 2:
                    raise ISSBridgeError("DISCARD_RESULT_NOT_TWO_CARDS")
                remaining = tuple(c for c in state.current_hand if c not in set(cards))
                wire = res.action
                if "O" in state.declaration:
                    if len(remaining) != 10:
                        raise ISSBridgeError("OUVERT_DISCARD_REMAINDER_NOT_TEN")
                    wire += "." + ".".join(remaining)
                return ISSDecision(req, res, wire)

        # Cardplay requires a declaration and, for pickup games, completed discards.
        if state.declaration is None:
            return None
        if state.picked_up_skat and state.declarer == state.seat and not state.discards:
            return None

        game_type = game_type_from_contract(state.declaration)
        trick_state = replay_tricks(
            state.cardplays,
            game_type=game_type,
            declarer=state.declarer,
        )
        if trick_state["expected_actor"] != state.seat:
            return None

        legal = legal_cards(
            state.current_hand,
            trick_state["current_trick"],
            game_type,
        )
        if not legal:
            return None

        declarer_points = int(trick_state["declarer_trick_points"])
        defender_points = int(trick_state["defender_trick_points"])

        # Frozen SkatZero includes known discarded skat points for a pickup
        # declarer, but not for defenders and not for blind-hand play.
        blind_hand = "H" in state.declaration
        known_skat: tuple[str, ...] = ()
        if (
            state.seat == state.declarer
            and state.picked_up_skat
            and len(state.discards) == 2
            and not blind_hand
        ):
            known_skat = state.discards
            declarer_points += sum(card_points(c) for c in known_skat)

        open_cards = state.open_hand_cards
        if "O" in state.declaration and state.seat == state.declarer:
            open_cards = state.current_hand

        obs = CardplayObservation.create(
            state.current_hand,
            seat=state.seat,
            declarer=state.declarer,
            contract=state.declaration,
            winning_bid=state.winning_bid,
            current_trick=trick_state["current_trick"],
            played_cards=state.cardplays,
            legal_cards=legal,
            points_self=declarer_points,
            points_other=defender_points,
            max_accepted_bids_by_seat=state.max_accepted_bids_by_seat,
            skat_cards=known_skat,
            blind_hand=blind_hand,
            open_hand_cards=open_cards,
        )
        req = _request(table, decision_type=DecisionType.PLAY_CARD, observation=obs)
        res = self._result(req, phase="CARDPLAY")
        return ISSDecision(req, res, res.action)
