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
            try:
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise SkatAIInterfaceError("HOST_REQUEST_NOT_OBJECT")
                response = handle_request(ai, release_id, payload)
            except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
                response = {
                    "schema": RESPONSE_SCHEMA,
                    "ok": False,
                    "error_code": type(exc).__name__,
                }
        sys.stdout.write(canonical_json(response) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
