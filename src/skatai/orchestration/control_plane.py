from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(os.environ.get("SKATAI_V2_REPO", "/workspace/skatai-v2")).resolve()
RUNTIME = Path(os.environ.get("SKATAI_V2_RUNTIME", "/workspace/skatai-v2-runtime")).resolve()
CONTROL = Path(os.environ.get("SKATAI_ORCHESTRATOR_CONTROL", "/var/lib/skatai-orchestrator")).resolve()
TRUSTED = Path(os.environ.get("SKATAI_ORCHESTRATOR_TRUSTED_ROOT", str(REPO))).resolve()
STATE_DIR = Path(os.environ.get("SKATAI_ORCHESTRATOR_STATE", "/workspace/skatai-v2-runtime/orchestrator/state")).resolve()
CONFIG = TRUSTED / "config/ORCHESTRATOR_RUNTIME_POLICY.json" if TRUSTED != REPO else REPO / "configs/orchestration/ORCHESTRATOR_RUNTIME_POLICY.json"
ROLE_REGISTRY = TRUSTED / "config/WORKER_ROLE_REGISTRY.json" if TRUSTED != REPO else REPO / "configs/orchestration/WORKER_ROLE_REGISTRY.json"
SUPERBRAIN_INSTRUCTIONS = TRUSTED / "config/SUPERBRAIN_INSTRUCTIONS.md" if TRUSTED != REPO else REPO / "configs/orchestration/SUPERBRAIN_INSTRUCTIONS.md"
WORKER_INSTRUCTIONS = TRUSTED / "config/WORKER_BASE_INSTRUCTIONS.md" if TRUSTED != REPO else REPO / "configs/orchestration/WORKER_BASE_INSTRUCTIONS.md"
FOUNDING_SPEC = TRUSTED / "authority/SKATAI_V2_FOUNDING_SPECIFICATION.md" if TRUSTED != REPO else REPO / "SKATAI_V2_FOUNDING_SPECIFICATION.md"
WORK_PROMPT = TRUSTED / "authority/SKATAI_V2_WORK_PROMPT.md" if TRUSTED != REPO else REPO / "SKATAI_V2_WORK_PROMPT.md"
ARCHITECTURE = TRUSTED / "config/SKATAI_V2_ORCHESTRATOR_SUPERBRAIN_ARCHITECTURE_v1.1.yaml" if TRUSTED != REPO else REPO / "configs/orchestration/SKATAI_V2_ORCHESTRATOR_SUPERBRAIN_ARCHITECTURE_v1.1.yaml"
API_KEY = Path("/run/skatai-v2-secrets/openai_agents_api_key")
BASE = "https://api.openai.com/v1"

PROJECT_STATE = STATE_DIR / "skatai_v2_project_state.json"
TASKS = STATE_DIR / "task_registry.json"
WORKERS = STATE_DIR / "worker_registry.json"
CAPABILITIES = STATE_DIR / "capability_gap_registry.json"
DECISIONS = STATE_DIR / "decision_registry.jsonl"
EVIDENCE = STATE_DIR / "evidence_registry.jsonl"
EXPERIMENTS = STATE_DIR / "experiment_registry.json"
RELEASES = STATE_DIR / "release_registry.json"
CONTROLLER_STATE = CONTROL / "controller_state.json"
LOG = CONTROL / "controller.log"
PID = CONTROL / "controller.pid"
COST = STATE_DIR / "model_usage_registry.jsonl"

TERMINAL_RESULT_STATES = {"COMPLETE", "PARTIAL", "BLOCKED", "FAILED"}
TASK_STATES = {"READY", "DISPATCH_REQUESTED", "RUNNING", "VERIFYING", "COMPLETE", "FAILED", "BLOCKED", "SUPERSEDED"}
PRIORITIES = {"P0", "P1", "P2", "P3"}


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log(message: str) -> None:
    CONTROL.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{utc_now()} {message}\n")


def strict_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    def pairs(rows):
        out = {}
        for k, v in rows:
            if k in out:
                raise RuntimeError(f"DUPLICATE_JSON_KEY:{k}")
            out[k] = v
        return out
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str, timeout: int = 30) -> str:
    cp = subprocess.run(["git", "-C", str(REPO), *args], text=True, capture_output=True, timeout=timeout)
    if cp.returncode:
        raise RuntimeError(cp.stderr.strip() or f"git rc={cp.returncode}")
    return cp.stdout.strip()


def api(method: str, path: str, body: Any = None, idem: str | None = None) -> dict[str, Any]:
    key = API_KEY.read_text(encoding="utf-8").strip()
    payload = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    req = urllib.request.Request(BASE + path, data=payload, method=method)
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("OpenAI-Beta", "agents=v1")
    req.add_header("Content-Type", "application/json")
    if idem:
        req.add_header("Idempotency-Key", idem)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read(8192))
        except Exception:
            detail = {"error": f"HTTP {exc.code}"}
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {detail}") from exc


def config() -> dict[str, Any]:
    return strict_json(CONFIG)


def roles() -> dict[str, Any]:
    return strict_json(ROLE_REGISTRY)["roles"]


def initial_capabilities() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "updated_at": utc_now(),
        "capabilities": {
            "data_acquisition": "PARTIAL",
            "dataset_validation": "PARTIAL",
            "dataset_versioning_and_provenance": "PARTIAL",
            "challenger_training": "PARTIAL",
            "controlled_scientific_evaluation": "PARTIAL",
            "promotion_rejection_gates": "PARTIAL",
            "iss_external_validation": "PARTIAL",
            "iss_evidence_capture": "PARTIAL",
            "autonomous_runtime": "PARTIAL",
            "release_packaging": "PARTIAL",
            "deployment_validation": "PARTIAL",
            "stable_skatai_interface": "NOT_STARTED",
            "skat_software_integration": "NOT_STARTED",
            "continuous_improvement_loop": "PARTIAL",
            "full_recovery_and_persistence": "PARTIAL",
        },
    }


def ensure_state() -> None:
    CONTROL.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not TASKS.exists():
        atomic_json(TASKS, {"schema_version": "1.0.0", "tasks": {}})
    if not WORKERS.exists():
        atomic_json(WORKERS, {"schema_version": "1.0.0", "workers": {}})
    if not CAPABILITIES.exists():
        atomic_json(CAPABILITIES, initial_capabilities())
    if not EXPERIMENTS.exists():
        atomic_json(EXPERIMENTS, {"schema_version": "1.0.0", "experiments": {}})
    if not RELEASES.exists():
        atomic_json(RELEASES, {"schema_version": "1.0.0", "releases": {}})
    if not PROJECT_STATE.exists():
        atomic_json(PROJECT_STATE, {
            "schema_version": "1.0.0",
            "project_id": "skatai-v2",
            "repository_revision": git("rev-parse", "HEAD"),
            "accepted_model": None,
            "accepted_release": None,
            "active_tasks": [],
            "blocked_tasks": [],
            "completed_tasks": [],
            "capability_states": strict_json(CAPABILITIES)["capabilities"],
            "current_blockers": [],
            "active_experiments": [],
            "active_iss_sessions": [],
            "infrastructure_state": {},
            "storage_state": {},
            "last_verified_at": utc_now(),
            "next_high_value_actions": [
                "Reconcile freshest V2 state after orchestrator cutover",
                "Preserve and integrate currently untracked verified work",
                "Continue highest-value capability gap without repeating completed work",
            ],
        })
    if not CONTROLLER_STATE.exists():
        atomic_json(CONTROLLER_STATE, {
            "schema_version": "1.0.0",
            "superbrain_agent_id": None,
            "superbrain_session_id": None,
            "worker_agent_ids": {},
            "event_seq": 1,
            "last_superbrain_event_seq": 0,
            "bootstrap_sent": False,
            "superbrain_tool_calls": 0,
            "created_at": utc_now(),
        })


