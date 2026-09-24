from __future__ import annotations

import subprocess
from pathlib import Path

from skatai.game.bidding import BID_VALUES
from skatai.runtime.bidding_adapter import LearnedBiddingAdapter
from skatai.runtime.interface import (
    BiddingObservation,
    CardplayObservation,
    DeclarationObservation,
    DiscardObservation,
    SkatAI,
    SkatAIInterfaceError,
)

BACKEND_SCHEMA = "skatai.v2.frozen-skatzero-cli-backend.v2"


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


def _run_cli(
    skatzero_root: Path,
    python_executable: Path,
    args: list[str],
    *,
    timeout_s: float = 120.0,
) -> list[str]:
    api = skatzero_root / "api.py"
    if not api.is_file():
        raise SkatAIInterfaceError(f"B0_API_MISSING:{api}")
    if not python_executable.is_file():
        raise SkatAIInterfaceError(f"B0_PYTHON_MISSING:{python_executable}")
    try:
        proc = subprocess.run(
            [str(python_executable), str(api), *args],
            cwd=str(skatzero_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SkatAIInterfaceError(f"B0_CLI_TIMEOUT:{args[0]}") from exc
    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()[-1:] or ["no stderr"]
        raise SkatAIInterfaceError(
            f"B0_CLI_FAILED:{args[0]}:rc={proc.returncode}:{tail[0]}"
        )
    lines = [x.strip() for x in proc.stdout.splitlines() if x.strip()]
    if not lines:
        raise SkatAIInterfaceError(f"EMPTY_B0_CLI_OUTPUT:{args[0]}")
    return lines


class FrozenB0BiddingPolicy:
    """Original SkatZero BID CLI mapped to the stable V2 bidding interface."""

    def __init__(
        self,
        skatzero_root: Path,
        python_executable: Path,
        *,
        timeout_s: float = 120.0,
    ) -> None:
        self.skatzero_root = skatzero_root
        self.python_executable = python_executable
        self.timeout_s = float(timeout_s)
        self._max_bid_cache: dict[tuple[tuple[str, ...], int], int] = {}

    def max_bid(self, hand: tuple[str, ...], seat: int) -> int:
        key = (hand, seat)
        if key not in self._max_bid_cache:
            lines = _run_cli(
                self.skatzero_root,
                self.python_executable,
                ["BID", ",".join(hand), str(seat)],
                timeout_s=self.timeout_s,
            )
            try:
                value = int(lines[-1])
            except ValueError as exc:
                raise SkatAIInterfaceError(
                    f"B0_BAD_MAX_BID_LINE:{lines[-1]!r}"
                ) from exc
            if value < 0:
                raise SkatAIInterfaceError(f"B0_NEGATIVE_MAX_BID:{value}")
            self._max_bid_cache[key] = value
        return self._max_bid_cache[key]

    def probability_continue(self, observation: BiddingObservation) -> float:
        max_bid = self.max_bid(observation.hand, observation.actor)
        return 1.0 if max_bid >= BID_VALUES[observation.bid_index] else 0.0


class FrozenB0DeclarationDiscardPolicy:
    """Original SkatZero declaration/discard CLI behind the V2 interface."""

    def __init__(
        self,
        skatzero_root: Path,
        python_executable: Path,
        *,
        timeout_s: float = 120.0,
    ) -> None:
        self.skatzero_root = skatzero_root
        self.python_executable = python_executable
        self.timeout_s = float(timeout_s)
        self._pickup_cache: dict[
            tuple[tuple[str, ...], int, int, tuple[int, int, int]], tuple[str, ...]
        ] = {}

    def _pickup_lines(
        self, observation: DeclarationObservation | DiscardObservation
    ) -> tuple[str, ...]:
        bids = _require_bid_vector(observation.max_accepted_bids_by_seat)
        opp1, opp2 = _relative_opponent_bids(observation.seat, bids)
        cards = (
            observation.cards
            if isinstance(observation, DeclarationObservation)
            else observation.hand12
        )
        if len(cards) != 12:
            raise SkatAIInterfaceError("B0_PICKUP_REQUIRES_12_CARDS")
        key = (tuple(cards), observation.seat, observation.winning_bid, bids)
        if key not in self._pickup_cache:
            lines = _run_cli(
                self.skatzero_root,
                self.python_executable,
                [
                    "DISCARD_AND_DECL",
                    ",".join(cards),
                    str(observation.seat),
                    str(opp1),
                    str(opp2),
                    str(observation.winning_bid),
                ],
                timeout_s=self.timeout_s,
            )
            self._pickup_cache[key] = tuple(lines)
        return self._pickup_cache[key]

    def _pickup_line(
        self, observation: DeclarationObservation | DiscardObservation
    ) -> str:
        return self._pickup_lines(observation)[-1]

    def choose_contract(self, observation: DeclarationObservation) -> str:
        bids = _require_bid_vector(observation.max_accepted_bids_by_seat)
        opp1, opp2 = _relative_opponent_bids(observation.seat, bids)
        if observation.picked_up_skat:
            lines = self._pickup_lines(observation)
            final = lines[-1]
            contract = final.split(".", 1)[0]
            if contract not in observation.legal_contracts:
                for line in lines[:-1]:
                    ranked = line.split(maxsplit=1)
                    if len(ranked) == 2 and ranked[0] in observation.legal_contracts:
                        contract = ranked[0]
                        break
        else:
            if len(observation.cards) != 10:
                raise SkatAIInterfaceError("B0_HAND_DECLARATION_REQUIRES_10_CARDS")
            lines = _run_cli(
                self.skatzero_root,
                self.python_executable,
                [
                    "SKAT_OR_HAND_DECL",
                    ",".join(observation.cards),
                    str(observation.seat),
                    str(opp1),
                    str(opp2),
                    str(observation.winning_bid),
                ],
                timeout_s=self.timeout_s,
            )
            final = lines[-1]
            contract = "PICKUP" if final == "s" else final.split(".", 1)[0]

        if contract not in observation.legal_contracts:
            raise SkatAIInterfaceError(
                f"B0_DECLARATION_NOT_IN_LEGAL_SET:{contract}"
            )
        return contract

    def choose_discard(self, observation: DiscardObservation) -> tuple[str, str]:
        final = self._pickup_line(observation)
        parts = final.split(".")
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

    # ISS/V2 seats are absolute FH/MH/RH (0/1/2). Frozen SkatZero cardplay
    # uses player ids relative to the declarer: 0=declarer, then 1/2 clockwise.
    history = ",".join(
        f"{(seat - observation.declarer) % 3}{card}"
        for seat, card in observation.played_cards
    )
    open_cards = (
        ",".join(observation.open_hand_cards)
        if observation.open_hand_cards
        else "??"
    )
    return [
        "CARDPLAY",
        _contract_base(observation.contract),
        ",".join(observation.hand),
        # Upstream api.py converts the declarer seat to starting_player.
        str(observation.declarer),
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
    """Original SkatZero CARDPLAY CLI behind the V2 interface."""

    def __init__(
        self,
        skatzero_root: Path,
        python_executable: Path,
        *,
        timeout_s: float = 120.0,
    ) -> None:
        self.skatzero_root = skatzero_root
        self.python_executable = python_executable
        self.timeout_s = float(timeout_s)

    def play_card(self, observation: CardplayObservation) -> str:
        lines = _run_cli(
            self.skatzero_root,
            self.python_executable,
            _cardplay_args(observation),
            timeout_s=self.timeout_s,
        )
        card = lines[-1]
        if card not in observation.legal_cards:
            raise SkatAIInterfaceError(f"B0_RETURNED_ILLEGAL_CARD:{card}")
        return card


def build_b0_skat_ai(
    skatzero_root: Path,
    python_executable: Path,
) -> SkatAI:
    bidding = FrozenB0BiddingPolicy(skatzero_root, python_executable)
    downstream = FrozenB0DeclarationDiscardPolicy(skatzero_root, python_executable)
    cardplay = FrozenB0CardplayPolicy(skatzero_root, python_executable)
    return SkatAI(
        bidding=bidding,
        declaration=downstream,
        discard=downstream,
        cardplay=cardplay,
        bidding_threshold=0.5,
    )


def build_b1_skat_ai(
    b1_model: Path,
    skatzero_root: Path,
    python_executable: Path,
    *,
    device: str = "cpu",
    threshold: float = 0.5,
) -> SkatAI:
    bidding = LearnedBiddingAdapter.load(b1_model, device=device)
    downstream = FrozenB0DeclarationDiscardPolicy(skatzero_root, python_executable)
    cardplay = FrozenB0CardplayPolicy(skatzero_root, python_executable)
    return SkatAI(
        bidding=bidding,
        declaration=downstream,
        discard=downstream,
        cardplay=cardplay,
        bidding_threshold=threshold,
    )
