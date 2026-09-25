#!/usr/bin/env python3
import hashlib
import json
import os
import pwd
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path("/workspace/openai-agent/platform-controller")
PROMPT = Path("/workspace/openai-agent/main-controller/MAIN_PROMPT.md")
CONTINUE = Path("/workspace/openai-agent/main-controller/CONTINUE_PROMPT.txt")
RUNTIME = Path("/workspace/skatai-v2-runtime")
APP_KEY = Path("/run/skatai-v2-secrets/openai_agents_api_key")
EXEC_KEY = Path("/run/skatai-v2-secrets/openai_executor_api_key")
INBOX = Path("/workspace/openai-agent/main-controller/inbox")
PROCESSED = INBOX / "processed"
STATE = ROOT / "session.json"
LOG = ROOT / "controller.log"
PIDFILE = ROOT / "controller.pid"
EXEC_PID = ROOT / "exec-server.pid"
EXEC_LOG = ROOT / "exec-server.log"
PAUSE_SUBMISSIONS = ROOT / "pause-submissions"
CODEX_HOME = Path("/workspace/openai-agent/main-controller/codex-home")
CODEX = "/workspace/openai-agent/bin/codex"
BASE = "https://api.openai.com/v1"
MODEL = "gpt-6-sol"
SERVICE_TIER = "flex"
SESSION_SUBMIT_BUDGET = 4

ROOT.mkdir(parents=True, exist_ok=True)
INBOX.mkdir(parents=True, exist_ok=True)
PROCESSED.mkdir(parents=True, exist_ok=True)
os.chmod(ROOT, 0o700)

def log(msg):
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}\n")

def atomic_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)

def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def safe_http_error(e):
    try:
        body = json.loads(e.read(8192))
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            return str(err.get("message") or err.get("code") or "request rejected")[:500]
        if isinstance(err, str):
            return err[:500]
        if isinstance(body, dict):
            return str(body.get("message") or "request rejected")[:500]
    except Exception:
        pass
    return "request rejected"

def api(method, path, body=None, extra_headers=None):
    key = APP_KEY.read_text(encoding="utf-8").strip()
    payload = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    req = urllib.request.Request(BASE + path, data=payload, method=method)
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("OpenAI-Beta", "agents=v1")
    req.add_header("Content-Type", "application/json")
    for hk, hv in (extra_headers or {}).items():
        req.add_header(hk, hv)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OpenAI HTTP {e.code}: {safe_http_error(e)}") from e

def executor_alive():
    try:
        pid = int(EXEC_PID.read_text().strip())
        os.kill(pid, 0)
        args = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
        return b"codex exec-server" in args
    except Exception:
        return False

def stop_stale_executor():
    try:
        pid = int(EXEC_PID.read_text().strip())
        if not executor_alive():
            EXEC_PID.unlink(missing_ok=True)
            return
        os.kill(pid, signal.SIGTERM)
        for _ in range(10):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        EXEC_PID.unlink(missing_ok=True)
    except Exception:
        EXEC_PID.unlink(missing_ok=True)