def safe_path(raw: str, *, allow_runtime: bool = True) -> tuple[Path, str]:
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeError("PATH_REQUIRED")
    p = Path(raw)
    if not p.is_absolute():
        p = REPO / p
    resolved = p.resolve(strict=False)
    roots = [REPO] + ([RUNTIME] if allow_runtime else [])
    for root in roots:
        try:
            rel = resolved.relative_to(root)
            key = ("repo:" if root == REPO else "runtime:") + rel.as_posix()
            if any(part in {".git"} for part in rel.parts):
                raise RuntimeError("GIT_INTERNAL_PATH_FORBIDDEN")
            if any(s in resolved.name.lower() for s in ("secret", "credential", "api_key", "password")):
                raise RuntimeError("SECRET_LIKE_PATH_FORBIDDEN")
            return resolved, key
        except ValueError:
            continue
    raise RuntimeError("PATH_OUTSIDE_PROJECT")


def task_scope_allows(task: dict[str, Any], mode: str, key: str) -> bool:
    authority = task.get("authority") or {}
    patterns = authority.get(mode) or []
    normalized = key.split(":", 1)[1] if ":" in key else key
    for pat in patterns:
        pat = str(pat)
        if pat.startswith("repo:") or pat.startswith("runtime:"):
            target = key
        else:
            target = normalized
        if fnmatch.fnmatch(target, pat) or target == pat:
            return True
    return False


def bounded_text(path: Path, start: int = 1, end: int | None = None, max_bytes: int = 4000) -> str:
    data = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, int(start or 1))
    end = min(len(data), int(end or min(len(data), start + 500)))
    out = "\n".join(f"{i}: {data[i-1]}" for i in range(start, end + 1))
    raw = out.encode("utf-8")
    if len(raw) > max_bytes:
        out = raw[:max_bytes].decode("utf-8", "ignore") + "\n...[truncated]"
    return out


def read_text_tool(args: dict[str, Any], task: dict[str, Any] | None = None) -> dict[str, Any]:
    p, key = safe_path(args["path"])
    if task is not None and not task_scope_allows(task, "read", key):
        raise RuntimeError(f"READ_NOT_AUTHORIZED:{key}")
    if not p.is_file():
        raise RuntimeError("NOT_A_FILE")
    return {"path": key, "text": bounded_text(p, args.get("start_line", 1), args.get("end_line"))}


def list_paths_tool(args: dict[str, Any], task: dict[str, Any] | None = None) -> dict[str, Any]:
    root, key = safe_path(args["path"])
    if task is not None and not task_scope_allows(task, "read", key):
        raise RuntimeError(f"LIST_NOT_AUTHORIZED:{key}")
    depth = min(4, max(0, int(args.get("depth", 2))))
    glob = str(args.get("glob") or "*")
    out = []
    base_depth = len(root.parts)
    if root.is_file():
        return {"paths": [key]}
    for p in root.rglob("*"):
        if len(p.parts) - base_depth > depth:
            continue
        if p.name.startswith(".") or ".git" in p.parts:
            continue
        if not fnmatch.fnmatch(p.name, glob):
            continue
        try:
            _, pkey = safe_path(str(p))
        except RuntimeError:
            continue
        if task is not None and not task_scope_allows(task, "read", pkey):
            continue
        out.append(pkey + ("/" if p.is_dir() else ""))
        if len(out) >= 80:
            break
    return {"paths": out, "truncated": len(out) >= 80}


def search_text_tool(args: dict[str, Any], task: dict[str, Any] | None = None) -> dict[str, Any]:
    root, key = safe_path(args["path"])
    if task is not None and not task_scope_allows(task, "read", key):
        raise RuntimeError(f"SEARCH_NOT_AUTHORIZED:{key}")
    query = str(args["query"])
    if not query:
        raise RuntimeError("QUERY_REQUIRED")
    glob = str(args.get("glob") or "*")
    max_results = min(20, max(1, int(args.get("max_results", 20))))
    candidates = [root] if root.is_file() else root.rglob(glob)
    out = []
    for p in candidates:
        if not p.is_file() or ".git" in p.parts:
            continue
        try:
            _, pkey = safe_path(str(p))
        except RuntimeError:
            continue
        if task is not None and not task_scope_allows(task, "read", pkey):
            continue
        try:
            for no, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if query.lower() in line.lower():
                    out.append({"path": pkey, "line": no, "text": line[:200]})
                    if len(out) >= max_results:
                        return {"matches": out, "truncated": True}
        except OSError:
            pass
    return {"matches": out, "truncated": False}


def project_snapshot() -> dict[str, Any]:
    ps = strict_json(PROJECT_STATE)
    tasks = strict_json(TASKS)["tasks"]
    try:
        processes = subprocess.check_output(
            ["ps", "-eo", "pid,etimes,args", "--sort=pid"], text=True, timeout=10
        ).splitlines()
        processes = [
            row.strip() for row in processes
            if any(x in row for x in ("skatai", "gate_worker", "supervise_frozen_r9", "runpod_cpu_upgrade_hunter"))
            and "orchestrator_superbrain_controller" not in row
        ][:80]
    except Exception:
        processes = []
    return {
        "time": utc_now(),
        "git_head": git("rev-parse", "HEAD"),
        "git_status": git("status", "--short"),
        "project_state": ps,
        "tasks": {
            "active": [k for k, v in tasks.items() if v.get("state") in {"READY", "DISPATCH_REQUESTED", "RUNNING", "VERIFYING"}],
            "blocked": [k for k, v in tasks.items() if v.get("state") == "BLOCKED"],
            "complete": [k for k, v in tasks.items() if v.get("state") == "COMPLETE"][-30:],
        },
        "worker_roles": list(roles()),
        "runtime_processes": processes,
        "authority_hashes": {
            "founding_spec": sha256(FOUNDING_SPEC),
            "work_prompt": sha256(WORK_PROMPT),
            "architecture": sha256(ARCHITECTURE),
        },
    }


