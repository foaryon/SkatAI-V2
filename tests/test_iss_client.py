import json

from skatai.iss.client import ISSClientCore, ISSClientPolicy, ISSJournal


class FakeTransport:
    def __init__(self, lines=()):
        self.lines = list(lines)
        self.sent = []
        self.connected = False
        self.authenticated_client_id = None
        self.closed = False

    def connect(self):
        self.connected = True

    def login(self, password):
        assert self.connected
        assert password == "secret-test-only"
        self.authenticated_client_id = "SkatAIV2"
        return "SkatAIV2"

    def read_line(self):
        if not self.lines:
            raise EOFError
        return self.lines.pop(0)

    def send_line(self, line):
        self.sent.append(line)

    def close(self):
        self.closed = True


def test_invite_join_create_ready_end_ready_lifecycle(tmp_path):
    transport = FakeTransport()
    journal = ISSJournal(tmp_path / "iss.jsonl")
    client = ISSClientCore(transport, journal=journal)
    assert client.connect_and_login("secret-test-only") == "SkatAIV2"

    client.handle_line("invite kermit T1 pw1")
    assert transport.sent == ["join T1 pw1"]

    client.handle_line("create T1 SkatAIV2 3")
    assert transport.sent[-1] == "table T1 SkatAIV2 ready"

    client.handle_line("table T1 SkatAIV2 start initial")
    client.handle_line("table T1 SkatAIV2 play w C7.C8.C9.CT.CJ.CQ.CK.CA.S7.S8|??.??.??.??.??.??.??.??.??.??|??.??.??.??.??.??.??.??.??.??|??.??")
    client.handle_line("table T1 SkatAIV2 end (;GM[Skat])")
    assert transport.sent[-1] == "table T1 SkatAIV2 ready"

    client.close()
    assert transport.closed
    assert not client.state.connected

    rows = [json.loads(x) for x in (tmp_path / "iss.jsonl").read_text().splitlines()]
    assert any(x["direction"] == "in" and x["line"].startswith("invite ") for x in rows)
    assert any(
        x["direction"] == "out"
        and x["line"] == "join T1 <redacted-table-password>"
        for x in rows
    )
    assert "secret-test-only" not in (tmp_path / "iss.jsonl").read_text()


def test_policy_can_disable_automatic_server_actions():
    transport = FakeTransport()
    client = ISSClientCore(
        transport,
        policy=ISSClientPolicy(
            accept_invitations=False,
            ready_when_joined=False,
            ready_after_game=False,
        ),
    )
    client.connect_and_login("secret-test-only")
    client.handle_line("invite zoot T2 pw2")
    client.handle_line("create T2 SkatAIV2 3")
    client.handle_line("table T2 SkatAIV2 end (;GM[Skat])")
    assert transport.sent == []


def test_table_play_updates_state_without_guessing_a_move():
    transport = FakeTransport()
    client = ISSClientCore(transport)
    client.connect_and_login("secret-test-only")
    client.handle_line("create T3 SkatAIV2 3")
    sent_before = list(transport.sent)
    client.handle_line("table T3 SkatAIV2 play 1 18")
    assert client.state.tables["T3"].last_move.action == "18"
    assert transport.sent == sent_before


def test_client_from_environment_supports_private_password_file(monkeypatch, tmp_path):
    from skatai.iss.client import client_from_environment

    secret = tmp_path / "iss-password"
    secret.write_text("secret-value\n")
    secret.chmod(0o600)
    monkeypatch.setenv("ISS_HOST", "skatgame.net")
    monkeypatch.setenv("ISS_CLIENT_ID", "SkatAI")
    monkeypatch.delenv("ISS_PASSWORD", raising=False)
    monkeypatch.setenv("ISS_PASSWORD_FILE", str(secret))
    monkeypatch.delenv("ISS_PORT", raising=False)

    client, password = client_from_environment()
    assert password == "secret-value"
    assert client.transport.config.host == "skatgame.net"
    assert client.transport.config.port == 7000
    assert client.transport.config.client_id == "SkatAI"


