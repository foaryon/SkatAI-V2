"""Decision-time-only records from bounded basic-contract self-play.

The record contains no full deal, opponent private hand, or original skat.
Caller-provided policy identities are provenance fields, not a trust claim.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from typing import Any, Mapping, Sequence

from skatai.selfplay.bidding import BiddingPolicy
from skatai.selfplay.cardplay import CardplayPolicy
from skatai.selfplay.declaration import DeclarationPolicy, DiscardPolicy
from skatai.selfplay.game import run_game
from skatai.selfplay.scoring import DEFAULT_BASIC_CONTRACTS, score_basic_episode

SCHEMA = "skatai.v2.selfplay.decision-trajectory.v2"
LEARNER_SCHEMA = "skatai.v2.selfplay.learner-seat.v1"
PHASES = ("BID", "DECLARATION", "DISCARD", "CARDPLAY")


@dataclass(frozen=True)
class CapturedDecision:
    ordinal: int
    phase: str
    seat: int
    observation: dict[str, Any]
    action: Any
    native_bid_action: str | None = None


@dataclass(frozen=True)
class CapturedTrajectory:
    schema: str
    source_commit: str
    policy_ids: dict[str, tuple[str, str, str]]
    deal_seed: int
    deal_sha256: str
    threshold: float
    legal_contracts: tuple[str, ...]
    decisions: tuple[CapturedDecision, ...]
    all_pass: bool
    declarer: int | None
    contract: str | None
    signed_basic_value: int | None
    decision_trace_sha256: str


@dataclass(frozen=True)
class LearnerSeatRecord:
    schema: str
    source_commit: str
    seat: int
    policy_family_ids: dict[str, str]
    threshold: float
    legal_contracts: tuple[str, ...]
    decisions: tuple[CapturedDecision, ...]
    contract: str
    signed_basic_value: int


def declarer_learner_view(
    trajectory: CapturedTrajectory,
    *,
    policy_family_ids: Mapping[str, str],
) -> LearnerSeatRecord:
    """Export one seat's lawful observations without reproducible deal keys.

    Raw trajectory, seed, deal hash and RNG state stay in privileged provenance.
    The caller must supply public policy family IDs that contain no RNG seed.
    """
    if trajectory.all_pass or trajectory.declarer is None or trajectory.contract is None or trajectory.signed_basic_value is None:
        raise ValueError("NO_DECLARER_OUTCOME_FOR_LEARNER")
    if set(policy_family_ids) != set(PHASES) or any(not str(x) for x in policy_family_ids.values()):
        raise ValueError("BAD_PUBLIC_POLICY_FAMILY_IDS")
    seat = trajectory.declarer
    selected = tuple(d for d in trajectory.decisions if d.seat == seat)
    if not selected or not any(d.phase == "CARDPLAY" for d in selected):
        raise ValueError("MISSING_DECLARER_DECISIONS")
    return LearnerSeatRecord(
        LEARNER_SCHEMA, trajectory.source_commit, seat,
        {phase: str(policy_family_ids[phase]) for phase in PHASES},
        trajectory.threshold, trajectory.legal_contracts, selected,
        trajectory.contract, trajectory.signed_basic_value,
    )


def _policy_ids(ids: Mapping[str, Sequence[str]]) -> dict[str, tuple[str, str, str]]:
    if set(ids) != set(PHASES):
        raise ValueError("POLICY_IDENTITIES_REQUIRED_FOR_ALL_PHASES")
    result = {}
    for phase in PHASES:
        values = tuple(str(x) for x in ids[phase])
        if len(values) != 3 or any(not x for x in values):
            raise ValueError(f"BAD_POLICY_IDENTITIES:{phase}")
        result[phase] = values
    return result


def capture_basic_game(
    seed: int,
    *,
    source_commit: str,
    policy_ids: Mapping[str, Sequence[str]],
    bidding_policies: Sequence[BiddingPolicy],
    declaration_policies: Sequence[DeclarationPolicy],
    discard_policies: Sequence[DiscardPolicy],
    cardplay_policies: Sequence[CardplayPolicy],
    legal_contracts: Sequence[str],
    threshold: float = 0.5,
) -> CapturedTrajectory:
    if len(source_commit) != 40 or any(c not in "0123456789abcdef" for c in source_commit):
        raise ValueError("BAD_SOURCE_COMMIT")
    ids = _policy_ids(policy_ids)
    if any(len(x) != 3 for x in (bidding_policies, declaration_policies, discard_policies, cardplay_policies)):
        raise ValueError("THREE_POLICIES_PER_PHASE_REQUIRED")
    contracts = tuple(legal_contracts)
    if not contracts or any(contract not in DEFAULT_BASIC_CONTRACTS for contract in contracts):
        raise ValueError("CAPTURE_REQUIRES_DEFAULT_SCORABLE_CONTRACTS")
    decisions: list[CapturedDecision] = []

    def record(phase: str, seat: int, view: Any, action: Any) -> Any:
        decisions.append(CapturedDecision(len(decisions), phase, seat, asdict(view), action))
        return action

    class Bid:
        def __init__(self, seat: int) -> None:
            self.seat = seat

        def probability_continue(self, view: Any) -> float:
            return record("BID", self.seat, view, bidding_policies[self.seat].probability_continue(view))

    class Declare:
        def __init__(self, seat: int) -> None:
            self.seat = seat

        def choose_contract(self, view: Any) -> str:
            return record("DECLARATION", self.seat, view, declaration_policies[self.seat].choose_contract(view))

    class Discard:
        def __init__(self, seat: int) -> None:
            self.seat = seat

        def choose_discard(self, view: Any) -> tuple[str, str]:
            return record("DISCARD", self.seat, view, discard_policies[self.seat].choose_discard(view))

    class Play:
        def __init__(self, seat: int) -> None:
            self.seat = seat

        def play_card(self, view: Any) -> str:
            return record("CARDPLAY", self.seat, view, cardplay_policies[self.seat].play_card(view))

    episode = run_game(
        seed,
        bidding_policies=[Bid(seat) for seat in range(3)],
        declaration_policies=[Declare(seat) for seat in range(3)],
        discard_policies=[Discard(seat) for seat in range(3)],
        cardplay_policies=[Play(seat) for seat in range(3)],
        legal_contracts=contracts, threshold=threshold,
    )
    auction_actions = iter(episode.bidding.actions)
    for index, decision in enumerate(decisions):
        if decision.phase == "BID":
            actor, native = next(auction_actions)
            if actor != decision.seat:
                raise ValueError("CAPTURED_AUCTION_ACTOR_MISMATCH")
            decisions[index] = replace(decision, native_bid_action=native)
    if next(auction_actions, None) is not None:
        raise ValueError("CAPTURED_AUCTION_LENGTH_MISMATCH")
    scored = None if episode.bidding.all_pass else score_basic_episode(episode)
    trace = hashlib.sha256(json.dumps(
        [asdict(x) for x in decisions], sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return CapturedTrajectory(
        SCHEMA, source_commit, ids, seed, episode.deal_sha256,
        float(threshold), contracts,
        tuple(decisions), episode.bidding.all_pass,
        episode.bidding.winner,
        None if episode.declaration is None else episode.declaration.contract,
        None if scored is None else scored.signed_game_value,
        trace,
    )
