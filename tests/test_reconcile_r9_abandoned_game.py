import importlib.util
from pathlib import Path
import pytest

def load():
    p=Path(__file__).resolve().parents[1]/"scripts/reconcile_r9_abandoned_game.py"
    s=importlib.util.spec_from_file_location("reconcile",p)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def valid():
    return dict(state={"state":"BLOCKED_RECONCILIATION","worker_pids":[],"launcher_pids":[],"pending_effects":0},payload={"source_commit":"a"*40,"games":[{"table_id":"AIabc","game_sequence":5}]},source="a"*40,last_error="skatai.runtime.interface.SkatAIInterfaceError: B0_CLI_TIMEOUT:SKAT_OR_HAND_DECL")

def test_valid_failure_is_eligible():
    assert load().eligible(**valid())

@pytest.mark.parametrize("field,value",[("worker_pids",[7]),("launcher_pids",[8]),("pending_effects",1),("state","MANUAL_HOLD")])
def test_reject_live_or_unsettled_authority(field,value):
    a=valid();a["state"][field]=value;assert not load().eligible(**a)

def test_reject_unknown_failure_and_source():
    a=valid();a["last_error"]="OtherError: unexpected";assert not load().eligible(**a)
    a=valid();a["source"]="b"*40;assert not load().eligible(**a)

def test_server_absence_must_be_exact_authenticated_target():
    m=load()
    assert m.confirmed_absent("SkatAI","SkatAI","AIabc",["error observe - _Table AIabc _not_exists"])
    assert not m.confirmed_absent("Other","SkatAI","AIabc",["error observe - _Table AIabc _not_exists"])
    assert not m.confirmed_absent("SkatAI","SkatAI","AIabc",["error observe - _Table AIother _not_exists"])

def test_clear_follows_durable_evidence_and_report():
    m=load();calls=[]
    m.durable_then_clear(lambda:calls.append("game"),lambda:calls.append("report"),lambda:calls.append("clear"))
    assert calls==["game","report","clear"]

def test_upload_failure_never_clears_authority():
    m=load();calls=[]
    def fail():raise RuntimeError("S3 unavailable")
    with pytest.raises(RuntimeError):m.durable_then_clear(fail,lambda:None,lambda:calls.append("clear"))
    assert not calls

def test_cooldown_and_daily_cap():
    m=load()
    assert m.attempt_allowed([],10000)
    assert not m.attempt_allowed([{"at":9999}],10000)
    assert not m.attempt_allowed([{"at":1},{"at":3700},{"at":7400}],12000)


def test_report_upload_failure_also_preserves_authority():
    m=load();calls=[]
    def fail():raise RuntimeError("report upload failed")
    with pytest.raises(RuntimeError):
        m.durable_then_clear(lambda:calls.append("game"),fail,lambda:calls.append("clear"))
    assert calls==["game"]


def test_daily_rollover_keeps_cooldown_and_then_allows_work():
    m=load()
    assert not m.attempt_allowed([{"at":86390}],86401)
    assert m.attempt_allowed([{"at":1000},{"at":5000},{"at":9000}],90000)
