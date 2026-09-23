from pathlib import Path


def test_iss_login_preflight_never_embeds_password_literal():
    text = Path("scripts/iss-login-preflight.sh").read_text()
    assert "ISS_PASSWORD=" not in text
    assert "ISS_PASSWORD_FILE=" not in text
    assert "connect_and_login(password)" in text
    assert '"scored_game": False' in text
    assert '"joined_table": False' in text


def test_iss_login_preflight_optional_paths_are_set_u_safe_and_journaled():
    text = Path("scripts/iss-login-preflight.sh").read_text()
    assert "${ISS_PREFLIGHT_JOURNAL:-" in text
    assert "${ISS_PREFLIGHT_RESULT:-" in text
    assert "client_from_environment(journal_path=journal)" in text
    assert '"journal_path": str(journal)' in text