def test_client_from_environment_rejects_open_password_file(monkeypatch, tmp_path):
    from skatai.iss.client import client_from_environment

    secret = tmp_path / "iss-password"
    secret.write_text("secret-value\n")
    secret.chmod(0o644)
    monkeypatch.setenv("ISS_HOST", "skatgame.net")
    monkeypatch.setenv("ISS_CLIENT_ID", "SkatAI")
    monkeypatch.delenv("ISS_PASSWORD", raising=False)
    monkeypatch.setenv("ISS_PASSWORD_FILE", str(secret))

    try:
        client_from_environment()
    except ValueError as exc:
        assert str(exc) == "ISS_PASSWORD_FILE_PERMISSIONS_TOO_OPEN"
    else:
        raise AssertionError("open password file must be rejected")


def test_decision_aware_client_persists_intent_before_send_and_confirms_on_echo(tmp_path):
    from skatai.iss.bridge import ISSBiddingDecisionProvider
    from skatai.iss.effects import ISSAuthorityGuard, ISSEffectJournal
    from tests.test_product_interface import _ai

    deal = (
        "??.??.??.??.??.??.??.??.??.??|"
        "??.??.??.??.??.??.??.??.??.??|"
        "HJ.C9.SK.S8.S7.HT.DA.DK.D9.D7|??.??"
    )
    tr = FakeTransport()
    ej = ISSEffectJournal(tmp_path / "effects.jsonl")
    client = ISSClientCore(
        tr,
        move_provider=ISSBiddingDecisionProvider(_ai(), release_id="R-B1"),
        effect_guard=ISSAuthorityGuard(ej),
    )
    client.state.set_connected("SkatAIV2")
    client.handle_line("create T SkatAIV2 3")
    client.handle_line("table T SkatAIV2 start x")
    client.handle_line(f"table T SkatAIV2 play w {deal}")
    client.handle_line("table T SkatAIV2 play 1 18")
    client.handle_line("table T SkatAIV2 play 0 y")
    client.handle_line("table T SkatAIV2 play 1 p")

    plays = [x for x in tr.sent if x == "table T SkatAIV2 play 20"]
    assert len(plays) == 1
    pending = ej.pending()
    assert len(pending) == 1
    assert pending[0].status == "SEND_RETURNED"

    # Re-entering the same decision surface cannot blindly replay an unresolved effect.
    client.handle_line("table T SkatAIV2 go")
    plays = [x for x in tr.sent if x == "table T SkatAIV2 play 20"]
    assert len(plays) == 1

    # Server echo commits the material effect.
    client.handle_line("table T SkatAIV2 play 2 20")
    assert ej.pending() == []


def test_decision_aware_restart_does_not_replay_unresolved_effect(tmp_path):
    from skatai.iss.bridge import ISSBiddingDecisionProvider
    from skatai.iss.effects import ISSAuthorityGuard, ISSEffectJournal
    from tests.test_product_interface import _ai

    deal = (
        "??.??.??.??.??.??.??.??.??.??|"
        "??.??.??.??.??.??.??.??.??.??|"
        "HJ.C9.SK.S8.S7.HT.DA.DK.D9.D7|??.??"
    )
    path = tmp_path / "effects.jsonl"

    def build():
        tr = FakeTransport()
        client = ISSClientCore(
            tr,
            move_provider=ISSBiddingDecisionProvider(_ai(), release_id="R-B1"),
            effect_guard=ISSAuthorityGuard(ISSEffectJournal(path)),
        )
        client.state.set_connected("SkatAIV2")
        client.handle_line("create T SkatAIV2 3")
        client.handle_line("table T SkatAIV2 start x")
        client.handle_line(f"table T SkatAIV2 play w {deal}")
        client.handle_line("table T SkatAIV2 play 1 18")
        client.handle_line("table T SkatAIV2 play 0 y")
        client.handle_line("table T SkatAIV2 play 1 p")
        return client, tr

    _, tr1 = build()
    assert tr1.sent.count("table T SkatAIV2 play 20") == 1

    _, tr2 = build()
    assert tr2.sent.count("table T SkatAIV2 play 20") == 0