def child_env(executor_key):
    # Build a minimal safe executor environment. The application Agents API
    # credential and raw RunPod/ISS secret values must never enter the
    # agent-controlled sandbox.
    e = dict(os.environ)

    # Remove application credentials and raw secret aliases if inherited.
    blocked = {
        "RUNPOD_SECRET_openai_agents_api_key",
        "openai_agents_api_key",
        "OPENAI_AGENTS_API_KEY",
        "OPENAI_API_KEY",
        "RUNPOD_SECRET_skatai_iss_password",
        "skatai_iss_password",
        "ISS_PASSWORD",
    }
    for k in list(e):
        ku = k.upper()
        if k in blocked or "OPENAI_AGENTS_API_KEY" in ku:
            e.pop(k, None)

    # PID 1 owns the current RunPod environment. Read only non-secret ISS
    # connection metadata from it because boot/supervisor layers may sanitize
    # inherited environment variables before this controller starts.
    pid1 = {}
    try:
        for item in Path("/proc/1/environ").read_bytes().split(b"\0"):
            if b"=" not in item:
                continue
            k, v = item.split(b"=", 1)
            ks = k.decode("utf-8", "replace")
            if ks in {"ISS_HOST", "ISS_PORT", "ISS_CLIENT_ID"}:
                pid1[ks] = v.decode("utf-8", "replace")
    except Exception:
        pass

    for k in ("ISS_HOST", "ISS_PORT", "ISS_CLIENT_ID"):
        if pid1.get(k):
            e[k] = pid1[k]

    # Pass only the private file path, never the password value itself.
    iss_file = Path("/run/skatai-v2-secrets/iss_password")
    if iss_file.is_file():
        e["ISS_PASSWORD_FILE"] = str(iss_file)
    else:
        e.pop("ISS_PASSWORD_FILE", None)

    e["CODEX_API_KEY"] = executor_key
    # Use the persistent, sentinelx-owned Codex home. The image does not
    # provide /home/sentinelx, which otherwise causes repeated PATH-alias
    # permission warnings and non-persistent executor state.
    e["HOME"] = str(CODEX_HOME)
    e["CODEX_HOME"] = str(CODEX_HOME)
    return e

def executor_has_iss_runtime_env():
    # start_executor() always launches with child_env(executor_key). In some
    # container configurations /proc/<child>/environ is intentionally not
    # readable even by the supervisor, so do not treat that as a failed ISS
    # contract and churn a healthy executor.
    if not executor_alive():
        return False
    try:
        probe = child_env("__probe__")
        return (
            bool(probe.get("ISS_HOST"))
            and bool(probe.get("ISS_CLIENT_ID"))
            and bool(probe.get("ISS_PORT"))
            and probe.get("ISS_PASSWORD_FILE") == "/run/skatai-v2-secrets/iss_password"
            and Path(probe["ISS_PASSWORD_FILE"]).is_file()
            and "ISS_PASSWORD" not in probe
            and "skatai_iss_password" not in probe
            and "RUNPOD_SECRET_skatai_iss_password" not in probe
        )
    except Exception:
        return False

def demote_to_sentinelx():
    sx = pwd.getpwnam("sentinelx")
    os.initgroups("sentinelx", sx.pw_gid)
    os.setgid(sx.pw_gid)
    os.setuid(sx.pw_uid)

def start_executor(environment):
    if executor_alive():
        return
    executor_key = EXEC_KEY.read_text(encoding="utf-8").strip()
    with EXEC_LOG.open("ab", buffering=0) as out:
        p = subprocess.Popen(
            [
                CODEX,
                "exec-server",
                "--remote", environment["remote_url"],
                "--environment-id", environment["id"],
            ],
            cwd="/workspace/skatai-v2",
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            env=child_env(executor_key),
            preexec_fn=demote_to_sentinelx,
            start_new_session=True,
        )
    EXEC_PID.write_text(str(p.pid), encoding="utf-8")
    os.chmod(EXEC_PID, 0o600)
    log(f"executor_started pid={p.pid} env={environment['id']}")

