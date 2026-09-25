from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Any


SCHEMA = "skatai.v2.frozen-r9-supervisor.v1"
WORKER_TOKEN = "skatai.iss.gate_worker"
LAUNCHER_TOKEN = "launch-r9-pinned.sh"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _processes_containing(token: str) -> list[int]:
    out: list[int] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return out
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", "replace"
            )
        except OSError:
            continue
        if token in cmd:
            out.append(int(entry.name))
    return sorted(out)


def _active_games(runtime: Path) -> int:
    path = runtime / "active-games.json"
    if not path.is_file():
        return 0
    raw = json.loads(path.read_text(encoding="utf-8"))
    games = raw.get("games")
    if not isinstance(games, list):
        raise RuntimeError("ACTIVE_GAMES_NOT_LIST")
    return len(games)


def _pending_effects(runtime: Path, frozen_repo: Path) -> int:
    effects = runtime / "effects.jsonl"
    if not effects.exists():
        return 0
    code = (
        "from pathlib import Path;"
        "from skatai.iss.effects import ISSEffectJournal;"
        f"print(len(ISSEffectJournal(Path({str(effects)!r})).pending()))"
    )
    env = dict(os.environ)
    src = str(frozen_repo / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    raw = subprocess.check_output(
        [sys.executable, "-c", code],
        text=True,
        timeout=20,
        env=env,
    ).strip()
    return int(raw)


def _next_target(runtime: Path) -> int | None | str:
    path = runtime / "status.json"
    if not path.is_file():
        return "UNKNOWN"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "next_per_arm_target" not in raw:
        return "UNKNOWN"
    value = raw["next_per_arm_target"]
    if value is None:
        return None
    return int(value)


def evaluate_restart_state(
    *,
    runtime: Path,
    frozen_repo: Path,
    worker_pids: list[int] | None = None,
    launcher_pids: list[int] | None = None,
    pending_effects: int | None = None,
) -> dict[str, Any]:
    runtime = Path(runtime)
    frozen_repo = Path(frozen_repo)
    workers = (
        _processes_containing(WORKER_TOKEN)
        if worker_pids is None
        else list(worker_pids)
    )
    launchers = (
        _processes_containing(LAUNCHER_TOKEN)
        if launcher_pids is None
        else list(launcher_pids)
    )
    active = _active_games(runtime)
    pending = (
        _pending_effects(runtime, frozen_repo)
        if pending_effects is None
        else int(pending_effects)
    )
    target = _next_target(runtime)

    if (runtime / ".supervisor-disable").exists():
        state = "MANUAL_HOLD"
    elif workers:
        state = "RUNNING"
    elif launchers:
        state = "BOOTSTRAPPING"
    elif target is None:
        state = "CAMPAIGN_COMPLETE"
    elif active or pending:
        state = "BLOCKED_RECONCILIATION"
    else:
        state = "SAFE_TO_RESTART"

    return {
        "schema": SCHEMA,
        "state": state,
        "worker_pids": workers,
        "launcher_pids": launchers,
        "active_games": active,
        "pending_effects": pending,
        "next_per_arm_target": target,
    }


def load_runtime_env(path: Path) -> dict[str, str]:
    allowed = {"ISS_HOST", "ISS_PORT", "ISS_CLIENT_ID", "ISS_PASSWORD_FILE"}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        if "=" not in line:
            raise RuntimeError("BAD_RUNTIME_ENV_LINE")
        key, encoded = line.split("=", 1)
        key = key.strip()
        if key not in allowed:
            raise RuntimeError(f"UNEXPECTED_RUNTIME_ENV_KEY:{key}")
        parsed = shlex.split(encoded, posix=True)
        if len(parsed) != 1:
            raise RuntimeError(f"BAD_RUNTIME_ENV_VALUE:{key}")
        values[key] = parsed[0]
    missing = sorted(allowed - values.keys())
    if missing:
        raise RuntimeError("MISSING_RUNTIME_ENV_KEYS:" + ",".join(missing))
    secret = Path(values["ISS_PASSWORD_FILE"])
    if not secret.is_file() or secret.stat().st_size <= 0:
        raise RuntimeError("ISS_PASSWORD_FILE_UNAVAILABLE")
    return values


def supervise(
    *,
    runtime: Path,
    frozen_repo: Path,
    launcher: Path,
    env_file: Path,
    poll_s: float = 5.0,
    blocked_poll_s: float = 30.0,
) -> None:
    runtime.mkdir(parents=True, exist_ok=True)
    lock_path = runtime / ".supervisor.lock"
    status_path = runtime / "supervisor-status.json"
    log_path = runtime / "supervisor-worker.log"

    lock = lock_path.open("a+")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("R9_SUPERVISOR_ALREADY_RUNNING")

    failures = 0
    while True:
        state = evaluate_restart_state(runtime=runtime, frozen_repo=frozen_repo)
        state["captured_unix_ns"] = time.time_ns()
        _atomic_json(status_path, state)

        if state["state"] == "CAMPAIGN_COMPLETE":
            return
        if state["state"] in {"RUNNING", "BOOTSTRAPPING"}:
            failures = 0
            time.sleep(poll_s)
            continue
        if state["state"] in {"BLOCKED_RECONCILIATION", "MANUAL_HOLD"}:
            time.sleep(blocked_poll_s)
            continue

        env = dict(os.environ)
        env.update(load_runtime_env(env_file))
        with log_path.open("ab", buffering=0) as log:
            proc = subprocess.Popen(
                [str(launcher)],
                cwd=str(runtime),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            rc = proc.wait()
        failures += 1
        _atomic_json(
            status_path,
            {
                "schema": SCHEMA,
                "state": "WORKER_EXITED",
                "exit_code": int(rc),
                "failure_streak": failures,
                "captured_unix_ns": time.time_ns(),
            },
        )
        # Bound repeated local/bootstrap failure churn. A successful long-lived
        # worker resets failures on the next RUNNING observation.
        time.sleep(min(300.0, max(poll_s, poll_s * (2 ** min(failures, 6)))))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--runtime",
        type=Path,
        default=Path("/workspace/skatai-v2-runtime/iss/external-gate-r9"),
    )
    p.add_argument(
        "--frozen-repo",
        type=Path,
        default=Path("/workspace/skatai-v2-wt-r9-ouvert-outbound"),
    )
    p.add_argument(
        "--launcher",
        type=Path,
        default=Path(
            "/workspace/skatai-v2-runtime/iss/external-gate-r9/launch-r9-pinned.sh"
        ),
    )
    p.add_argument(
        "--env-file",
        type=Path,
        default=Path("/workspace/skatai-v2-runtime/iss/iss-runtime.env"),
    )
    p.add_argument("--check", action="store_true")
    args = p.parse_args()

    if args.check:
        print(
            json.dumps(
                evaluate_restart_state(
                    runtime=args.runtime,
                    frozen_repo=args.frozen_repo,
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    supervise(
        runtime=args.runtime,
        frozen_repo=args.frozen_repo,
        launcher=args.launcher,
        env_file=args.env_file,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
