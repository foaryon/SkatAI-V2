#!/usr/bin/env python3
"""Deterministic 24-hour R9 operational soak; no model calls or promotion."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import time

ROOT=Path("/workspace/skatai-v2-runtime/iss/external-gate-r9")
OUT=ROOT/"recovery"/"unattended-soak"
def write(path,data):
    tmp=path.with_suffix(".tmp");tmp.write_text(json.dumps(data,indent=2,sort_keys=True)+"\n")
    os.replace(tmp,path)
def sample(state,now,supervisor,status):
    state.setdefault("started_at",now)
    state.setdefault("failures",[])
    previous=state.get("last_sample_at",now)
    if now-previous>900:
        state["failures"].append({"at":now,"reason":"OBSERVATION_GAP_OVER_900S"})
    if now-float(supervisor.get("captured_unix_ns",0))/1e9>90:
        state["failures"].append({"at":now,"reason":"STALE_SUPERVISOR"})
    phase=supervisor.get("state")
    if phase not in {"RUNNING","BOOTSTRAPPING","SAFE_TO_RESTART","WORKER_EXITED","CAMPAIGN_COMPLETE"}:
        since=state.setdefault("blocked_since",now)
        if now-since>240:
            state["failures"].append({"at":now,"reason":"UNRESOLVED_HOLD_OVER_240S","state":phase})
    else:
        state.pop("blocked_since",None)
    state["last_sample_at"]=now
    state["samples"]=int(state.get("samples",0))+1
    state.setdefault("initial_scored_games",status.get("scored_games"))
    state["latest_scored_games"]=status.get("scored_games")
    state["latest_supervisor_state"]=phase
    state["gate_present"]=status.get("gate") is not None
    state["source_commit"]=status.get("source_commit")
    state["failures"]=state["failures"][-100:]
    state["classification"]="RUNNING"
    if now-state["started_at"]>=86400:
        progress=int(state.get("latest_scored_games") or 0)>int(state.get("initial_scored_games") or 0)
        state["classification"]="PASS_R9_OPERATIONAL_SOAK" if not state["failures"] and progress else "FAIL"
    state["full_afk_product_acceptance"]=False
    return state
def main():
    p=argparse.ArgumentParser();p.add_argument("--once",action="store_true");a=p.parse_args()
    OUT.mkdir(exist_ok=True,parents=True)
    with (OUT/".lock").open("a+") as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        path=OUT/"state.json"
        state=json.loads(path.read_text()) if path.exists() else {"schema":"skatai.v2.r9-operational-soak.v1"}
        while state.get("classification") not in {"PASS_R9_OPERATIONAL_SOAK","FAIL"}:
            now=time.time()
            try:
                supervisor=json.loads((ROOT/"supervisor-status.json").read_text())
                status=json.loads((ROOT/"status.json").read_text())
                state=sample(state,now,supervisor,status)
            except Exception as exc:
                state.setdefault("failures",[]).append({"at":now,"reason":type(exc).__name__})
                state["classification"]="RUNNING"
            write(path,state)
            with (OUT/"samples.jsonl").open("a") as f:
                f.write(json.dumps({k:state.get(k) for k in ["last_sample_at","latest_scored_games","latest_supervisor_state","classification","gate_present"]})+"\n")
            if a.once:
                print(json.dumps(state));return
            if state.get("classification") in {"PASS_R9_OPERATIONAL_SOAK","FAIL"}:break
            time.sleep(300)
if __name__=="__main__":main()
