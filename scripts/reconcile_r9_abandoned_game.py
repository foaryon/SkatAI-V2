#!/usr/bin/env python3
"""Bounded recovery of a declaration-timeout game whose ISS table is gone.

Uses the frozen campaign's evidence implementation. Never sends a move, joins,
changes treatment, invents a score, or clears authority before remote evidence.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

TIMEOUT_ERROR = "skatai.runtime.interface.SkatAIInterfaceError: B0_CLI_TIMEOUT:SKAT_OR_HAND_DECL"

def eligible(*, state, payload, source, last_error):
    return (
        state.get("state") == "BLOCKED_RECONCILIATION"
        and not state.get("worker_pids") and not state.get("launcher_pids")
        and state.get("pending_effects") == 0
        and payload.get("source_commit") == source
        and len(payload.get("games", [])) == 1
        and last_error == TIMEOUT_ERROR
    )

def confirmed_absent(actual, expected, table, lines):
    return actual == expected and f"error observe - _Table {table} _not_exists" in lines

def attempt_allowed(attempts, now):
    today = int(now // 86400)
    return (
        sum(int(x["at"] // 86400) == today for x in attempts) < 3
        and (not attempts or now - max(x["at"] for x in attempts) >= 3600)
    )

def durable_then_clear(game_upload, report_upload, clear_authority):
    game_upload()
    report_upload()
    return clear_authority()

def atomic(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    with tmp.open("rb") as f: os.fsync(f.fileno())
    os.replace(tmp, path)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--runtime", type=Path, required=True)
    p.add_argument("--frozen-repo", type=Path, required=True)
    p.add_argument("--env-file", type=Path, required=True)
    args = p.parse_args()
    from supervise_frozen_r9 import evaluate_restart_state, load_runtime_env
    root, repo = args.runtime, args.frozen_repo
    source = subprocess.check_output(["git","-C",str(repo),"rev-parse","HEAD"],text=True).strip()
    if subprocess.check_output(["git","-C",str(repo),"status","--porcelain"],text=True).strip():
        raise RuntimeError("FROZEN_SOURCE_DIRTY")
    sys.path.insert(0, str(repo / "src"))
    from skatai.iss.gate_worker import GateEvidence, GatePaths, GameAssignment, load_identities
    from skatai.iss.transport import ISSConnectionConfig, ISSLineTransport
    from skatai.iss.client import ISSJournal
    from skatai.iss.service import command_observe
    def state():
        return evaluate_restart_state(runtime=root, frozen_repo=repo)
    def active():
        return json.loads((root / "active-games.json").read_text())
    log = root / "supervisor-worker.log"
    with log.open("rb") as f:
        f.seek(max(0, log.stat().st_size - 16000))
        errors = [x.strip() for x in f.read().decode("utf-8","replace").splitlines() if "Error:" in x or "Exception:" in x]
    last_error = errors[-1] if errors else ""
    payload = active()
    if not eligible(state=state(),payload=payload,source=source,last_error=last_error):
        return 0
    folder = root / "recovery"
    folder.mkdir(exist_ok=True)
    with (root / ".recovery.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not eligible(state=state(),payload=active(),source=source,last_error=last_error):
            return 0
        attempts_path = folder / "automatic-abandoned-attempts.json"
        attempts = json.loads(attempts_path.read_text()) if attempts_path.exists() else []
        now = time.time()
        if not attempt_allowed(attempts, now):
            return 0
        entry = payload["games"][0]
        table = entry["table_id"]
        attempt = {"at": now, "table_id": table, "game_sequence": entry["game_sequence"], "status":"STARTED"}
        attempts.append(attempt)
        attempts = [x for x in attempts if now - x["at"] < 7*86400]
        atomic(attempts_path, attempts)
        env = load_runtime_env(args.env_file)
        transport = ISSLineTransport(ISSConnectionConfig(env["ISS_HOST"],int(env["ISS_PORT"]),env["ISS_CLIENT_ID"],read_timeout_s=8))
        lines = []
        actual = None
        try:
            transport.connect()
            actual = transport.login(Path(env["ISS_PASSWORD_FILE"]).read_text().strip())
            transport.send_line(command_observe(table))
            start = time.monotonic()
            while time.monotonic()-start < 20:
                try: line = transport.read_line()
                except Exception: break
                lines.append(ISSJournal._redact_service_secret("in",line))
                if confirmed_absent(actual, env["ISS_CLIENT_ID"], table, lines): break
        finally:
            transport.close()
        capture = folder / f"automatic-absence-{int(now)}.json"
        atomic(capture, {"at":now,"authenticated_client_id":actual,"table_id":table,"commands":[command_observe(table)],"lines":lines})
        if not confirmed_absent(actual,env["ISS_CLIENT_ID"],table,lines):
            attempt["status"]="TABLE_ABSENCE_NOT_CONFIRMED"
            atomic(attempts_path,attempts)
            return 0
        if active() != payload or not eligible(state=state(),payload=active(),source=source,last_error=last_error):
            raise RuntimeError("AUTHORITY_CHANGED_DURING_OBSERVATION")
        with (root / "service.jsonl").open("rb") as f:
            f.seek(entry["protocol_offset"])
            if any(json.loads(x).get("line","").startswith(f"table {table} {actual} end ") for x in f if x.strip()):
                raise RuntimeError("TERMINAL_EVIDENCE_REQUIRES_SCORED_RECONCILIATION")
        os.environ.update(SKATAI_V2_ROOT=str(repo), ISS_GATE_RUNTIME_ROOT=str(root))
        evidence = GateEvidence(paths=GatePaths.defaults(),identities=load_identities(repo),source_commit=source)
        before_scored = len(evidence.scored_rows())
        backup = folder / f"automatic-active-before-{int(now)}.json"
        atomic(backup,payload)
        stored = evidence.append_failure_without_terminal(
            assignment=GameAssignment(**entry["assignment"]),table_id=table,
            game_sequence=entry["game_sequence"],protocol_offset=entry["protocol_offset"],
            effect_offset=entry["effect_offset"],status="MODEL_FAILURE",
            failure_reason="B0_CLI_TIMEOUT_SKAT_OR_HAND_DECL_SERVER_TABLE_ABSENT_OUTCOME_UNKNOWN")
        if len(evidence.scored_rows()) != before_scored:
            raise RuntimeError("UNSCORED_RECOVERY_CHANGED_SCORED_COUNT")
        report = folder / f"automatic-abandoned-{stored['game_id']}.json"
        atomic(report, {"schema":"skatai.v2.automatic-abandoned-recovery.v1","at":now,
            "source_commit":source,"table_id":table,"game_sequence":entry["game_sequence"],
            "game_id":stored["game_id"],"classification":"MODEL_FAILURE_UNSCORED_OUTCOME_UNKNOWN",
            "score":None,"scored_games":before_scored,"strength_look_opened":False,
            "capture_sha256":hashlib.sha256(capture.read_bytes()).hexdigest(),
            "phase":"PREPARED_REMOTE_PERSISTENCE_BEFORE_AUTHORITY_CLEAR"})
        def upload_report():
            for path in (capture,backup,report):
                evidence.mirror.upload_verified(path,"recovery/"+path.name)
        def clear():
            # Recheck after uploads. Failure preserves local authority for retry.
            if active()!=payload or not eligible(
                state=state(), payload=payload, source=source, last_error=last_error
            ):
                raise RuntimeError("AUTHORITY_CHANGED_BEFORE_CLEAR")
            empty={"schema":payload["schema"],"source_commit":source,"games":[]}
            prepared=folder / f"automatic-active-cleared-{stored['game_id']}.json"
            atomic(prepared,empty)
            # Upload next authority first; local authority stays retained on failure.
            evidence.mirror.upload_verified(prepared,"current/active-games.json")
            atomic(root/"active-games.json",empty)
        durable_then_clear(lambda:evidence.mirror_game(stored["game_id"]),upload_report,clear)
        attempt["status"]="COMPLETED"
        attempt["game_id"]=stored["game_id"]
        atomic(attempts_path,attempts)
        print(json.dumps({"reconciled":True,"game_id":stored["game_id"],"scored_games":before_scored}))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