def ensure_saved_agent(state):
    agent_id = state.get("agent_id")
    if agent_id:
        try:
            a = api("GET", f"/agents/{agent_id}")
            a = api("POST", f"/agents/{agent_id}", {
                "model": MODEL,
                "instructions": PROMPT.read_text(encoding="utf-8"),
                "service_tier": SERVICE_TIER,
            })
            if a.get("service_tier") != SERVICE_TIER:
                raise RuntimeError(f"saved agent service_tier verification failed: {a.get('service_tier')!r}")
            log(f"saved_agent_verified id={agent_id} model={a.get('model')} service_tier={a.get('service_tier')}")
            return a, state
            a = api("POST", f"/agents/{agent_id}", {
                "model": MODEL,
                "service_tier": SERVICE_TIER,
            })
            if a.get("service_tier") != SERVICE_TIER:
                raise RuntimeError(f"saved agent service_tier verification failed: {a.get('service_tier')!r}")
            return a, state
        except RuntimeError as e:
            log("saved_agent_reuse_failed=" + repr(e)[:400])

    a = api("POST", "/agents", {
        "name": "SkatAI V2 MAIN",
        "model": MODEL,
        "instructions": PROMPT.read_text(encoding="utf-8"),
        "service_tier": SERVICE_TIER,
        "metadata": {
            "project": "SkatAI-V2",
            "role": "MAIN",
        },
    })
    if a.get("service_tier") != SERVICE_TIER:
        raise RuntimeError(f"saved agent created with service_tier={a.get('service_tier')!r}")
    state["agent_id"] = a["id"]
    atomic_json(STATE, state)
    log(f"saved_agent_created id={a['id']} model={a.get('model')} service_tier={a.get('service_tier')}")
    return a, state

def create_session():
    state = load_state()
    agent, state = ensure_saved_agent(state)
    body = {
        "agent_id": agent["id"],
        "agent": {
            "model": MODEL,
            "service_tier": SERVICE_TIER,
        },
        "environment": {
            "type": "self_hosted",
            "workspace_directory": "/workspace/skatai-v2",
        },
        "metadata": {
            "project": "SkatAI-V2",
            "role": "MAIN",
            "execution_owner": "runpod-server",
            "pc_phone_dependency": "none",
        },
    }
    s = api("POST", "/agents/sessions", body)
    actual = ((s.get("agent") or {}).get("service_tier"))
    if actual != SERVICE_TIER:
        raise RuntimeError(f"session created with service_tier={actual!r}, expected {SERVICE_TIER!r}")
    env = s.get("environment") or {}
    old_session_id = state.get("session_id")
    previous = list(state.get("previous_session_ids") or [])
    if old_session_id and old_session_id != s["id"]:
        previous.append(old_session_id)
        previous = previous[-20:]
    state.update({
        "session_id": s["id"],
        "agent_id": agent["id"],
        "agent": {
            "name": agent.get("name"),
            "model": (s.get("agent") or {}).get("model"),
            "service_tier": actual,
        },
        "environment": {
            "id": env.get("id"),
            "remote_url": env.get("remote_url"),
        },
        "initial_sent": False,
        "created_at": s.get("created_at"),
        # Preserve no-progress state across bounded session rotation. A new
        # Agents session is a context-cost boundary, not evidence of project
        # progress.
        "no_progress": int(state.get("no_progress") or 0),
        "session_submit_count": 0,
        "previous_session_ids": previous,
    })
    atomic_json(STATE, state)
    log(f"session_created id={state['session_id']} agent_id={agent['id']} model={state['agent']['model']} service_tier={actual}")
    return state

def retrieve_session(session_id):
    return api("GET", f"/agents/sessions/{session_id}")

def ensure_flex(session_id, session):
    agent = session.get("agent") or {}
    if agent.get("service_tier") == SERVICE_TIER and agent.get("model") == MODEL:
        return session
    updated = api("POST", f"/agents/sessions/{session_id}", {
        "agent": {"model": MODEL, "service_tier": SERVICE_TIER}
    })
    actual = (updated.get("agent") or {}).get("service_tier")
    if actual != SERVICE_TIER:
        raise RuntimeError(f"service_tier verification failed: {actual!r}")
    log("session_settings_verified model=gpt-6-sol service_tier=flex")
    return updated

def send_message(session_id, text):
    idem = hashlib.sha256((session_id + "\n" + text + "\n" + str(time.time_ns())).encode()).hexdigest()
    api("POST", f"/agents/sessions/{session_id}/events", {
        "events": [{
            "type": "agent.session.input.message",
            "input": [{
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            }],
        }],
    }, extra_headers={"Idempotency-Key": idem})

