"""Controlled hand or pickup declaration for V2 self-play.

The caller supplies the legal contract set. Contract values, overbids, and
the exact ISS message order are not modeled here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from skatai.runtime.interface import DeclarationObservation, DiscardObservation
from skatai.selfplay.cardplay import Deal, make_deal

SCHEMA = "skatai.v2.selfplay.declaration.v1"


class DeclarationPolicy(Protocol):
    def choose_contract(self, observation: DeclarationObservation) -> str: ...


class DiscardPolicy(Protocol):
    def choose_discard(self, observation: DiscardObservation) -> tuple[str, str]: ...


@dataclass(frozen=True)
class DeclarationEpisode:
    schema: str
    deal_seed: int
    deal_sha256: str
    declarer: int
    winning_bid: int
    picked_up_skat: bool
    contract: str
    discarded: tuple[str, ...]
    final_hand: tuple[str, ...]
    final_skat: tuple[str, str]


def run_declaration(
    deal: Deal,
    *,
    declarer: int,
    winning_bid: int,
    max_accepted_bids_by_seat: Sequence[int],
    legal_contracts: Sequence[str],
    declaration_policy: DeclarationPolicy,
    discard_policy: DiscardPolicy,
) -> DeclarationEpisode:
    if deal != make_deal(deal.seed):
        raise ValueError("DEAL_IDENTITY_MISMATCH")
    if declarer not in (0, 1, 2) or winning_bid < 18:
        raise ValueError("BAD_DECLARER_OR_WINNING_BID")
    legal = tuple(str(x) for x in legal_contracts)
    if not legal or "PICKUP" in legal or len(set(legal)) != len(legal):
        raise ValueError("BAD_LEGAL_CONTRACT_SET")
    bids = tuple(int(x) for x in max_accepted_bids_by_seat)
    if len(bids) != 3 or any(x < 0 for x in bids):
        raise ValueError("BAD_BID_VECTOR")
    initial = DeclarationObservation.create(
        deal.hands[declarer], seat=declarer, winning_bid=winning_bid,
        picked_up_skat=False, legal_contracts=("PICKUP", *legal),
        max_accepted_bids_by_seat=bids,
    )
    choice = str(declaration_policy.choose_contract(initial))
    if choice == "PICKUP":
        hand12 = (*deal.hands[declarer], *deal.skat)
        discard_view = DiscardObservation.create(
            hand12, seat=declarer, winning_bid=winning_bid,
            max_accepted_bids_by_seat=bids,
        )
        discarded = tuple(str(x) for x in discard_policy.choose_discard(discard_view))
        if len(discarded) != 2 or len(set(discarded)) != 2 or not set(discarded).issubset(set(hand12)):
            raise ValueError("ILLEGAL_DISCARD")
        final_hand = tuple(card for card in hand12 if card not in discarded)
        final_skat = (discarded[0], discarded[1])
        pickup_view = DeclarationObservation.create(
            hand12, seat=declarer, winning_bid=winning_bid,
            picked_up_skat=True, legal_contracts=legal,
            max_accepted_bids_by_seat=bids,
        )
        contract = str(declaration_policy.choose_contract(pickup_view))
    else:
        discarded = ()
        final_hand = deal.hands[declarer]
        final_skat = deal.skat
        contract = choice
    if contract not in legal:
        raise ValueError("CONTRACT_NOT_IN_LEGAL_SET")
    return DeclarationEpisode(
        schema=SCHEMA, deal_seed=deal.seed, deal_sha256=deal.identity_sha256,
        declarer=declarer, winning_bid=winning_bid,
        picked_up_skat=choice == "PICKUP", contract=contract,
        discarded=discarded, final_hand=final_hand, final_skat=final_skat,
    )
