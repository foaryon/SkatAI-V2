from pathlib import Path


def test_iss_login_preflight_never_embeds_password_literal():
    text = Path("scripts/iss-login-preflight.sh").read_text()
    assert "ISS_PASSWORD=" not in text
    assert "ISS_PASSWORD_FILE=" not in text
    assert "connect_and_login(password)" in text
    assert '"scored_game": False' in text
    assert '"joined_table": False' in text
