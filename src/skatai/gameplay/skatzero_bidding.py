from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from skatai.gameplay.bidding import AuctionResult, NeuralBiddingPolicy


@dataclass(frozen=True)
class MappedAuctionResult:
    auction: AuctionResult
    seat_to_player: tuple[int, int, int]
    winner_player_id: int | None
    max_accepted_bids_by_player: tuple[int, int, int]


def bidding_seat_to_player(starting_player: int) -> tuple[int, int, int]:
    """Map V2 bidding seats (FH/MH/RH) to SkatZero player ids.

    SkatZero starting_player is forehand/current leader at game start.
    Historical V2 bidding features use fixed semantic seats:
      0 = forehand, 1 = middlehand, 2 = rearhand.
    """
    if starting_player not in (0, 1, 2):
        raise ValueError(f"BAD_STARTING_PLAYER:{starting_player}")
    return (
        starting_player,
        (starting_player + 1) % 3,
        (starting_player + 2) % 3,
    )


def max_accepted_bids_by_seat(auction: AuctionResult) -> tuple[int, int, int]:
    out = [0, 0, 0]
    for d in auction.decisions:
        if d.native_action != "p":
            out[d.actor] = max(out[d.actor], d.current_offer)
    return tuple(out)


def map_auction_to_players(
    auction: AuctionResult,
    *,
    starting_player: int,
) -> MappedAuctionResult:
    seat_to_player = bidding_seat_to_player(starting_player)
    by_seat = max_accepted_bids_by_seat(auction)
    by_player = [0, 0, 0]
    for seat, player_id in enumerate(seat_to_player):
        by_player[player_id] = by_seat[seat]
    winner = (
        None
        if auction.winner is None
        else seat_to_player[int(auction.winner)]
    )
    return MappedAuctionResult(
        auction=auction,
        seat_to_player=seat_to_player,
        winner_player_id=winner,
        max_accepted_bids_by_player=tuple(by_player),
    )


def run_skatzero_auction(
    policy: NeuralBiddingPolicy,
    player_hands: Sequence[Sequence[str]],
    *,
    starting_player: int,
    threshold: float = 0.5,
) -> MappedAuctionResult:
    if len(player_hands) != 3:
        raise ValueError(f"EXPECTED_3_PLAYER_HANDS:{len(player_hands)}")
    mapping = bidding_seat_to_player(starting_player)
    bidding_hands = [player_hands[player_id] for player_id in mapping]
    auction = policy.auction(bidding_hands, threshold=threshold)
    return map_auction_to_players(auction, starting_player=starting_player)