def validate_write_scopes(task: dict[str, Any]) -> None:
    for raw in task.get("authority", {}).get("write", []):
        value = str(raw)
        if value.startswith("repo:"):
            value = value[5:]
        if value.startswith("runtime:") or any(ch in value for ch in "*?[]") or value in {"", "."} or value.endswith("/"):
            raise RuntimeError("WRITE_SCOPE_MUST_BE_EXACT_REPO_FILE")
        if value.startswith("/") or ".." in Path(value).parts:
            raise RuntimeError("WRITE_SCOPE_MUST_BE_REPO_RELATIVE")
        if value.startswith(("configs/orchestration/", "state/", "src/skatai/orchestration/",
                             "scripts/orchestrator_", "scripts/install_orchestrator_runtime.sh")):
            raise RuntimeError("CONTROL_PLANE_WRITE_REQUIRES_DIRECT_ORCHESTRATOR_CHANGE")
        if (REPO / value).is_dir():
            raise RuntimeError("WRITE_SCOPE_MUST_BE_EXACT_REPO_FILE")


def validate_task_contract(task: dict[str, Any]) -> dict[str, Any]:
    required = [
        "task_id", "task_family", "assigned_agent", "priority", "global_goal_reference",
        "work_prompt_capability_reference", "current_gap", "objective", "rationale", "scope",
        "authority", "evidence_requirements", "success_criteria", "execution_profile",
    ]
    missing = [k for k in required if not task.get(k)]
    if missing:
        raise RuntimeError("TASK_MISSING:" + ",".join(missing))
    tid = str(task["task_id"])
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,120}", tid):
        raise RuntimeError("TASK_ID_INVALID")
    if task["assigned_agent"] not in roles():
        raise RuntimeError("UNKNOWN_WORKER_ROLE")
    if task["priority"] not in PRIORITIES:
        raise RuntimeError("PRIORITY_INVALID")
    authority = task["authority"]
    for k in ("read", "write", "execute", "forbidden"):
        if not isinstance(authority.get(k, []), list):
            raise RuntimeError(f"AUTHORITY_{k.upper()}_INVALID")
    validate_write_scopes(task)
    if any(not isinstance(dep, str) or dep == tid for dep in task.get("dependencies", [])):
        raise RuntimeError("DEPENDENCIES_INVALID")
    profile = task["execution_profile"]
    mode = profile.get("preferred_execution_mode", "very_low_cost_model")
    if mode not in {"deterministic", "very_low_cost_model", "low_cost_model", "mid_cost_model", "orchestrator_only"}:
        raise RuntimeError("EXECUTION_MODE_INVALID")
    max_class = profile.get("max_cost_class")
    expected = {"deterministic": "deterministic", "very_low_cost_model": "very_low", "low_cost_model": "low", "mid_cost_model": "mid"}
    if mode in expected and max_class != expected[mode]:
        raise RuntimeError("EXECUTION_COST_CLASS_MISMATCH")
    if profile.get("model_escalation_requires_orchestrator") is not True:
        raise RuntimeError("MODEL_ESCALATION_POLICY_INVALID")
    if mode == "deterministic" and len(authority.get("execute", [])) != 1:
        raise RuntimeError("DETERMINISTIC_REQUIRES_ONE_COMMAND")
    if mode == "orchestrator_only":
        raise RuntimeError("ORCHESTRATOR_ONLY_TASK_CANNOT_USE_WORKER")
    if mode == "mid_cost_model" and not profile.get("escalation_reason"):
        raise RuntimeError("MID_COST_REQUIRES_ESCALATION_REASON")
    now = utc_now()
    task = json.loads(json.dumps(task))
    task["state"] = "READY"
    task.setdefault("parent_task_id", None)
    task["created_at"] = now
    task["updated_at"] = now
    task["result_path"] = str(result_file(tid))
    return task