def test_service_journal_redacts_ephemeral_table_passwords(tmp_path):
    import json
    from skatai.iss.client import ISSJournal

    p = tmp_path / "journal.jsonl"
    j = ISSJournal(p)
    j.write("in", "invite kermit T7 secretpw")
    j.write("out", "join T7 secretpw")
    j.write("out", "create / 3 AIG12345 secretpw")
    j.write("out", "table T7 SkatAI ready")

    rows = [json.loads(x) for x in p.read_text().splitlines()]
    text = "\\n".join(x["line"] for x in rows)
    assert "secretpw" not in text
    assert rows[0]["line"] == "invite kermit T7 <redacted-table-password>"
    assert rows[1]["line"] == "join T7 <redacted-table-password>"
    assert rows[2]["line"] == "create / 3 AIG12345 <redacted-table-password>"
    assert rows[3]["line"] == "table T7 SkatAI ready"


def test_public_service_command_is_journaled_and_validated(tmp_path):
    import json
    import pytest
    from skatai.iss.client import ISSClientCore, ISSJournal

    tr = FakeTransport()
    c = ISSClientCore(tr, journal=ISSJournal(tmp_path / "j.jsonl"))
    c.send_service_command("create / 3")
    assert tr.sent[-1] == "create / 3"
    row = json.loads((tmp_path / "j.jsonl").read_text().splitlines()[-1])
    assert row["direction"] == "out"
    assert row["line"] == "create / 3"
    with pytest.raises(ValueError, match="BAD_SERVICE_COMMAND"):
        c.send_service_command("time" + chr(10) + "error")


def test_service_send_lock_serializes_public_command_path(tmp_path):
    from skatai.iss.client import ISSClientCore, ISSJournal

    tr = FakeTransport()
    c = ISSClientCore(tr, journal=ISSJournal(tmp_path / "j.jsonl"))
    assert hasattr(c, "_send_lock")
    c.send_service_command("time")
    assert tr.sent[-1] == "time"


def test_client_environment_sets_safe_connection_timeouts(monkeypatch):
    from skatai.iss.client import client_from_environment

    monkeypatch.setenv("ISS_HOST", "skatgame.net")
    monkeypatch.setenv("ISS_CLIENT_ID", "SkatAI")
    monkeypatch.setenv("ISS_PASSWORD", "test-only")
    monkeypatch.setenv("ISS_CONNECT_TIMEOUT_S", "7")
    monkeypatch.setenv("ISS_READ_TIMEOUT_S", "90")
    client, _ = client_from_environment()
    assert client.transport.config.connect_timeout_s == 7
    assert client.transport.config.read_timeout_s == 90


def test_client_environment_can_disable_read_timeout(monkeypatch):
    from skatai.iss.client import client_from_environment

    monkeypatch.setenv("ISS_HOST", "skatgame.net")
    monkeypatch.setenv("ISS_CLIENT_ID", "SkatAI")
    monkeypatch.setenv("ISS_PASSWORD", "test-only")
    monkeypatch.setenv("ISS_READ_TIMEOUT_S", "")
    client, _ = client_from_environment()
    assert client.transport.config.read_timeout_s is None



def test_client_does_not_redecide_while_game_effect_is_pending(tmp_path):
    from types import SimpleNamespace

    from skatai.iss.effects import ISSAuthorityGuard, ISSEffectJournal
    from tests.test_iss_effects import request_result

    class MustNotRunProvider:
        def next_decision(self, table):
            raise AssertionError("provider must not run while a game effect is pending")

    journal = ISSEffectJournal(tmp_path / "effects.jsonl")
    req, result = request_result()
    effect, _ = journal.begin(
        req,
        result,
        external_state_hash="a" * 64,
        table_id="T",
        game_sequence=1,
        protocol_sequence=4,
        wire_action="18",
        outbound_line="table T SkatAI play 18",
    )
    journal.mark_send_returned(effect.effect_id)

    client = ISSClientCore(
        FakeTransport(),
        move_provider=MustNotRunProvider(),
        effect_guard=ISSAuthorityGuard(journal),
    )
    table = SimpleNamespace(
        table_id="T",
        game_sequence=1,
        is_player=True,
        in_progress=True,
    )

    client._maybe_send_move(table)
    assert client.transport.sent == []
    assert len(journal.pending_for_game("T", 1)) == 1
