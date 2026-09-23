from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SERVICE_SCHEMA = "skatai.v2.iss-service-lines.v1"


class ISSServiceError(ValueError):
    pass


@dataclass(frozen=True)
class ServiceEvent:
    kind: str
    raw: str
    fields: dict[str, Any]


def _need(parts: list[str], n: int, kind: str) -> None:
    if len(parts) < n:
        raise ISSServiceError(f"{kind}:EXPECTED_AT_LEAST_{n}_FIELDS:{len(parts)}")


def parse_service_line(line: str) -> ServiceEvent:
    raw = line.rstrip("\r\n")
    if not raw:
        raise ISSServiceError("EMPTY_SERVICE_LINE")
    parts = raw.split()

    if parts[0] == "create":
        _need(parts, 4, "CREATE")
        return ServiceEvent(
            "create",
            raw,
            {
                "table_id": parts[1],
                "viewer_name": parts[2],
                "is_player": parts[2] != ".",
                "table_type": parts[3],
                "tail": parts[4:],
            },
        )

    if parts[0] == "destroy":
        _need(parts, 3, "DESTROY")
        return ServiceEvent(
            "destroy",
            raw,
            {
                "table_id": parts[1],
                "viewer_name": parts[2],
            },
        )

    if parts[0] == "invite":
        _need(parts, 4, "INVITE")
        return ServiceEvent(
            "invite",
            raw,
            {
                "from_id": parts[1],
                "table_id": parts[2],
                "table_password": parts[3],
            },
        )

    if parts[0] == "error":
        return ServiceEvent(
            "error",
            raw,
            {"text": raw[len("error"):].lstrip()},
        )

    if parts[:2] == ["clients", "+"]:
        if len(parts) != 11:
            raise ISSServiceError(f"CLIENT_UPDATE_FIELD_COUNT:{len(parts)}")
        try:
            fields = {
                "client_id": parts[2],
                "permission": int(parts[3]),
                "languages": parts[4],
                "games": int(parts[5]),
                "rating": float(parts[6]),
                "disconnects": int(parts[7]),
                "timeouts": int(parts[8]),
                "group": int(parts[9]),
                "clone_num": int(parts[10]),
            }
        except ValueError as exc:
            raise ISSServiceError("CLIENT_UPDATE_BAD_NUMBER") from exc
        return ServiceEvent("client_update", raw, fields)

    if parts[:2] == ["clients", "-"]:
        if len(parts) != 3:
            raise ISSServiceError(f"CLIENT_REMOVE_FIELD_COUNT:{len(parts)}")
        return ServiceEvent("client_remove", raw, {"client_id": parts[2]})

    if parts[:2] == ["tables", "+"]:
        _need(parts, 9, "TABLE_UPDATE")
        try:
            player_num = int(parts[3])
            game_num = int(parts[4])
        except ValueError as exc:
            raise ISSServiceError("TABLE_UPDATE_BAD_NUMBER") from exc
        if player_num < 0 or len(parts) < 5 + player_num:
            raise ISSServiceError("TABLE_UPDATE_PLAYER_COUNT_MISMATCH")
        return ServiceEvent(
            "table_update",
            raw,
            {
                "table_id": parts[2],
                "player_num": player_num,
                "game_num": game_num,
                "players": parts[5:5 + player_num],
                "tail": parts[5 + player_num:],
            },
        )

    if parts[:2] == ["tables", "-"]:
        if len(parts) != 3:
            raise ISSServiceError(f"TABLE_REMOVE_FIELD_COUNT:{len(parts)}")
        return ServiceEvent("table_remove", raw, {"table_id": parts[2]})

    if parts[0] == "table":
        _need(parts, 3, "TABLE")
        table_id = parts[1]

        # stop is the only documented table event without viewer/player slot.
        if len(parts) >= 3 and parts[2] == "stop":
            return ServiceEvent("table_stop", raw, {"table_id": table_id})

        _need(parts, 4, "TABLE_EVENT")
        viewer = parts[2]
        verb = parts[3]
        tail_text = raw.split(None, 4)[4] if len(parts) >= 5 else ""

        common = {"table_id": table_id, "viewer_name": viewer}

        if verb == "play":
            _need(parts, 6, "TABLE_PLAY")
            return ServiceEvent(
                "table_play",
                raw,
                {
                    **common,
                    "move_actor": parts[4],
                    "move": parts[5],
                    "tail": parts[6:],
                },
            )
        if verb == "start":
            return ServiceEvent(
                "table_start",
                raw,
                {**common, "payload": tail_text},
            )
        if verb == "end":
            return ServiceEvent(
                "table_end",
                raw,
                {**common, "game_sgf": tail_text},
            )
        if verb == "state":
            return ServiceEvent(
                "table_state",
                raw,
                {**common, "payload": tail_text},
            )
        if verb == "go":
            return ServiceEvent("table_go", raw, common)
        if verb == "tell":
            _need(parts, 5, "TABLE_TELL")
            prefix = " ".join(parts[:5])
            text = raw[len(prefix):].lstrip()
            return ServiceEvent(
                "table_tell",
                raw,
                {**common, "from_id": parts[4], "text": text},
            )
        if verb == "error":
            prefix = " ".join(parts[:4])
            text = raw[len(prefix):].lstrip()
            return ServiceEvent(
                "table_error",
                raw,
                {**common, "text": text},
            )

        return ServiceEvent(
            "table_unknown",
            raw,
            {**common, "verb": verb, "payload": tail_text},
        )

    return ServiceEvent("unknown", raw, {"parts": parts})


def command_ready(table_id: str, viewer_name: str) -> str:
    return f"table {table_id} {viewer_name} ready"


def command_play(table_id: str, viewer_name: str, move: str) -> str:
    if not move or any(ch in move for ch in "\r\n"):
        raise ISSServiceError("BAD_OUTBOUND_MOVE")
    return f"table {table_id} {viewer_name} play {move}"


def command_join(table_id: str, table_password: str) -> str:
    if not table_id or not table_password:
        raise ISSServiceError("JOIN_REQUIRES_TABLE_AND_PASSWORD")
    if any(ch in table_id + table_password for ch in "\r\n "):
        raise ISSServiceError("BAD_JOIN_TOKEN")
    return f"join {table_id} {table_password}"


def command_leave(table_id: str, viewer_name: str) -> str:
    return f"table {table_id} {viewer_name} leave"


def command_keepalive() -> str:
    return "time"


@dataclass(frozen=True)
class LoginHandshake:
    requested_client_id: str

    def client_id_line(self) -> str:
        if not self.requested_client_id or any(
            ch.isspace() for ch in self.requested_client_id
        ):
            raise ISSServiceError("BAD_CLIENT_ID")
        return self.requested_client_id

    @staticmethod
    def accept_password_prompt(line: str) -> None:
        if line.rstrip("\r\n") != "password:":
            raise ISSServiceError("LOGIN_EXPECTED_PASSWORD_PROMPT")

    @staticmethod
    def accept_welcome(line: str) -> str:
        stripped = line.rstrip("\r\n")
        if not stripped.startswith("Welcome "):
            raise ISSServiceError("LOGIN_EXPECTED_WELCOME")
        parts = stripped.split()
        if len(parts) < 2:
            raise ISSServiceError("LOGIN_WELCOME_MISSING_CLIENT_ID")
        return parts[1]
