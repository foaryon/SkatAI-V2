#!/usr/bin/env python3
import hashlib
import json
import os
import pwd
import signal
import stat
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

TRUSTED_ROOT = Path("/opt/skatai-main-controller/current")
CONTROL_ROOT = Path("/var/lib/skatai-main-controller")
PROMPT = TRUSTED_ROOT / "config/MAIN_AGENT_INSTRUCTIONS.md"
CONTINUE = TRUSTED_ROOT / "config/MAIN_CONTINUE_EXECUTION_POLICY.txt"
GOVERNOR = TRUSTED_ROOT / "config/MAIN_EXECUTION_GOVERNOR.json"
GOAL_POLICY = TRUSTED_ROOT / "config/MAIN_GOAL_POLICY.json"
MASTER_PROMPT = TRUSTED_ROOT / "authority/SKATAI_V2_MASTER_CONTINUE_MERGED.md"
FOUNDING_SPEC = TRUSTED_ROOT / "authority/SKATAI_V2_FOUNDING_SPECIFICATION.md"
WORK_PROMPT = TRUSTED_ROOT / "authority/SKATAI_V2_WORK_PROMPT.md"
END_STATE_SCORECARD = Path("/workspace/skatai-v2/provenance/MAIN_END_STATE_SCORECARD.json")
REPO_EXECUTION_LOCK = Path("/workspace/skatai-v2/provenance/MAIN_EXECUTION_LOCK.json")
EXECUTION_LOCK = CONTROL_ROOT / "EXECUTION_LOCK.json"
TURN_OUTCOME = Path("/var/lib/skatai-main-agent/outbox/MAIN_TURN_OUTCOME.json")
CURRENT_STATE = Path("/workspace/skatai-v2/provenance/MAIN_CURRENT_STATE.json")
RUNTIME = Path("/workspace/skatai-v2-runtime")
APP_KEY = Path("/run/skatai-v2-secrets/openai_agents_api_key")
EXEC_KEY = Path("/run/skatai-v2-secrets/openai_executor_api_key")
INBOX = CONTROL_ROOT / "inbox"
PROCESSED = INBOX / "processed"
STATE = CONTROL_ROOT / "session.json"
LOG = CONTROL_ROOT / "controller.log"
PIDFILE = CONTROL_ROOT / "controller.pid"
EXEC_PID = CONTROL_ROOT / "exec-server.pid"
EXEC_LOG = CONTROL_ROOT / "exec-server.log"
PAUSE_SUBMISSIONS = CONTROL_ROOT / "pause-submissions"
HARD_DISABLE = CONTROL_ROOT / "AGENT_HARD_DISABLED"
REENABLE_APPROVED = CONTROL_ROOT / "AGENT_REENABLE_APPROVED"
WORK_PERMIT = CONTROL_ROOT / "ACTIVE_WORK_PERMIT.json"
WRITE_SCOPE_STATE = CONTROL_ROOT / "executor-write-scope.json"
CODEX_HOME = Path("/var/lib/skatai-main-agent/codex-home")
EXECUTOR_CAP_DIR = Path("/run/skatai-main-agent")
EXECUTOR_ISS_SECRET = EXECUTOR_CAP_DIR / "iss_password"
CODEX = str(TRUSTED_ROOT / "codex")
EXECUTOR_USER = "skatai-main-agent"
BASE = "https://api.openai.com/v1"
SERVICE_TIER = "flex"
POLICY_EPOCH = "goal-cost-discipline-v3-20260925"
TURN_OUTCOME_SCHEMA = "skatai.v2.main-turn-outcome.v2"
LOCK_SCHEMA = "skatai.v2.main-execution-lock.v2"
GOVERNOR_SCHEMA = "skatai.v2.main-execution-governor.v2"
GOAL_POLICY_SCHEMA = "skatai.v2.main-goal-policy.v1"
REENABLE_APPROVAL_SCHEMA = "skatai.v2.main-reenable-approval.v1"
WORK_PERMIT_SCHEMA = "skatai.v2.main-work-permit.v1"
TERMINAL_CLASSIFICATIONS = {"ACCEPT", "REJECT", "INCONCLUSIVE", "CONCLUDED"}
EXTERNAL_WAIT_CLASSIFICATIONS = {"BLOCKED_EXTERNAL", "WAITING_EXTERNAL"}

REPO_ROOT = Path("/workspace/skatai-v2")
EVIDENCE_ROOTS = (REPO_ROOT, RUNTIME)

def _safe_evidence_path(ref):
    if not isinstance(ref, str) or not ref.strip():
        return None
    raw = ref.strip()
    p = Path(raw)
    if not p.is_absolute():
        p = REPO_ROOT / p
    try:
        resolved = p.resolve(strict=False)
    except Exception:
        return None
    for root in EVIDENCE_ROOTS:
        try:
            resolved.relative_to(root.resolve())
            return resolved
        except ValueError:
            continue
    return None

def controller_verifiable_evidence(refs, since=None):
    if not isinstance(refs, list) or not refs:
        return False, []
    verified = []
    for ref in refs:
        p = _safe_evidence_path(ref)
        if p is None or not p.is_file():
            continue
        if since is not None:
            try:
                if p.stat().st_mtime < float(since) - 2.0:
                    continue
            except OSError:
                continue
        verified.append(str(p))
    return bool(verified), verified

def evidence_content_fingerprint(paths):
    rows = []
    for raw in sorted(paths):
        p = Path(raw)
        h = hashlib.sha256()
        try:
            with p.open("rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    h.update(chunk)
            rows.append({"path": str(p), "sha256": h.hexdigest()})
        except OSError:
            continue
    return rows

def followup_signature(outcome, verified_paths=None):
    evidence_rows = evidence_content_fingerprint(verified_paths or [])
    payload = {
        "primary_gate_id": outcome.get("primary_gate_id"),
        "goal_path_id": outcome.get("goal_path_id"),
        "progress_kind": outcome.get("progress_kind"),
        "next_action": outcome.get("next_action"),
        "evidence": evidence_rows,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

def background_event(lock, state):
    """Read cheap deterministic watch state; never call the model to poll."""
    events = []
    for dep in lock.get("external_dependencies", []):
        watch = dep.get("event_watch")
        if not isinstance(watch, dict):
            continue
        if watch.get("type") != "R9_GATE":
            continue
        status_path = Path(watch.get("status_path", ""))
        supervisor_path = Path(watch.get("supervisor_path", ""))
        status = {}
        supervisor = {}
        try:
            status = strict_json_load(status_path)
        except Exception:
            pass
        try:
            supervisor = strict_json_load(supervisor_path)
        except Exception:
            pass
        snapshot = {
            "gate": status.get("gate"),
            "next_per_arm_target": status.get("next_per_arm_target"),
            "source_commit": status.get("source_commit"),
            "supervisor_state": supervisor.get("state"),
        }
        key = dep.get("gate_id", "external")
        previous = (state.get("background_watch") or {}).get(key)
        state.setdefault("background_watch", {})[key] = snapshot
        abnormal_supervisor = snapshot.get("supervisor_state") in {
            "BLOCKED_RECONCILIATION", "CAMPAIGN_COMPLETE", "MANUAL_HOLD", "WORKER_EXITED"
        }
        if previous is None:
            meaningful = (
                snapshot.get("gate") is not None
                or snapshot.get("next_per_arm_target") != watch.get("expected_next_per_arm_target")
                or (
                    watch.get("expected_source_commit")
                    and snapshot.get("source_commit") != watch.get("expected_source_commit")
                )
                or abnormal_supervisor
            )
        else:
            meaningful = (
                snapshot.get("gate") is not None
                or snapshot.get("next_per_arm_target") != previous.get("next_per_arm_target")
                or snapshot.get("source_commit") != previous.get("source_commit")
                or abnormal_supervisor
            )
        if meaningful and snapshot != previous:
            event_key = hashlib.sha256(
                json.dumps(
                    {"gate_id": key, "snapshot": snapshot},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:24]
            evidence = [
                str(status_path),
                str(supervisor_path),
            ]
            events.append(
                {
                    "trigger_type": "EXTERNAL_EVENT",
                    "event_key": event_key,
                    "gate_id": key,
                    "evidence": [p for p in evidence if Path(p).exists()],
                    "snapshot": snapshot,
                }
            )
    return events[0] if events else None

def ensure_control_root():
    if os.geteuid() != 0:
        raise RuntimeError("MAIN_CONTROL_ROOT_REQUIRES_ROOT")
    CONTROL_ROOT.mkdir(parents=True, exist_ok=True)
    INBOX.mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    os.chmod(CONTROL_ROOT, 0o700)
    os.chmod(INBOX, 0o700)
    os.chmod(PROCESSED, 0o700)

def log(msg):
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}\n")

def atomic_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)

def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def git_head():
    try:
        return subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            text=True,
            timeout=10,
        ).strip()
    except Exception:
        return None

