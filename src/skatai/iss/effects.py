from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping

from skatai.runtime.decision import DecisionRequest, DecisionResult, canonical_json

EFFECT_SCHEMA = "skatai.v2.iss-effect-journal.v1"
EVENTS = frozenset(
    {
        "INTENT",
        "SEND_RETURNED",
        "CONFIRMED",
        "RETRY_AUTHORIZED",
        "ABORTED_STALE",
    }
)
TERMINAL = frozenset({"CONFIRMED", "ABORTED_STALE"})


class ISSEffectError(RuntimeError):
    pass


@dataclass(frozen=True)
class EffectState:
    effect_id: str
    request_id: str
    decision_id: str
    position_hash: str
    external_state_hash: str
    table_id: str
    game_sequence: int
    protocol_sequence: int
    wire_action: str
    outbound_line_sha256: str
    release_id: str
    status: str
    attempts: int

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL


def table_state_hash(table: Any) -> str:
    payload = {
        "table_id": str(table.table_id),
        "game_sequence": int(table.game_sequence),
        "in_progress": bool(table.in_progress),
        "stopped": bool(table.stopped),
        "start_payload": table.start_payload,
        "state_payload": table.state_payload,
        "moves": [
            {
                "actor": str(m.actor),
                "action": str(m.action),
                "kind": str(m.kind),
            }
            for m in table.moves
        ],
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class ISSEffectJournal:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def _locked(self):
        fd = os.open(self.lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
        return fd

    @staticmethod
    def _hash_event(raw: Mapping[str, Any]) -> str:
        body = {k: v for k, v in raw.items() if k != "event_hash"}
        return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()

    def events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        prev_hash: str | None = None
        with self.path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                if not line.strip():
                    continue
                raw = json.loads(line)
                if raw.get("schema") != EFFECT_SCHEMA:
                    raise ISSEffectError(f"SCHEMA_MISMATCH_LINE:{line_no}")
                if int(raw.get("journal_seq", -1)) != len(out):
                    raise ISSEffectError(f"SEQUENCE_MISMATCH_LINE:{line_no}")
                if raw.get("previous_event_hash") != prev_hash:
                    raise ISSEffectError(f"HASH_CHAIN_PREDECESSOR_MISMATCH_LINE:{line_no}")
                expected = self._hash_event(raw)
                if raw.get("event_hash") != expected:
                    raise ISSEffectError(f"EVENT_HASH_MISMATCH_LINE:{line_no}")
                if raw.get("event") not in EVENTS:
                    raise ISSEffectError(f"UNKNOWN_EVENT_LINE:{line_no}")
                out.append(raw)
                prev_hash = expected
        return out

    def states(self) -> dict[str, EffectState]:
        states: dict[str, dict[str, Any]] = {}
        for e in self.events():
            effect_id = str(e["effect_id"])
            event = str(e["event"])
            if event == "INTENT":
                if effect_id in states:
                    raise ISSEffectError(f"DUPLICATE_INTENT:{effect_id}")
                states[effect_id] = {
                    "effect_id": effect_id,
                    "request_id": str(e["request_id"]),
                    "decision_id": str(e["decision_id"]),
                    "position_hash": str(e["position_hash"]),
                    "external_state_hash": str(e["external_state_hash"]),
                    "table_id": str(e["table_id"]),
                    "game_sequence": int(e["game_sequence"]),
                    "protocol_sequence": int(e["protocol_sequence"]),
                    "wire_action": str(e["wire_action"]),
                    "outbound_line_sha256": str(e["outbound_line_sha256"]),
                    "release_id": str(e["release_id"]),
                    "status": "INTENT",
                    "attempts": 0,
                }
                continue
            if effect_id not in states:
                raise ISSEffectError(f"EVENT_WITHOUT_INTENT:{effect_id}:{event}")
            state = states[effect_id]
            if state["status"] in TERMINAL:
                raise ISSEffectError(f"EVENT_AFTER_TERMINAL:{effect_id}:{event}")
            if event == "SEND_RETURNED":
                state["status"] = "SEND_RETURNED"
                state["attempts"] += 1
            elif event == "RETRY_AUTHORIZED":
                state["status"] = "RETRY_AUTHORIZED"
            elif event == "CONFIRMED":
                state["status"] = "CONFIRMED"
            elif event == "ABORTED_STALE":
                state["status"] = "ABORTED_STALE"

        return {k: EffectState(**v) for k, v in states.items()}

    def _append(self, event: str, effect_id: str, **fields: Any) -> dict[str, Any]:
        if event not in EVENTS:
            raise ISSEffectError(f"BAD_EVENT:{event}")
        lock_fd = self._locked()
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            existing = self.events()
            previous = existing[-1]["event_hash"] if existing else None
            raw = {
                "schema": EFFECT_SCHEMA,
                "journal_seq": len(existing),
                "unix_ns": time.time_ns(),
                "previous_event_hash": previous,
                "event": event,
                "effect_id": effect_id,
                **fields,
            }
            raw["event_hash"] = self._hash_event(raw)
            encoded = json.dumps(
                raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ) + "\n"
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, encoded.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            return raw
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def begin(
        self,
        request: DecisionRequest,
        result: DecisionResult,
        *,
        external_state_hash: str,
        table_id: str,
        game_sequence: int,
        protocol_sequence: int,
        wire_action: str,
        outbound_line: str,
    ) -> tuple[EffectState, bool]:
        if result.request_id != request.request_id:
            raise ISSEffectError("RESULT_REQUEST_ID_MISMATCH")
        if result.position_hash != request.position_hash:
            raise ISSEffectError("RESULT_POSITION_HASH_MISMATCH")
        if not external_state_hash:
            raise ISSEffectError("EMPTY_EXTERNAL_STATE_HASH")
        if not wire_action or not outbound_line:
            raise ISSEffectError("EMPTY_WIRE_ACTION_OR_LINE")

        outbound_sha = hashlib.sha256(outbound_line.encode("utf-8")).hexdigest()
        effect_id = hashlib.sha256(
            (
                "skatai.v2.iss.effect\0"
                + str(table_id)
                + "\0"
                + str(int(game_sequence))
                + "\0"
                + request.position_hash
                + "\0"
                + result.decision_id
                + "\0"
                + wire_action
                + "\0"
                + outbound_sha
            ).encode("utf-8")
        ).hexdigest()

        states = self.states()
        if effect_id in states:
            return states[effect_id], False

        for state in states.values():
            if (
                not state.terminal
                and state.table_id == str(table_id)
                and state.game_sequence == int(game_sequence)
                and state.position_hash == request.position_hash
                and state.effect_id != effect_id
            ):
                raise ISSEffectError(
                    f"CONFLICTING_PENDING_EFFECT_FOR_POSITION:{state.effect_id}"
                )

        self._append(
            "INTENT",
            effect_id,
            request_id=request.request_id,
            decision_id=result.decision_id,
            position_hash=request.position_hash,
            external_state_hash=external_state_hash,
            table_id=str(table_id),
            game_sequence=int(game_sequence),
            protocol_sequence=int(protocol_sequence),
            wire_action=str(wire_action),
            outbound_line_sha256=outbound_sha,
            release_id=result.release_id,
        )
        return self.states()[effect_id], True

    def _state(self, effect_id: str) -> EffectState:
        try:
            return self.states()[effect_id]
        except KeyError as exc:
            raise ISSEffectError(f"UNKNOWN_EFFECT:{effect_id}") from exc

    def mark_send_returned(self, effect_id: str) -> EffectState:
        state = self._state(effect_id)
        if state.status not in {"INTENT", "RETRY_AUTHORIZED"}:
            raise ISSEffectError(f"SEND_NOT_AUTHORIZED_FROM:{state.status}")
        self._append("SEND_RETURNED", effect_id)
        return self._state(effect_id)

    def authorize_retry(
        self,
        effect_id: str,
        *,
        current_external_state_hash: str,
        same_request_still_pending: bool,
        reason: str,
    ) -> EffectState:
        state = self._state(effect_id)
        if state.terminal:
            raise ISSEffectError(f"RETRY_AFTER_TERMINAL:{state.status}")
        if current_external_state_hash != state.external_state_hash:
            raise ISSEffectError("RETRY_STATE_HASH_CHANGED")
        if not same_request_still_pending:
            raise ISSEffectError("RETRY_REQUEST_NOT_PENDING")
        self._append("RETRY_AUTHORIZED", effect_id, reason=str(reason))
        return self._state(effect_id)

    def confirm(self, effect_id: str, *, reason: str) -> EffectState:
        state = self._state(effect_id)
        if state.terminal:
            if state.status == "CONFIRMED":
                return state
            raise ISSEffectError(f"CONFIRM_AFTER_TERMINAL:{state.status}")
        self._append("CONFIRMED", effect_id, reason=str(reason))
        return self._state(effect_id)

    def abort_stale(self, effect_id: str, *, reason: str) -> EffectState:
        state = self._state(effect_id)
        if state.terminal:
            return state
        self._append("ABORTED_STALE", effect_id, reason=str(reason))
        return self._state(effect_id)

    def pending(self) -> list[EffectState]:
        return [s for s in self.states().values() if not s.terminal]

    def pending_for_game(self, table_id: str, game_sequence: int) -> list[EffectState]:
        return [
            s
            for s in self.pending()
            if s.table_id == str(table_id) and s.game_sequence == int(game_sequence)
        ]

    def confirm_observed_action(
        self,
        *,
        table_id: str,
        game_sequence: int,
        wire_action: str,
    ) -> EffectState | None:
        matches = [
            s
            for s in self.pending_for_game(table_id, game_sequence)
            if s.wire_action == str(wire_action)
        ]
        if not matches:
            return None
        if len(matches) > 1:
            raise ISSEffectError("AMBIGUOUS_PENDING_EFFECT_FOR_OBSERVED_ACTION")
        return self.confirm(matches[0].effect_id, reason="server_echo_observed")


class ISSAuthorityGuard:
    def __init__(self, journal: ISSEffectJournal) -> None:
        self.journal = journal

    def prepare(
        self,
        request: DecisionRequest,
        result: DecisionResult,
        *,
        current_external_state_hash: str,
        table_id: str,
        game_sequence: int,
        protocol_sequence: int,
        wire_action: str,
        outbound_line: str,
    ) -> tuple[EffectState, bool]:
        if request.position_hash != result.position_hash:
            raise ISSEffectError("STALE_OR_MISMATCHED_RESULT")
        return self.journal.begin(
            request,
            result,
            external_state_hash=current_external_state_hash,
            table_id=table_id,
            game_sequence=game_sequence,
            protocol_sequence=protocol_sequence,
            wire_action=wire_action,
            outbound_line=outbound_line,
        )

    def send(
        self,
        effect: EffectState,
        *,
        created_now: bool,
        send_line: Callable[[str], None],
        outbound_line: str,
    ) -> EffectState:
        current = self.journal.states()[effect.effect_id]
        if created_now:
            if current.status != "INTENT":
                raise ISSEffectError(f"NEW_EFFECT_BAD_STATE:{current.status}")
        elif current.status != "RETRY_AUTHORIZED":
            raise ISSEffectError(
                f"UNRESOLVED_EFFECT_REQUIRES_RECONCILIATION:{current.status}"
            )

        actual_sha = hashlib.sha256(outbound_line.encode("utf-8")).hexdigest()
        if actual_sha != current.outbound_line_sha256:
            raise ISSEffectError("OUTBOUND_LINE_HASH_MISMATCH")
        send_line(outbound_line)
        return self.journal.mark_send_returned(effect.effect_id)