def command_input_hashes(task: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for argv in task.get("authority", {}).get("execute", []):
        for arg in argv:
            if not isinstance(arg, str) or arg.startswith("-"):
                continue
            p = Path(arg)
            p = p if p.is_absolute() else REPO / p
            try:
                p = p.resolve(strict=True)
                p.relative_to(REPO)
                if p.is_file():
                    result[str(p.relative_to(REPO))] = sha256(p)
            except (OSError, ValueError):
                continue
    return result


def create_task(task: dict[str, Any]) -> dict[str, Any]:
    reg = strict_json(TASKS)
    normalized = validate_task_contract(task)
    tid = normalized["task_id"]
    commands = normalized["authority"].get("execute", [])
    hashes = command_input_hashes(normalized)
    if commands:
        for prior in reg["tasks"].values():
            if prior.get("state") in {"BLOCKED", "COMPLETE", "VERIFYING", "RUNNING", "DISPATCH_REQUESTED"} and prior.get("authority", {}).get("execute") == commands:
                if not prior.get("command_input_hashes") or prior["command_input_hashes"] == hashes:
                    raise RuntimeError("DUPLICATE_BLOCKED_WORK" if prior.get("state") == "BLOCKED" else "DUPLICATE_WORK")
    normalized["command_input_hashes"] = hashes
    if tid in reg["tasks"] and reg["tasks"][tid].get("state") not in {"FAILED", "BLOCKED", "SUPERSEDED"}:
        raise RuntimeError("TASK_ALREADY_EXISTS")
    reg["tasks"][tid] = normalized
    atomic_json(TASKS, reg)
    return normalized


def dispatch_task(tid: str) -> dict[str, Any]:
    reg = strict_json(TASKS)
    task = reg["tasks"].get(tid)
    if not task:
        raise RuntimeError("TASK_NOT_FOUND")
    if task["state"] != "READY":
        raise RuntimeError(f"TASK_NOT_READY:{task['state']}")
    validate_write_scopes(task)
    deps = task.get("dependencies", [])
    if any(reg["tasks"].get(dep, {}).get("state") != "COMPLETE" for dep in deps):
        raise RuntimeError("DEPENDENCIES_NOT_COMPLETE")
    task["state"] = "DISPATCH_REQUESTED"
    task["updated_at"] = utc_now()
    reg["tasks"][tid] = task
    atomic_json(TASKS, reg)
    bump_event("task_dispatch_requested", tid)
    return {"task_id": tid, "state": task["state"]}


def result_file(tid: str) -> Path:
    return STATE_DIR / "results" / f"{tid}.json"


def accept_worker_result(tid: str, accepted: bool, reason: str) -> dict[str, Any]:
    reg = strict_json(TASKS)
    task = reg["tasks"].get(tid)
    if not task:
        raise RuntimeError("TASK_NOT_FOUND")
    rp = result_file(tid)
    if not rp.is_file():
        raise RuntimeError("WORKER_RESULT_MISSING")
    result = strict_json(rp)
    task["state"] = "COMPLETE" if accepted and result.get("status") == "COMPLETE" else ("BLOCKED" if result.get("status") == "BLOCKED" else "FAILED")
    task["updated_at"] = utc_now()
    task["integration_decision"] = {"accepted": bool(accepted), "reason": str(reason), "at": utc_now()}
    reg["tasks"][tid] = task
    atomic_json(TASKS, reg)
    append_jsonl(DECISIONS, {
        "time": utc_now(), "type": "worker_result_integration", "task_id": tid,
        "accepted": bool(accepted), "reason": str(reason), "result_sha256": sha256(rp),
    })
    refresh_project_state()
    return {"task_id": tid, "state": task["state"]}


def refresh_project_state() -> None:
    ps = strict_json(PROJECT_STATE)
    reg = strict_json(TASKS)["tasks"]
    ps["repository_revision"] = git("rev-parse", "HEAD")
    ps["active_tasks"] = [k for k, v in reg.items() if v.get("state") in {"READY", "DISPATCH_REQUESTED", "RUNNING", "VERIFYING"}]
    ps["blocked_tasks"] = [k for k, v in reg.items() if v.get("state") == "BLOCKED"]
    ps["completed_tasks"] = [k for k, v in reg.items() if v.get("state") == "COMPLETE"]
    ps["capability_states"] = strict_json(CAPABILITIES)["capabilities"]
    ps["last_verified_at"] = utc_now()
    atomic_json(PROJECT_STATE, ps)


def decision_file_size() -> int:
    return DECISIONS.stat().st_size if DECISIONS.exists() else 0


def integrated_since_session_start(state: dict[str, Any]) -> bool:
    return decision_file_size() > int(state.get("session_start_decision_bytes", 0))


def persist_usage(session: dict[str, Any], role: str, tool_calls: int, outcome: str) -> None:
    row = {"time": utc_now(), "session_id": session["id"], "role": role,
           "model": session.get("agent", {}).get("model"), "tool_calls": tool_calls,
           "outcome": outcome, "usage": session.get("usage")}
    append_jsonl(COST, row)


def bump_event(kind: str, subject: str) -> None:
    st = strict_json(CONTROLLER_STATE)
    st["event_seq"] = int(st.get("event_seq", 0)) + 1
    st["last_event"] = {"kind": kind, "subject": subject, "time": utc_now()}
    atomic_json(CONTROLLER_STATE, st)


def orchestration_tools() -> list[dict[str, Any]]:
    task_contract = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "parent_task_id": {"type": ["string", "null"]},
            "task_family": {"type": "string"},
            "assigned_agent": {"type": "string", "enum": sorted(roles())},
            "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
            "global_goal_reference": {"type": "array", "items": {"type": "string"}},
            "work_prompt_capability_reference": {"type": "array", "items": {"type": "string"}},
            "current_gap": {"type": "string"},
            "objective": {"type": "string"},
            "rationale": {"type": "string"},
            "scope": {"type": "array", "items": {"type": "string"}},
            "non_goals": {"type": "array", "items": {"type": "string"}},
            "dependencies": {"type": "array", "items": {"type": "string"}},
            "required_inputs": {"type": "array", "items": {"type": "string"}},
            "authority": {
                "type": "object",
                "properties": {
                    "read": {"type": "array", "items": {"type": "string"}},
                    "write": {"type": "array", "items": {"type": "string"}},
                    "execute": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                    "forbidden": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["read", "write", "execute", "forbidden"],
                "additionalProperties": False,
            },
            "evidence_requirements": {"type": "array", "items": {"type": "string"}},
            "success_criteria": {"type": "array", "items": {"type": "string"}},
            "rollback_requirements": {"type": "array", "items": {"type": "string"}},
            "execution_profile": {
                "type": "object",
                "properties": {
                    "preferred_execution_mode": {"type": "string", "enum": ["very_low_cost_model", "low_cost_model", "mid_cost_model"]},
                    "reasoning_effort": {"type": "string", "enum": ["minimal", "low", "medium"]},
                    "max_cost_class": {"type": "string", "enum": ["very_low", "low", "mid"]},
                    "model_escalation_requires_orchestrator": {"type": "boolean"},
                    "escalation_reason": {"type": ["string", "null"]},
                },
                "required": ["preferred_execution_mode", "reasoning_effort", "max_cost_class", "model_escalation_requires_orchestrator"],
                "additionalProperties": False,
            },
        },
        "required": [
            "task_id", "task_family", "assigned_agent", "priority", "global_goal_reference",
            "work_prompt_capability_reference", "current_gap", "objective", "rationale", "scope",
            "authority", "evidence_requirements", "success_criteria", "execution_profile"
        ],
        "additionalProperties": False,
    }
    return [
        {"type": "function", "name": "get_project_snapshot", "description": "Read a compact current repository/runtime/task snapshot and authority hashes.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
        {"type": "function", "name": "read_text", "description": "Read bounded project/runtime text.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "required": ["path"], "additionalProperties": False}},
        {"type": "function", "name": "search_text", "description": "Search project/runtime text for a literal string.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "query": {"type": "string"}, "glob": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["path", "query"], "additionalProperties": False}},
        {"type": "function", "name": "list_paths", "description": "List project/runtime paths.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "depth": {"type": "integer"}, "glob": {"type": "string"}}, "required": ["path"], "additionalProperties": False}},
        {"type": "function", "name": "list_tasks", "description": "Read task registry, optionally filtered by state.", "parameters": {"type": "object", "properties": {"state": {"type": ["string", "null"]}}, "additionalProperties": False}},
        {"type": "function", "name": "create_task", "description": "Create one bounded worker task contract. Workers never broaden it.", "parameters": {"type": "object", "properties": {"contract": task_contract}, "required": ["contract"], "additionalProperties": False}},
        {"type": "function", "name": "dispatch_task", "description": "Request deterministic controller dispatch of a READY worker task.", "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"], "additionalProperties": False}},
        {"type": "function", "name": "read_worker_result", "description": "Read a persisted worker result for verification.", "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"], "additionalProperties": False}},
        {"type": "function", "name": "accept_worker_result", "description": "Accept or reject a worker result after evidence review.", "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}, "accepted": {"type": "boolean"}, "reason": {"type": "string"}}, "required": ["task_id", "accepted", "reason"], "additionalProperties": False}},
        {"type": "function", "name": "record_decision", "description": "Append a durable orchestrator decision record.", "parameters": {"type": "object", "properties": {"decision_type": {"type": "string"}, "subject": {"type": "string"}, "decision": {"type": "string"}, "evidence": {"type": "array", "items": {"type": "string"}}}, "required": ["decision_type", "subject", "decision", "evidence"], "additionalProperties": False}},
    ]


def worker_tools() -> list[dict[str, Any]]:
    return [
        {"type": "function", "name": "read_text", "description": "Read only task-authorized project/runtime text.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "required": ["path"], "additionalProperties": False}},
        {"type": "function", "name": "search_text", "description": "Search only task-authorized project/runtime text.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "query": {"type": "string"}, "glob": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["path", "query"], "additionalProperties": False}},
        {"type": "function", "name": "list_paths", "description": "List only task-authorized paths.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "depth": {"type": "integer"}, "glob": {"type": "string"}}, "required": ["path"], "additionalProperties": False}},
        {"type": "function", "name": "git_status", "description": "Read current Git head/status/diff summary.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
        {"type": "function", "name": "apply_replacements", "description": "Apply exact authorized text replacements atomically; each old_text must occur exactly once.", "parameters": {"type": "object", "properties": {"replacements": {"type": "array", "minItems": 1, "maxItems": 16, "items": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"], "additionalProperties": False}}}, "required": ["replacements"], "additionalProperties": False}},
        {"type": "function", "name": "write_text", "description": "Write one explicitly authorized text file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False}},
        {"type": "function", "name": "run_command", "description": "Run one exact argv command explicitly present in the task contract; no shell expansion.", "parameters": {"type": "object", "properties": {"argv": {"type": "array", "items": {"type": "string"}}, "timeout_seconds": {"type": "integer"}}, "required": ["argv"], "additionalProperties": False}},
        {"type": "function", "name": "submit_worker_result", "description": "Persist the final structured worker result. Call exactly once before finishing.", "parameters": {"type": "object", "properties": {"result": {"type": "object", "properties": {"status": {"type": "string", "enum": ["COMPLETE", "PARTIAL", "BLOCKED", "FAILED"]}, "observations": {"type": "array", "items": {"type": "string"}}, "hypotheses": {"type": "array", "items": {"type": "string"}}, "confirmed_causes": {"type": "array", "items": {"type": "string"}}, "changes": {"type": "array", "items": {"type": "string"}}, "evidence": {"type": "array", "items": {"type": "string"}}, "verification": {"type": "array", "items": {"type": "string"}}, "unresolved": {"type": "array", "items": {"type": "string"}}, "risks": {"type": "array", "items": {"type": "string"}}, "artifacts": {"type": "array", "items": {"type": "string"}}, "recommended_followups": {"type": "array", "items": {"type": "string"}}}, "required": ["status", "observations", "changes", "evidence", "verification", "unresolved"], "additionalProperties": False}}, "required": ["result"], "additionalProperties": False}},
    ]


def run_exact_worker_command(task: dict[str, Any], argv: list[str], timeout: int) -> dict[str, Any]:
    allowed = task.get("authority", {}).get("execute", [])
    if argv not in allowed:
        raise RuntimeError("COMMAND_NOT_AUTHORIZED")
    if not argv or argv[0] in {"sudo", "su", "ssh", "scp", "curl", "wget", "rm"}:
        raise RuntimeError("COMMAND_FORBIDDEN")
    timeout = min(int(timeout or 300), 900)
    worker_env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LC_ALL": "C.UTF-8",
        "PYTHONPATH": str(REPO / "src"), "PYTHONDONTWRITEBYTECODE": "1", "TZ": "UTC",
    }
    cp = subprocess.run(argv, cwd=REPO, text=True, capture_output=True, timeout=timeout,
                        user="sentinelx", group="sentinelx", env=worker_env)
    out = (cp.stdout + ("\nSTDERR:\n" + cp.stderr if cp.stderr else ""))[-60000:]
    return {"returncode": cp.returncode, "output": out}


def apply_replacements(task: dict[str, Any], replacements: list[dict[str, str]]) -> dict[str, Any]:
    staged: list[tuple[Path, str, str]] = []
    originals: dict[Path, str] = {}
    for row in replacements:
        p, key = safe_path(row["path"], allow_runtime=False)
        if not task_scope_allows(task, "write", key):
            raise RuntimeError(f"WRITE_NOT_AUTHORIZED:{key}")
        old = row["old_text"]
        new = row["new_text"]
        text = p.read_text(encoding="utf-8")
        if text.count(old) != 1:
            raise RuntimeError(f"REPLACEMENT_MATCH_COUNT:{key}:{text.count(old)}")
        originals[p] = text
        staged.append((p, text, text.replace(old, new, 1)))
    try:
        for p, _, new_text in staged:
            fd, tmp = tempfile.mkstemp(prefix="." + p.name + ".", dir=p.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(new_text); f.flush(); os.fsync(f.fileno())
            os.replace(tmp, p)
    except Exception:
        for p, text in originals.items():
            p.write_text(text, encoding="utf-8")
        raise
    return {"changed": [str(p.relative_to(REPO)) for p, _, _ in staged]}


def write_text_worker(task: dict[str, Any], raw: str, content: str) -> dict[str, Any]:
    p, key = safe_path(raw, allow_runtime=False)
    if not task_scope_allows(task, "write", key):
        raise RuntimeError(f"WRITE_NOT_AUTHORIZED:{key}")
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + p.name + ".", dir=p.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, p)
    return {"changed": str(p.relative_to(REPO)), "bytes": len(content.encode())}


def submit_worker_result(task: dict[str, Any], worker_id: str, session_id: str, result: dict[str, Any]) -> dict[str, Any]:
    if result.get("status") not in TERMINAL_RESULT_STATES:
        raise RuntimeError("WORKER_RESULT_STATUS_INVALID")
    if worker_id != task["assigned_agent"]:
        raise RuntimeError("WORKER_RESULT_IDENTITY_MISMATCH")
    required = ("observations", "changes", "evidence", "verification", "unresolved")
    if any(not isinstance(result.get(k), list) for k in required):
        raise RuntimeError("WORKER_RESULT_SCHEMA_INVALID")
    if result["status"] == "COMPLETE" and (not result["evidence"] or not result["verification"]):
        raise RuntimeError("WORKER_RESULT_EVIDENCE_MISSING")
    tid = task["task_id"]
    rp = result_file(tid)
    if rp.exists():
        raise RuntimeError("WORKER_RESULT_ALREADY_SUBMITTED")
    row = {
        "task_id": tid,
        "worker_id": worker_id,
        "worker_session_id": session_id,
        **result,
        "repository_revision": git("rev-parse", "HEAD"),
        "dirty_worktree": bool(git("status", "--porcelain")),
        "completed_at": utc_now(),
    }
    atomic_json(rp, row)
    append_jsonl(EVIDENCE, {"time": utc_now(), "type": "worker_result", "task_id": tid, "path": str(rp), "sha256": sha256(rp)})
    return {"persisted": str(rp), "sha256": sha256(rp)}


def dispatch_tool(name: str, args: dict[str, Any], *, kind: str, task: dict[str, Any] | None = None, worker_id: str | None = None, session_id: str | None = None) -> Any:
    if kind == "orchestrator":
        if name == "get_project_snapshot":
            return project_snapshot()
        if name == "read_text":
            return read_text_tool(args)
        if name == "search_text":
            return search_text_tool(args)
        if name == "list_paths":
            return list_paths_tool(args)
        if name == "list_tasks":
            reg = strict_json(TASKS)["tasks"]
            state = args.get("state")
            return {"tasks": {k: v for k, v in reg.items() if not state or v.get("state") == state}}
        if name == "create_task":
            return create_task(args["contract"])
        if name == "dispatch_task":
            return dispatch_task(args["task_id"])
        if name == "read_worker_result":
            rp = result_file(args["task_id"])
            return strict_json(rp) if rp.exists() else {"missing": True}
        if name == "accept_worker_result":
            return accept_worker_result(args["task_id"], args["accepted"], args["reason"])
        if name == "record_decision":
            row = {"time": utc_now(), **args}
            append_jsonl(DECISIONS, row)
            return {"recorded": True}
        raise RuntimeError("UNKNOWN_ORCHESTRATOR_TOOL:" + name)
    if task is None or worker_id is None or session_id is None:
        raise RuntimeError("WORKER_CONTEXT_MISSING")
    if name == "read_text":
        return read_text_tool(args, task)
    if name == "search_text":
        return search_text_tool(args, task)
    if name == "list_paths":
        return list_paths_tool(args, task)
    if name == "git_status":
        return {"head": git("rev-parse", "HEAD"), "status": git("status", "--short"), "diff_stat": git("diff", "--stat")}
    if name == "apply_replacements":
        return apply_replacements(task, args["replacements"])
    if name == "write_text":
        return write_text_worker(task, args["path"], args["content"])
    if name == "run_command":
        return run_exact_worker_command(task, args["argv"], args.get("timeout_seconds", 300))
    if name == "submit_worker_result":
        return submit_worker_result(task, worker_id, session_id, args["result"])
    raise RuntimeError("UNKNOWN_WORKER_TOOL:" + name)


def agent_config(model: str, instructions: str, tools: list[dict[str, Any]], *, reasoning: str | None, verbosity: str, service_tier: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "service_tier": service_tier,
        "text": {"verbosity": verbosity},
        "tools": tools,
        "multi_agent": {"enabled": False},
    }
    if reasoning:
        body["reasoning"] = {"effort": reasoning}
    return body


def ensure_agents(state: dict[str, Any]) -> dict[str, Any]:
    cfg = config()
    sb = cfg["superbrain"]
    sb_config = agent_config(sb["model"], SUPERBRAIN_INSTRUCTIONS.read_text(), orchestration_tools(), reasoning=sb["reasoning_effort"], verbosity=sb["text_verbosity"], service_tier=sb["service_tier"])
    aid = state.get("superbrain_agent_id")
    if aid:
        try:
            a = api("POST", f"/agents/{aid}", sb_config)
            if a.get("model") != sb["model"]:
                raise RuntimeError("SUPERBRAIN_MODEL_MISMATCH")
        except Exception:
            aid = None
    if not aid:
        a = api("POST", "/agents", {"name": sb["name"], **sb_config, "metadata": {"project": "SkatAI-V2", "role": "ORCHESTRATOR_SUPERBRAIN"}})
        aid = a["id"]
        state["superbrain_agent_id"] = aid
        atomic_json(CONTROLLER_STATE, state)
        log(f"superbrain_agent_created id={aid} model={a.get('model')}")
    worker_ids = dict(state.get("worker_agent_ids") or {})
    wcfg = cfg["workers"]
    base = WORKER_INSTRUCTIONS.read_text()
    for rid, role in roles().items():
        instructions = base + "\n\nROLE_ID=" + rid + "\nROLE=" + role["role"] + "\nRESPONSIBILITIES=" + ", ".join(role["responsibilities"]) + "\n"
        acfg = agent_config(wcfg["default_model"], instructions, worker_tools(), reasoning=wcfg.get("default_reasoning_effort"), verbosity=wcfg["text_verbosity"], service_tier=wcfg["service_tier"])
        wid = worker_ids.get(rid)
        if wid:
            try:
                a = api("POST", f"/agents/{wid}", acfg)
                if a.get("model") != wcfg["default_model"]:
                    raise RuntimeError("WORKER_MODEL_MISMATCH")
            except Exception:
                wid = None
        if not wid:
            a = api("POST", "/agents", {"name": f"SkatAI V2 WORKER {rid}", **acfg, "metadata": {"project": "SkatAI-V2", "role": role["role"], "worker_id": rid}})
            wid = a["id"]
            worker_ids[rid] = wid
            state["worker_agent_ids"] = worker_ids
            atomic_json(CONTROLLER_STATE, state)
            log(f"worker_agent_created role={rid} id={wid} model={a.get('model')}")
    state["worker_agent_ids"] = worker_ids
    atomic_json(CONTROLLER_STATE, state)
    return state


def create_session(agent_id: str, model: str, service_tier: str, text: str, *, reasoning: str | None, metadata: dict[str, str], idem_seed: str) -> dict[str, Any]:
    agent_override: dict[str, Any] = {"model": model, "service_tier": service_tier}
    if reasoning:
        agent_override["reasoning"] = {"effort": reasoning}
    body = {
        "agent_id": agent_id,
        "agent": agent_override,
        "environment": {"type": "none"},
        "input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}],
        "metadata": metadata,
    }
    idem = hashlib.sha256(idem_seed.encode()).hexdigest()
    return api("POST", "/agents/sessions", body, idem=idem)


def send_message(session_id: str, text: str, seed: str) -> None:
    body = {"events": [{"type": "agent.session.input.message", "input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}]}]}
    api("POST", f"/agents/sessions/{session_id}/events", body, idem=hashlib.sha256(seed.encode()).hexdigest())


def cancel_session(session_id: str, reason: str) -> None:
    try:
        api("POST", f"/agents/sessions/{session_id}/events", {"events": [{"type": "agent.session.input.cancel"}]}, idem=hashlib.sha256((session_id + reason).encode()).hexdigest())
    except Exception:
        pass


def delete_session(session_id: str) -> None:
    try:
        api("DELETE", f"/agents/sessions/{session_id}")
    except Exception as exc:
        log(f"session_delete_failed id={session_id} err={exc!r}")


def resolve_required_actions(session: dict[str, Any], *, kind: str, task: dict[str, Any] | None = None, worker_id: str | None = None) -> None:
    sid = session["id"]
    actions = session.get("required_actions") or []
    if not actions:
        raise RuntimeError("REQUIRES_ACTION_WITHOUT_ACTIONS")
    events = []
    for action in actions:
        if action.get("type") != "function_call":
            raise RuntimeError("UNEXPECTED_ACTION_TYPE")
        args = action.get("arguments")
        if isinstance(args, str):
            args = json.loads(args)
        if not isinstance(args, dict):
            raise RuntimeError("TOOL_ARGS_INVALID")
        name = str(action.get("name") or "")
        try:
            payload = dispatch_tool(name, args, kind=kind, task=task, worker_id=worker_id, session_id=sid)
            success = True
            output = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        except Exception as exc:
            success = False
            output = json.dumps({"error": type(exc).__name__, "message": str(exc)[:4000]}, sort_keys=True)
        events.append({
            "type": "agent.session.input.tool_result",
            "turn_id": action.get("turn_id"),
            "call_id": action.get("call_id"),
            "success": success,
            "output": output,
        })
    seed = "tools:" + sid + ":" + ":".join(str(e["call_id"]) for e in events)
    api("POST", f"/agents/sessions/{sid}/events", {"events": events}, idem=hashlib.sha256(seed.encode()).hexdigest())


def superbrain_bootstrap_text() -> str:
    return (
        "Bootstrap a single real, bounded V2 work order within at most six tool calls. "
        "First use get_project_snapshot and list_tasks. Reuse existing READY tasks if valid; "
        "reject or revise any contract with a broad write scope. The R9 ISS gate worker is running; do not restart it. "
        "The founding specification requires evidence over claims. The Work Prompt seeks the full V2 product. "
        "A focused deterministic validation of pending JSkat source portability is a candidate if still unverified. "
        "Dispatch one safe, nonconflicting task with exact command and minimum context. "
        "Do not browse the entire repository first. On result, verify, integrate, persist, then continue."
    )


def ensure_superbrain_session(state: dict[str, Any]) -> dict[str, Any]:
    cfg = config()["superbrain"]
    sid = state.get("superbrain_session_id")
    if sid:
        try:
            api("GET", f"/agents/sessions/{sid}")
            return state
        except Exception:
            state["superbrain_session_id"] = None
    s = create_session(
        state["superbrain_agent_id"], cfg["model"], cfg["service_tier"], superbrain_bootstrap_text(),
        reasoning=cfg["reasoning_effort"],
        metadata={"project": "SkatAI-V2", "role": "ORCHESTRATOR_SUPERBRAIN"},
        idem_seed="superbrain-bootstrap:" + str(state.get("event_seq", 0)),
    )
    state["superbrain_session_id"] = s["id"]
    state["bootstrap_sent"] = True
    state["superbrain_tool_calls"] = 0
    state["session_start_decision_bytes"] = decision_file_size()
    state["last_superbrain_event_seq"] = int(state.get("event_seq", 0))
    atomic_json(CONTROLLER_STATE, state)
    log(f"superbrain_session_created id={s['id']} model={cfg['model']} reasoning={cfg['reasoning_effort']}")
    return state


def task_model(task: dict[str, Any]) -> tuple[str, str | None]:
    cfg = config()["workers"]
    mode = task.get("execution_profile", {}).get("preferred_execution_mode", "very_low_cost_model")
    if mode == "deterministic":
        return None, None
    if mode == "mid_cost_model":
        if not task.get("execution_profile", {}).get("escalation_reason"):
            raise RuntimeError("MID_COST_ESCALATION_REASON_REQUIRED")
        row = cfg["authorized_escalation_tiers"]["mid_local"]
        return row["model"], row.get("reasoning_effort")
    if mode == "low_cost_model" and task.get("execution_profile", {}).get("escalation_reason"):
        row = cfg["authorized_escalation_tiers"]["low_reasoning"]
        return row["model"], row.get("reasoning_effort")
    return cfg["default_model"], cfg.get("default_reasoning_effort")


def launch_ready_workers(state: dict[str, Any]) -> None:
    cfg = config()["workers"]
    reg = strict_json(TASKS)
    wr = strict_json(WORKERS)
    running = sum(1 for v in wr["workers"].values() if v.get("state") == "RUNNING")
    capacity = max(0, int(cfg["max_concurrent_sessions"]) - running)
    if capacity <= 0:
        return
    candidates = sorted(
        (v for v in reg["tasks"].values() if v.get("state") == "DISPATCH_REQUESTED"),
        key=lambda x: (x.get("priority", "P3"), x.get("created_at", "")),
    )
    active_writes = {
        path for t in reg["tasks"].values() if t.get("state") == "RUNNING"
        for path in t.get("authority", {}).get("write", [])
    }
    for task in candidates:
        if capacity <= 0 and task["execution_profile"]["preferred_execution_mode"] != "deterministic":
            continue
        if set(task.get("authority", {}).get("write", [])) & active_writes:
            continue
        if any(reg["tasks"].get(dep, {}).get("state") != "COMPLETE" for dep in task.get("dependencies", [])):
            continue
        tid = task["task_id"]
        if task["execution_profile"]["preferred_execution_mode"] == "deterministic":
            argv = task["authority"]["execute"][0]
            try:
                outcome = run_exact_worker_command(task, argv, min(300, cfg["max_task_runtime_seconds"]))
                status = "COMPLETE" if outcome["returncode"] == 0 else "FAILED"
                reason = "exact authorized command exited " + str(outcome["returncode"])
            except Exception as exc:
                outcome = {"error": str(exc)[:1000]}
                status, reason = "FAILED", "exact authorized command failed"
            rp = result_file(tid)
            atomic_json(rp, {"task_id": tid, "worker_id": task["assigned_agent"],
                "worker_session_id": None, "status": status, "observations": [reason],
                "changes": [], "evidence": [outcome], "verification": [reason] if status == "COMPLETE" else [],
                "unresolved": [] if status == "COMPLETE" else [reason], "completed_at": utc_now()})
            append_jsonl(EVIDENCE, {"time": utc_now(), "type": "deterministic_result", "task_id": tid, "sha256": sha256(rp)})
            task["state"] = "VERIFYING"
            task["updated_at"] = utc_now()
            reg["tasks"][tid] = task
            bump_event("worker_result", tid)
            continue
        capacity -= 1
        active_writes.update(task.get("authority", {}).get("write", []))
        rid = task["assigned_agent"]
        model, reasoning = task_model(task)
        if model != cfg["default_model"] and not task.get("execution_profile", {}).get("escalation_reason"):
            raise RuntimeError("WORKER_ESCALATION_NOT_AUTHORIZED")
        text = (
            "Execute this task contract exactly. It is your complete authority and context boundary.\n\n"
            + json.dumps(task, indent=2, sort_keys=True)
            + "\n\nReturn evidence through submit_worker_result. Do not broaden scope."
        )
        s = create_session(
            state["worker_agent_ids"][rid], model, cfg["service_tier"], text,
            reasoning=reasoning,
            metadata={"project": "SkatAI-V2", "role": roles()[rid]["role"], "task_id": tid},
            idem_seed=f"worker:{tid}:{task.get('updated_at')}",
        )
        wr["workers"][tid] = {
            "task_id": tid, "worker_id": rid, "agent_id": state["worker_agent_ids"][rid],
            "session_id": s["id"], "model": model, "reasoning_effort": reasoning,
            "state": "RUNNING", "started_epoch": time.time(), "started_at": utc_now(), "tool_calls": 0,
        }
        task["state"] = "RUNNING"
        task["updated_at"] = utc_now()
        reg["tasks"][tid] = task
        log(f"worker_session_started task={tid} role={rid} session={s['id']} model={model} reasoning={reasoning}")
    atomic_json(WORKERS, wr)
    atomic_json(TASKS, reg)
    refresh_project_state()


def synthetic_result(task: dict[str, Any], worker: dict[str, Any], status: str, reason: str) -> None:
    rp = result_file(task["task_id"])
    if rp.exists():
        return
    atomic_json(rp, {
        "task_id": task["task_id"], "worker_id": worker["worker_id"],
        "worker_session_id": worker.get("session_id"), "status": status,
        "observations": [reason], "changes": [], "evidence": [],
        "verification": [], "unresolved": [reason], "risks": [],
        "artifacts": [], "recommended_followups": ["Escalate to orchestrator with persisted failure evidence"],
        "repository_revision": git("rev-parse", "HEAD"),
        "dirty_worktree": bool(git("status", "--porcelain")),
        "completed_at": utc_now(),
    })


def poll_workers(state: dict[str, Any]) -> None:
    cfg = config()["workers"]
    reg = strict_json(TASKS)
    wr = strict_json(WORKERS)
    changed = False
    for tid, worker in list(wr["workers"].items()):
        if worker.get("state") != "RUNNING":
            continue
        task = reg["tasks"].get(tid)
        if not task:
            continue
        sid = worker["session_id"]
        if time.time() - float(worker.get("started_epoch", time.time())) > int(cfg["max_task_runtime_seconds"]):
            cancel_session(sid, "worker_task_timeout")
            synthetic_result(task, worker, "FAILED", "worker task exceeded bounded runtime")
            delete_session(sid)
            worker["state"] = "FINISHED"
            task["state"] = "VERIFYING"
            changed = True
            bump_event("worker_result", tid)
            continue
        try:
            s = api("GET", f"/agents/sessions/{sid}")
        except Exception as exc:
            synthetic_result(task, worker, "FAILED", f"worker session retrieval failed: {exc!r}")
            worker["state"] = "FINISHED"
            task["state"] = "VERIFYING"
            changed = True
            bump_event("worker_result", tid)
            continue
        status = s.get("status")
        worker["last_status"] = status
        if status == "requires_action":
            actions = s.get("required_actions") or []
            if int(worker.get("tool_calls", 0)) + len(actions) > int(cfg["max_task_tool_calls"]):
                cancel_session(sid, "worker_tool_budget_exhausted")
                synthetic_result(task, worker, "BLOCKED", "worker tool-call budget exhausted; escalation required")
                delete_session(sid)
                worker["state"] = "FINISHED"
                task["state"] = "VERIFYING"
                changed = True
                bump_event("worker_result", tid)
            else:
                resolve_required_actions(s, kind="worker", task=task, worker_id=worker["worker_id"])
                worker["tool_calls"] = int(worker.get("tool_calls", 0)) + len(actions)
                changed = True
        elif status == "idle":
            if result_file(tid).exists():
                persist_usage(s, worker["worker_id"], int(worker.get("tool_calls", 0)), "submitted_result")
                delete_session(sid)
                worker["state"] = "FINISHED"
                worker["finished_at"] = utc_now()
                task["state"] = "VERIFYING"
                task["updated_at"] = utc_now()
                changed = True
                bump_event("worker_result", tid)
            else:
                synthetic_result(task, worker, "BLOCKED", "worker became idle without submitting required result contract")
                delete_session(sid)
                worker["state"] = "FINISHED"
                task["state"] = "VERIFYING"
                changed = True
                bump_event("worker_result", tid)
        elif status == "failed":
            synthetic_result(task, worker, "FAILED", "worker session failed: " + str(s.get("error"))[:1000])
            delete_session(sid)
            worker["state"] = "FINISHED"
            task["state"] = "VERIFYING"
            changed = True
            bump_event("worker_result", tid)
    if changed:
        atomic_json(WORKERS, wr)
        atomic_json(TASKS, reg)
        refresh_project_state()


def poll_superbrain(state: dict[str, Any]) -> dict[str, Any]:
    sid = state["superbrain_session_id"]
    try:
        s = api("GET", f"/agents/sessions/{sid}")
    except Exception as exc:
        log(f"superbrain_session_lost id={sid} err={exc!r}")
        state["superbrain_session_id"] = None
        atomic_json(CONTROLLER_STATE, state)
        return ensure_superbrain_session(state)
    status = s.get("status")
    if state.get("superbrain_paused_reason"):
        return state
    if status == "requires_action":
        actions = s.get("required_actions") or []
        count = int(state.get("superbrain_tool_calls", 0))
        initial_scan = not strict_json(TASKS)["tasks"]
        limit = 8 if initial_scan else 16
        if count + len(actions) > limit:
            progress = integrated_since_session_start(state)
            log(f"superbrain_tool_budget_exhausted session={sid} calls={count} integrated={progress}")
            cancel_session(sid, "superbrain_tool_budget_exhausted")
            try:
                final = api("GET", f"/agents/sessions/{sid}")
                persist_usage(final, "orchestrator-superbrain", count, "rotated_after_integration" if progress else "paused_no_progress")
            except Exception as exc:
                log(f"superbrain_usage_unavailable id={sid} err={exc!r}")
            if progress:
                state["superbrain_session_id"] = None
                state["event_seq"] = int(state.get("event_seq", 0)) + 1
                atomic_json(CONTROLLER_STATE, state)
                return ensure_superbrain_session(state)
            state["superbrain_paused_reason"] = "no-progress session tool budget exhausted; requires review"
            atomic_json(CONTROLLER_STATE, state)
            return state
        resolve_required_actions(s, kind="orchestrator")
        state = strict_json(CONTROLLER_STATE)
        state["superbrain_tool_calls"] = count + len(actions)
        atomic_json(CONTROLLER_STATE, state)
    elif status == "failed":
        log(f"superbrain_session_failed id={sid} err={s.get('error')!r}")
        delete_session(sid)
        state["superbrain_session_id"] = None
        atomic_json(CONTROLLER_STATE, state)
        return ensure_superbrain_session(state)
    elif status == "idle":
        current = int(state.get("event_seq", 0))
        last = int(state.get("last_superbrain_event_seq", 0))
        if current > last:
            event = state.get("last_event") or {}
            text = (
                f"Verified orchestration event seq={current}: {json.dumps(event, sort_keys=True)}. "
                "Reconcile it against current project state. Inspect worker results when applicable, "
                "integrate only verified evidence, then dispatch the next highest-value bounded work if justified."
            )
            send_message(sid, text, f"superbrain-event:{current}")
            state["last_superbrain_event_seq"] = current
            atomic_json(CONTROLLER_STATE, state)
    return state


def validate_runtime_files() -> None:
    for p in (CONFIG, ROLE_REGISTRY, SUPERBRAIN_INSTRUCTIONS, WORKER_INSTRUCTIONS, FOUNDING_SPEC, WORK_PROMPT, ARCHITECTURE):
        if not p.is_file():
            raise RuntimeError(f"REQUIRED_FILE_MISSING:{p}")
    cfg = config()
    if cfg["superbrain"]["model"] != "gpt-6-sol" or cfg["superbrain"]["reasoning_effort"] != "medium":
        raise RuntimeError("SUPERBRAIN_MODEL_POLICY_INVALID")
    if cfg["workers"]["default_model"] != "gpt-6-luna":
        raise RuntimeError("WORKER_DEFAULT_MODEL_POLICY_INVALID")
    if len(roles()) != 14:
        raise RuntimeError("WORKER_ROLE_COUNT_INVALID")


def run_controller() -> None:
    validate_runtime_files()
    ensure_state()
    if not API_KEY.is_file() or API_KEY.stat().st_size == 0:
        raise RuntimeError("OPENAI_AGENTS_API_KEY_MISSING")
    PID.write_text(str(os.getpid()), encoding="utf-8")
    os.chmod(PID, 0o600)
    state = strict_json(CONTROLLER_STATE)
    state = ensure_agents(state)
    state = ensure_superbrain_session(state)
    log("orchestrator_controller_started")
    while True:
        state = strict_json(CONTROLLER_STATE)
        state = poll_superbrain(state)
        launch_ready_workers(state)
        poll_workers(state)
        time.sleep(3)


if __name__ == "__main__":
    run_controller()