def nonprovenance_worktree_fingerprint():
    """Fingerprint dirty/untracked repo content outside provenance/."""
    rows = []
    try:
        raw = subprocess.check_output(
            [
                "git",
                "-C",
                str(REPO_ROOT),
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            ],
            timeout=15,
        )
        for item in raw.split(b"\0"):
            if not item:
                continue
            text_item = item.decode("utf-8", "surrogateescape")
            path_text = text_item[3:] if len(text_item) >= 4 else text_item
            if path_text.startswith("provenance/"):
                continue
            p = REPO_ROOT / path_text
            digest = None
            if p.is_file():
                try:
                    digest = sha256_file(p)
                except OSError:
                    digest = None
            rows.append(
                {"status": text_item[:2], "path": path_text, "sha256": digest}
            )
    except Exception as exc:
        rows.append({"error": type(exc).__name__})
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

def material_effect_policy_violation(state):
    # Use permissions frozen before the turn. A model-written lock update cannot
    # retroactively authorize effects already taken in the same turn.
    effects = set(state.get("turn_allowed_material_effects") or [])
    before_head = state.get("turn_git_head")
    after_head = git_head()
    if "LOCAL_GIT_CHANGE" not in effects and before_head and after_head != before_head:
        return "unauthorized_git_head_change"
    if "LOCAL_GIT_CHANGE" not in effects:
        before_nonprov = state.get("turn_nonprovenance_fingerprint")
        after_nonprov = nonprovenance_worktree_fingerprint()
        if before_nonprov and after_nonprov != before_nonprov:
            return "unauthorized_nonprovenance_repo_change"
    return None

def approval_policy_hashes():
    paths = {
        "trusted_manifest": TRUSTED_ROOT / "MANIFEST.sha256",
        "controller": Path(__file__).resolve(),
        "agent_instructions": PROMPT,
        "governor": GOVERNOR,
        "goal_policy": GOAL_POLICY,
        "master_prompt": MASTER_PROMPT,
        "founding_spec": FOUNDING_SPEC,
        "work_prompt": WORK_PROMPT,
        "continue_policy": CONTINUE,
        "boot": TRUSTED_ROOT / "openai_agent_boot.sh",
        "guardian": TRUSTED_ROOT / "openai_platform_guardian.sh",
        "preflight": TRUSTED_ROOT / "check_main_startup.py",
        "secret_prep": TRUSTED_ROOT / "prepare_runtime_secrets.py",
        "executor_user_prep": TRUSTED_ROOT / "prepare_main_executor_user.sh",
        "user_input_submitter": TRUSTED_ROOT / "submit_main_user_input.py",
        "readiness_validator": TRUSTED_ROOT / "validate_main_agent_readiness.py",
        "execution_lock": EXECUTION_LOCK,
    }
    return {name: sha256_file(path) for name, path in paths.items()}

def reenable_approval_valid():
    if HARD_DISABLE.exists():
        return False, "hard_disable_present"
    if not REENABLE_APPROVED.is_file():
        return False, "reenable_approval_absent"
    try:
        raw = strict_json_load(REENABLE_APPROVED)
    except Exception:
        return False, "reenable_approval_unreadable"
    if raw.get("schema") != REENABLE_APPROVAL_SCHEMA:
        return False, "reenable_approval_schema"
    if raw.get("policy_epoch") != POLICY_EPOCH:
        return False, "reenable_approval_policy_epoch"
    if raw.get("policy_hashes") != approval_policy_hashes():
        return False, "reenable_approval_hash_mismatch"
    if raw.get("approved") is not True:
        return False, "reenable_approval_not_approved"
    return True, None

def work_permit_valid(state=None, now=None):
    if HARD_DISABLE.exists():
        return None, "hard_disable_present"
    if not WORK_PERMIT.is_file():
        return None, "work_permit_absent"
    try:
        raw = strict_json_load(WORK_PERMIT)
    except Exception:
        return None, "work_permit_unreadable"
    if raw.get("schema") != WORK_PERMIT_SCHEMA:
        return None, "work_permit_schema"
    if raw.get("policy_epoch") != POLICY_EPOCH:
        return None, "work_permit_policy_epoch"
    if raw.get("policy_hashes") != approval_policy_hashes():
        return None, "work_permit_policy_hash_mismatch"
    if raw.get("approved") is not True:
        return None, "work_permit_not_approved"
    if raw.get("mode") != "BOUNDED_GATE":
        return None, "work_permit_mode_invalid"
    permit_id = str(raw.get("permit_id") or "").strip()
    if len(permit_id) < 16:
        return None, "work_permit_id_invalid"
    now = time.time() if now is None else float(now)
    try:
        expires_at = float(raw["expires_at_epoch"])
        max_total = int(raw["max_model_submits_total"])
        max_auto = int(raw["max_autonomous_submits_total"])
    except (KeyError, TypeError, ValueError):
        return None, "work_permit_limits_invalid"
    if expires_at <= now:
        return None, "work_permit_expired"
    if not (1 <= max_total <= 10 and 0 <= max_auto <= max_total):
        return None, "work_permit_limits_invalid"
    trigger_types = raw.get("allowed_trigger_types")
    if not isinstance(trigger_types, list) or not trigger_types:
        return None, "work_permit_trigger_types_invalid"
    try:
        allowed_policy_triggers = set(load_goal_policy()["turn_triggers"]["allowed"])
    except Exception:
        return None, "work_permit_goal_policy_invalid"
    if not set(trigger_types).issubset(allowed_policy_triggers):
        return None, "work_permit_trigger_types_invalid"
    if "EXTERNAL_EVENT" in trigger_types and max_auto <= 0:
        return None, "work_permit_external_event_without_autonomous_budget"
    if not EXECUTION_LOCK.is_file():
        return None, "work_permit_execution_lock_missing"
    if raw.get("execution_lock_sha256") != sha256_file(EXECUTION_LOCK):
        return None, "work_permit_execution_lock_changed"
    try:
        lock = load_execution_lock()
    except Exception:
        return None, "work_permit_execution_lock_invalid"
    if raw.get("primary_gate_id") != lock["primary"]["gate_id"]:
        return None, "work_permit_primary_gate_mismatch"
    if raw.get("goal_path_id") != lock["primary"]["goal_path_id"]:
        return None, "work_permit_goal_path_mismatch"
    if state is not None and state.get("work_permit_id") not in {None, permit_id}:
        return None, "work_permit_state_id_mismatch"
    return raw, None

def work_permit_budget_status(state, permit, kind):
    total = int(state.get("permit_total_model_submits") or 0)
    auto = int(state.get("permit_autonomous_submits") or 0)
    if total >= int(permit["max_model_submits_total"]):
        return False, "work_permit_total_submit_cap"
    if kind == "autonomous" and auto >= int(permit["max_autonomous_submits_total"]):
        return False, "work_permit_autonomous_submit_cap"
    return True, None

def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def strict_json_load(path):
    def no_duplicates(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise RuntimeError(f"DUPLICATE_JSON_KEY:{key}")
            out[key] = value
        return out
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicates)

def load_governor():
    raw = strict_json_load(GOVERNOR)
    if raw.get("schema") != GOVERNOR_SCHEMA:
        raise RuntimeError("GOVERNOR_SCHEMA_MISMATCH")
    return raw

def load_goal_policy():
    raw = strict_json_load(GOAL_POLICY)
    if raw.get("schema") != GOAL_POLICY_SCHEMA:
        raise RuntimeError("GOAL_POLICY_SCHEMA_MISMATCH")
    ids = [row.get("id") for row in raw.get("goal_path", []) if isinstance(row, dict)]
    if not ids or len(ids) != len(set(ids)):
        raise RuntimeError("GOAL_POLICY_PATH_INVALID")
    return raw