def pending_inputs():
    return sorted(INBOX.glob("*.msg"))

def send_next_input(session_id, state):
    files = pending_inputs()
    if files:
        parts = []
        for f in files:
            try:
                parts.append(f"--- INPUT {f.name} ---\n{f.read_text(encoding='utf-8')}\n--- END INPUT ---")
            except Exception:
                continue
        if parts:
            send_message(session_id, "\n\n".join(parts))
            for f in files:
                try:
                    os.replace(f, PROCESSED / f.name)
                except FileNotFoundError:
                    pass
            log(f"submitted_user_inbox count={len(files)}")
            return True

    if not state.get("initial_sent"):
        text = (
            "RESUME NOW from the durable SkatAI V2 project state on the attached RunPod. The saved MAIN instructions "
            "and full Big Prompt remain binding. Read only the minimum fresh state needed, starting with the durable "
            "continuation/evidence state; do not restart completed work or reconstruct old chat history. The user's PC "
            "and phone are optional input terminals only. Use a work-conserving long turn: do not yield after a single "
            "diagnosis, fix, test, or milestone while additional safe executable work remains; continue through coherent "
            "inspect-act-verify-persist chains."
        )
        state["initial_sent"] = True
    else:
        text = CONTINUE.read_text(encoding="utf-8")
    send_message(session_id, text)
    log("submitted_autonomous_continue")
    return True

def progress_fingerprint():
    """Hash durable MAIN progress, not independently changing worker heartbeats."""
    h = hashlib.sha256()
    p = RUNTIME / "continuation/CONTINUATION_STATE_CURRENT.json"
    try:
        current = json.loads(p.read_text(encoding="utf-8"))
        # Exclude captured_at, active ISS games/timestamps, raw resource
        # telemetry and other volatile fields. Those can change while MAIN
        # makes no decision/progress and must not defeat token backoff.
        stable = {
            "current_highest_value_action": current.get("current_highest_value_action"),
            "open_blockers": current.get("open_blockers"),
            "nearest_consequential_gates": current.get("nearest_consequential_gates"),
            "champion": current.get("champion"),
            "candidates": current.get("candidates"),
            "evaluations": current.get("evaluations"),
            "releases": current.get("releases"),
            "git": current.get("git"),
        }
        h.update(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode())
    except Exception:
        # Fall back to file identity only if the compact state is temporarily
        # unreadable; never use volatile R9 ledgers as a proxy for MAIN work.
        try:
            st = p.stat()
            h.update(str(st.st_size).encode())
            h.update(str(st.st_mtime_ns).encode())
        except FileNotFoundError:
            pass
    try:
        head = subprocess.check_output(
            ["git", "-C", "/workspace/skatai-v2", "rev-parse", "HEAD"],
            text=True,
            timeout=5,
        ).strip()
        h.update(head.encode())
    except Exception:
        pass
    return h.hexdigest()

def backoff_seconds(state):
    n = int(state.get("no_progress") or 0)
    if n <= 0:
        return 5
    # Sustained no-progress should become cheap. User inbox bypasses this
    # delay, and any durable project-state change resets it.
    return min(3600, 30 * (2 ** min(n, 7)))

