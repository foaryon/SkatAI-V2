import socket
import threading

import pytest

from skatai.iss.transport import (
    ISSConnectionConfig,
    ISSLineTransport,
    ISSTransportError,
)


class OneShotFakeServer:
    def __init__(self, expected_id="skatai", expected_password="secret"):
        self.expected_id = expected_id
        self.expected_password = expected_password
        self.received = []
        self.ready = threading.Event()
        self.done = threading.Event()
        self.error = None
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.host, self.port = self.listener.getsockname()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            self.ready.set()
            conn, _ = self.listener.accept()
            with conn:
                r = conn.makefile("r", encoding="utf-8", newline="\n")
                w = conn.makefile("w", encoding="utf-8", newline="\n")
                client_id = r.readline().rstrip("\r\n")
                self.received.append(("client_id", client_id))
                w.write("password:\n")
                w.flush()
                password = r.readline().rstrip("\r\n")
                self.received.append(("password", password))
                if client_id != self.expected_id or password != self.expected_password:
                    w.write("login failed\n")
                    w.flush()
                    return
                w.write(f"Welcome {client_id} version 14.11033.16\n")
                w.flush()
                line = r.readline().rstrip("\r\n")
                self.received.append(("line", line))
                w.write("time 123456\n")
                w.flush()
        except BaseException as exc:
            self.error = exc
        finally:
            self.done.set()
            self.listener.close()

    def __enter__(self):
        self.thread.start()
        self.ready.wait(2)
        return self

    def __exit__(self, exc_type, exc, tb):
        self.done.wait(2)
        if self.error:
            raise self.error


def test_fake_server_login_and_line_io():
    with OneShotFakeServer() as server:
        cfg = ISSConnectionConfig(
            server.host,
            server.port,
            "skatai",
            connect_timeout_s=2,
            read_timeout_s=2,
        )
        with ISSLineTransport(cfg) as t:
            assert t.login("secret") == "skatai"
            assert t.authenticated_client_id == "skatai"
            t.send_line("time")
            assert t.read_line() == "time 123456"

        server.done.wait(2)
        assert server.received == [
            ("client_id", "skatai"),
            ("password", "secret"),
            ("line", "time"),
        ]


def test_password_is_not_stored_on_transport():
    cfg = ISSConnectionConfig("localhost", 1234, "skatai")
    t = ISSLineTransport(cfg)
    assert "password" not in t.__dict__


def test_outbound_newline_is_rejected():
    cfg = ISSConnectionConfig("localhost", 1234, "skatai")
    left, right = socket.socketpair()
    try:
        t = ISSLineTransport(cfg, socket_factory=lambda addr, timeout: left)
        t.connect()
        with pytest.raises(ISSTransportError):
            t.send_line("time\nerror")
        t.close()
    finally:
        right.close()


def test_bad_config_rejected():
    with pytest.raises(ValueError):
        ISSConnectionConfig("bad host", 80, "x").validate()
    with pytest.raises(ValueError):
        ISSConnectionConfig("localhost", 0, "x").validate()