def load_execution_lock():
    raw = strict_json_load(EXECUTION_LOCK)
    if raw.get("schema") != LOCK_SCHEMA:
        raise RuntimeError("EXECUTION_LOCK_SCHEMA_MISMATCH")
    goal_policy = load_goal_policy()
    valid_goal_ids = {row["id"] for row in goal_policy["goal_path"]}
    primary = raw.get("primary")
    required = {
        "gate_id", "goal_path_id", "end_state_contribution", "selection_basis",
        "selection_evidence", "completion", "model_tier", "model_reason", "evidence",
        "allowed_material_effects", "executor_capabilities", "progress_contract",
        "writable_files"
    }
    if not isinstance(primary, dict) or not required.issubset(primary):
        raise RuntimeError("EXECUTION_LOCK_PRIMARY_INVALID")
    if primary.get("goal_path_id") not in valid_goal_ids:
        raise RuntimeError("EXECUTION_LOCK_GOAL_PATH_INVALID")
    if not isinstance(primary.get("completion"), list) or not primary["completion"]:
        raise RuntimeError("EXECUTION_LOCK_COMPLETION_INVALID")
    for field in ("end_state_contribution", "selection_basis", "model_reason"):
        if len(str(primary.get(field) or "").strip()) < 20:
            raise RuntimeError(f"EXECUTION_LOCK_{field.upper()}_INVALID")
    evidence_ok, _ = controller_verifiable_evidence(primary.get("evidence"))
    if not evidence_ok:
        raise RuntimeError("EXECUTION_LOCK_EVIDENCE_UNVERIFIED")
    selection_ok, _ = controller_verifiable_evidence(primary.get("selection_evidence"))
    if not selection_ok:
        raise RuntimeError("EXECUTION_LOCK_SELECTION_EVIDENCE_UNVERIFIED")
    governor = load_governor()
    security = governor.get("executor_security") or {}
    effects = primary.get("allowed_material_effects")
    capabilities = primary.get("executor_capabilities")
    if not isinstance(effects, list) or not effects:
        raise RuntimeError("EXECUTION_LOCK_MATERIAL_EFFECTS_INVALID")
    if not isinstance(capabilities, list):
        raise RuntimeError("EXECUTION_LOCK_CAPABILITIES_INVALID")
    permitted_effects = set(security.get("permitted_material_effects") or [])
    permitted_caps = set(security.get("permitted_capabilities") or [])
    goal_effects = set((security.get("goal_effects") or {}).get(primary["goal_path_id"]) or [])
    goal_caps = set((security.get("goal_capabilities") or {}).get(primary["goal_path_id"]) or [])
    if not set(effects).issubset(permitted_effects) or not set(effects).issubset(goal_effects):
        raise RuntimeError("EXECUTION_LOCK_MATERIAL_EFFECT_NOT_PERMITTED")
    if not set(capabilities).issubset(permitted_caps) or not set(capabilities).issubset(goal_caps):
        raise RuntimeError("EXECUTION_LOCK_CAPABILITY_NOT_PERMITTED")
    writable_files = primary.get("writable_files")
    if not isinstance(writable_files, list) or len(writable_files) > 16:
        raise RuntimeError("EXECUTION_LOCK_WRITABLE_FILES_INVALID")
    protected_repo = {
        REPO_EXECUTION_LOCK.resolve(),
        (REPO_ROOT / "SKATAI_V2_FOUNDING_SPECIFICATION.md").resolve(),
        (REPO_ROOT / "SKATAI_V2_WORK_PROMPT.md").resolve(),
        (REPO_ROOT / "SKATAI_V2_MASTER_CONTINUE_MERGED.md").resolve(),
    }
    for ref in writable_files:
        p = _safe_evidence_path(ref)
        if p is None or not p.is_file() or p.is_symlink():
            raise RuntimeError("EXECUTION_LOCK_WRITABLE_FILE_INVALID")
        rel = p.relative_to(REPO_ROOT.resolve()) if str(p).startswith(str(REPO_ROOT.resolve())) else None
        if p in protected_repo or (rel is not None and rel.parts[:2] == ("configs", "control")):
            raise RuntimeError("EXECUTION_LOCK_WRITABLE_FILE_PROTECTED")
    if writable_files and not ({"PROVENANCE_WRITE", "LOCAL_GIT_CHANGE"} & set(effects)):
        raise RuntimeError("EXECUTION_LOCK_WRITABLE_FILE_WITHOUT_EFFECT")
    contract = primary.get("progress_contract")
    if not isinstance(contract, dict):
        raise RuntimeError("EXECUTION_LOCK_PROGRESS_CONTRACT_INVALID")
    kind = contract.get("kind")
    if kind not in {"JSON_FIELD_TRANSITION", "FILE_CONTENT_CHANGE"}:
        raise RuntimeError("EXECUTION_LOCK_PROGRESS_CONTRACT_KIND_INVALID")
    contract_path = _safe_evidence_path(contract.get("path"))
    if contract_path is None:
        raise RuntimeError("EXECUTION_LOCK_PROGRESS_CONTRACT_PATH_INVALID")
    if kind == "JSON_FIELD_TRANSITION" and not str(contract.get("field") or "").strip():
        raise RuntimeError("EXECUTION_LOCK_PROGRESS_CONTRACT_FIELD_INVALID")
    secondary = raw.get("secondary")
    if secondary is not None and not isinstance(secondary, dict):
        raise RuntimeError("EXECUTION_LOCK_SECONDARY_INVALID")
    if secondary is not None and primary.get("status") not in EXTERNAL_WAIT_CLASSIFICATIONS:
        raise RuntimeError("EXECUTION_LOCK_SECONDARY_NOT_ALLOWED")
    for dep in raw.get("external_dependencies", []):
        if not isinstance(dep, dict) or dep.get("goal_path_id") not in valid_goal_ids:
            raise RuntimeError("EXECUTION_LOCK_EXTERNAL_DEPENDENCY_INVALID")
        watch = dep.get("event_watch")
        if watch is None:
            continue
        if not isinstance(watch, dict) or watch.get("type") != "R9_GATE":
            raise RuntimeError("EXECUTION_LOCK_EVENT_WATCH_INVALID")
        for field in ("status_path", "supervisor_path"):
            p = Path(str(watch.get(field) or "")).resolve(strict=False)
            try:
                p.relative_to(RUNTIME.resolve())
            except ValueError:
                raise RuntimeError("EXECUTION_LOCK_EVENT_WATCH_PATH_INVALID")
            if not p.is_file():
                raise RuntimeError("EXECUTION_LOCK_EVENT_WATCH_PATH_MISSING")
        if not isinstance(watch.get("expected_next_per_arm_target"), int):
            raise RuntimeError("EXECUTION_LOCK_EVENT_WATCH_TARGET_INVALID")
        if len(str(watch.get("expected_source_commit") or "")) != 40:
            raise RuntimeError("EXECUTION_LOCK_EVENT_WATCH_SOURCE_INVALID")
    return raw

def _json_field(raw, dotted):
    cur = raw
    for part in str(dotted).split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise RuntimeError("PROGRESS_CONTRACT_FIELD_MISSING:" + str(dotted))
        cur = cur[part]
    return cur

def capture_progress_contract(lock):
    contract = json.loads(json.dumps(lock["primary"]["progress_contract"]))
    path = _safe_evidence_path(contract["path"])
    if path is None:
        raise RuntimeError("PROGRESS_CONTRACT_PATH_INVALID")
    baseline = {"contract": contract, "path": str(path), "exists": path.is_file()}
    if path.is_file():
        baseline["sha256"] = sha256_file(path)
    if contract["kind"] == "JSON_FIELD_TRANSITION":
        if not path.is_file():
            raise RuntimeError("PROGRESS_CONTRACT_JSON_MISSING")
        raw = strict_json_load(path)
        value = _json_field(raw, contract["field"])
        baseline["value"] = value
        prefix = contract.get("before_prefix")
        if prefix is not None and not str(value).startswith(str(prefix)):
            raise RuntimeError("PROGRESS_CONTRACT_BASELINE_MISMATCH")
    return baseline

