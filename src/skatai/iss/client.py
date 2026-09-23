from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from typing import Protocol

from skatai.iss.service import (
    ServiceEvent,
    command_join,
    command_ready,
    parse_service_line,
)
from skatai.iss.session import ISSSessionState
from skatai.iss.transport import ISSConnectionConfig, ISSLineTransport
from skatai.iss.effects import ISSAuthorityGuard, table_state_hash

CLIENT_RUNTIME_SCHEMA = "skatai.v2.iss-client-runtime.v1"


class TableMoveProvider(Protocol):
    def next_action(self, table) -> str | None: ...


class TableDecisionProvider(Protocol):
    def next_decision(self, table): ...


class LineTransport(Protocol):
    authenticated_client_id: str | None

    def connect(self) -> None: ...
    def login(self, password: str) -> str: ...
    def read_line(self) -> str: ...
    def send_line(self, line: str) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class ISSClientPolicy:
    accept_invitations: bool = True
    ready_when_joined: bool = True
    ready_after_game: bool = True


class ISSJournal:
    """Append-only service traffic journal.

    Authentication is intentionally outside this journal. The password never
    reaches this object. Each record is flushed immediately so connection loss
    does not erase the last observed protocol line.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _redact_service_secret(direction: str, line: str) -> str:
        parts = str(line).split()
        if direction == "in" and len(parts) >= 4 and parts[0] == "invite":
            return " ".join(parts[:3] + ["<redacted-table-password>"])
        if direction == "out" and len(parts) >= 3 and parts[0] == "join":
            return " ".join(parts[:2] + ["<redacted-table-password>"])
        if (
            direction == "out"
            and len(parts) >= 5
            and parts[0] == "create"
            and parts[1] == "/"
        ):
            return " ".join(parts[:4] + ["<redacted-table-password>"])
        return str(line)

    def write(self, direction: str, line: str) -> None:
        if direction not in {"in", "out"}:
            raise ValueError("BAD_JOURNAL_DIRECTION")
        payload = {
            "schema": CLIENT_RUNTIME_SCHEMA,
            "unix_ns": time.time_ns(),
            "direction": direction,
            "line": self._redact_service_secret(direction, line),
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())


class ISSClientCore:
    def __init__(
        self,
        transport: LineTransport,
        *,
        policy: ISSClientPolicy = ISSClientPolicy(),
        journal: ISSJournal | None = None,
        move_provider: TableMoveProvider | TableDecisionProvider | None = None,
        effect_guard: ISSAuthorityGuard | None = None,
    ) -> None:
        self.transport = transport
        self.policy = policy
        self.journal = journal
        self.move_provider = move_provider
        self.effect_guard = effect_guard
        self.state = ISSSessionState()
        self._sent_decision_keys: set[tuple[str, int, int, str]] = set()

    def _send(self, line: str) -> None:
        self.transport.send_line(line)
        if self.journal is not None:
            self.journal.write("out", line)

    def _maybe_send_move(self, table) -> None:
        if self.move_provider is None or not table.is_player or not table.in_progress:
            return

        from skatai.iss.service import command_play

        decision_method = getattr(self.move_provider, "next_decision", None)
        if callable(decision_method):
            decision = decision_method(table)
            if decision is None:
                return
            outbound = command_play(
                table.table_id,
                table.viewer_name,
                str(decision.wire_action),
            )
            if self.effect_guard is not None:
                state_hash = table_state_hash(table)
                effect, created = self.effect_guard.prepare(
                    decision.request,
                    decision.result,
                    current_external_state_hash=state_hash,
                    table_id=table.table_id,
                    game_sequence=table.game_sequence,
                    protocol_sequence=len(table.moves),
                    wire_action=str(decision.wire_action),
                    outbound_line=outbound,
                )
                if not created:
                    # An unresolved durable intent from this exact position exists.
                    # Never replay it merely because process/session code ran again.
                    return
                self.effect_guard.send(
                    effect,
                    created_now=True,
                    send_line=self._send,
                    outbound_line=outbound,
                )
                return

            key = (
                table.table_id,
                table.game_sequence,
                len(table.moves),
                str(decision.wire_action),
            )
            if key in self._sent_decision_keys:
                return
            self._send(outbound)
            self._sent_decision_keys.add(key)
            return

        action = self.move_provider.next_action(table)
        if action is None:
            return
        key = (table.table_id, table.game_sequence, len(table.moves), str(action))
        if key in self._sent_decision_keys:
            return
        self._send(command_play(table.table_id, table.viewer_name, str(action)))
        self._sent_decision_keys.add(key)

    def connect_and_login(self, password: str) -> str:
        self.transport.connect()
        client_id = self.transport.login(password)
        self.state.set_connected(client_id)
        return client_id

    def handle_line(self, line: str) -> ServiceEvent:
        if self.journal is not None:
            self.journal.write("in", line)
        event = parse_service_line(line)
        table = self.state.apply(event)

        if (
            self.effect_guard is not None
            and event.kind in {"table_start", "table_play", "table_state", "table_go"}
            and table is not None
        ):
            self.effect_guard.journal.reconcile_table(table)

        if event.kind == "invite" and self.policy.accept_invitations:
            self._send(
                command_join(
                    str(event.fields["table_id"]),
                    str(event.fields["table_password"]),
                )
            )
        elif (
            event.kind == "create"
            and table is not None
            and table.is_player
            and self.policy.ready_when_joined
        ):
            self._send(command_ready(table.table_id, table.viewer_name))
        elif (
            event.kind == "table_end"
            and table is not None
            and table.is_player
            and self.policy.ready_after_game
        ):
            self._send(command_ready(table.table_id, table.viewer_name))

        if event.kind in {"table_start", "table_play", "table_state", "table_go"} and table is not None:
            self._maybe_send_move(table)
        return event

    def step(self) -> ServiceEvent:
        return self.handle_line(self.transport.read_line())

    def run(self) -> None:
        while True:
            self.step()

    def close(self) -> None:
        self.state.set_disconnected()
        self.transport.close()


def client_from_environment(
    *,
    journal_path: Path | None = None,
    policy: ISSClientPolicy = ISSClientPolicy(),
) -> tuple[ISSClientCore, str]:
    """Build a client from environment without retaining the password.

    Required:
      ISS_HOST
      ISS_CLIENT_ID
      one of ISS_PASSWORD or ISS_PASSWORD_FILE
    Optional:
      ISS_PORT (default 7000)

    ISS_PASSWORD_FILE is intended for mounted/RunPod secrets and must not be
    group- or world-readable.
    """
    host = os.environ.get("ISS_HOST", "")
    client_id = os.environ.get("ISS_CLIENT_ID", "")
    password = os.environ.get("ISS_PASSWORD", "")
    password_file = os.environ.get("ISS_PASSWORD_FILE", "")
    if not password and password_file:
        secret_path = Path(password_file)
        st = secret_path.stat()
        if st.st_mode & 0o077:
            raise ValueError("ISS_PASSWORD_FILE_PERMISSIONS_TOO_OPEN")
        if st.st_size > 4096:
            raise ValueError("ISS_PASSWORD_FILE_TOO_LARGE")
        password = secret_path.read_text(encoding="utf-8").rstrip("\r\n")
    try:
        port = int(os.environ.get("ISS_PORT", "7000"))
    except ValueError as exc:
        raise ValueError("BAD_ISS_PORT_ENV") from exc

    if not host:
        raise ValueError("MISSING_ISS_HOST")
    if not client_id:
        raise ValueError("MISSING_ISS_CLIENT_ID")
    if not password:
        raise ValueError("MISSING_ISS_PASSWORD")

    transport = ISSLineTransport(
        ISSConnectionConfig(host=host, port=port, client_id=client_id)
    )
    client = ISSClientCore(
        transport,
        policy=policy,
        journal=None if journal_path is None else ISSJournal(journal_path),
    )
    return client, password
