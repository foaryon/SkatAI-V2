import importlib.util
from pathlib import Path

def load():
    p=Path(__file__).resolve().parents[1]/"scripts/watch_r9_recovery_soak.py"
    s=importlib.util.spec_from_file_location("soak",p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def test_no_premature_or_full_product_acceptance():
    m=load();s=m.sample({},100,{"captured_unix_ns":100e9,"state":"RUNNING"},{"scored_games":1})
    assert s["classification"]=="RUNNING" and s["full_afk_product_acceptance"] is False

def test_stale_and_gap_prevent_acceptance():
    m=load();s=m.sample({"started_at":0,"last_sample_at":1,"initial_scored_games":1},86400,{"captured_unix_ns":1e9,"state":"RUNNING"},{"scored_games":2})
    assert s["classification"]=="FAIL"

def test_full_day_with_progress_can_pass_only_operational_scope():
    m=load();s=m.sample({"started_at":0,"last_sample_at":86100,"initial_scored_games":1},86400,{"captured_unix_ns":86400e9,"state":"RUNNING"},{"scored_games":2})
    assert s["classification"]=="PASS_R9_OPERATIONAL_SOAK" and not s["full_afk_product_acceptance"]