def progress_contract_status(state):
    baseline = state.get("turn_progress_contract")
    if not isinstance(baseline, dict):
        return False, "progress_contract_baseline_missing"
    contract = baseline.get("contract") or {}
    path = Path(str(baseline.get("path") or ""))
    kind = contract.get("kind")
    if kind == "FILE_CONTENT_CHANGE":
        if not path.is_file():
            return False, "progress_contract_file_missing"
        after_hash = sha256_file(path)
        if after_hash == baseline.get("sha256"):
            return False, "progress_contract_no_content_change"
        return True, None
    if kind == "JSON_FIELD_TRANSITION":
        if not path.is_file():
            return False, "progress_contract_json_missing"
        try:
            raw = strict_json_load(path)
            after = _json_field(raw, contract["field"])
        except Exception:
            return False, "progress_contract_json_invalid"
        before = baseline.get("value")
        if after == before:
            return False, "progress_contract_no_field_transition"
        forbid = contract.get("after_forbid_prefix")
        if forbid is not None and str(after).startswith(str(forbid)):
            return False, "progress_contract_forbidden_after_state"
        allowed = contract.get("after_allowed_values")
        if allowed is not None and after not in allowed:
            return False, "progress_contract_after_value_not_allowed"
        return True, None
    return False, "progress_contract_kind_invalid"

def utc_day_key(now=None):
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() if now is None else now))

def refresh_budget_state(state, governor, now=None):
    day = utc_day_key(now)
    if state.get("budget_day") != day:
        state["budget_day"] = day
        state["total_model_submits_today"] = 0
        state["estimated_total_cost_usd_today"] = 0.0
        state["autonomous_submits_today"] = 0
        state["estimated_autonomous_cost_usd_today"] = 0.0
    return state

def hard_total_budget_status(state, governor, now=None):
    now = time.time() if now is None else float(now)
    refresh_budget_state(state, governor, now)
    budget = governor["hard_total_budget"]
    count = int(state.get("total_model_submits_today") or 0)
    est = float(state.get("estimated_total_cost_usd_today") or 0.0)
    per = float(budget["incident_estimated_cost_per_submit_usd"])
    if count >= int(budget["max_model_submits_per_utc_day"]):
        return False, "hard_daily_model_submit_cap"
    if est + per > float(budget["max_estimated_cost_usd_per_utc_day"]) + 1e-9:
        return False, "hard_daily_estimated_cost_cap"
    return True, None

def record_model_submit(state, governor, kind, now=None):
    now = time.time() if now is None else float(now)
    refresh_budget_state(state, governor, now)
    hard = governor["hard_total_budget"]
    per = float(hard["incident_estimated_cost_per_submit_usd"])
    state["total_model_submits_today"] = int(state.get("total_model_submits_today") or 0) + 1
    state["estimated_total_cost_usd_today"] = round(
        float(state.get("estimated_total_cost_usd_today") or 0.0) + per, 6
    )
    if state.get("work_permit_id"):
        state["permit_total_model_submits"] = int(
            state.get("permit_total_model_submits") or 0
        ) + 1
        if kind == "autonomous":
            state["permit_autonomous_submits"] = int(
                state.get("permit_autonomous_submits") or 0
            ) + 1
    if kind == "autonomous":
        budget = governor["autonomous_budget"]
        state["autonomous_submits_today"] = int(state.get("autonomous_submits_today") or 0) + 1
        state["estimated_autonomous_cost_usd_today"] = round(
            float(state.get("estimated_autonomous_cost_usd_today") or 0.0)
            + float(budget["incident_estimated_cost_per_submit_usd"]),
            6,
        )
        state["last_autonomous_submit_at"] = int(now)

def autonomous_budget_status(state, governor, now=None):
    now = time.time() if now is None else float(now)
    refresh_budget_state(state, governor, now)
    budget = governor["autonomous_budget"]
    count = int(state.get("autonomous_submits_today") or 0)
    est = float(state.get("estimated_autonomous_cost_usd_today") or 0.0)
    per = float(budget["incident_estimated_cost_per_submit_usd"])
    if count >= int(budget["max_submits_per_utc_day"]):
        return False, "daily_submit_cap"
    if est + per > float(budget["max_estimated_cost_usd_per_utc_day"]) + 1e-9:
        return False, "daily_estimated_cost_cap"
    last = float(state.get("last_autonomous_submit_at") or 0)
    if last and now - last < float(budget["min_seconds_between_submits"]):
        return False, "minimum_submit_interval"
    return True, None

def turn_nonce(session_id, state):
    raw = f"{POLICY_EPOCH}\n{session_id}\n{int(state.get('session_submit_count') or 0)}\n{time.time_ns()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

def read_turn_outcome(expected_nonce, governor):
    try:
        raw = strict_json_load(TURN_OUTCOME)
    except Exception as exc:
        return None, f"turn_outcome_unreadable:{type(exc).__name__}"
    if raw.get("schema") != TURN_OUTCOME_SCHEMA:
        return None, "turn_outcome_schema_mismatch"
    if raw.get("turn_nonce") != expected_nonce:
        return None, "turn_outcome_nonce_mismatch"
    if not isinstance(raw.get("primary_gate_id"), str) or not raw["primary_gate_id"]:
        return None, "turn_outcome_primary_missing"
    if not isinstance(raw.get("goal_path_id"), str) or not raw["goal_path_id"]:
        return None, "turn_outcome_goal_path_missing"
    goal_policy = load_goal_policy()
    valid_goal_ids = {row["id"] for row in goal_policy["goal_path"]}
    if raw["goal_path_id"] not in valid_goal_ids:
        return None, "turn_outcome_goal_path_invalid"
    trigger_type = raw.get("trigger_type")
    if trigger_type not in set(goal_policy["turn_triggers"]["allowed"]):
        return None, "turn_outcome_trigger_invalid"
    if not isinstance(raw.get("event_key"), str) or len(raw["event_key"].strip()) < 8:
        return None, "turn_outcome_event_key_invalid"
    if not isinstance(raw.get("material_progress"), bool):
        return None, "turn_outcome_material_progress_invalid"
    if not isinstance(raw.get("request_followup"), bool):
        return None, "turn_outcome_followup_invalid"
    classifications = {"CONTINUE", "ACCEPT", "REJECT", "INCONCLUSIVE", "CONCLUDED", "BLOCKED_EXTERNAL", "WAITING_EXTERNAL", "ERROR"}
    if raw.get("classification") not in classifications:
        return None, "turn_outcome_classification_invalid"
    allowed = set(governor["progress"]["allowed_progress_kinds"]) | {"NONE"}
    if raw.get("progress_kind") not in allowed:
        return None, "turn_outcome_progress_kind_invalid"
    return raw, None

