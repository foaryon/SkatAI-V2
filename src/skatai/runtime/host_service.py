"""Stable JSON-lines host boundary for a validated SkatAI release package."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from skatai.artifacts.release import validate_release_package
from skatai.runtime.decision import (
    DecisionRequest,
    DecisionType,
    canonical_json,
    decide,
    sha256_json,
)
from skatai.runtime.interface import (
    BiddingObservation,
    CardplayObservation,
    DeclarationObservation,
    DiscardObservation,
    SkatAI,
    SkatAIInterfaceError,
)
from skatai.runtime.release_loader import load_model

REQUEST_SCHEMA = "skatai.v2.host-request.v1"
RESPONSE_SCHEMA = "skatai.v2.host-response.v1"
PICKUP_PLAN_REQUEST_SCHEMA = "skatai.v2.host-pickup-plan-request.v1"
PICKUP_PLAN_RESPONSE_SCHEMA = "skatai.v2.host-pickup-plan-response.v1"
PICKUP_CONTRACTS = frozenset({"C", "S", "H", "D", "G", "N", "NO"})
MAX_REQUEST_BYTES = 1024 * 1024


def handle_request(ai: SkatAI, release_id: str, payload: Mapping[str, Any]) -> dict:
    if payload.get("schema") != REQUEST_SCHEMA:
        raise SkatAIInterfaceError("HOST_REQUEST_SCHEMA_MISMATCH")
    dtype = DecisionType(payload["decision_type"])
    data = payload["observation"]
    if not isinstance(data, dict):
        raise SkatAIInterfaceError("HOST_OBSERVATION_NOT_OBJECT")
    constructors = {
        DecisionType.BID: (BiddingObservation, "hand"),
        DecisionType.DECLARATION: (DeclarationObservation, "cards"),
        DecisionType.DISCARD: (DiscardObservation, "hand12"),
        DecisionType.PLAY_CARD: (CardplayObservation, "hand"),
    }
    cls, cards_key = constructors[dtype]
    fields = dict(data)
    cards = fields.pop(cards_key)
    observation = cls.create(cards, **fields)
    request = DecisionRequest.create(
        game_id=payload["game_id"],
        sequence_no=payload["sequence_no"],
        decision_type=dtype,
        observation=observation,
        source=str(payload.get("source") or "HOST"),
        source_context=payload.get("source_context"),
        legal_actions=payload.get("legal_actions"),
    )
    result = decide(ai, request, release_id=release_id)
    return {"schema": RESPONSE_SCHEMA, "ok": True, "result": asdict(result)}


def handle_pickup_plan(ai: SkatAI, release_id: str, payload: Mapping[str, Any]) -> dict:
    """Commit both pickup choices while the host still has the same 12 own cards.

    The host must retain this result until its later announceGame callback and
    verify the actual post-discard hand before returning the contract.
    """
    if payload.get("schema") != PICKUP_PLAN_REQUEST_SCHEMA:
        raise SkatAIInterfaceError("PICKUP_PLAN_SCHEMA_MISMATCH")
    allowed_fields = {
        "schema", "game_id", "sequence_no", "hand12", "seat",
        "winning_bid", "max_accepted_bids_by_seat", "legal_contracts",
    }
    if set(payload) - allowed_fields:
        raise SkatAIInterfaceError("PICKUP_PLAN_UNKNOWN_FIELD")
    hand12 = payload["hand12"]
    common = {
        "seat": payload["seat"],
        "winning_bid": payload["winning_bid"],
        "max_accepted_bids_by_seat": payload["max_accepted_bids_by_seat"],
    }
    declaration = DeclarationObservation.create(
        hand12,
        picked_up_skat=True,
        legal_contracts=payload["legal_contracts"],
        **common,
    )
    discard = DiscardObservation.create(hand12, **common)
    if declaration.winning_bid < 18:
        raise SkatAIInterfaceError("PICKUP_PLAN_BAD_WINNING_BID")
    if (len(set(declaration.legal_contracts)) != len(declaration.legal_contracts)
            or not set(declaration.legal_contracts).issubset(PICKUP_CONTRACTS)):
        raise SkatAIInterfaceError("BAD_PICKUP_CONTRACT_SET")
    if (("N" in declaration.legal_contracts and declaration.winning_bid > 23)
            or ("NO" in declaration.legal_contracts and declaration.winning_bid > 46)):
        raise SkatAIInterfaceError("PICKUP_NULL_BELOW_WINNING_BID")
    game_id = payload["game_id"]
    sequence_no = payload["sequence_no"]
    contract_request = DecisionRequest.create(
        game_id=game_id, sequence_no=sequence_no,
        decision_type=DecisionType.DECLARATION, observation=declaration,
        source="HOST_PICKUP_PLAN",
    )
    discard_request = DecisionRequest.create(
        game_id=game_id, sequence_no=int(sequence_no) + 1,
        decision_type=DecisionType.DISCARD, observation=discard,
        source="HOST_PICKUP_PLAN",
    )
    contract_result = decide(ai, contract_request, release_id=release_id)
    discard_result = decide(ai, discard_request, release_id=release_id)
    buried = tuple(discard_result.action.split("."))
    final_hand = tuple(card for card in declaration.cards if card not in buried)
    if len(buried) != 2 or len(final_hand) != 10:
        raise SkatAIInterfaceError("PICKUP_PLAN_BAD_DISCARD")
    body = {
        "schema": PICKUP_PLAN_RESPONSE_SCHEMA,
        "ok": True,
        "release_id": release_id,
        "game_id": contract_request.game_id,
        "sequence_no": contract_request.sequence_no,
        "hand12_sha256": sha256_json(declaration.cards),
        "discard": list(buried),
        "contract": contract_result.action,
        "final_hand": list(final_hand),
        "decisions": [asdict(contract_result), asdict(discard_result)],
    }
    identity = {
        "release_id": release_id,
        "game_id": contract_request.game_id,
        "sequence_no": contract_request.sequence_no,
        "hand12_sha256": body["hand12_sha256"],
        "contract_decision_id": contract_result.decision_id,
        "discard_decision_id": discard_result.decision_id,
    }
    return {**body, "plan_id": sha256_json(identity)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--materialize-to", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    args = parser.parse_args()
    manifest = validate_release_package(args.package)["manifest"]
    ai = load_model(
        args.package,
        materialize_to=args.materialize_to,
        python_executable=args.python,
    )
    release_id = str(manifest["release_id"])

    while raw := sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1):
        if len(raw) > MAX_REQUEST_BYTES:
            while raw and not raw.endswith(b"\n"):
                raw = sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1)
            response = {"schema": RESPONSE_SCHEMA, "ok": False, "error_code": "REQUEST_TOO_LARGE"}
        else:
            payload = None
            try:
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise SkatAIInterfaceError("HOST_REQUEST_NOT_OBJECT")
                response = (
                    handle_pickup_plan(ai, release_id, payload)
                    if payload.get("schema") == PICKUP_PLAN_REQUEST_SCHEMA
                    else handle_request(ai, release_id, payload)
                )
            except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
                response = {
                    "schema": (PICKUP_PLAN_RESPONSE_SCHEMA
                               if isinstance(payload, dict) and payload.get("schema") == PICKUP_PLAN_REQUEST_SCHEMA
                               else RESPONSE_SCHEMA),
                    "ok": False,
                    "error_code": type(exc).__name__,
                }
        sys.stdout.write(canonical_json(response) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
