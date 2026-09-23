import pytest

from skatai.iss.service import (
    ISSServiceError,
    LoginHandshake,
    command_join,
    command_keepalive,
    command_play,
    command_ready,
    parse_service_line,
)


def test_login_handshake_contract():
    h = LoginHandshake("skatai")
    assert h.client_id_line() == "skatai"
    h.accept_password_prompt("password:")
    assert h.accept_welcome("Welcome skatai version 14.11033.16") == "skatai"


def test_login_rejects_unexpected_prompt():
    with pytest.raises(ISSServiceError):
        LoginHandshake.accept_password_prompt("Password")


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("create t1 bot skat", "create"),
        ("destroy t1 bot", "destroy"),
        ("invite kermit t1 pw123", "invite"),
        ("tables - t1", "table_remove"),
        ("table t1 bot go", "table_go"),
        ("table t1 stop", "table_stop"),
        ("table t1 bot error illegal move", "table_error"),
        ("error something broke", "error"),
    ],
)
def test_service_event_shapes(line, kind):
    assert parse_service_line(line).kind == kind


def test_table_play_event():
    e = parse_service_line("table T42 skatai play 1 18")
    assert e.kind == "table_play"
    assert e.fields["table_id"] == "T42"
    assert e.fields["viewer_name"] == "skatai"
    assert e.fields["move_actor"] == "1"
    assert e.fields["move"] == "18"


def test_table_end_preserves_sgf_tail():
    sgf = "(;GM[Skat]ID[9]MV[w ...])"
    e = parse_service_line(f"table T42 skatai end {sgf}")
    assert e.kind == "table_end"
    assert e.fields["game_sgf"] == sgf


def test_client_update_is_typed():
    e = parse_service_line("clients + kermit 3 en 123 2500.5 2 1 7 0")
    assert e.kind == "client_update"
    assert e.fields["client_id"] == "kermit"
    assert e.fields["rating"] == 2500.5
    assert e.fields["games"] == 123


def test_table_update_extracts_players():
    e = parse_service_line("tables + T1 3 99 alice bob carol 0")
    assert e.kind == "table_update"
    assert e.fields["players"] == ["alice", "bob", "carol"]
    assert e.fields["game_num"] == 99


def test_outbound_command_formatters():
    assert command_ready("T1", "skatai") == "table T1 skatai ready"
    assert command_play("T1", "skatai", "18") == "table T1 skatai play 18"
    assert command_join("T1", "pw") == "join T1 pw"
    assert command_keepalive() == "time"


def test_outbound_newline_rejected():
    with pytest.raises(ISSServiceError):
        command_play("T1", "skatai", "18\nerror")


def test_official_table_lifecycle_commands():
    from skatai.iss.service import (
        command_create_table,
        command_invite,
        command_observe,
    )

    assert command_create_table(players=3) == "create / 3"
    assert command_create_table(
        players=3, table_name="SkatAItest", table_password="pw123"
    ) == "create / 3 SkatAItest pw123"
    assert command_observe("T7") == "observe T7"
    assert command_join("T7") == "join T7"
    assert command_invite("T7", "SkatAI", "kermit") == (
        "table T7 SkatAI invite kermit"
    )


def test_table_lifecycle_commands_fail_closed_on_bad_tokens():
    from skatai.iss.service import (
        ISSServiceError,
        command_create_table,
        command_invite,
    )
    import pytest

    with pytest.raises(ISSServiceError):
        command_create_table(players=2)
    with pytest.raises(ISSServiceError):
        command_create_table(players=3, table_password="pw")
    with pytest.raises(ISSServiceError):
        command_invite("T 7", "SkatAI", "kermit")