def outcome_allows_followup(outcome, lock, governor, state=None):
    state = state or {}
    if not outcome.get("material_progress"):
        return False, "no_material_progress"
    if outcome.get("progress_kind") not in governor["progress"]["allowed_progress_kinds"]:
        return False, "nonconsequential_progress"
    if not outcome.get("request_followup"):
        return False, "followup_not_requested"
    if not outcome.get("next_action"):
        return False, "followup_without_next_action"
    if state.get("turn_event_key") and outcome.get("event_key") != state.get("turn_event_key"):
        return False, "turn_outcome_event_mismatch"

    evidence = outcome.get("evidence")
    verified_ok, verified_paths = controller_verifiable_evidence(
        evidence, since=state.get("turn_started_at")
    )
    if not verified_ok:
        return False, "material_progress_without_fresh_verifiable_evidence"

    classification = outcome.get("classification")
    turn_trigger = state.get("turn_trigger_type")
    if turn_trigger and outcome.get("trigger_type") != turn_trigger:
        return False, "turn_outcome_trigger_mismatch"
    if turn_trigger != "USER_DIRECTIVE":
        contract_ok, contract_reason = progress_contract_status(state)
        if not contract_ok:
            return False, contract_reason
    expected_gate = state.get("turn_primary_gate_id")
    expected_goal = state.get("turn_goal_path_id")
    current_gate = lock["primary"]["gate_id"]
    current_goal = lock["primary"]["goal_path_id"]
    outcome_gate = outcome["primary_gate_id"]
    outcome_goal = outcome["goal_path_id"]
    start_effects = set(state.get("turn_allowed_material_effects") or [])
    current_effects = set(lock["primary"].get("allowed_material_effects") or [])
    start_caps = set(state.get("turn_executor_capabilities") or [])
    current_caps = set(lock["primary"].get("executor_capabilities") or [])
    authority_broadened = bool((current_effects - start_effects) or (current_caps - start_caps))
    if authority_broadened and turn_trigger != "USER_DIRECTIVE" and classification not in TERMINAL_CLASSIFICATIONS:
        return False, "autonomous_authority_broadening"

    if turn_trigger in {"USER_DIRECTIVE", "EXTERNAL_EVENT"}:
        if outcome_gate != current_gate or outcome_goal != current_goal:
            return False, "user_reprioritization_not_atomically_locked"
    else:
        if expected_gate and outcome_gate != expected_gate:
            return False, "turn_outcome_gate_mismatch"
        if expected_goal and outcome_goal != expected_goal:
            return False, "turn_outcome_goal_mismatch"
        if classification not in TERMINAL_CLASSIFICATIONS:
            if current_gate != outcome_gate or current_goal != outcome_goal:
                return False, "primary_changed_without_terminal_outcome"
        elif current_gate == outcome_gate and outcome.get("request_followup"):
            # A terminal decision may stop. If it requests automatic continuation,
            # it must atomically install the next valid PRIMARY first.
            return False, "terminal_followup_without_new_primary"

    signature = followup_signature(outcome, verified_paths)
    if signature == state.get("last_followup_signature"):
        return False, "repeated_followup_signature"

    same_gate = int(state.get("same_gate_followups") or 0)
    if outcome_gate == state.get("last_followup_gate"):
        same_gate += 1
    else:
        same_gate = 1
    cap = int(governor["autonomous_budget"]["max_same_gate_followups_without_terminal"])
    if classification not in TERMINAL_CLASSIFICATIONS and same_gate > cap:
        return False, "same_gate_followup_cap"

    outcome["_controller_verified_evidence"] = verified_paths
    outcome["_controller_followup_signature"] = signature
    outcome["_controller_same_gate_followups"] = same_gate
    return True, None

def trip_hard_disable(reason):
    HARD_DISABLE.write_text(
        "HARD_DISABLED=1\n"
        f"reason={reason}\n"
        "restart_policy=DO_NOT_START until controller safety is reviewed.\n",
        encoding="utf-8",
    )
    log(f"hard_disable_tripped reason={reason}")

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
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        for _ in range(10):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        else:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        EXEC_PID.unlink(missing_ok=True)
    except Exception:
        EXEC_PID.unlink(missing_ok=True)
    finally:
        clear_executor_capabilities()
        clear_executor_write_scope()

def clear_executor_write_scope():
    try:
        raw = strict_json_load(WRITE_SCOPE_STATE)
    except Exception:
        raw = {"files": []}
    for row in raw.get("files", []):
        try:
            p = Path(row["path"])
            if p.is_file():
                os.chown(p, int(row["uid"]), int(row["gid"]))
                os.chmod(p, int(row["mode"]))
        except Exception:
            pass
    WRITE_SCOPE_STATE.unlink(missing_ok=True)

def prepare_executor_write_scope():
    clear_executor_write_scope()
    lock = load_execution_lock()
    account = pwd.getpwnam(EXECUTOR_USER)
    rows = []
    try:
        for ref in lock["primary"].get("writable_files") or []:
            p = _safe_evidence_path(ref)
            if p is None or not p.is_file():
                raise RuntimeError("EXECUTOR_WRITABLE_FILE_UNAVAILABLE:" + str(ref))
            st = p.stat()
            original_mode = stat.S_IMODE(st.st_mode)
            rows.append({
                "path": str(p),
                "uid": st.st_uid,
                "gid": st.st_gid,
                "mode": original_mode,
            })
            os.chown(p, st.st_uid, account.pw_gid)
            os.chmod(p, (original_mode | stat.S_IWGRP) & ~stat.S_IWOTH)
        atomic_json(WRITE_SCOPE_STATE, {"files": rows})
    except Exception:
        for row in reversed(rows):
            try:
                p = Path(row["path"])
                os.chown(p, int(row["uid"]), int(row["gid"]))
                os.chmod(p, int(row["mode"]))
            except Exception:
                pass
        WRITE_SCOPE_STATE.unlink(missing_ok=True)
        raise

def clear_executor_capabilities():
    try:
        EXECUTOR_ISS_SECRET.unlink(missing_ok=True)
    except Exception:
        pass
    try:
        if EXECUTOR_CAP_DIR.is_dir() and not any(EXECUTOR_CAP_DIR.iterdir()):
            EXECUTOR_CAP_DIR.rmdir()
    except Exception:
        pass

def prepare_executor_capabilities():
    clear_executor_capabilities()
    lock = load_execution_lock()
    requested = set(lock["primary"].get("executor_capabilities") or [])
    if not requested:
        return
    account = pwd.getpwnam(EXECUTOR_USER)
    EXECUTOR_CAP_DIR.mkdir(parents=True, exist_ok=True)
    os.chown(EXECUTOR_CAP_DIR, 0, account.pw_gid)
    os.chmod(EXECUTOR_CAP_DIR, 0o750)
    if "ISS_RUNTIME" in requested:
        master = Path("/run/skatai-v2-secrets/iss_password")
        if not master.is_file() or master.stat().st_size <= 0:
            raise RuntimeError("ISS_PASSWORD_FILE_UNAVAILABLE")
        tmp = EXECUTOR_CAP_DIR / ".iss_password.tmp"
        tmp.write_bytes(master.read_bytes())
        os.chown(tmp, account.pw_uid, account.pw_gid)
        os.chmod(tmp, 0o400)
        os.replace(tmp, EXECUTOR_ISS_SECRET)

def child_env(executor_key):
    """Build a default-deny executor environment from explicit safe fields only."""
    governor = load_governor()
    lock = load_execution_lock()
    security = governor.get("executor_security") or {}
    requested = set(lock["primary"].get("executor_capabilities") or [])
    permitted = set(security.get("permitted_capabilities") or [])
    if not requested.issubset(permitted):
        raise RuntimeError("EXECUTOR_CAPABILITY_NOT_PERMITTED:" + ",".join(sorted(requested - permitted)))

    e = {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/workspace/openai-agent/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    }
    for k in ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        if os.environ.get(k):
            e[k] = os.environ[k]
    e["CODEX_API_KEY"] = executor_key
    e["HOME"] = str(CODEX_HOME)
    e["CODEX_HOME"] = str(CODEX_HOME)
    e["TMPDIR"] = "/var/lib/skatai-main-agent/tmp"
    e["SKATAI_MAIN_WORKDIR"] = "/var/lib/skatai-main-agent/work"
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    e["PYTHONPYCACHEPREFIX"] = "/var/lib/skatai-main-agent/tmp/pycache"
    e["SKATAI_MAIN_POLICY_EPOCH"] = POLICY_EPOCH
    e["SKATAI_MAIN_PRIMARY_GATE"] = str(lock["primary"]["gate_id"])
    e["SKATAI_MAIN_GOAL_PATH"] = str(lock["primary"]["goal_path_id"])
    e["SKATAI_MAIN_EXECUTION_LOCK"] = str(TRUSTED_ROOT / "config/INITIAL_EXECUTION_LOCK.json")
    e["SKATAI_MAIN_TURN_OUTCOME"] = str(TURN_OUTCOME)
    permit, permit_reason = work_permit_valid()
    if permit is None:
        raise RuntimeError("WORK_PERMIT_INVALID_FOR_EXECUTOR:" + str(permit_reason))
    e["SKATAI_MAIN_WORK_PERMIT_ID"] = str(permit["permit_id"])

    if "ISS_RUNTIME" in requested:
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
        if not EXECUTOR_ISS_SECRET.is_file():
            raise RuntimeError("ISS_EXECUTOR_SECRET_UNAVAILABLE")
        e["ISS_PASSWORD_FILE"] = str(EXECUTOR_ISS_SECRET)

    forbidden_markers = (
        "AWS_",
        "RUNPOD_API_KEY",
        "RUNPOD_DEPLOY_API_KEY",
        "SENTINELX_ENROLL_TOKEN",
        "OPENAI_AGENTS_API_KEY",
        "RUNPOD_SECRET_OPENAI_AGENTS_API_KEY",
        "SKATAI_ISS_PASSWORD",
        "ISS_PASSWORD",
    )
    for k in e:
        ku = k.upper()
        if k in {"ISS_PASSWORD_FILE"}:
            continue
        if any(marker in ku for marker in forbidden_markers):
            raise RuntimeError(f"FORBIDDEN_EXECUTOR_ENV:{k}")
    return e