def main():
    PIDFILE.write_text(str(os.getpid()), encoding="utf-8")
    os.chmod(PIDFILE, 0o600)
    log("platform_controller_start")
    last_idle_fp = None
    while True:
        if not APP_KEY.exists() or APP_KEY.stat().st_size == 0:
            log("waiting_for_application_api_key")
            time.sleep(30)
            continue
        if not EXEC_KEY.exists() or EXEC_KEY.stat().st_size == 0:
            log("waiting_for_executor_key")
            time.sleep(30)
            continue
        try:
            state = load_state()
            if not state.get("session_id"):
                state = create_session()

            session = retrieve_session(state["session_id"])
            session = ensure_flex(state["session_id"], session)

            env = session.get("environment") or {}
            env_id, remote = env.get("id"), env.get("remote_url")
            if not env_id or not remote:
                raise RuntimeError("self-hosted environment identity/remote_url missing")

            current_env = state.get("environment") or {}
            if current_env.get("id") != env_id or current_env.get("remote_url") != remote:
                stop_stale_executor()
            state["environment"] = {"id": env_id, "remote_url": remote}
            state["agent"] = {
                "model": (session.get("agent") or {}).get("model"),
                "service_tier": (session.get("agent") or {}).get("service_tier"),
            }
            atomic_json(STATE, state)
            start_executor(state["environment"])

            status = session.get("status")
            state["last_status"] = status
            atomic_json(STATE, state)

            # Do not interrupt an active agent turn merely to refresh executor
            # environment. At the next idle boundary, repair the executor
            # atomically and verify that the ISS password-file contract is
            # really visible before any new autonomous turn is submitted.
            if status == "idle" and not executor_has_iss_runtime_env():
                log("iss_runtime_env_missing; recycling idle executor")
                stop_stale_executor()
                start_executor(state["environment"])
                time.sleep(2)
                if not executor_has_iss_runtime_env():
                    raise RuntimeError("ISS runtime environment repair failed")
                log("iss_runtime_env_verified password_file_only=yes")

            if status == "idle":
                if PAUSE_SUBMISSIONS.exists():
                    log("submissions_paused_for_cutover")
                    time.sleep(5)
                    continue

                # Bound conversational history growth. The durable project
                # state, not an ever-growing Agents session, is the continuity
                # authority. Rotate only at a safe idle boundary.
                submit_count = int(state.get("session_submit_count") or 0)
                if submit_count >= SESSION_SUBMIT_BUDGET:
                    old_id = state["session_id"]
                    log(
                        f"session_rotation_due old={old_id} "
                        f"submits={submit_count} budget={SESSION_SUBMIT_BUDGET}"
                    )
                    stop_stale_executor()
                    state = create_session()
                    state["rotation_reason"] = "bounded_context_cost"
                    state["rotated_from_session"] = old_id
                    atomic_json(STATE, state)
                    last_idle_fp = None
                    continue

                fp = progress_fingerprint()
                previous_fp = state.get("last_progress_fingerprint")
                if previous_fp is not None and fp == previous_fp:
                    state["no_progress"] = int(state.get("no_progress") or 0) + 1
                else:
                    state["no_progress"] = 0
                state["last_progress_fingerprint"] = fp
                last_idle_fp = fp
                delay = backoff_seconds(state)
                atomic_json(STATE, state)
                if pending_inputs():
                    delay = 0
                if delay:
                    log(f"idle_backoff seconds={delay} no_progress={state['no_progress']}")
                    time.sleep(delay)
                # Re-read before submitting in case a UI/user message started a turn.
                fresh = retrieve_session(state["session_id"])
                if fresh.get("status") == "idle":
                    send_next_input(state["session_id"], state)
                    state["last_submit_at"] = int(time.time())
                    state["session_submit_count"] = int(state.get("session_submit_count") or 0) + 1
                    atomic_json(STATE, state)
                    time.sleep(10)
            elif status == "in_progress":
                time.sleep(10)
            elif status == "requires_action":
                log("requires_action; preserving state for agent/tool resolution")
                time.sleep(15)
            elif status == "failed":
                err = session.get("error")
                log("session_failed error=" + str(err)[:500])
                time.sleep(60)
            else:
                log(f"unknown_session_status={status!r}")
                time.sleep(30)
        except Exception as e:
            log("controller_error=" + repr(e)[:700])
            time.sleep(30)

if __name__ == "__main__":
    try:
        main()
    finally:
        PIDFILE.unlink(missing_ok=True)
