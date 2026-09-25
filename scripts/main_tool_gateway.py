#!/usr/bin/env python3
"""Lease-bound function-tool gateway for SkatAI V2 MAIN.

The model never receives a privileged shell. Every project read or material
change crosses this module and is checked against the root-owned execution lock.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import time
from typing import Any

MAX_PATCH_BYTES = 200_000
DEFAULT_MAX_OUTPUT_BYTES = 60_000


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def strict_json(path: Path) -> Any:
    def pairs(rows):
        out = {}
        for k, v in rows:
            if k in out:
                raise RuntimeError(f"DUPLICATE_JSON_KEY:{k}")
            out[k] = v
        return out
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)


def function_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "get_active_lease",
            "description": "Return the authenticated current PRIMARY lease, safe permit limits, and current controller turn metadata. Call this instead of trusting workspace copies.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "read_text",
            "description": "Read a bounded UTF-8 text range from the SkatAI V2 repository or runtime evidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "search_text",
            "description": "Search bounded repository/runtime text files for a literal string.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "query": {"type": "string"},
                    "glob": {"type": "string"},
                    "case_sensitive": {"type": "boolean"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["path", "query"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "list_paths",
            "description": "List bounded paths under a repository/runtime directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "depth": {"type": "integer", "minimum": 0, "maximum": 4},
                    "glob": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "git_query",
            "description": "Run a read-only Git query: status, diff, show, or log.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": ["status", "diff", "show", "log"]},
                    "path": {"type": "string"},
                    "ref": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 30},
                },
                "required": ["operation"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "apply_patch",
            "description": "Apply one standard unified Git diff only to lease-authorized writable_files. The patch MUST contain diff --git a/<path> b/<path>, --- a/<path>, +++ b/<path>, and @@ hunk headers. Never use *** Begin Patch / *** Update File syntax.",
            "parameters": {
                "type": "object",
                "properties": {"patch": {"type": "string", "description": "Literal standard Git unified diff beginning with diff --git; *** Begin Patch syntax is invalid."}},
                "required": ["patch"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "run_authorized_command",
            "description": "Run a deterministic command whose executable, script hash, arguments, and timeout are pre-authorized in the immutable lease.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command_id": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["command_id", "arguments"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "record_turn_outcome",
            "description": "Persist the nonce-bound turn outcome through the controller. This is the only accepted turn-outcome write path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "turn_nonce": {"type": "string"},
                    "primary_gate_id": {"type": "string"},
                    "goal_path_id": {"type": "string"},
                    "trigger_type": {"type": "string"},
                    "event_key": {"type": "string"},
                    "classification": {"type": "string"},
                    "material_progress": {"type": "boolean"},
                    "progress_kind": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "next_action": {"type": "string"},
                    "request_followup": {"type": "boolean"},
                },
                "required": [
                    "turn_nonce", "primary_gate_id", "goal_path_id", "trigger_type",
                    "event_key", "classification", "material_progress", "progress_kind",
                    "evidence", "next_action", "request_followup"
                ],
                "additionalProperties": False,
            },
        },
    ]


class ToolGateway:
    def __init__(
        self,
        *,
        repo_root: Path,
        runtime_root: Path,
        control_root: Path,
        execution_lock: Path,
        work_permit: Path,
        governor: Path,
        turn_outcome: Path,
        executor_user: str = "skatai-main-agent",
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.runtime_root = runtime_root.resolve()
        self.control_root = control_root.resolve()
        self.execution_lock = execution_lock
        self.work_permit = work_permit
        self.governor = governor
        self.turn_outcome = turn_outcome
        self.executor_user = executor_user

    def _limit(self, text: str) -> str:
        try:
            cap = int(strict_json(self.governor)["tool_budget"]["max_tool_output_bytes"])
        except Exception:
            cap = DEFAULT_MAX_OUTPUT_BYTES
        raw = text.encode("utf-8", "replace")
        if len(raw) <= cap:
            return text
        clipped = raw[:cap].decode("utf-8", "ignore")
        return clipped + f"\n...[TRUNCATED {len(raw)-cap} bytes]"

    def _safe_path(self, ref: str, *, must_exist: bool = True) -> Path:
        if not isinstance(ref, str) or not ref.strip():
            raise RuntimeError("PATH_INVALID")
        p = Path(ref.strip())
        if not p.is_absolute():
            p = self.repo_root / p
        resolved = p.resolve(strict=False)
        allowed = False
        for root in (self.repo_root, self.runtime_root):
            try:
                resolved.relative_to(root)
                allowed = True
                break
            except ValueError:
                pass
        if not allowed:
            raise RuntimeError("PATH_OUTSIDE_ALLOWED_ROOTS")
        if must_exist and not resolved.exists():
            raise RuntimeError("PATH_MISSING")
        return resolved

    def _lock(self) -> dict[str, Any]:
        raw = strict_json(self.execution_lock)
        if not isinstance(raw.get("primary"), dict):
            raise RuntimeError("ACTIVE_LOCK_INVALID")
        return raw

    def _permit(self) -> dict[str, Any]:
        return strict_json(self.work_permit)

    def _assert_active_permit(self, state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        lock = self._lock()
        permit = self._permit()
        if permit.get("schema") != "skatai.v2.main-work-permit.v1":
            raise RuntimeError("TOOL_PERMIT_SCHEMA_INVALID")
        if permit.get("mode") != "BOUNDED_GATE" or permit.get("approved") is not True:
            raise RuntimeError("TOOL_PERMIT_NOT_ACTIVE")
        try:
            if float(permit["expires_at_epoch"]) <= time.time():
                raise RuntimeError("TOOL_PERMIT_EXPIRED")
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("TOOL_PERMIT_EXPIRY_INVALID")
        if permit.get("execution_lock_sha256") != sha256_file(self.execution_lock):
            raise RuntimeError("TOOL_PERMIT_LOCK_HASH_MISMATCH")
        primary = lock["primary"]
        if permit.get("primary_gate_id") != primary.get("gate_id"):
            raise RuntimeError("TOOL_PERMIT_GATE_MISMATCH")
        if permit.get("goal_path_id") != primary.get("goal_path_id"):
            raise RuntimeError("TOOL_PERMIT_GOAL_MISMATCH")
        permit_id = str(permit.get("permit_id") or "")
        if state.get("work_permit_id") not in {None, permit_id}:
            raise RuntimeError("TOOL_PERMIT_STATE_MISMATCH")
        if state.get("turn_primary_gate_id") not in {None, primary.get("gate_id")}:
            raise RuntimeError("TOOL_TURN_GATE_MISMATCH")
        return lock, permit

    def _get_active_lease(self, state: dict[str, Any]) -> dict[str, Any]:
        lock = self._lock()
        permit = self._permit()
        safe_permit = {
            k: permit.get(k)
            for k in (
                "permit_id", "mode", "expires_at_epoch", "max_model_submits_total",
                "max_autonomous_submits_total", "max_total_tokens",
                "allowed_trigger_types", "primary_gate_id", "goal_path_id",
            )
        }
        policy_hashes = permit.get("policy_hashes") or {}
        authority_hashes = {
            k: policy_hashes.get(k)
            for k in (
                "founding_spec", "work_prompt", "master_prompt", "goal_policy",
                "agent_instructions", "governor", "execution_lock",
            )
            if policy_hashes.get(k)
        }
        return {
            "authority_hashes": authority_hashes,
            "primary": lock["primary"],
            "secondary": lock.get("secondary"),
            "external_dependencies": lock.get("external_dependencies", []),
            "permit": safe_permit,
            "turn": {
                "turn_nonce": state.get("last_turn_nonce"),
                "trigger_type": state.get("turn_trigger_type"),
                "event_key": state.get("turn_event_key"),
                "primary_gate_id": state.get("turn_primary_gate_id"),
                "goal_path_id": state.get("turn_goal_path_id"),
            },
        }

    def _read_text(self, args: dict[str, Any]) -> dict[str, Any]:
        p = self._safe_path(args["path"])
        if not p.is_file() or p.stat().st_size > 8 * 1024 * 1024:
            raise RuntimeError("READ_TEXT_FILE_INVALID")
        start = max(1, int(args.get("start_line") or 1))
        end = int(args.get("end_line") or (start + 399))
        end = min(end, start + 399)
        if end < start:
            raise RuntimeError("READ_TEXT_RANGE_INVALID")
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        selected = lines[start - 1:end]
        return {
            "path": str(p),
            "start_line": start,
            "end_line": min(end, len(lines)),
            "total_lines": len(lines),
            "text": self._limit("\n".join(selected)),
        }

    def _search_text(self, args: dict[str, Any]) -> dict[str, Any]:
        base = self._safe_path(args["path"])
        query = str(args["query"])
        if not query or len(query) > 1000:
            raise RuntimeError("SEARCH_QUERY_INVALID")
        glob_pat = str(args.get("glob") or "*")
        case_sensitive = bool(args.get("case_sensitive", False))
        max_results = min(200, int(args.get("max_results") or 50))
        q = query if case_sensitive else query.lower()
        files: list[Path]
        if base.is_file():
            files = [base]
        else:
            files = []
            for root, dirs, names in os.walk(base):
                dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv"}]
                for name in names:
                    if fnmatch.fnmatch(name, glob_pat):
                        files.append(Path(root) / name)
                if len(files) > 5000:
                    break
        matches = []
        for p in files[:5000]:
            try:
                if p.stat().st_size > 4 * 1024 * 1024:
                    continue
                text = p.read_text(encoding="utf-8", errors="strict")
            except Exception:
                continue
            for idx, line in enumerate(text.splitlines(), 1):
                hay = line if case_sensitive else line.lower()
                if q in hay:
                    matches.append({"path": str(p), "line": idx, "text": line[:1000]})
                    if len(matches) >= max_results:
                        return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": False}

    def _list_paths(self, args: dict[str, Any]) -> dict[str, Any]:
        base = self._safe_path(args["path"])
        if not base.is_dir():
            raise RuntimeError("LIST_PATH_NOT_DIRECTORY")
        depth = min(4, int(args.get("depth") or 2))
        glob_pat = str(args.get("glob") or "*")
        max_results = min(500, int(args.get("max_results") or 200))
        rows = []
        base_parts = len(base.parts)
        for root, dirs, files in os.walk(base):
            rel_depth = len(Path(root).parts) - base_parts
            if rel_depth >= depth:
                dirs[:] = []
            dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv"}]
            for name in sorted(dirs + files):
                if not fnmatch.fnmatch(name, glob_pat):
                    continue
                p = Path(root) / name
                rows.append({
                    "path": str(p),
                    "type": "dir" if p.is_dir() else "file",
                    "size": None if p.is_dir() else p.stat().st_size,
                })
                if len(rows) >= max_results:
                    return {"paths": rows, "truncated": True}
        return {"paths": rows, "truncated": False}

    def _git_query(self, args: dict[str, Any]) -> dict[str, Any]:
        op = args["operation"]
        cmd = ["git", "-C", str(self.repo_root)]
        if op == "status":
            cmd += ["status", "--short", "--branch"]
        elif op == "diff":
            cmd += ["diff", "--no-ext-diff"]
            if args.get("path"):
                p = self._safe_path(args["path"])
                rel = p.relative_to(self.repo_root)
                cmd += ["--", str(rel)]
        elif op == "show":
            ref = str(args.get("ref") or "HEAD")
            if ref.startswith("-") or not re.fullmatch(r"[A-Za-z0-9._/@~^{}+-]{1,160}", ref):
                raise RuntimeError("GIT_REF_INVALID")
            cmd += ["show", "--stat", "--oneline", "--decorate", ref]
            if args.get("path"):
                p = self._safe_path(args["path"])
                cmd += ["--", str(p.relative_to(self.repo_root))]
        elif op == "log":
            limit = min(30, int(args.get("limit") or 10))
            cmd += ["log", f"-{limit}", "--oneline", "--decorate"]
        else:
            raise RuntimeError("GIT_OPERATION_INVALID")
        cp = subprocess.run(cmd, text=True, capture_output=True, timeout=30)
        return {
            "returncode": cp.returncode,
            "stdout": self._limit(cp.stdout),
            "stderr": self._limit(cp.stderr),
        }

    @staticmethod
    def _patch_paths(patch: str) -> list[str]:
        if "*** Begin Patch" in patch or "*** Update File:" in patch:
            raise RuntimeError("PATCH_FORMAT_INVALID_BEGIN_PATCH_USE_GIT_UNIFIED_DIFF")
        paths = []
        forbidden = (
            "new file mode ", "deleted file mode ", "rename from ", "rename to ",
            "GIT binary patch", "old mode ", "new mode ",
        )
        for line in patch.splitlines():
            if line.startswith(forbidden):
                raise RuntimeError("PATCH_STRUCTURAL_CHANGE_FORBIDDEN")
            if line.startswith("diff --git "):
                parts = line.split()
                if len(parts) != 4 or not parts[2].startswith("a/") or not parts[3].startswith("b/"):
                    raise RuntimeError("PATCH_HEADER_INVALID")
                a = parts[2][2:]
                b = parts[3][2:]
                if a != b or not a or a.startswith("/") or ".." in Path(a).parts:
                    raise RuntimeError("PATCH_PATH_INVALID")
                paths.append(a)
        if not paths:
            raise RuntimeError("PATCH_NO_PATHS")
        return sorted(set(paths))

    def _apply_patch(self, args: dict[str, Any]) -> dict[str, Any]:
        patch = str(args["patch"])
        if len(patch.encode("utf-8")) > MAX_PATCH_BYTES:
            raise RuntimeError("PATCH_TOO_LARGE")
        lock = self._lock()
        primary = lock["primary"]
        effects = set(primary.get("allowed_material_effects") or [])
        if not ({"LOCAL_GIT_CHANGE", "PROVENANCE_WRITE"} & effects):
            raise RuntimeError("PATCH_EFFECT_NOT_AUTHORIZED")
        allowed = set(primary.get("writable_files") or [])
        paths = self._patch_paths(patch)
        if not set(paths).issubset(allowed):
            raise RuntimeError("PATCH_PATH_NOT_AUTHORIZED:" + ",".join(sorted(set(paths) - allowed)))
        resolved = []
        originals: dict[Path, bytes] = {}
        for rel in paths:
            p = self._safe_path(rel)
            if not p.is_file() or p.is_symlink():
                raise RuntimeError("PATCH_TARGET_INVALID:" + rel)
            resolved.append(p)
            originals[p] = p.read_bytes()
        check = subprocess.run(
            ["git", "-C", str(self.repo_root), "apply", "--check", "--whitespace=nowarn", "-"],
            input=patch, text=True, capture_output=True, timeout=30,
        )
        if check.returncode != 0:
            # Retry/recovery: the exact patch may already be applied.
            reverse = subprocess.run(
                ["git", "-C", str(self.repo_root), "apply", "--reverse", "--check", "--whitespace=nowarn", "-"],
                input=patch, text=True, capture_output=True, timeout=30,
            )
            if reverse.returncode == 0:
                return {"status": "ALREADY_APPLIED", "paths": paths}
            raise RuntimeError("PATCH_CHECK_FAILED:" + self._limit(check.stderr))
        apply = subprocess.run(
            ["git", "-C", str(self.repo_root), "apply", "--whitespace=nowarn", "-"],
            input=patch, text=True, capture_output=True, timeout=30,
        )
        if apply.returncode != 0:
            raise RuntimeError("PATCH_APPLY_FAILED:" + self._limit(apply.stderr))
        try:
            for p in resolved:
                if p.suffix == ".json":
                    strict_json(p)
        except Exception:
            for p, data in originals.items():
                p.write_bytes(data)
            raise RuntimeError("PATCH_POSTVALIDATION_FAILED")
        return {
            "status": "APPLIED",
            "paths": paths,
            "sha256": {str(p.relative_to(self.repo_root)): sha256_file(p) for p in resolved},
        }

    def _resolve_arg(self, spec: dict[str, Any], value: Any) -> str:
        typ = spec.get("type")
        if typ == "sha256":
            s = str(value)
            if not re.fullmatch(r"[0-9a-f]{64}", s):
                raise RuntimeError("COMMAND_SHA256_ARG_INVALID")
            return s
        if typ == "path":
            p = self._safe_path(str(value))
            kind = spec.get("kind")
            if kind == "file" and not p.is_file():
                raise RuntimeError("COMMAND_PATH_NOT_FILE")
            if kind == "dir" and not p.is_dir():
                raise RuntimeError("COMMAND_PATH_NOT_DIR")
            return str(p)
        if typ == "string":
            s = str(value)
            if len(s) > int(spec.get("max_length") or 1000):
                raise RuntimeError("COMMAND_STRING_ARG_TOO_LONG")
            return s
        if typ == "integer":
            i = int(value)
            lo = spec.get("minimum")
            hi = spec.get("maximum")
            if lo is not None and i < int(lo):
                raise RuntimeError("COMMAND_INTEGER_ARG_LOW")
            if hi is not None and i > int(hi):
                raise RuntimeError("COMMAND_INTEGER_ARG_HIGH")
            return str(i)
        raise RuntimeError("COMMAND_ARG_TYPE_INVALID")

    def _run_authorized_command(self, args: dict[str, Any]) -> dict[str, Any]:
        lock = self._lock()
        primary = lock["primary"]
        command_id = str(args["command_id"])
        specs = {
            row.get("id"): row
            for row in primary.get("authorized_commands") or []
            if isinstance(row, dict) and row.get("id")
        }
        spec = specs.get(command_id)
        if spec is None:
            raise RuntimeError("COMMAND_NOT_AUTHORIZED")
        argv_prefix = spec.get("argv_prefix")
        if not isinstance(argv_prefix, list) or not argv_prefix:
            raise RuntimeError("COMMAND_SPEC_INVALID")
        script = spec.get("script")
        expected = spec.get("script_sha256")
        if script:
            p = self._safe_path(str(script))
            if not p.is_file() or not expected or sha256_file(p) != expected:
                raise RuntimeError("COMMAND_SCRIPT_IDENTITY_MISMATCH")
        supplied = args.get("arguments")
        if not isinstance(supplied, dict):
            raise RuntimeError("COMMAND_ARGUMENTS_INVALID")
        schema = spec.get("arg_schema") or {}
        if set(supplied) != set(schema):
            raise RuntimeError("COMMAND_ARGUMENT_KEYS_MISMATCH")
        argv = [str(x) for x in argv_prefix]
        for key, arg_spec in schema.items():
            value = self._resolve_arg(arg_spec, supplied[key])
            flag = arg_spec.get("flag")
            if flag:
                argv += [str(flag), value]
            else:
                argv.append(value)
        timeout_s = min(1800, max(1, int(spec.get("timeout_seconds") or 300)))
        account = pwd.getpwnam(self.executor_user)
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "PYTHONPATH": str(self.repo_root / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": f"/var/lib/{self.executor_user}/tmp",
        }

        def demote():
            os.initgroups(self.executor_user, account.pw_gid)
            os.setgid(account.pw_gid)
            os.setuid(account.pw_uid)

        cp = subprocess.run(
            argv,
            cwd=str(self.repo_root),
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            preexec_fn=demote,
        )
        return {
            "command_id": command_id,
            "returncode": cp.returncode,
            "stdout": self._limit(cp.stdout),
            "stderr": self._limit(cp.stderr),
        }

    def _record_turn_outcome(self, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        expected = {
            "turn_nonce": state.get("last_turn_nonce"),
            "primary_gate_id": state.get("turn_primary_gate_id"),
            "goal_path_id": state.get("turn_goal_path_id"),
            "trigger_type": state.get("turn_trigger_type"),
            "event_key": state.get("turn_event_key"),
        }
        for key, value in expected.items():
            if not value or args.get(key) != value:
                raise RuntimeError("OUTCOME_BINDING_MISMATCH:" + key)
        raw = dict(args)
        raw["schema"] = "skatai.v2.main-turn-outcome.v2"
        tmp = self.turn_outcome.with_suffix(".json.tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.turn_outcome)
        return {"status": "RECORDED", "turn_nonce": raw["turn_nonce"]}

    def dispatch(self, name: str, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        self._assert_active_permit(state)
        if name == "get_active_lease":
            return self._get_active_lease(state)
        if name == "read_text":
            return self._read_text(args)
        if name == "search_text":
            return self._search_text(args)
        if name == "list_paths":
            return self._list_paths(args)
        if name == "git_query":
            return self._git_query(args)
        if name == "apply_patch":
            return self._apply_patch(args)
        if name == "run_authorized_command":
            return self._run_authorized_command(args)
        if name == "record_turn_outcome":
            return self._record_turn_outcome(args, state)
        raise RuntimeError("UNKNOWN_TOOL:" + str(name))