def executor_environment_contract_valid():
    if not executor_alive():
        return False
    try:
        probe = child_env("__probe__")
        lock = load_execution_lock()
        requested = set(lock["primary"].get("executor_capabilities") or [])
        forbidden = {
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "RUNPOD_API_KEY",
            "RUNPOD_DEPLOY_API_KEY",
            "SENTINELX_ENROLL_TOKEN",
            "OPENAI_AGENTS_API_KEY",
            "OPENAI_API_KEY",
            "skatai_iss_password",
            "ISS_PASSWORD",
        }
        if forbidden.intersection(probe):
            return False
        if "ISS_RUNTIME" in requested:
            return (
                bool(probe.get("ISS_HOST"))
                and bool(probe.get("ISS_CLIENT_ID"))
                and bool(probe.get("ISS_PORT"))
                and probe.get("ISS_PASSWORD_FILE") == str(EXECUTOR_ISS_SECRET)
                and Path(probe["ISS_PASSWORD_FILE"]).is_file()
            )
        return not any(k.startswith("ISS_") for k in probe)
    except Exception:
        return False

def demote_to_executor():
    sx = pwd.getpwnam(EXECUTOR_USER)
    os.initgroups(EXECUTOR_USER, sx.pw_gid)
    os.setgid(sx.pw_gid)
    os.setuid(sx.pw_uid)

def start_executor(environment):
    if executor_alive():
        return
    executor_key = EXEC_KEY.read_text(encoding="utf-8").strip()
    lock = load_execution_lock()
    network_allowed = "ISS_RUNTIME" in set(
        lock["primary"].get("executor_capabilities") or []
    )
    try:
        prepare_executor_write_scope()
        prepare_executor_capabilities()
        command = [
            CODEX,
            "exec-server",
            "--strict-config",
            "-c", 'sandbox_mode="workspace-write"',
            "-c", 'approval_policy="never"',
            "-c", "sandbox_workspace_write.network_access="
                + ("true" if network_allowed else "false"),
            "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
            "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=false",
            "-c", 'shell_environment_policy.inherit="all"',
            "-c", "shell_environment_policy.ignore_default_excludes=false",
            "-c", 'shell_environment_policy.exclude=["CODEX_API_KEY","OPENAI_*","*KEY*","*SECRET*","*TOKEN*"]',
            "--remote", environment["remote_url"],
            "--environment-id", environment["id"],
        ]
        with EXEC_LOG.open("ab", buffering=0) as out:
            p = subprocess.Popen(
                command,
                cwd="/workspace/skatai-v2",
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                env=child_env(executor_key),
                preexec_fn=demote_to_executor,
                start_new_session=True,
            )
        EXEC_PID.write_text(str(p.pid), encoding="utf-8")
        os.chmod(EXEC_PID, 0o600)
        log(
            f"executor_started pid={p.pid} env={environment['id']} "
            f"shell_network_allowed={network_allowed}"
        )
    except Exception:
        clear_executor_capabilities()
        clear_executor_write_scope()
        raise

def desired_model():
    governor = load_governor()
    lock = load_execution_lock()
    policy = governor["model_policy"]
    tier = lock["primary"].get("model_tier")
    if tier != policy["default_tier"]:
        raise RuntimeError(f"MODEL_TIER_NOT_VERIFIED:{tier}")
    model = str(policy.get("default_model") or "").strip()
    if not model:
        raise RuntimeError("VERIFIED_MODEL_MISSING")
    return model

