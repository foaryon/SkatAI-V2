from __future__ import annotations

from dataclasses import dataclass
import socket
from typing import Callable, Iterator, TextIO

from skatai.iss.service import ISSServiceError, LoginHandshake

TRANSPORT_SCHEMA = "skatai.v2.iss-transport.v1"


class ISSTransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class ISSConnectionConfig:
    host: str
    port: int = 80
    client_id: str = ""
    connect_timeout_s: float = 15.0
    read_timeout_s: float | None = None

    def validate(self) -> None:
        if not self.host or any(ch.isspace() for ch in self.host):
            raise ValueError("BAD_ISS_HOST")
        if not 1 <= self.port <= 65535:
            raise ValueError("BAD_ISS_PORT")
        LoginHandshake(self.client_id).client_id_line()
        if self.connect_timeout_s <= 0:
            raise ValueError("BAD_CONNECT_TIMEOUT")
        if self.read_timeout_s is not None and self.read_timeout_s <= 0:
            raise ValueError("BAD_READ_TIMEOUT")


SocketFactory = Callable[[tuple[str, int], float], socket.socket]


def _default_socket_factory(address: tuple[str, int], timeout: float) -> socket.socket:
    return socket.create_connection(address, timeout=timeout)


class ISSLineTransport:
    def __init__(
        self,
        config: ISSConnectionConfig,
        *,
        socket_factory: SocketFactory = _default_socket_factory,
    ) -> None:
        config.validate()
        self.config = config
        self._socket_factory = socket_factory
        self._sock: socket.socket | None = None
        self._reader: TextIO | None = None
        self._writer: TextIO | None = None
        self.authenticated_client_id: str | None = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> None:
        if self.connected:
            raise ISSTransportError("ALREADY_CONNECTED")
        try:
            sock = self._socket_factory(
                (self.config.host, self.config.port),
                self.config.connect_timeout_s,
            )
            if self.config.read_timeout_s is not None:
                sock.settimeout(self.config.read_timeout_s)
            reader = sock.makefile("r", encoding="utf-8", newline="\n")
            writer = sock.makefile("w", encoding="utf-8", newline="\n")
        except OSError as exc:
            raise ISSTransportError(f"CONNECT_FAILED:{exc.__class__.__name__}") from exc
        self._sock = sock
        self._reader = reader
        self._writer = writer

    def login(self, password: str) -> str:
        if not self.connected or self._reader is None or self._writer is None:
            raise ISSTransportError("NOT_CONNECTED")
        if not password or "\n" in password or "\r" in password:
            raise ISSTransportError("BAD_PASSWORD_VALUE")

        handshake = LoginHandshake(self.config.client_id)
        self.send_line(handshake.client_id_line())
        prompt = self.read_line()
        try:
            handshake.accept_password_prompt(prompt)
        except ISSServiceError as exc:
            raise ISSTransportError(str(exc)) from exc

        # Password exists only in this stack frame; transport never stores it.
        self.send_line(password)
        welcome = self.read_line()
        try:
            actual_id = handshake.accept_welcome(welcome)
        except ISSServiceError as exc:
            raise ISSTransportError(str(exc)) from exc
        self.authenticated_client_id = actual_id
        return actual_id

    def send_line(self, line: str) -> None:
        if self._writer is None:
            raise ISSTransportError("NOT_CONNECTED")
        if not line or "\n" in line or "\r" in line:
            raise ISSTransportError("BAD_OUTBOUND_LINE")
        try:
            self._writer.write(line + "\n")
            self._writer.flush()
        except OSError as exc:
            raise ISSTransportError(f"WRITE_FAILED:{exc.__class__.__name__}") from exc

    def read_line(self) -> str:
        if self._reader is None:
            raise ISSTransportError("NOT_CONNECTED")
        try:
            line = self._reader.readline()
        except OSError as exc:
            raise ISSTransportError(f"READ_FAILED:{exc.__class__.__name__}") from exc
        if line == "":
            raise ISSTransportError("REMOTE_EOF")
        return line.rstrip("\r\n")

    def iter_lines(self) -> Iterator[str]:
        while True:
            try:
                yield self.read_line()
            except ISSTransportError as exc:
                if str(exc) == "REMOTE_EOF":
                    return
                raise

    def close(self) -> None:
        reader, writer, sock = self._reader, self._writer, self._sock
        self._reader = None
        self._writer = None
        self._sock = None
        self.authenticated_client_id = None
        for obj in (reader, writer, sock):
            if obj is not None:
                try:
                    obj.close()
                except OSError:
                    pass

    def __enter__(self) -> "ISSLineTransport":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
