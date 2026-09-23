from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
import hashlib
import json
import time
from typing import Any, Mapping, Sequence

from skatai.runtime.interface import (
    BiddingObservation,
    CardplayObservation,
    DeclarationObservation,
    DiscardObservation,
    SkatAI,
    SkatAIInterfaceError,
)

DECISION_SCHEMA = "skatai.v2.decision-envelope.v1"


class DecisionType(str, Enum):
    BID = "BID"
    DECLARATION = "DECLARATION"
    DISCARD = "DISCARD"
    PLAY_CARD = "PLAY_CARD"


Observation = (
    BiddingObservation
    | DeclarationObservation
    | DiscardObservation
    | CardplayObservation
)


def _canonical(value: Any) -> Any:
    if is_dataclass(value):
        return _canonical(asdict(value))
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda x: str(x[0]))}
    if isinstance(value, (tuple, list)):
        return [_canonical(x) for x in value]
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"UNSUPPORTED_CANONICAL_VALUE:{type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _default_legal_actions(
    decision_type: DecisionType,
    observation: Observation,
) -> tuple[str, ...]:
    if decision_type is DecisionType.BID:
        if not isinstance(observation, BiddingObservation):
            raise SkatAIInterfaceError("BID_REQUIRES_BIDDING_OBSERVATION")
        return ("PASS", "CONTINUE")
    if decision_type is DecisionType.DECLARATION:
        if not isinstance(observation, DeclarationObservation):
            raise SkatAIInterfaceError("DECLARATION_REQUIRES_DECLARATION_OBSERVATION")
        return tuple(observation.legal_contracts)
    if decision_type is DecisionType.DISCARD:
        if not isinstance(observation, DiscardObservation):
            raise SkatAIInterfaceError("DISCARD_REQUIRES_DISCARD_OBSERVATION")
        cards = tuple(observation.hand12)
        return tuple(
            f"{cards[i]}.{cards[j]}"
            for i in range(len(cards))
            for j in range(i + 1, len(cards))
        )
    if decision_type is DecisionType.PLAY_CARD:
        if not isinstance(observation, CardplayObservation):
            raise SkatAIInterfaceError("PLAY_CARD_REQUIRES_CARDPLAY_OBSERVATION")
        return tuple(observation.legal_cards)
    raise SkatAIInterfaceError(f"UNSUPPORTED_DECISION_TYPE:{decision_type}")


@dataclass(frozen=True)
class DecisionRequest:
    schema: str
    request_id: str
    game_id: str
    position_hash: str
    sequence_no: int
    decision_type: DecisionType
    observation: Observation
    legal_actions: tuple[str, ...]
    source: str
    source_context: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        game_id: str,
        sequence_no: int,
        decision_type: DecisionType | str,
        observation: Observation,
        source: str,
        source_context: Mapping[str, Any] | None = None,
        legal_actions: Sequence[str] | None = None,
    ) -> "DecisionRequest":
        dtype = DecisionType(decision_type)
        gid = str(game_id).strip()
        src = str(source).strip()
        seq = int(sequence_no)
        if not gid:
            raise SkatAIInterfaceError("EMPTY_GAME_ID")
        if not src:
            raise SkatAIInterfaceError("EMPTY_DECISION_SOURCE")
        if seq < 0:
            raise SkatAIInterfaceError("NEGATIVE_SEQUENCE_NO")
        legal = (
            _default_legal_actions(dtype, observation)
            if legal_actions is None
            else tuple(str(x) for x in legal_actions)
        )
        if not legal or len(set(legal)) != len(legal) or any(not x for x in legal):
            raise SkatAIInterfaceError("BAD_LEGAL_ACTION_SET")
        context = {} if source_context is None else dict(source_context)

        position_body = {
            "schema": DECISION_SCHEMA,
            "game_id": gid,
            "sequence_no": seq,
            "decision_type": dtype.value,
            "observation": observation,
            "legal_actions": legal,
            "source": src,
            "source_context": context,
        }
        position_hash = sha256_json(position_body)
        request_id = hashlib.sha256(
            ("skatai.v2.request\0" + position_hash).encode("utf-8")
        ).hexdigest()
        return cls(
            schema=DECISION_SCHEMA,
            request_id=request_id,
            game_id=gid,
            position_hash=position_hash,
            sequence_no=seq,
            decision_type=dtype,
            observation=observation,
            legal_actions=legal,
            source=src,
            source_context=context,
        )


@dataclass(frozen=True)
class DecisionResult:
    schema: str
    decision_id: str
    request_id: str
    position_hash: str
    decision_type: DecisionType
    action: str
    release_id: str
    latency_ms: float
    metadata: dict[str, Any]

    @classmethod
    def create(
        cls,
        request: DecisionRequest,
        *,
        action: str,
        release_id: str,
        latency_ms: float,
        metadata: Mapping[str, Any] | None = None,
    ) -> "DecisionResult":
        action = str(action)
        release = str(release_id).strip()
        latency = float(latency_ms)
        if action not in request.legal_actions:
            raise SkatAIInterfaceError(f"ACTION_NOT_LEGAL_FOR_REQUEST:{action}")
        if not release:
            raise SkatAIInterfaceError("EMPTY_RELEASE_ID")
        if latency < 0:
            raise SkatAIInterfaceError("NEGATIVE_DECISION_LATENCY")
        decision_id = hashlib.sha256(
            (
                "skatai.v2.decision\0"
                + request.request_id
                + "\0"
                + release
                + "\0"
                + action
            ).encode("utf-8")
        ).hexdigest()
        return cls(
            schema=DECISION_SCHEMA,
            decision_id=decision_id,
            request_id=request.request_id,
            position_hash=request.position_hash,
            decision_type=request.decision_type,
            action=action,
            release_id=release,
            latency_ms=latency,
            metadata={} if metadata is None else dict(metadata),
        )


def decide(
    ai: SkatAI,
    request: DecisionRequest,
    *,
    release_id: str,
    metadata: Mapping[str, Any] | None = None,
) -> DecisionResult:
    start = time.monotonic_ns()
    obs = request.observation
    if request.decision_type is DecisionType.BID:
        if not isinstance(obs, BiddingObservation):
            raise SkatAIInterfaceError("BID_REQUIRES_BIDDING_OBSERVATION")
        action = ai.decide_bid(obs)
    elif request.decision_type is DecisionType.DECLARATION:
        if not isinstance(obs, DeclarationObservation):
            raise SkatAIInterfaceError("DECLARATION_REQUIRES_DECLARATION_OBSERVATION")
        action = ai.choose_contract(obs)
    elif request.decision_type is DecisionType.DISCARD:
        if not isinstance(obs, DiscardObservation):
            raise SkatAIInterfaceError("DISCARD_REQUIRES_DISCARD_OBSERVATION")
        cards = ai.choose_discard(obs)
        action = ".".join(cards)
    elif request.decision_type is DecisionType.PLAY_CARD:
        if not isinstance(obs, CardplayObservation):
            raise SkatAIInterfaceError("PLAY_CARD_REQUIRES_CARDPLAY_OBSERVATION")
        action = ai.play_card(obs)
    else:
        raise SkatAIInterfaceError(f"UNSUPPORTED_DECISION_TYPE:{request.decision_type}")

    elapsed_ms = (time.monotonic_ns() - start) / 1_000_000.0
    return DecisionResult.create(
        request,
        action=action,
        release_id=release_id,
        latency_ms=elapsed_ms,
        metadata=metadata,
    )
