from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts" / "prepare_r9_runtime_secrets.py"
    spec = importlib.util.spec_from_file_location("prepare_r9_runtime_secrets", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_r9_secret_is_owner_only_and_owned_by_runtime_account(tmp_path, monkeypatch):
    mod = _load()
    uid, gid = os.geteuid(), os.getegid()
    monkeypatch.setattr(
        mod.pwd, "getpwnam",
        lambda name: SimpleNamespace(pw_uid=uid, pw_gid=gid),
    )
    monkeypatch.setattr(mod.os, "chown", lambda path, owner, group: None)
    path = mod.materialize(
        {"RUNPOD_SECRET_skatai_iss_password": "fake-test-secret"},
        out=tmp_path / "secrets",
    )
    st = path.stat()
    assert st.st_mode & 0o077 == 0
    assert st.st_mode & 0o700 == 0o600
    assert path.read_text(encoding="utf-8") == "fake-test-secret"


def test_r9_secret_prep_fails_closed_without_secret_source(tmp_path):
    mod = _load()
    with pytest.raises(RuntimeError, match="R9_ISS_SECRET_SOURCE_UNAVAILABLE"):
        mod.materialize({}, out=tmp_path / "secrets")


def test_trusted_r9_boot_pins_current_frozen_identity():
    root = Path(__file__).resolve().parents[1]
    text = (root / "scripts" / "r9_trusted_boot.sh").read_text(encoding="utf-8")
    assert "prepare_r9_runtime_secrets.py" in text
    assert "prepare_runtime_secrets.py" not in text
    assert "5c4b6f977ebdf883e836e95eb0530121b99394e7" in text
    assert "b76fd0159f72b08af303bf06692d5a73ed1c05baf1d3e35e4141f4c241c87b3d" in text
    assert "SKATAI_R9_FROZEN_REPO" in text
    assert "SKATAI_R9_LAUNCHER" in text
