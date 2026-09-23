from __future__ import annotations

import contextlib
import io
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class B0BidResult:
    max_bid: int
    elapsed_s: float
    seed: int
    accuracy: int
    bid_threshold: float


def _install_skatzero_import(skatzero_root: Path) -> None:
    root = str(skatzero_root.resolve())
    if root not in sys.path:
        sys.path.insert(0, root)


def _seed_b0(seed: int) -> None:
    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)


def _parse_max_bid(stdout: str) -> int:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise ValueError("EMPTY_B0_BID_OUTPUT")
    try:
        value = int(lines[-1])
    except ValueError as exc:
        raise ValueError(f"BAD_B0_FINAL_BID_LINE:{lines[-1]!r}") from exc
    if value < 0:
        raise ValueError(f"NEGATIVE_B0_BID:{value}")
    return value


def frozen_b0_max_bid(
    skatzero_root: Path,
    hand: Sequence[str],
    seat: int,
    *,
    seed: int,
    accuracy: int = 231,
    bid_threshold: float = -5.0,
) -> B0BidResult:
    """Call the frozen upstream deployment bidding implementation unchanged.

    The wrapper controls RNG only; bidding logic remains upstream api.bid().
    """
    if seat not in (0, 1, 2):
        raise ValueError(f"BAD_SEAT:{seat}")
    if len(hand) != 10 or len(set(hand)) != 10:
        raise ValueError("B0_BID_REQUIRES_10_UNIQUE_CARDS")

    _install_skatzero_import(skatzero_root)
    _seed_b0(seed)

    import api as skatzero_api  # type: ignore

    args = ["BID", ",".join(str(x) for x in hand), str(seat)]
    capture = io.StringIO()
    start = time.perf_counter()
    with contextlib.redirect_stdout(capture):
        skatzero_api.bid(args, accuracy, bid_threshold)
    elapsed = time.perf_counter() - start
    max_bid = _parse_max_bid(capture.getvalue())
    return B0BidResult(
        max_bid=max_bid,
        elapsed_s=elapsed,
        seed=seed,
        accuracy=accuracy,
        bid_threshold=bid_threshold,
    )


@dataclass(frozen=True)
class B0DeclarationResult:
    declaration: str
    elapsed_s: float
    seed: int
    mode: str


def _capture_final_line(callable_) -> tuple[str, float]:
    capture = io.StringIO()
    start = time.perf_counter()
    with contextlib.redirect_stdout(capture):
        callable_()
    elapsed = time.perf_counter() - start
    lines = [line.strip() for line in capture.getvalue().splitlines() if line.strip()]
    if not lines:
        raise ValueError("EMPTY_B0_API_OUTPUT")
    return lines[-1], elapsed


def frozen_b0_skat_or_hand(
    skatzero_root: Path,
    hand: Sequence[str],
    seat: int,
    opponent_bid_1: int,
    opponent_bid_2: int,
    winning_bid: int,
    *,
    seed: int,
    accuracy: int = 231,
    bid_threshold: float = -5.0,
) -> B0DeclarationResult:
    """Call upstream SKAT_OR_HAND_DECL unchanged under deterministic RNG."""
    if len(hand) != 10 or len(set(hand)) != 10:
        raise ValueError("SKAT_OR_HAND_REQUIRES_10_UNIQUE_CARDS")
    _install_skatzero_import(skatzero_root)
    _seed_b0(seed)
    import api as skatzero_api  # type: ignore

    args = [
        "SKAT_OR_HAND_DECL",
        ",".join(str(x) for x in hand),
        str(int(seat)),
        str(int(opponent_bid_1)),
        str(int(opponent_bid_2)),
        str(int(winning_bid)),
    ]
    final, elapsed = _capture_final_line(
        lambda: skatzero_api.bid(args, accuracy, bid_threshold)
    )
    if final != "s" and not (
        final.endswith("H") or final.startswith("NHO.")
    ):
        raise ValueError(f"BAD_SKAT_OR_HAND_DECL:{final!r}")
    return B0DeclarationResult(final, elapsed, seed, "SKAT_OR_HAND_DECL")


def frozen_b0_discard_and_decl(
    skatzero_root: Path,
    hand_with_skat: Sequence[str],
    seat: int,
    opponent_bid_1: int,
    opponent_bid_2: int,
    winning_bid: int,
    *,
    seed: int,
) -> B0DeclarationResult:
    """Call upstream DISCARD_AND_DECL unchanged under deterministic RNG."""
    if len(hand_with_skat) != 12 or len(set(hand_with_skat)) != 12:
        raise ValueError("DISCARD_DECL_REQUIRES_12_UNIQUE_CARDS")
    _install_skatzero_import(skatzero_root)
    _seed_b0(seed)
    import api as skatzero_api  # type: ignore

    args = [
        "DISCARD_AND_DECL",
        ",".join(str(x) for x in hand_with_skat),
        str(int(seat)),
        str(int(opponent_bid_1)),
        str(int(opponent_bid_2)),
        str(int(winning_bid)),
    ]
    final, elapsed = _capture_final_line(lambda: skatzero_api.declare(args))
    parts = final.split(".")
    if len(parts) < 3 or parts[0] not in {"C", "S", "H", "D", "G", "N", "NO"}:
        raise ValueError(f"BAD_DISCARD_DECL:{final!r}")
    return B0DeclarationResult(final, elapsed, seed, "DISCARD_AND_DECL")
