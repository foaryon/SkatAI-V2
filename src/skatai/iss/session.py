from __future__ import annotations

from dataclasses import dataclass, field

from skatai.iss.protocol import ISSProtocolError, WireMove, parse_move_line
from skatai.iss.service import ISSServiceError, ServiceEvent, parse_table_start_payload

SESSION_SCHEMA = "skatai.v2.iss-session.v1"


class ISSSessionError(ValueError):
    pass


@dataclass
class TableSession:
    table_id: str
    viewer_name: str
    table_type: str
    is_player: bool
    in_progress: bool = False
    stopped: bool = False
    start_payload: str | None = None
    state_payload: str | None = None
    game_sgf: str | None = None
    moves: list[WireMove] = field(default_factory=list)
    tells: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    game_sequence: int = 0
    server_game_num: int | None = None
    players: tuple[str, str, str] | None = None
    remaining_time_s: tuple[float, float, float] | None = None

    def apply(self, event: ServiceEvent) -> None:
        table_id = event.fields.get("table_id")
        if table_id is not None and str(table_id) != self.table_id:
            raise ISSSessionError(
                f"TABLE_ID_MISMATCH:{table_id}!={self.table_id}"
            )

        if event.kind == "table_start":
            self.start_payload = str(event.fields.get("payload") or "")
            try:
                start = parse_table_start_payload(self.start_payload)
            except ISSServiceError:
                # Synthetic/older fixtures may not carry the documented start
                # metadata. Keep a local monotonic fallback, but live ISS start
                # payloads bind effect identity to the server game number.
                self.game_sequence += 1
                self.server_game_num = None
                self.players = None
                self.remaining_time_s = None
            else:
                self.server_game_num = int(start["game_num"])
                self.game_sequence = self.server_game_num
                self.players = tuple(start["players"])
                self.remaining_time_s = tuple(start["remaining_time_s"])
            self.in_progress = True
            self.stopped = False
            self.game_sgf = None
            self.moves.clear()
            return

        if event.kind == "table_play":
            actor = str(event.fields["move_actor"])
            move = str(event.fields["move"])
            try:
                parsed = parse_move_line(f"{actor} {move}")
            except ISSProtocolError as exc:
                raise ISSSessionError(f"INVALID_TABLE_MOVE:{exc}") from exc
            self.moves.append(parsed)
            return

        if event.kind == "table_state":
            self.state_payload = str(event.fields.get("payload") or "")
            return

        if event.kind == "table_end":
            self.game_sgf = str(event.fields.get("game_sgf") or "")
            self.in_progress = False
            return

        if event.kind == "table_stop":
            self.stopped = True
            self.in_progress = False
            return

        if event.kind == "table_tell":
            self.tells.append(
                (
                    str(event.fields.get("from_id") or ""),
                    str(event.fields.get("text") or ""),
                )
            )
            return

        if event.kind == "table_error":
            self.errors.append(str(event.fields.get("text") or ""))
            return

        if event.kind in {"table_go", "table_unknown"}:
            return

        raise ISSSessionError(f"EVENT_NOT_APPLICABLE_TO_TABLE:{event.kind}")

    @property
    def last_move(self) -> WireMove | None:
        return self.moves[-1] if self.moves else None


@dataclass
class ISSSessionState:
    client_id: str | None = None
    connected: bool = False
    tables: dict[str, TableSession] = field(default_factory=dict)
    invitations: list[dict[str, str]] = field(default_factory=list)
    service_errors: list[str] = field(default_factory=list)

    def set_connected(self, client_id: str) -> None:
        if not client_id:
            raise ISSSessionError("EMPTY_CLIENT_ID")
        self.client_id = client_id
        self.connected = True

    def set_disconnected(self) -> None:
        self.connected = False
        for table in self.tables.values():
            table.in_progress = False

    def apply(self, event: ServiceEvent) -> TableSession | None:
        if event.kind == "create":
            table_id = str(event.fields["table_id"])
            if table_id in self.tables:
                raise ISSSessionError(f"DUPLICATE_TABLE_CREATE:{table_id}")
            table = TableSession(
                table_id=table_id,
                viewer_name=str(event.fields["viewer_name"]),
                table_type=str(event.fields["table_type"]),
                is_player=bool(event.fields["is_player"]),
            )
            self.tables[table_id] = table
            return table

        if event.kind == "destroy":
            table_id = str(event.fields["table_id"])
            table = self.tables.pop(table_id, None)
            if table is None:
                raise ISSSessionError(f"DESTROY_UNKNOWN_TABLE:{table_id}")
            return table

        if event.kind == "invite":
            self.invitations.append(
                {
                    "from_id": str(event.fields["from_id"]),
                    "table_id": str(event.fields["table_id"]),
                    "table_password": str(event.fields["table_password"]),
                }
            )
            return None

        if event.kind == "error":
            self.service_errors.append(str(event.fields.get("text") or ""))
            return None

        if event.kind.startswith("table_"):
            table_id = str(event.fields.get("table_id") or "")
            table = self.tables.get(table_id)
            if table is None:
                raise ISSSessionError(f"EVENT_FOR_UNKNOWN_TABLE:{table_id}")
            table.apply(event)
            return table

        # Directory/listing events are intentionally outside the per-game state.
        if event.kind in {
            "client_update",
            "client_remove",
            "table_update",
            "table_remove",
            "unknown",
        }:
            return None

        raise ISSSessionError(f"UNSUPPORTED_SERVICE_EVENT:{event.kind}")