def ensure_saved_agent(state):
    model = desired_model()
    agent_id = state.get("agent_id")
    if agent_id:
        try:
            api("GET", f"/agents/{agent_id}")
            a = api("POST", f"/agents/{agent_id}", {
                "model": model,
                "instructions": PROMPT.read_text(encoding="utf-8"),
                "service_tier": SERVICE_TIER,
            })
            if a.get("service_tier") != SERVICE_TIER:
                raise RuntimeError(f"saved agent service_tier verification failed: {a.get('service_tier')!r}")
            if a.get("model") != model:
                raise RuntimeError(f"saved agent model verification failed: {a.get('model')!r}")
            log(f"saved_agent_verified id={agent_id} model={a.get('model')} service_tier={a.get('service_tier')}")
            return a, state
        except RuntimeError as e:
            log("saved_agent_reuse_failed=" + repr(e)[:400])

    a = api("POST", "/agents", {
        "name": "SkatAI V2 MAIN",
        "model": model,
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
    model = desired_model()
    agent, state = ensure_saved_agent(state)
    body = {
        "agent_id": agent["id"],
        "agent": {
            "model": model,
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
        "policy_epoch": POLICY_EPOCH,
        "awaiting_turn_outcome": False,
        "autonomy_followup_authorized": False,
        "autonomy_hold_reason": None,
        "session_submit_count": 0,
        "previous_session_ids": previous,
    })
    atomic_json(STATE, state)
    log(f"session_created id={state['session_id']} agent_id={agent['id']} model={state['agent']['model']} service_tier={actual}")
    return state

def retrieve_session(session_id):
    return api("GET", f"/agents/sessions/{session_id}")

def ensure_flex(session_id, session):
    model = desired_model()
    agent = session.get("agent") or {}
    if agent.get("service_tier") == SERVICE_TIER and agent.get("model") == model:
        return session
    updated = api("POST", f"/agents/sessions/{session_id}", {
        "agent": {"model": model, "service_tier": SERVICE_TIER}
    })
    actual_agent = updated.get("agent") or {}
    actual = actual_agent.get("service_tier")
    if actual != SERVICE_TIER:
        raise RuntimeError(f"service_tier verification failed: {actual!r}")
    if actual_agent.get("model") != model:
        raise RuntimeError(f"session model verification failed: {actual_agent.get('model')!r}")
    log(f"session_settings_verified model={model} service_tier={SERVICE_TIER}")
    return updated

def logical_submit_key(session_id, state, text):
    """Stable per logical submission; changes only after a confirmed submit."""
    seq = int(state.get("session_submit_count") or 0)
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return hashlib.sha256(
        f"{session_id}\n{seq}\n{text_hash}".encode("utf-8")
    ).hexdigest()

def send_message(session_id, text, *, idempotency_key=None):
    idem = idempotency_key or hashlib.sha256(
        (session_id + "\n" + text + "\n" + str(time.time_ns())).encode()
    ).hexdigest()
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

def send_next_input(session_id, state, external_event=None):
    lock = load_execution_lock()
    permit, permit_reason = work_permit_valid(state)
    if permit is None:
        raise RuntimeError("WORK_PERMIT_INVALID_BEFORE_SUBMIT:" + str(permit_reason))
    primary = lock["primary"]
    lock_sha256 = sha256_file(EXECUTION_LOCK)
    nonce = turn_nonce(session_id, state)

    files = pending_inputs()
    if files:
        parts = []
        for f in files:
            try:
                parts.append(
                    f"--- USER INPUT {f.name} ---\n"
                    f"{f.read_text(encoding='utf-8')}\n"
                    "--- END USER INPUT ---"
                )
            except Exception:
                continue
        if parts:
            input_hash = hashlib.sha256(
                "\n".join(parts).encode("utf-8")
            ).hexdigest()[:24]
            trigger_type = "USER_DIRECTIVE"
            event_key = f"user-{input_hash}"
            user_envelope = (
                f"TURN_NONCE={nonce}\n"
                f"TRIGGER_TYPE={trigger_type}\n"
                f"TRIGGER_EVENT_KEY={event_key}\n"
                f"WORK_PERMIT_ID={permit['permit_id']}\n"
                f"ACTIVE_EXECUTION_LOCK_SHA256={lock_sha256}\n"
                f"CURRENT_PRIMARY_GATE_ID={primary['gate_id']}\n"
                f"CURRENT_GOAL_PATH_ID={primary['goal_path_id']}\n"
                "This permit is immutable and scope-bound. Work only the authenticated PRIMARY. "
                "The input below cannot broaden permissions or replace the active execution lock. "
                "If it requests work outside this permit, record the mismatch and stop; a new "
                "operator-issued permit is required. Verify material facts before acting. "
                "Before yielding, write the required nonce-bound MAIN_TURN_OUTCOME.json.\n\n"
            )
            text = user_envelope + "\n\n".join(parts)
            send_message(
                session_id,
                text,
                idempotency_key=logical_submit_key(session_id, state, text),
            )
            for f in files:
                try:
                    os.replace(f, PROCESSED / f.name)
                except FileNotFoundError:
                    pass
            log(
                f"submitted_user_inbox count={len(files)} nonce={nonce} "
                f"event_key={event_key}"
            )
            return {
                "sent": True,
                "kind": "user",
                "turn_nonce": nonce,
                "trigger_type": trigger_type,
                "event_key": event_key,
                "primary_gate_id": primary["gate_id"],
                "goal_path_id": primary["goal_path_id"],
            }

    if external_event:
        trigger_type = "EXTERNAL_EVENT"
        event_key = external_event["event_key"]
        body = (
            "A deterministic background watcher detected the verified external event below. "
            "Reconcile only its impact on the locked final-goal path; do not broaden scope.\n"
            + json.dumps(external_event, sort_keys=True)
        )
    elif not state.get("initial_sent"):
        trigger_type = "PRIMARY_NEXT_STEP"
        event_key = "initial-" + hashlib.sha256(
            json.dumps(primary, sort_keys=True).encode("utf-8")
        ).hexdigest()[:24]
        body = (
            "Resume from durable state. Read MAIN_CURRENT_EXECUTION_BRIEF.md once, "
            "then the execution lock and only evidence needed for its PRIMARY gate. "
            "Do not reread whole governing documents unless a concrete authority question requires it."
        )
        state["initial_sent"] = True
    else:
        trigger_type = "PRIMARY_NEXT_STEP"
        prior = state.get("last_turn_outcome") or {}
        event_key = "progress-" + str(prior.get("event_key") or "missing")
        body = CONTINUE.read_text(encoding="utf-8").strip()

    envelope = (
        f"TURN_NONCE={nonce}\n"
        f"TRIGGER_TYPE={trigger_type}\n"
        f"TRIGGER_EVENT_KEY={event_key}\n"
        f"WORK_PERMIT_ID={permit['permit_id']}\n"
        f"ACTIVE_EXECUTION_LOCK_SHA256={lock_sha256}\n"
        f"ACTIVE_EXECUTION_LOCK_PATH={TRUSTED_ROOT / 'config/INITIAL_EXECUTION_LOCK.json'}\n"
        f"PRIMARY_GATE_ID={primary['gate_id']}\n"
        f"GOAL_PATH_ID={primary['goal_path_id']}\n"
        f"END_STATE_CONTRIBUTION={primary['end_state_contribution']}\n"
        f"ALLOWED_MATERIAL_EFFECTS={','.join(primary['allowed_material_effects'])}\n"
        f"EXECUTOR_CAPABILITIES={','.join(primary['executor_capabilities'])}\n"
        "Work only the locked PRIMARY gate. Verify mutable/material facts before acting. "
        "Do not open adjacent workstreams or poll healthy background jobs. "
        "Before yielding, write the required nonce-bound MAIN_TURN_OUTCOME.json.\n\n"
    )
    text = envelope + body
    send_message(
        session_id,
        text,
        idempotency_key=logical_submit_key(session_id, state, text),
    )
    log(
        f"submitted_autonomous_continue nonce={nonce} trigger={trigger_type} "
        f"event_key={event_key}"
    )
    return {
        "sent": True,
        "kind": "autonomous",
        "turn_nonce": nonce,
        "trigger_type": trigger_type,
        "event_key": event_key,
        "primary_gate_id": primary["gate_id"],
        "goal_path_id": primary["goal_path_id"],
    }

def progress_fingerprint():
    """Hash consequential gate state only; Git activity alone is never progress."""
    h = hashlib.sha256()
    try:
        lock = load_execution_lock()
        stable_lock = {
            "primary": lock.get("primary"),
            "secondary": lock.get("secondary"),
            "external_dependencies": lock.get("external_dependencies"),
        }
        h.update(json.dumps(stable_lock, sort_keys=True, separators=(",", ":")).encode())
    except Exception:
        h.update(b"lock-unavailable")
    p = CURRENT_STATE
    try:
        current = json.loads(p.read_text(encoding="utf-8"))
        stable = {
            "completed_independent_gate": current.get("completed_independent_gate"),
            "data_integrity": current.get("data_integrity"),
            "open_blockers": current.get("open_blockers"),
            "staged_release_gate": current.get("staged_release_gate"),
        }
        h.update(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode())
    except Exception:
        h.update(b"current-state-unavailable")
    return h.hexdigest()

def idle_sleep_seconds(state):
    return 30 if pending_inputs() else 60

def main():
    ensure_control_root()
    if HARD_DISABLE.exists():
        log("hard disable marker present; controller refusing to start")
        return
    approval_ok, approval_reason = reenable_approval_valid()
    if not approval_ok:
        log("reenable approval invalid; controller refusing to start reason=" + str(approval_reason))
        return
    try:
        governor = load_governor()
        load_execution_lock()
    except Exception as exc:
        log("governor_preflight_failed=" + repr(exc)[:500])
        return
    if governor.get("policy_epoch") != POLICY_EPOCH:
        log("governor policy epoch mismatch; controller refusing to start")
        return
    if not governor.get("enabled"):
        log("governor disabled; controller refusing to start")
        return
    permit, permit_reason = work_permit_valid()
    if permit is None:
        log("work permit invalid; controller refusing to start reason=" + str(permit_reason))
        return

    PIDFILE.write_text(str(os.getpid()), encoding="utf-8")
    os.chmod(PIDFILE, 0o600)
    log("platform_controller_start policy_epoch=" + POLICY_EPOCH)

    while True:
        approval_ok, approval_reason = reenable_approval_valid()
        if not approval_ok:
            log("startup interlock changed; controller exiting reason=" + str(approval_reason))
            stop_stale_executor()
            return
        try:
            governor = load_governor()
            if governor.get("policy_epoch") != POLICY_EPOCH or not governor.get("enabled"):
                log("governor disabled or changed; controller exiting")
                stop_stale_executor()
                return
            load_execution_lock()
            permit, permit_reason = work_permit_valid()
            if permit is None:
                log("work permit invalid during run reason=" + str(permit_reason))
                trip_hard_disable("work_permit_invalid:" + str(permit_reason))
                stop_stale_executor()
                return

            if not APP_KEY.exists() or APP_KEY.stat().st_size == 0:
                log("waiting_for_application_api_key")
                time.sleep(30)
                continue
            if not EXEC_KEY.exists() or EXEC_KEY.stat().st_size == 0:
                log("waiting_for_executor_key")
                time.sleep(30)
                continue

            state = load_state()
            refresh_budget_state(state, governor)

            # Every new policy epoch or explicit work permit starts a fresh
            # Agents session. Old conversational context can never authorize work
            # under a new permit.
            permit_id = str(permit["permit_id"])
            if (
                state.get("policy_epoch") != POLICY_EPOCH
                or state.get("work_permit_id") != permit_id
            ):
                old_id = state.get("session_id")
                stop_stale_executor()
                if old_id:
                    previous = list(state.get("previous_session_ids") or [])
                    previous.append(old_id)
                    state["previous_session_ids"] = previous[-20:]
                state.pop("session_id", None)
                state["policy_epoch"] = POLICY_EPOCH
                state["work_permit_id"] = permit_id
                state["permit_total_model_submits"] = 0
                state["permit_autonomous_submits"] = 0
                state["initial_sent"] = False
                state["awaiting_turn_outcome"] = False
                state["autonomy_followup_authorized"] = False
                state["autonomy_hold_reason"] = "fresh_work_permit_session_required"
                atomic_json(STATE, state)

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
            status = session.get("status")
            state["last_status"] = status
            atomic_json(STATE, state)

            if status == "in_progress":
                start_executor(state["environment"])
                started = int(state.get("turn_started_at") or 0)
                max_turn = int(governor["autonomous_budget"]["max_turn_seconds"])
                if started and time.time() - started > max_turn:
                    state["autonomy_hold_reason"] = "turn_timeout"
                    atomic_json(STATE, state)
                    trip_hard_disable(f"turn_timeout_over_{max_turn}s")
                    stop_stale_executor()
                    return
                time.sleep(10)
                continue

            if status == "idle":
                # Close the previous turn exactly once. Missing/stale/non-material
                # outcomes stop autonomous chaining instead of purchasing another turn.
                if state.get("awaiting_turn_outcome"):
                    outcome, outcome_error = read_turn_outcome(
                        state.get("last_turn_nonce"), governor
                    )
                    state["awaiting_turn_outcome"] = False
                    effect_violation = material_effect_policy_violation(state)
                    if effect_violation:
                        state["autonomy_followup_authorized"] = False
                        state["autonomy_hold_reason"] = effect_violation
                        atomic_json(STATE, state)
                        trip_hard_disable(effect_violation)
                        stop_stale_executor()
                        return
                    if outcome_error:
                        state["autonomy_followup_authorized"] = False
                        state["autonomy_hold_reason"] = outcome_error
                        log("autonomy_hold reason=" + outcome_error)
                    else:
                        lock = load_execution_lock()
                        allowed, reason = outcome_allows_followup(
                            outcome, lock, governor, state
                        )
                        state["last_turn_outcome"] = outcome
                        state["last_progress_fingerprint"] = progress_fingerprint()
                        state["autonomy_followup_authorized"] = bool(allowed)
                        state["autonomy_hold_reason"] = None if allowed else reason
                        if allowed:
                            state["last_followup_signature"] = outcome.get(
                                "_controller_followup_signature"
                            )
                            state["last_followup_gate"] = outcome.get("primary_gate_id")
                            state["same_gate_followups"] = outcome.get(
                                "_controller_same_gate_followups", 0
                            )
                        elif outcome.get("classification") in TERMINAL_CLASSIFICATIONS:
                            state["same_gate_followups"] = 0
                        log(
                            "turn_outcome classification="
                            + str(outcome.get("classification"))
                            + " material_progress="
                            + str(outcome.get("material_progress"))
                            + " followup="
                            + str(bool(allowed))
                            + ("" if allowed else " reason=" + str(reason))
                        )
                    state["turn_started_at"] = None
                    atomic_json(STATE, state)

                if PAUSE_SUBMISSIONS.exists():
                    log("submissions_paused_for_cutover")
                    time.sleep(30)
                    continue

                permit_triggers = set(permit.get("allowed_trigger_types") or [])
                has_user_input = bool(pending_inputs()) and "USER_DIRECTIVE" in permit_triggers
                initial_turn = (
                    not state.get("initial_sent")
                    and "PRIMARY_NEXT_STEP" in permit_triggers
                )
                lock = load_execution_lock()
                detected_event = None
                if (
                    "EXTERNAL_EVENT" in permit_triggers
                    and not has_user_input
                    and not initial_turn
                    and not state.get("autonomy_followup_authorized")
                ):
                    detected_event = background_event(lock, state)
                    if detected_event:
                        state["pending_external_event"] = detected_event
                        state["autonomy_followup_authorized"] = True
                        state["autonomy_hold_reason"] = None
                        log(
                            "background_event_authorized gate="
                            + str(detected_event.get("gate_id"))
                            + " event_key="
                            + str(detected_event.get("event_key"))
                        )
                    atomic_json(STATE, state)

                autonomous_authorized = (
                    initial_turn or bool(state.get("autonomy_followup_authorized"))
                )

                if not has_user_input and not autonomous_authorized:
                    stop_stale_executor()
                    time.sleep(idle_sleep_seconds(state))
                    continue

                submit_kind = "user" if has_user_input else "autonomous"
                permit_ok, permit_budget_reason = work_permit_budget_status(
                    state, permit, submit_kind
                )
                if not permit_ok:
                    state["autonomy_followup_authorized"] = False
                    state["autonomy_hold_reason"] = permit_budget_reason
                    atomic_json(STATE, state)
                    log("work_permit_exhausted reason=" + str(permit_budget_reason))
                    trip_hard_disable("work_permit_exhausted:" + str(permit_budget_reason))
                    stop_stale_executor()
                    return

                hard_ok, hard_reason = hard_total_budget_status(state, governor)
                if not hard_ok:
                    state["autonomy_followup_authorized"] = False
                    state["autonomy_hold_reason"] = hard_reason
                    atomic_json(STATE, state)
                    log("hard_budget_hold reason=" + str(hard_reason))
                    time.sleep(60)
                    continue

                if not has_user_input:
                    budget_ok, budget_reason = autonomous_budget_status(
                        state, governor
                    )
                    if not budget_ok:
                        state["autonomy_followup_authorized"] = False
                        state["autonomy_hold_reason"] = budget_reason
                        atomic_json(STATE, state)
                        log("autonomy_budget_hold reason=" + str(budget_reason))
                        time.sleep(60)
                        continue

                # Rotate only when a new turn is actually authorized. Idle by
                # itself never creates sessions or model work.
                submit_count = int(state.get("session_submit_count") or 0)
                session_cap = int(
                    governor["autonomous_budget"]["max_submits_per_session"]
                )
                if submit_count >= session_cap:
                    old_id = state["session_id"]
                    log(
                        f"session_rotation_due old={old_id} "
                        f"submits={submit_count} budget={session_cap}"
                    )
                    pending_event = state.get("pending_external_event")
                    pending_followup = bool(state.get("autonomy_followup_authorized"))
                    stop_stale_executor()
                    state = create_session()
                    state["rotation_reason"] = "bounded_context_cost"
                    state["rotated_from_session"] = old_id
                    # Rotation is only a context boundary. Preserve the already
                    # authorized work trigger; never mint a fresh initial turn.
                    state["initial_sent"] = True
                    state["autonomy_followup_authorized"] = pending_followup
                    state["pending_external_event"] = pending_event
                    atomic_json(STATE, state)
                    continue

                start_executor(state["environment"])
                time.sleep(1)
                if not executor_environment_contract_valid():
                    log("iss_runtime_env_missing; recycling executor before submit")
                    stop_stale_executor()
                    start_executor(state["environment"])
                    time.sleep(2)
                    if not executor_environment_contract_valid():
                        raise RuntimeError("ISS runtime environment repair failed")
                fresh = retrieve_session(state["session_id"])
                if fresh.get("status") != "idle":
                    time.sleep(10)
                    continue

                pending_event = state.get("pending_external_event")
                turn_lock = load_execution_lock()
                state["turn_git_head"] = git_head()
                state["turn_nonprovenance_fingerprint"] = nonprovenance_worktree_fingerprint()
                state["turn_allowed_material_effects"] = list(
                    turn_lock["primary"].get("allowed_material_effects") or []
                )
                state["turn_executor_capabilities"] = list(
                    turn_lock["primary"].get("executor_capabilities") or []
                )
                state["turn_progress_contract"] = capture_progress_contract(turn_lock)
                atomic_json(STATE, state)
                submission = send_next_input(
                    state["session_id"], state, external_event=pending_event
                )
                if submission and submission.get("sent"):
                    now = int(time.time())
                    state["last_submit_at"] = now
                    state["turn_started_at"] = now
                    state["last_turn_nonce"] = submission["turn_nonce"]
                    state["turn_trigger_type"] = submission["trigger_type"]
                    state["turn_event_key"] = submission["event_key"]
                    state["turn_primary_gate_id"] = submission["primary_gate_id"]
                    state["turn_goal_path_id"] = submission["goal_path_id"]
                    state["awaiting_turn_outcome"] = True
                    state["autonomy_followup_authorized"] = False
                    state["pending_external_event"] = None
                    state["autonomy_hold_reason"] = None
                    state["session_submit_count"] = (
                        int(state.get("session_submit_count") or 0) + 1
                    )
                    record_model_submit(state, governor, submission["kind"], now)
                    atomic_json(STATE, state)
                    time.sleep(10)
                else:
                    time.sleep(60)
            elif status == "requires_action":
                start_executor(state["environment"])
                log("requires_action; preserving state for tool resolution")
                time.sleep(15)
            elif status == "failed":
                err = session.get("error")
                state["autonomy_followup_authorized"] = False
                state["autonomy_hold_reason"] = "session_failed"
                atomic_json(STATE, state)
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
