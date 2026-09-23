from __future__ import annotations

import contextlib
import hashlib
import io
import random
import sys
from pathlib import Path
from typing import Sequence

from skatai.evaluation.skatzero_bidding_baseline import (
    frozen_b0_discard_and_decl,
    frozen_b0_skat_or_hand,
)
from skatai.runtime.interface import (
    CardplayObservation,
    DeclarationObservation,
    DiscardObservation,
    SkatAIInterfaceError,
)

BACKEND_SCHEMA = "skatai.v2.frozen-skatzero-backend.v1"


def _install(root: Path) -> None:
    value = str(root.resolve())
    if value not in sys.path:
        sys.path.insert(0, value)


def _seed(*parts: object) -> int:
    h = hashlib.sha256(":".join(str(x) for x in parts).encode()).digest()
    return int.from_bytes(h[:4], "big")


def _seed_runtime(seed: int) -> None:
    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)


def _require_bid_vector(
    values: tuple[int, int, int] | None,
) -> tuple[int, int, int]:
    if values is None:
        raise SkatAIInterfaceError("B0_REQUIRES_MAX_ACCEPTED_BIDS_BY_SEAT")
    return values


def _relative_opponent_bids(
    declarer: int,
    bids: tuple[int, int, int],
) -> tuple[int, int]:
    return bids[(declarer + 1) % 3], bids[(declarer + 2) % 3]


def _contract_base(contract: str) -> str:
    c = str(contract).upper()
    if c.startswith("NO") or c.startswith("N"):
        return "N"
    if c.startswith("G"):
        return "G"
    if c and c[0] in {"C", "S", "H", "D"}:
        return c[0]
    raise SkatAIInterfaceError(f"UNSUPPORTED_B0_CONTRACT:{contract}")


class FrozenB0DeclarationDiscardPolicy:
    """Frozen upstream SkatZero declaration/discard behind the V2 interface."""

    def __init__(
        self,
        skatzero_root: Path,
        *,
        master_seed: int = 20260923,
        accuracy: int = 231,
        bid_threshold: float = -5.0,
    ) -> None:
        self.skatzero_root = skatzero_root
        self.master_seed = int(master_seed)
        self.accuracy = int(accuracy)
        self.bid_threshold = float(bid_threshold)

    def _seed_for(
        self,
        cards: Sequence[str],
        seat: int,
        bids: Sequence[int],
        winning_bid: int,
        phase: str,
    ) -> int:
        return _seed(
            self.master_seed,
            phase,
            seat,
            winning_bid,
            ",".join(cards),
            ",".join(str(x) for x in bids),
        )

    def _pickup_result(self, observation: DeclarationObservation | DiscardObservation):
        bids = _require_bid_vector(observation.max_accepted_bids_by_seat)
        opp1, opp2 = _relative_opponent_bids(observation.seat, bids)
        cards = (
            observation.cards
            if isinstance(observation, DeclarationObservation)
            else observation.hand12
        )
        if len(cards) != 12:
            raise SkatAIInterfaceError("B0_PICKUP_REQUIRES_12_CARDS")
        return frozen_b0_discard_and_decl(
            self.skatzero_root,
            cards,
            observation.seat,
            opp1,
            opp2,
            observation.winning_bid,
            seed=self._seed_for(
                cards,
                observation.seat,
                bids,
                observation.winning_bid,
                "pickup-declaration",
            ),
        )

    def choose_contract(self, observation: DeclarationObservation) -> str:
        bids = _require_bid_vector(observation.max_accepted_bids_by_seat)
        opp1, opp2 = _relative_opponent_bids(observation.seat, bids)
        if observation.picked_up_skat:
            result = self._pickup_result(observation)
            contract = result.declaration.split(".", 1)[0]
        else:
            if len(observation.cards) != 10:
                raise SkatAIInterfaceError("B0_HAND_DECLARATION_REQUIRES_10_CARDS")
            result = frozen_b0_skat_or_hand(
                self.skatzero_root,
                observation.cards,
                observation.seat,
                opp1,
                opp2,
                observation.winning_bid,
                seed=self._seed_for(
                    observation.cards,
                    observation.seat,
                    bids,
                    observation.winning_bid,
                    "skat-or-hand",
                ),
                accuracy=self.accuracy,
                bid_threshold=self.bid_threshold,
            )
            if result.declaration == "s":
                contract = "PICKUP"
            else:
                contract = result.declaration.split(".", 1)[0]

        if contract not in observation.legal_contracts:
            raise SkatAIInterfaceError(
                f"B0_DECLARATION_NOT_IN_LEGAL_SET:{contract}"
            )
        return contract

    def choose_discard(self, observation: DiscardObservation) -> tuple[str, str]:
        result = self._pickup_result(observation)
        parts = result.declaration.split(".")
        if len(parts) < 3:
            raise SkatAIInterfaceError("B0_DISCARD_OUTPUT_MALFORMED")
        cards = (parts[1], parts[2])
        if len(set(cards)) != 2 or not set(cards).issubset(set(observation.hand12)):
            raise SkatAIInterfaceError("B0_DISCARD_NOT_OWNED_OR_DUPLICATE")
        return cards


def _cardplay_args(observation: CardplayObservation) -> list[str]:
    bids = _require_bid_vector(observation.max_accepted_bids_by_seat)
    if observation.points_self is None or observation.points_other is None:
        raise SkatAIInterfaceError("B0_CARDPLAY_REQUIRES_PUBLIC_POINT_STATE")

    role = (observation.seat - observation.declarer) % 3
    opp1, opp2 = _relative_opponent_bids(observation.declarer, bids)
    skat = observation.skat_cards
    if len(skat) == 2:
        skat1, skat2 = skat
    elif len(skat) == 0:
        skat1 = skat2 = "??"
    else:
        raise SkatAIInterfaceError("B0_CARDPLAY_SKAT_MUST_BE_ZERO_OR_TWO_CARDS")

    history = ",".join(f"{seat}{card}" for seat, card in observation.played_cards)
    open_cards = (
        ",".join(observation.open_hand_cards)
        if observation.open_hand_cards
        else "??"
    )
    return [
        "CARDPLAY",
        _contract_base(observation.contract),
        ",".join(observation.hand),
        str(observation.seat),
        str(observation.points_self),
        str(observation.points_other),
        str(opp1),
        str(opp2),
        skat1,
        skat2,
        "1" if observation.blind_hand else "0",
        str(role),
        open_cards,
        history,
    ]


class FrozenB0CardplayPolicy:
    def __init__(self, skatzero_root: Path, *, master_seed: int = 20260923) -> None:
        self.skatzero_root = skatzero_root
        self.master_seed = int(master_seed)

    def play_card(self, observation: CardplayObservation) -> str:
        _install(self.skatzero_root)
        seed = _seed(
            self.master_seed,
            "cardplay",
            observation.seat,
            observation.declarer,
            observation.contract,
            observation.winning_bid,
            ",".join(observation.hand),
            ",".join(f"{s}{c}" for s, c in observation.played_cards),
        )
        _seed_runtime(seed)
        import api as skatzero_api  # type: ignore

        args = _cardplay_args(observation)
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            skatzero_api.cardplay(args)
        lines = [x.strip() for x in capture.getvalue().splitlines() if x.strip()]
        if not lines:
            raise SkatAIInterfaceError("EMPTY_B0_CARDPLAY_OUTPUT")
        card = lines[-1]
        if card not in observation.legal_cards:
            raise SkatAIInterfaceError(f"B0_RETURNED_ILLEGAL_CARD:{card}")
        return card
