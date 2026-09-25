from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import statistics
import subprocess
import tempfile
import threading
import time
from typing import Any, Iterable, Mapping

from skatai.evaluation.iss_campaign import (
    ARMS,
    DEFAULT_OPPONENTS,
    LOOKS_PER_ARM,
    Stratum,
    observed_counts,
    quota_status,
    target_quotas,
    next_targets,
)
from skatai.evaluation.iss_gate import ISSGameOutcome, decide_external_gate
from skatai.evaluation.iss_identity import load_identities
from skatai.evaluation.iss_ledger import ISSGateLedger, ISSGateLedgerRecord
from skatai.evaluation.iss_readiness import assess_iss_gate_readiness, sha256_file
from skatai.evaluation.iss_result import ISSResultError, live_game_result, player_seat
from skatai.iss.bridge import ISSSkatAIDecisionProvider
from skatai.iss.client import ISSClientCore, ISSClientPolicy, client_from_environment
from skatai.iss.effects import ISSAuthorityGuard, ISSEffectJournal
from skatai.iss.transport import ISSTransportError
from skatai.iss.service import (
    command_create_table,
    command_invite,
    command_keepalive,
    command_leave,
    command_ready,
    parse_service_line,
    parse_table_start_payload,
)
from skatai.runtime.skatzero_backend import build_b0_skat_ai, build_b1_skat_ai

SCHEMA = "skatai.v2.external-iss-gate-worker.v1"
PRIMARY_STACKS = tuple(DEFAULT_OPPONENTS)


class ISSGateWorkerError(RuntimeError):
    pass


@dataclass(frozen=True)
class GatePaths:
    repo_root: Path
    runtime_root: Path
    skatzero_root: Path
    skatzero_python: Path
    b1_model: Path

    @classmethod
    def defaults(cls) -> "GatePaths":
        return cls(
            repo_root=Path(os.environ.get("SKATAI_V2_ROOT", "/workspace/skatai-v2")),
            runtime_root=Path(
                os.environ.get(
                    "ISS_GATE_RUNTIME_ROOT",
                    "/workspace/skatai-v2-runtime/iss/external-gate",
                )
            ),
            skatzero_root=Path(
                os.environ.get("SKATZERO_ROOT", "/tmp/skatai-v2-b0")
            ),
            skatzero_python=Path(
                os.environ.get(
                    "SKATZERO_PYTHON",
                    "/tmp/skatai-v2-b0-venv/bin/python",
                )
            ),
            b1_model=Path(
                os.environ.get(
                    "B1_MODEL",
                    "/tmp/skatai-v2-b1-linearish-full-v1/model.pt",
                )
            ),
        )


@dataclass(frozen=True)
class GameAssignment:
    arm: str
    stack: str
    seat: int
    per_arm_target: int
    primary: bool


@dataclass(frozen=True)
class ActiveGame:
    assignment: GameAssignment
    protocol_offset: int
    effect_offset: int


ACTIVE_GAMES_SCHEMA = "skatai.v2.external-iss-active-games.v1"


@dataclass(frozen=True)
class ReconnectPolicy:
    base_delay_s: float = 2.0
    max_delay_s: float = 60.0
    attempts_per_cycle: int = 12
    cooldown_s: float = 300.0

    def validate(self) -> None:
        if self.base_delay_s <= 0 or self.max_delay_s <= 0:
            raise ValueError("RECONNECT_DELAY_MUST_BE_POSITIVE")
        if self.max_delay_s < self.base_delay_s:
            raise ValueError("RECONNECT_MAX_DELAY_LT_BASE")
        if self.attempts_per_cycle < 1:
            raise ValueError("RECONNECT_ATTEMPTS_PER_CYCLE_LT_ONE")
        if self.cooldown_s <= 0:
            raise ValueError("RECONNECT_COOLDOWN_MUST_BE_POSITIVE")


@dataclass(frozen=True)
class MirrorPolicy:
    batch_games: int = 2
    max_delay_s: float = 60.0
    retry_delay_s: float = 10.0
    poll_interval_s: float = 1.0
    max_pending_games: int = 4
    max_pending_bytes: int = 64 * 1024 * 1024
    max_backlog_age_s: float = 120.0

    def validate(self) -> None:
        if self.batch_games < 1:
            raise ValueError("MIRROR_BATCH_GAMES_LT_ONE")
        if self.max_delay_s <= 0:
            raise ValueError("MIRROR_MAX_DELAY_MUST_BE_POSITIVE")
        if self.retry_delay_s <= 0:
            raise ValueError("MIRROR_RETRY_DELAY_MUST_BE_POSITIVE")
        if self.poll_interval_s <= 0:
            raise ValueError("MIRROR_POLL_INTERVAL_MUST_BE_POSITIVE")
        if self.max_pending_games < self.batch_games:
            raise ValueError("MIRROR_MAX_PENDING_GAMES_LT_BATCH")
        if self.max_pending_bytes <= 0:
            raise ValueError("MIRROR_MAX_PENDING_BYTES_MUST_BE_POSITIVE")
        if self.max_backlog_age_s < self.max_delay_s:
            raise ValueError("MIRROR_MAX_BACKLOG_AGE_LT_DELAY")


def mirror_policy_from_environment(
    environ: Mapping[str, str] | None = None,
) -> MirrorPolicy:
    env = os.environ if environ is None else environ
    try:
        policy = MirrorPolicy(
            batch_games=int(env.get("ISS_GATE_MIRROR_BATCH_GAMES", "2")),
            max_delay_s=float(env.get("ISS_GATE_MIRROR_MAX_DELAY_S", "60")),
            retry_delay_s=float(env.get("ISS_GATE_MIRROR_RETRY_DELAY_S", "10")),
            poll_interval_s=float(env.get("ISS_GATE_MIRROR_POLL_INTERVAL_S", "1")),
            max_pending_games=int(env.get("ISS_GATE_MIRROR_MAX_PENDING_GAMES", "4")),
            max_pending_bytes=int(
                env.get("ISS_GATE_MIRROR_MAX_PENDING_BYTES", str(64 * 1024 * 1024))
            ),
            max_backlog_age_s=float(
                env.get("ISS_GATE_MIRROR_MAX_BACKLOG_AGE_S", "120")
            ),
        )
    except ValueError as exc:
        raise ValueError("BAD_ISS_MIRROR_ENV") from exc
    policy.validate()
    return policy


def reconnect_policy_from_environment(
    environ: Mapping[str, str] | None = None,
) -> ReconnectPolicy:
    env = os.environ if environ is None else environ
    try:
        policy = ReconnectPolicy(
            base_delay_s=float(env.get("ISS_RECONNECT_BASE_DELAY_S", "2")),
            max_delay_s=float(env.get("ISS_RECONNECT_MAX_DELAY_S", "60")),
            attempts_per_cycle=int(env.get("ISS_RECONNECT_ATTEMPTS_PER_CYCLE", "12")),
            cooldown_s=float(env.get("ISS_RECONNECT_COOLDOWN_S", "300")),
        )
    except ValueError as exc:
        raise ValueError("BAD_ISS_RECONNECT_ENV") from exc
    policy.validate()
    return policy


def reconnect_delay_s(policy: ReconnectPolicy, attempt_in_cycle: int) -> float:
    policy.validate()
    if attempt_in_cycle < 1:
        raise ValueError("RECONNECT_ATTEMPT_LT_ONE")
    return min(
        policy.max_delay_s,
        policy.base_delay_s * (2 ** (attempt_in_cycle - 1)),
    )


def recoverable_transport_error(exc: ISSTransportError) -> bool:
    text = str(exc)
    return (
        text == "REMOTE_EOF"
        or text == "NOT_CONNECTED"
        or text.startswith("CONNECT_FAILED:")
        or text.startswith("READ_FAILED:")
        or text.startswith("WRITE_FAILED:")
    )


def active_games_payload(
    games: Mapping[tuple[str, int], ActiveGame],
    *,
    source_commit: str,
) -> dict[str, Any]:
    return {
        "schema": ACTIVE_GAMES_SCHEMA,
        "source_commit": str(source_commit),
        "games": [
            {
                "table_id": table_id,
                "game_sequence": int(game_sequence),
                "assignment": {
                    "arm": active.assignment.arm,
                    "stack": active.assignment.stack,
                    "seat": active.assignment.seat,
                    "per_arm_target": active.assignment.per_arm_target,
                    "primary": active.assignment.primary,
                },
                "protocol_offset": int(active.protocol_offset),
                "effect_offset": int(active.effect_offset),
            }
            for (table_id, game_sequence), active in sorted(games.items())
        ],
    }


def parse_active_games_payload(
    payload: Mapping[str, Any],
    *,
    expected_source_commit: str,
) -> dict[tuple[str, int], ActiveGame]:
    if payload.get("schema") != ACTIVE_GAMES_SCHEMA:
        raise ISSGateWorkerError("ACTIVE_GAMES_SCHEMA_MISMATCH")
    rows = payload.get("games")
    if not isinstance(rows, list):
        raise ISSGateWorkerError("ACTIVE_GAMES_ROWS_NOT_LIST")
    if rows and payload.get("source_commit") != expected_source_commit:
        raise ISSGateWorkerError(
            "ACTIVE_GAME_SOURCE_COMMIT_MISMATCH:"
            + str(payload.get("source_commit"))
            + "!="
            + str(expected_source_commit)
        )
    out: dict[tuple[str, int], ActiveGame] = {}
    for row in rows:
        key = (str(row["table_id"]), int(row["game_sequence"]))
        if key in out:
            raise ISSGateWorkerError(f"DUPLICATE_ACTIVE_GAME:{key}")
        a = row["assignment"]
        assignment = GameAssignment(
            arm=str(a["arm"]),
            stack=str(a["stack"]),
            seat=int(a["seat"]),
            per_arm_target=int(a["per_arm_target"]),
            primary=bool(a["primary"]),
        )
        if assignment.arm not in ARMS or assignment.seat not in (0, 1, 2):
            raise ISSGateWorkerError(f"BAD_ACTIVE_GAME_ASSIGNMENT:{key}")
        out[key] = ActiveGame(
            assignment=assignment,
            protocol_offset=int(row["protocol_offset"]),
            effect_offset=int(row["effect_offset"]),
        )
    if len(out) > 1:
        raise ISSGateWorkerError("MULTIPLE_ACTIVE_ISS_GAMES_NOT_SUPPORTED")
    return out


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def reconcile_mirror_departure(
    marker: Path, protocol_journal: Path, *, source_commit: str
) -> bool:
    """Confirm an interrupted LEAVE only from later, fsynced ISS traffic.

    A send return alone cannot prove departure. The marker binds a byte offset
    before LEAVE, so an older DESTROY for the same table cannot clear it.
    Unknown or legacy markers remain blocked for explicit reconciliation.
    """
    pending = json.loads(marker.read_text(encoding="utf-8"))
    if pending.get("schema") != "skatai.v2.iss-mirror-departure-pending.v2":
        return False
    if pending.get("source_commit") != source_commit:
        return False
    table_id = pending.get("table_id")
    viewer = pending.get("viewer_name")
    offset = pending.get("protocol_offset")
    if (
        not isinstance(table_id, str)
        or not table_id
        or not isinstance(viewer, str)
        or not viewer
        or type(offset) is not int
        or offset < 0
        or not protocol_journal.is_file()
        or offset > protocol_journal.stat().st_size
    ):
        return False
    leave_line = command_leave(table_id, viewer)
    leave_offset = None
    destroy_offset = None
    try:
        with protocol_journal.open("rb") as stream:
            stream.seek(offset)
            while True:
                row_offset = stream.tell()
                raw = stream.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    return False
                row = json.loads(raw)
                direction = row["direction"]
                line = row["line"]
                if direction == "out" and line == leave_line:
                    if leave_offset is not None:
                        return False
                    leave_offset = row_offset
                elif direction == "in" and line == f"destroy {table_id} {viewer}":
                    if leave_offset is None:
                        return False
                    destroy_offset = row_offset
                    break
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        return False
    if leave_offset is None or destroy_offset is None:
        return False
    _atomic_json(
        marker.parent / "mirror-departure-reconciliation.json",
        {
            "schema": "skatai.v2.iss-mirror-departure-reconciliation.v1",
            "source_commit": source_commit,
            "table_id": table_id,
            "game_id": pending.get("game_id"),
            "marker_sha256": sha256_file(marker),
            "protocol_offset": offset,
            "leave_offset": leave_offset,
            "destroy_offset": destroy_offset,
            "outcome": "CONFIRMED_DEPARTURE_NO_ACTION_REPLAY",
        },
    )
    marker.unlink()
    return True


def _group(name: str) -> str:
    return str(name).split(":", 1)[0]


def canonical_opponent_stack(
    players: Iterable[str],
    *,
    skatai_seat: int,
) -> str:
    names = tuple(_group(x) for x in players)
    if len(names) != 3 or skatai_seat not in (0, 1, 2):
        raise ISSGateWorkerError("BAD_THREE_PLAYER_STACK_INPUT")
    opponents = [x for i, x in enumerate(names) if i != skatai_seat]
    rank = {"kermit": 0, "zoot": 1, "theCount": 2}
    if all(x in rank for x in opponents):
        opponents.sort(key=rank.__getitem__)
    else:
        opponents.sort()
    return "+".join(opponents)


def current_target(scored_rows: list[dict[str, Any]]) -> tuple[int | None, dict | None]:
    """Return the next cumulative frozen look and any completed gate decision."""
    last_analysis = None
    outcomes = [ISSGameOutcome.from_mapping(x) for x in scored_rows]
    for look in LOOKS_PER_ARM:
        q = quota_status(scored_rows, per_arm=look)
        if not q["complete"]:
            return look, last_analysis
        analysis = decide_external_gate(outcomes)
        last_analysis = analysis
        if analysis["status"] == "COMPLETE":
            return None, analysis
        if analysis["status"] != "CONTINUE":
            raise ISSGateWorkerError(
                f"UNEXPECTED_GATE_STATUS_AT_COMPLETE_QUOTA:{analysis['status']}"
            )
    return None, last_analysis


def choose_arm_for_stratum(
    scored_rows: list[dict[str, Any]],
    *,
    per_arm: int,
    stack: str,
    seat: int,
) -> GameAssignment:
    if stack not in PRIMARY_STACKS or seat not in (0, 1, 2):
        return GameAssignment("B0", stack, seat, per_arm, False)

    targets = target_quotas(per_arm)
    counts = observed_counts(scored_rows)
    arm_totals = {
        arm: sum(v for s, v in counts.items() if s.arm == arm)
        for arm in ARMS
    }
    candidates = []
    for arm in ARMS:
        s = Stratum(arm, stack, seat)
        remaining = max(0, targets[s] - counts[s])
        if remaining:
            candidates.append(
                (-remaining, arm_totals[arm], ARMS.index(arm), arm)
            )
    if not candidates:
        # The game may already have been started by ISS before the worker could
        # rotate tables. Play a valid B0 diagnostic game but do not score it.
        return GameAssignment("B0", stack, seat, per_arm, False)
    candidates.sort()
    return GameAssignment(candidates[0][-1], stack, seat, per_arm, True)


def stack_complete(
    scored_rows: list[dict[str, Any]],
    *,
    per_arm: int,
    stack: str,
) -> bool:
    q = quota_status(scored_rows, per_arm=per_arm)
    items = [x for x in q["strata"] if x["opponent"] == stack]
    return bool(items) and all(int(x["remaining"]) == 0 for x in items)


def next_underfilled_stack(
    scored_rows: list[dict[str, Any]],
    *,
    per_arm: int,
) -> str | None:
    """Choose the stack from the frozen global quota-priority rule."""
    targets = next_targets(scored_rows, per_arm=per_arm)
    return None if not targets else str(targets[0]["opponent"])


class RecordingSwitchProvider:
    def __init__(self, providers: Mapping[str, ISSSkatAIDecisionProvider]) -> None:
        self.providers = dict(providers)
        self.arm = "B0"
        self.latencies: dict[str, dict[str, float]] = {}

    def set_arm(self, arm: str) -> None:
        if arm not in self.providers:
            raise ISSGateWorkerError(f"UNKNOWN_ARM:{arm}")
        self.arm = arm

    def next_decision(self, table):
        decision = self.providers[self.arm].next_decision(table)
        if decision is not None:
            game = decision.request.game_id
            self.latencies.setdefault(game, {})[decision.result.decision_id] = float(
                decision.result.latency_ms
            )
        return decision

    def latency_summary(self, game_id: str) -> tuple[float | None, float | None]:
        xs = sorted(self.latencies.pop(game_id, {}).values())
        if not xs:
            return None, None
        p50 = statistics.median(xs)
        # Nearest-rank p95, deterministic for small decision counts.
        idx = max(0, min(len(xs) - 1, int((0.95 * len(xs) + 0.999999999) // 1) - 1))
        return float(p50), float(xs[idx])


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


RCLONE_S3_ARGS = (
    "--s3-provider", "Other",
    "--s3-env-auth",
    "--s3-endpoint", "https://fsn1.your-objectstorage.com",
    "--s3-region", "fsn1",
)


class HetznerEvidenceMirror:
    def __init__(self, *, local_root: Path) -> None:
        self.local_root = local_root
        remote_root = os.environ.get("ISS_GATE_S3_PREFIX")
        if not remote_root:
            persisted = local_root / "object-storage-readiness.json"
            if persisted.is_file():
                try:
                    remote_root = str(
                        json.loads(persisted.read_text(encoding="utf-8"))["remote_root"]
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ISSGateWorkerError(
                        "MIRROR_PERSISTED_REMOTE_ROOT_INVALID"
                    ) from exc
        self.remote_root = (
            remote_root
            or ":s3:skatai-v2/evidence/V2-B1-bidding-linearish-full-v1/external-iss-gate"
        ).rstrip("/")
        try:
            self.command_timeout_s = float(
                os.environ.get("ISS_GATE_MIRROR_RCLONE_TIMEOUT_S", "120")
            )
        except ValueError as exc:
            raise ISSGateWorkerError("MIRROR_BAD_RCLONE_TIMEOUT") from exc
        if self.command_timeout_s <= 0:
            raise ISSGateWorkerError("MIRROR_BAD_RCLONE_TIMEOUT")

    def probe(self) -> dict[str, Any]:
        try:
            proc = subprocess.run(
                ["rclone", "lsd", ":s3:skatai-v2", *RCLONE_S3_ARGS],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=min(30.0, self.command_timeout_s),
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "remote_root": self.remote_root,
                "returncode": None,
                "reason": "RCLONE_READINESS_TIMEOUT",
            }
        return {
            "ok": proc.returncode == 0,
            "remote_root": self.remote_root,
            "returncode": proc.returncode,
        }

    def download_optional(self, remote_rel: str, local: Path) -> bool:
        remote_rel = remote_rel.lstrip("/")
        parent_rel, _, name = remote_rel.rpartition("/")
        parent = self.remote_root + ("/" + parent_rel if parent_rel else "")
        listed = subprocess.run(
            ["rclone", "lsf", parent, "--files-only", *RCLONE_S3_ARGS],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=self.command_timeout_s,
        )
        if listed.returncode != 0:
            raise ISSGateWorkerError(
                f"MIRROR_REMOTE_LIST_FAILED:{remote_rel}:{listed.returncode}"
            )
        names = {x.rstrip("/") for x in listed.stdout.splitlines() if x.strip()}
        if name not in names:
            return False
        remote = self.remote_root + "/" + remote_rel
        local.parent.mkdir(parents=True, exist_ok=True)
        tmp = local.with_suffix(local.suffix + ".restore-tmp")
        proc = subprocess.run(
            ["rclone", "copyto", remote, str(tmp), *RCLONE_S3_ARGS],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=self.command_timeout_s,
        )
        if proc.returncode != 0:
            tmp.unlink(missing_ok=True)
            raise ISSGateWorkerError(
                f"MIRROR_REMOTE_RESTORE_FAILED:{remote_rel}:{proc.returncode}"
            )
        os.chmod(tmp, 0o600)
        os.replace(tmp, local)
        return True

    def upload_verified(self, local: Path, remote_rel: str) -> dict[str, Any]:
        if not local.is_file():
            raise ISSGateWorkerError(f"MIRROR_LOCAL_FILE_MISSING:{local}")

        # Current journals continue to grow while a game is being finalized.
        # Bind verification to immutable bytes, never to a live source path
        # whose contents can change between hashing and rclone reading it.
        data = local.read_bytes()
        expected = hashlib.sha256(data).hexdigest()
        snapshot = local.with_name(
            f".{local.name}.upload-snapshot-{secrets.token_hex(8)}"
        )
        snapshot.write_bytes(data)
        os.chmod(snapshot, 0o600)

        remote = self.remote_root + "/" + remote_rel.lstrip("/")
        try:
            subprocess.run(
                ["rclone", "copyto", str(snapshot), remote, *RCLONE_S3_ARGS],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=self.command_timeout_s,
            )
            readback = subprocess.run(
                ["rclone", "cat", remote, *RCLONE_S3_ARGS],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=self.command_timeout_s,
            )
            if readback.returncode != 0:
                raise ISSGateWorkerError(
                    f"MIRROR_REMOTE_READ_FAILED:{remote_rel}:{readback.returncode}"
                )
            actual = hashlib.sha256(readback.stdout).hexdigest()
            if actual != expected:
                raise ISSGateWorkerError(
                    f"MIRROR_HASH_MISMATCH:{remote_rel}:{actual}!={expected}"
                )
        finally:
            snapshot.unlink(missing_ok=True)

        return {
            "local": str(local),
            "remote": remote.replace(":s3:", "s3://", 1),
            "sha256": expected,
            "bytes": len(data),
        }

    def upload_batch_verified(
        self,
        files: Iterable[tuple[Path, str]],
    ) -> list[dict[str, Any]]:
        items = list(files)
        if not items:
            return []

        with tempfile.TemporaryDirectory(
            prefix="skatai-iss-mirror-batch-",
        ) as tmpdir:
            stage = Path(tmpdir)
            metadata: list[dict[str, Any]] = []
            seen_remote: set[str] = set()
            game_paths: list[str] = []
            manifest_paths: list[str] = []
            current_paths: list[str] = []

            for local, remote_rel in items:
                if not local.is_file():
                    raise ISSGateWorkerError(
                        f"MIRROR_LOCAL_FILE_MISSING:{local}"
                    )
                remote_rel = remote_rel.lstrip("/")
                if ("\n" in remote_rel or ".." in Path(remote_rel).parts
                        or not remote_rel.startswith(("games/", "manifests/", "current/"))):
                    raise ISSGateWorkerError(
                        f"MIRROR_BATCH_REMOTE_SCOPE_INVALID:{remote_rel}"
                    )
                if remote_rel in seen_remote:
                    raise ISSGateWorkerError(
                        f"MIRROR_BATCH_DUPLICATE_REMOTE:{remote_rel}"
                    )
                seen_remote.add(remote_rel)
                if remote_rel.startswith("current/"):
                    current_paths.append(remote_rel)
                elif remote_rel.startswith("manifests/"):
                    manifest_paths.append(remote_rel)
                else:
                    game_paths.append(remote_rel)

                data = local.read_bytes()
                expected = hashlib.sha256(data).hexdigest()
                staged = stage / remote_rel
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(data)
                os.chmod(staged, 0o600)
                metadata.append(
                    {
                        "local": str(local),
                        "remote": (
                            self.remote_root + "/" + remote_rel
                        ).replace(":s3:", "s3://", 1),
                        "sha256": expected,
                        "bytes": len(data),
                    }
                )

            for label, paths, immutable in (
                ("IMMUTABLE", game_paths, True),
                # The manifest is the publication marker. Verify every game
                # object remotely before exposing its manifest to readers.
                ("MANIFEST", manifest_paths, True),
                ("CURRENT", current_paths, False),
            ):
                if not paths:
                    continue
                selection = stage / f".{label.lower()}-files"
                selection.write_text("".join(path + "\n" for path in paths))
                flags = ["--files-from", str(selection)]
                copied = subprocess.run(
                    [
                        "rclone", "copy", str(stage), self.remote_root,
                        *flags, "--checksum", "--no-traverse",
                        *(["--immutable"] if immutable else []),
                        *RCLONE_S3_ARGS,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                    timeout=self.command_timeout_s,
                )
                if copied.returncode != 0:
                    raise ISSGateWorkerError(
                        f"MIRROR_BATCH_{label}_COPY_FAILED:{copied.returncode}"
                    )

                checked = subprocess.run(
                    [
                        "rclone", "check", str(stage), self.remote_root,
                        *flags, "--download", "--one-way",
                        *RCLONE_S3_ARGS,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                    timeout=self.command_timeout_s,
                )
                if checked.returncode != 0:
                    raise ISSGateWorkerError(
                        f"MIRROR_BATCH_{label}_VERIFY_FAILED:{checked.returncode}"
                    )

            return metadata


def object_storage_readiness(paths: GatePaths | None = None) -> dict[str, Any]:
    paths = GatePaths.defaults() if paths is None else paths
    return HetznerEvidenceMirror(local_root=paths.runtime_root).probe()


def _source_commit(repo_root: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return proc.stdout.strip()


def verify_deployment_assets(paths: GatePaths) -> dict[str, Any]:
    b0 = json.loads((paths.repo_root / "provenance/B0_SKATZERO_BASELINE.json").read_text())
    b1 = json.loads(
        (paths.repo_root / "provenance/B1_BIDDING_LINEARISH_FULL_V1.json").read_text()
    )
    if not paths.skatzero_python.is_file():
        raise ISSGateWorkerError("SKATZERO_PYTHON_MISSING")
    git_dir = paths.skatzero_root / ".git"
    if git_dir.exists():
        proc = subprocess.run(
            ["git", "-C", str(paths.skatzero_root), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        actual_commit = proc.stdout.strip()
    else:
        commit_marker = paths.skatzero_root / ".skatai-upstream-commit"
        archive_marker = paths.skatzero_root / ".skatai-source-archive-sha256"
        if not commit_marker.is_file() or not archive_marker.is_file():
            raise ISSGateWorkerError("B0_SOURCE_IDENTITY_MARKERS_MISSING")
        actual_commit = commit_marker.read_text(encoding="utf-8").strip()
        archive_sha = archive_marker.read_text(encoding="utf-8").strip()
        expected_archive = b0["git_archive_sha256"]
        if archive_sha != expected_archive:
            raise ISSGateWorkerError(
                f"B0_SOURCE_ARCHIVE_MISMATCH:{archive_sha}!={expected_archive}"
            )
    if actual_commit != b0["upstream_commit"]:
        raise ISSGateWorkerError(
            f"B0_SOURCE_COMMIT_MISMATCH:{actual_commit}!={b0['upstream_commit']}"
        )
    for name, expected in b0["pretrained_models"].items():
        model = paths.skatzero_root / "models/latest" / name
        if not model.is_file() or sha256_file(model) != expected:
            raise ISSGateWorkerError(f"B0_MODEL_MISMATCH:{name}")
    expected_b1 = b1["artifacts"]["model.pt"]["sha256"]
    if not paths.b1_model.is_file() or sha256_file(paths.b1_model) != expected_b1:
        raise ISSGateWorkerError("B1_MODEL_MISMATCH")
    return {
        "b0_upstream_commit": actual_commit,
        "b1_model_sha256": expected_b1,
        "deployment_identities": load_identities(paths.repo_root),
    }


def readiness(paths: GatePaths) -> dict[str, Any]:
    b1 = json.loads(
        (paths.repo_root / "provenance/B1_BIDDING_LINEARISH_FULL_V1.json").read_text()
    )
    result = assess_iss_gate_readiness(
        local_confirmation_decision=(
            paths.repo_root / "provenance/B1_LOCAL_CONFIRMATION_RESULT.json"
        ),
        b1_model=paths.b1_model,
        expected_b1_sha256=b1["artifacts"]["model.pt"]["sha256"],
        protocol_files={
            "gate_protocol": paths.repo_root
            / "provenance/B1_EXTERNAL_ISS_GATE_PROTOCOL.json",
            "decision_rule": paths.repo_root
            / "provenance/B1_EXTERNAL_ISS_DECISION_RULE.json",
            "ledger_contract": paths.repo_root
            / "provenance/B1_EXTERNAL_ISS_LEDGER_CONTRACT.json",
            "campaign_quotas": paths.repo_root
            / "provenance/B1_EXTERNAL_ISS_CAMPAIGN_QUOTAS.json",
            "deployment_identities": paths.repo_root
            / "provenance/B1_EXTERNAL_ISS_DEPLOYMENT_IDENTITIES.json",
            "three_player_amendment": paths.repo_root
            / "provenance/B1_EXTERNAL_ISS_THREE_PLAYER_STRATA_AMENDMENT.json",
        },
    )
    return result


class GateEvidence:
    def __init__(
        self,
        *,
        paths: GatePaths,
        identities: Mapping[str, Mapping[str, Any]],
        source_commit: str,
    ) -> None:
        self.paths = paths
        self.identities = identities
        self.source_commit = source_commit
        self.ledger = ISSGateLedger(paths.runtime_root / "gate-ledger.jsonl")
        self.diagnostic_path = paths.runtime_root / "diagnostic-games.jsonl"
        self.protocol_journal = paths.runtime_root / "service.jsonl"
        self.effect_journal = paths.runtime_root / "effects.jsonl"
        self.games_dir = paths.runtime_root / "games"
        self.games_dir.mkdir(parents=True, exist_ok=True)
        self.active_games_path = paths.runtime_root / "active-games.json"
        self.connection_events_path = paths.runtime_root / "connection-events.jsonl"
        self.mirror = HetznerEvidenceMirror(local_root=paths.runtime_root)
        self.mirror_queue_dir = paths.runtime_root / "mirror-queue"
        self.mirror_queue_dir.mkdir(parents=True, exist_ok=True)
        self.mirror_current_dirty_path = (
            paths.runtime_root / "mirror-current-dirty.json"
        )
        self.mirror_status_path = paths.runtime_root / "mirror-status.json"
        self._mirror_lock = threading.RLock()

    def _game_outbox_artifacts(self, game_id: str) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        for local, remote in self._game_mirror_files(str(game_id)):
            if not local.is_file():
                raise ISSGateWorkerError(
                    f"MIRROR_QUEUE_ARTIFACT_MISSING:{game_id}:{local.name}"
                )
            artifacts.append(
                {
                    "name": local.name,
                    "remote": remote,
                    "sha256": sha256_file(local),
                    "bytes": local.stat().st_size,
                }
            )
        return artifacts

    def _verify_outbox_artifacts(
        self,
        game_id: str,
        expected: Iterable[Mapping[str, Any]],
    ) -> None:
        actual = self._game_outbox_artifacts(game_id)
        normalized_expected = [
            {
                "name": str(item["name"]),
                "remote": str(item["remote"]),
                "sha256": str(item["sha256"]),
                "bytes": int(item["bytes"]),
            }
            for item in expected
        ]
        if actual != normalized_expected:
            raise ISSGateWorkerError(
                f"MIRROR_QUEUE_ARTIFACT_BINDING_MISMATCH:{game_id}"
            )

    def enqueue_mirror_game(self, game_id: str) -> Path:
        evidence_path = self.games_dir / f"{game_id}.evidence.json"
        if not evidence_path.is_file():
            raise ISSGateWorkerError(
                f"MIRROR_QUEUE_EVIDENCE_MISSING:{game_id}"
            )
        artifacts = self._game_outbox_artifacts(game_id)
        marker = self.mirror_queue_dir / f"{game_id}.json"
        if marker.exists():
            payload = json.loads(marker.read_text(encoding="utf-8"))
            if (
                payload.get("schema") != "skatai.v2.iss-mirror-queue.v2"
                or payload.get("game_id") != game_id
            ):
                raise ISSGateWorkerError(
                    f"MIRROR_QUEUE_MARKER_CONFLICT:{game_id}"
                )
            self._verify_outbox_source(game_id, payload)
            self._verify_outbox_artifacts(
                game_id,
                payload.get("artifacts") or [],
            )
            return marker
        self._verify_outbox_source(
            game_id, {"source_commit": self.source_commit}
        )
        _atomic_json(
            marker,
            {
                "schema": "skatai.v2.iss-mirror-queue.v2",
                "game_id": game_id,
                "source_commit": self.source_commit,
                "enqueued_unix_ns": time.time_ns(),
                "artifacts": artifacts,
            },
        )
        return marker

    def _verify_outbox_source(
        self, game_id: str, payload: Mapping[str, Any]
    ) -> None:
        source = payload.get("source_commit")
        try:
            game = json.loads(
                (self.games_dir / f"{game_id}.evidence.json").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, ValueError, TypeError) as exc:
            raise ISSGateWorkerError(
                f"MIRROR_QUEUE_SOURCE_UNVERIFIED:{game_id}"
            ) from exc
        if (
            not isinstance(source, str)
            or not source
            or not isinstance(game, dict)
            or game.get("game_id") != game_id
            or game.get("source_commit") != source
        ):
            raise ISSGateWorkerError(
                f"MIRROR_QUEUE_SOURCE_UNVERIFIED:{game_id}"
            )

    def mark_current_mirror_dirty(self) -> None:
        now = time.time_ns()
        first_dirty = now
        if self.mirror_current_dirty_path.exists():
            payload = json.loads(
                self.mirror_current_dirty_path.read_text(encoding="utf-8")
            )
            first_dirty = int(
                payload.get(
                    "first_dirty_unix_ns",
                    payload.get("generation_unix_ns", now),
                )
            )
        _atomic_json(
            self.mirror_current_dirty_path,
            {
                "schema": "skatai.v2.iss-mirror-current-dirty.v1",
                "source_commit": self.source_commit,
                "first_dirty_unix_ns": first_dirty,
                "generation_unix_ns": now,
            },
        )

    def pending_mirror_entries(self) -> list[tuple[Path, dict[str, Any]]]:
        out: list[tuple[Path, dict[str, Any]]] = []
        for marker in sorted(self.mirror_queue_dir.glob("*.json")):
            payload = json.loads(marker.read_text(encoding="utf-8"))
            if payload.get("schema") != "skatai.v2.iss-mirror-queue.v2":
                raise ISSGateWorkerError(
                    f"MIRROR_QUEUE_SCHEMA_MISMATCH:{marker.name}"
                )
            game_id = str(payload.get("game_id"))
            if marker.name != f"{game_id}.json":
                raise ISSGateWorkerError(
                    f"MIRROR_QUEUE_GAME_ID_MISMATCH:{marker.name}"
                )
            artifacts = payload.get("artifacts")
            if not isinstance(artifacts, list) or not artifacts:
                raise ISSGateWorkerError(
                    f"MIRROR_QUEUE_ARTIFACT_BINDING_MISSING:{marker.name}"
                )
            self._verify_outbox_source(game_id, payload)
            out.append((marker, payload))
        out.sort(key=lambda item: int(item[1]["enqueued_unix_ns"]))
        return out

    def mirror_backlog_status(self) -> dict[str, Any]:
        entries = self.pending_mirror_entries()
        now = time.time_ns()
        oldest = (
            None
            if not entries
            else max(
                0.0,
                (now - int(entries[0][1]["enqueued_unix_ns"])) / 1e9,
            )
        )
        pending_bytes = sum(
            sum(int(a.get("bytes", 0)) for a in (payload.get("artifacts") or []))
            for _, payload in entries
        )
        return {
            "pending_games": len(entries),
            "pending_bytes": pending_bytes,
            "oldest_pending_age_s": oldest,
            "current_dirty": self.mirror_current_dirty_path.exists(),
        }

    def _write_mirror_status(self, **fields: Any) -> None:
        _atomic_json(
            self.mirror_status_path,
            {
                "schema": "skatai.v2.iss-mirror-status.v1",
                "source_commit": self.source_commit,
                "updated_unix_ns": time.time_ns(),
                **self.mirror_backlog_status(),
                **fields,
            },
        )

    def append_connection_event(self, event: str, **fields: Any) -> None:
        payload = {
            "schema": "skatai.v2.external-iss-connection-event.v1",
            "unix_ns": time.time_ns(),
            "event": str(event),
            **fields,
        }
        fd = os.open(
            self.connection_events_path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            os.write(
                fd,
                (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(),
            )
            os.fsync(fd)
        finally:
            os.close(fd)

    def restore_active_games(
        self,
        *,
        source_commit: str,
    ) -> dict[tuple[str, int], ActiveGame]:
        if not self.active_games_path.exists():
            self.mirror.download_optional(
                "current/active-games.json",
                self.active_games_path,
            )
        if not self.active_games_path.exists():
            return {}
        payload = json.loads(self.active_games_path.read_text(encoding="utf-8"))
        return parse_active_games_payload(
            payload,
            expected_source_commit=source_commit,
        )

    def persist_active_games(
        self,
        games: Mapping[tuple[str, int], ActiveGame],
        *,
        source_commit: str,
    ) -> dict[str, Any]:
        payload = active_games_payload(games, source_commit=source_commit)
        _atomic_json(self.active_games_path, payload)
        self.mark_current_mirror_dirty()
        return payload


    def reconcile_terminal_active_games(
        self,
        games: Mapping[tuple[str, int], ActiveGame],
        *,
        source_commit: str,
    ) -> tuple[dict[tuple[str, int], ActiveGame], list[str]]:
        """Finish an interrupted terminal-game mirror without replaying ISS.

        A game is auto-reconciled only when the local terminal evidence package
        and immutable gate ledger independently prove that the active authority
        already reached a valid SCORED terminal under this exact source commit.
        Any matching-but-incomplete/conflicting terminal package fails closed.
        Mid-game authorities with no terminal evidence are left untouched for
        normal reconnect replay/effect reconciliation.
        """
        remaining = dict(games)
        recovered: list[str] = []
        if not remaining:
            return remaining, recovered

        ledger_by_game = {record.game_id: record for record in self.ledger.records()}
        candidates: dict[
            tuple[str, int], list[tuple[Path, dict[str, Any]]]
        ] = {key: [] for key in remaining}

        for evidence_path in sorted(self.games_dir.glob("*.evidence.json")):
            try:
                payload = json.loads(evidence_path.read_text(encoding="utf-8"))
                key = (
                    str(payload["table_id"]),
                    int(payload["server_game_num"]),
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if key in candidates:
                candidates[key].append((evidence_path, payload))

        for key, active in list(remaining.items()):
            matches = candidates.get(key, [])
            if not matches:
                # No terminal package: preserve authority for ordinary reconnect.
                continue
            if len(matches) != 1:
                raise ISSGateWorkerError(
                    f"TERMINAL_ACTIVE_RECOVERY_AMBIGUOUS_EVIDENCE:{key}:{len(matches)}"
                )

            evidence_path, evidence = matches[0]
            game_id = str(evidence.get("game_id") or "")
            if (
                evidence.get("schema") != "skatai.v2.external-iss-game-evidence.v1"
                or evidence.get("source_commit") != source_commit
                or evidence.get("status") != "SCORED"
                or evidence.get("failure_reason") is not None
                or not game_id
                or evidence_path.name != f"{game_id}.evidence.json"
            ):
                raise ISSGateWorkerError(
                    f"TERMINAL_ACTIVE_RECOVERY_EVIDENCE_MISMATCH:{key}"
                )

            expected_assignment = active.assignment.__dict__
            if evidence.get("assignment") != expected_assignment:
                raise ISSGateWorkerError(
                    f"TERMINAL_ACTIVE_RECOVERY_ASSIGNMENT_MISMATCH:{key}"
                )
            if evidence.get("actual_stack") != active.assignment.stack:
                raise ISSGateWorkerError(
                    f"TERMINAL_ACTIVE_RECOVERY_STACK_MISMATCH:{key}"
                )

            identity = self.identities.get(active.assignment.arm)
            if (
                identity is None
                or evidence.get("deployment_identity_sha256")
                != identity.get("deployment_identity_sha256")
                or evidence.get("release_id") != identity.get("release_id")
            ):
                raise ISSGateWorkerError(
                    f"TERMINAL_ACTIVE_RECOVERY_DEPLOYMENT_IDENTITY_MISMATCH:{key}"
                )

            result = evidence.get("result")
            if (
                not isinstance(result, dict)
                or result.get("game_id") != game_id
                or int(result.get("seat", -1)) != active.assignment.seat
            ):
                raise ISSGateWorkerError(
                    f"TERMINAL_ACTIVE_RECOVERY_RESULT_MISMATCH:{key}"
                )

            effects = evidence.get("effects")
            if not isinstance(effects, list) or any(
                not isinstance(effect, dict)
                or effect.get("status") != "CONFIRMED"
                for effect in effects
            ):
                raise ISSGateWorkerError(
                    f"TERMINAL_ACTIVE_RECOVERY_NONTERMINAL_EFFECT:{key}"
                )

            artifacts = evidence.get("artifacts")
            if not isinstance(artifacts, dict):
                raise ISSGateWorkerError(
                    f"TERMINAL_ACTIVE_RECOVERY_ARTIFACTS_MISSING:{key}"
                )
            required_artifacts = {
                "terminal_sgf": self.games_dir / f"{game_id}.sgf",
                "service_slice": self.games_dir / f"{game_id}.service.jsonl",
                "effect_slice": self.games_dir / f"{game_id}.effects.jsonl",
            }
            for artifact_name, artifact_path in required_artifacts.items():
                meta = artifacts.get(artifact_name)
                if (
                    not isinstance(meta, dict)
                    or not artifact_path.is_file()
                    or int(meta.get("bytes", -1)) != artifact_path.stat().st_size
                    or meta.get("sha256") != sha256_file(artifact_path)
                ):
                    raise ISSGateWorkerError(
                        "TERMINAL_ACTIVE_RECOVERY_ARTIFACT_MISMATCH:"
                        f"{key}:{artifact_name}"
                    )

            record = ledger_by_game.get(game_id)
            if (
                record is None
                or record.status != "SCORED"
                or record.source_commit != source_commit
                or record.arm != active.assignment.arm
                or record.opponent != active.assignment.stack
                or record.seat != active.assignment.seat
                or record.model_sha256
                != evidence.get("deployment_identity_sha256")
                or record.raw_sgf_sha256
                != artifacts["terminal_sgf"]["sha256"]
                or record.journal_sha256 != sha256_file(evidence_path)
                or record.score != float(result.get("score"))
            ):
                raise ISSGateWorkerError(
                    f"TERMINAL_ACTIVE_RECOVERY_LEDGER_MISMATCH:{key}"
                )

            # Mirror immutable terminal evidence first. If it raises, the
            # durable local active authority remains unchanged and restart is
            # safe to retry without producing any ISS material effect.
            self.mirror_game(game_id, include_current=False)

            next_remaining = dict(remaining)
            next_remaining.pop(key, None)
            self.persist_active_games(
                next_remaining,
                source_commit=source_commit,
            )
            # Recovery is intentionally stricter than ordinary gameplay:
            # remotely persist the cleared authority before declaring recovery
            # complete. The normal hot path uses write-behind batching.
            self.mirror_current_state()
            self.mirror_current_dirty_path.unlink(missing_ok=True)
            (self.mirror_queue_dir / f"{game_id}.json").unlink(
                missing_ok=True
            )
            remaining = next_remaining
            recovered.append(game_id)
            self.append_connection_event(
                "TERMINAL_ACTIVE_GAME_RECOVERED",
                game_id=game_id,
                table_id=key[0],
                game_sequence=key[1],
                source_commit=source_commit,
            )

        return remaining, recovered

    @staticmethod
    def _file_size(path: Path) -> int:
        return path.stat().st_size if path.exists() else 0

    @staticmethod
    def _write_slice(source: Path, start: int, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = b""
        if source.exists():
            with source.open("rb") as f:
                f.seek(start)
                data = f.read()
        tmp = destination.with_suffix(destination.suffix + ".tmp")
        tmp.write_bytes(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, destination)

    def scored_rows(self) -> list[dict[str, Any]]:
        return self.ledger.scored_for_gate()

    def append_diagnostic(self, payload: Mapping[str, Any]) -> None:
        self.diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            self.diagnostic_path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            os.write(
                fd,
                (json.dumps(dict(payload), sort_keys=True, separators=(",", ":")) + "\n").encode(),
            )
            os.fsync(fd)
        finally:
            os.close(fd)

    def append_game(
        self,
        *,
        assignment: GameAssignment,
        viewer_name: str,
        sgf: str,
        latency_p50: float | None,
        latency_p95: float | None,
        protocol_offset: int,
        effect_offset: int,
        table_id: str,
        game_sequence: int,
    ) -> dict[str, Any]:
        result = live_game_result(sgf, viewer_name=viewer_name)
        actual_stack = canonical_opponent_stack(
            result["players"], skatai_seat=int(result["seat"])
        )
        if actual_stack != assignment.stack:
            assignment = GameAssignment(
                assignment.arm,
                actual_stack,
                int(result["seat"]),
                assignment.per_arm_target,
                False,
            )

        sgf_path = self.games_dir / f"{result['game_id']}.sgf"
        if not sgf_path.exists():
            tmp = sgf_path.with_suffix(".sgf.tmp")
            tmp.write_text(sgf, encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, sgf_path)
        elif sha256_file(sgf_path) != result["raw_sgf_sha256"]:
            raise ISSGateWorkerError("GAME_ID_COLLISION_WITH_DIFFERENT_SGF")

        protocol_slice = self.games_dir / f"{result['game_id']}.service.jsonl"
        effect_slice = self.games_dir / f"{result['game_id']}.effects.jsonl"
        evidence_manifest = self.games_dir / f"{result['game_id']}.evidence.json"
        self._write_slice(self.protocol_journal, protocol_offset, protocol_slice)
        self._write_slice(self.effect_journal, effect_offset, effect_slice)

        effect_states = [
            state
            for state in ISSEffectJournal(self.effect_journal).states().values()
            if state.table_id == str(table_id)
            and state.game_sequence == int(game_sequence)
        ]
        effect_states.sort(key=lambda x: (x.protocol_sequence, x.effect_id))
        unresolved = [x for x in effect_states if not x.terminal]
        stale = [x for x in effect_states if x.status == "ABORTED_STALE"]

        effect_latencies = sorted(float(x.latency_ms) for x in effect_states)
        if effect_latencies:
            latency_p50 = float(statistics.median(effect_latencies))
            p95_index = max(
                0,
                min(
                    len(effect_latencies) - 1,
                    int((0.95 * len(effect_latencies) + 0.999999999) // 1) - 1,
                ),
            )
            latency_p95 = float(effect_latencies[p95_index])

        ident = self.identities[assignment.arm]
        failure_reason = result["failure_reason"]
        if unresolved:
            status = "PROTOCOL_FAILURE"
            score = None
            failure_reason = f"UNRESOLVED_EXTERNAL_EFFECTS_AT_TERMINAL:{len(unresolved)}"
        elif stale:
            status = "PROTOCOL_FAILURE"
            score = None
            failure_reason = f"STALE_EXTERNAL_EFFECTS_AT_TERMINAL:{len(stale)}"
        elif failure_reason is not None:
            status = (
                "PROTOCOL_FAILURE"
                if str(failure_reason).startswith("ISS_PENALTY:")
                else "INFRA_FAILURE"
            )
            score = None
        else:
            status = "SCORED"
            score = result["score"]

        evidence_payload = {
            "schema": "skatai.v2.external-iss-game-evidence.v1",
            "game_id": result["game_id"],
            "table_id": str(table_id),
            "server_game_num": int(game_sequence),
            "source_commit": self.source_commit,
            "assignment": assignment.__dict__,
            "actual_stack": actual_stack,
            "deployment_identity_sha256": ident["deployment_identity_sha256"],
            "release_id": ident["release_id"],
            "status": status,
            "failure_reason": failure_reason,
            "result": result,
            "artifacts": {
                "terminal_sgf": {
                    "sha256": sha256_file(sgf_path),
                    "bytes": sgf_path.stat().st_size,
                },
                "service_slice": {
                    "sha256": sha256_file(protocol_slice),
                    "bytes": protocol_slice.stat().st_size,
                },
                "effect_slice": {
                    "sha256": sha256_file(effect_slice),
                    "bytes": effect_slice.stat().st_size,
                },
            },
            "effects": [
                {
                    "effect_id": x.effect_id,
                    "request_id": x.request_id,
                    "decision_id": x.decision_id,
                    "position_hash": x.position_hash,
                    "protocol_sequence": x.protocol_sequence,
                    "wire_action": x.wire_action,
                    "release_id": x.release_id,
                    "decision_type": x.decision_type,
                    "latency_ms": x.latency_ms,
                    "status": x.status,
                    "attempts": x.attempts,
                }
                for x in effect_states
            ],
            "latency_ms_p50": latency_p50,
            "latency_ms_p95": latency_p95,
        }
        _atomic_json(evidence_manifest, evidence_payload)
        journal_sha = sha256_file(evidence_manifest)

        payload = {
            "schema": SCHEMA,
            "assignment": assignment.__dict__,
            "actual_stack": actual_stack,
            "result": result,
            "evidence_manifest_sha256": journal_sha,
        }
        if not assignment.primary:
            self.append_diagnostic(payload)
            return {"primary": False, **payload}

        record = ISSGateLedgerRecord.create(
            game_id=result["game_id"],
            arm=assignment.arm,
            opponent=actual_stack,
            seat=int(result["seat"]),
            status=status,
            score=score,
            declarer=(
                None
                if result["declarer"] is None
                else int(result["declarer"]) == int(result["seat"])
            ),
            contract=result["contract"],
            winning_bid=result["winning_bid"],
            overbid=result["overbid"],
            latency_ms_p50=latency_p50,
            latency_ms_p95=latency_p95,
            raw_sgf_sha256=result["raw_sgf_sha256"],
            journal_sha256=journal_sha,
            model_sha256=ident["deployment_identity_sha256"],
            source_commit=self.source_commit,
            failure_reason=failure_reason,
        )
        existing = {x.game_id: x for x in self.ledger.records()}
        if record.game_id in existing:
            old = existing[record.game_id]
            comparable = (
                old.arm,
                old.opponent,
                old.seat,
                old.status,
                old.score,
                old.model_sha256,
                old.source_commit,
                old.raw_sgf_sha256,
            )
            new = (
                record.arm,
                record.opponent,
                record.seat,
                record.status,
                record.score,
                record.model_sha256,
                record.source_commit,
                record.raw_sgf_sha256,
            )
            if comparable != new:
                raise ISSGateWorkerError("CONFLICTING_DUPLICATE_GAME_RESULT")
            return {"primary": True, "duplicate_reused": True, **payload}
        self.ledger.append(record)
        return {"primary": True, "duplicate_reused": False, **payload}

    def append_failure_without_terminal(
        self,
        *,
        assignment: GameAssignment,
        table_id: str,
        game_sequence: int,
        protocol_offset: int,
        effect_offset: int,
        status: str,
        failure_reason: str,
    ) -> dict[str, Any]:
        if status not in {"INFRA_FAILURE", "PROTOCOL_FAILURE", "MODEL_FAILURE"}:
            raise ValueError(f"BAD_TERMINALLESS_FAILURE_STATUS:{status}")
        if not failure_reason:
            raise ValueError("TERMINALLESS_FAILURE_REQUIRES_REASON")

        identity_payload = {
            "schema": "skatai.v2.external-iss-terminal-less-failure-id.v1",
            "source_commit": self.source_commit,
            "table_id": str(table_id),
            "game_sequence": int(game_sequence),
            "assignment": assignment.__dict__,
        }
        game_id = hashlib.sha256(
            json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

        protocol_slice = self.games_dir / f"{game_id}.service.jsonl"
        effect_slice = self.games_dir / f"{game_id}.effects.jsonl"
        evidence_manifest = self.games_dir / f"{game_id}.evidence.json"
        self._write_slice(self.protocol_journal, protocol_offset, protocol_slice)
        self._write_slice(self.effect_journal, effect_offset, effect_slice)

        effect_states = [
            state
            for state in ISSEffectJournal(self.effect_journal).states().values()
            if state.table_id == str(table_id)
            and state.game_sequence == int(game_sequence)
        ]
        effect_states.sort(key=lambda x: (x.protocol_sequence, x.effect_id))
        effect_latencies = sorted(float(x.latency_ms) for x in effect_states)
        latency_p50 = None
        latency_p95 = None
        if effect_latencies:
            latency_p50 = float(statistics.median(effect_latencies))
            p95_index = max(
                0,
                min(
                    len(effect_latencies) - 1,
                    int((0.95 * len(effect_latencies) + 0.999999999) // 1) - 1,
                ),
            )
            latency_p95 = float(effect_latencies[p95_index])

        ident = self.identities[assignment.arm]
        evidence_payload = {
            "schema": "skatai.v2.external-iss-terminal-less-failure-evidence.v1",
            "game_id": game_id,
            "table_id": str(table_id),
            "server_game_num": int(game_sequence),
            "source_commit": self.source_commit,
            "assignment": assignment.__dict__,
            "deployment_identity_sha256": ident["deployment_identity_sha256"],
            "release_id": ident["release_id"],
            "status": status,
            "failure_reason": str(failure_reason),
            "terminal_sgf_present": False,
            "artifacts": {
                "service_slice": {
                    "sha256": sha256_file(protocol_slice),
                    "bytes": protocol_slice.stat().st_size,
                },
                "effect_slice": {
                    "sha256": sha256_file(effect_slice),
                    "bytes": effect_slice.stat().st_size,
                },
            },
            "effects": [
                {
                    "effect_id": x.effect_id,
                    "request_id": x.request_id,
                    "decision_id": x.decision_id,
                    "position_hash": x.position_hash,
                    "protocol_sequence": x.protocol_sequence,
                    "wire_action": x.wire_action,
                    "release_id": x.release_id,
                    "decision_type": x.decision_type,
                    "latency_ms": x.latency_ms,
                    "status": x.status,
                    "attempts": x.attempts,
                }
                for x in effect_states
            ],
            "latency_ms_p50": latency_p50,
            "latency_ms_p95": latency_p95,
        }
        _atomic_json(evidence_manifest, evidence_payload)
        journal_sha = sha256_file(evidence_manifest)

        payload = {
            "schema": SCHEMA,
            "assignment": assignment.__dict__,
            "game_id": game_id,
            "status": status,
            "failure_reason": str(failure_reason),
            "evidence_manifest_sha256": journal_sha,
        }
        if not assignment.primary:
            self.append_diagnostic(payload)
            return {"primary": False, **payload}

        record = ISSGateLedgerRecord.create(
            game_id=game_id,
            arm=assignment.arm,
            opponent=assignment.stack,
            seat=assignment.seat,
            status=status,
            score=None,
            declarer=None,
            contract=None,
            winning_bid=None,
            overbid=None,
            latency_ms_p50=latency_p50,
            latency_ms_p95=latency_p95,
            raw_sgf_sha256=None,
            journal_sha256=journal_sha,
            model_sha256=ident["deployment_identity_sha256"],
            source_commit=self.source_commit,
            failure_reason=str(failure_reason),
        )
        existing = {x.game_id: x for x in self.ledger.records()}
        if record.game_id in existing:
            old = existing[record.game_id]
            comparable = (
                old.arm,
                old.opponent,
                old.seat,
                old.status,
                old.score,
                old.model_sha256,
                old.source_commit,
                old.failure_reason,
            )
            new = (
                record.arm,
                record.opponent,
                record.seat,
                record.status,
                record.score,
                record.model_sha256,
                record.source_commit,
                record.failure_reason,
            )
            if comparable != new:
                raise ISSGateWorkerError("CONFLICTING_DUPLICATE_FAILURE_RESULT")
            return {"primary": True, "duplicate_reused": True, **payload}
        self.ledger.append(record)
        return {"primary": True, "duplicate_reused": False, **payload}

    def _current_mirror_files(self) -> list[tuple[Path, str]]:
        files: list[tuple[Path, str]] = []
        for local, remote in (
            (self.ledger.path, "current/gate-ledger.jsonl"),
            (self.diagnostic_path, "current/diagnostic-games.jsonl"),
            (self.protocol_journal, "current/service.jsonl"),
            (self.effect_journal, "current/effects.jsonl"),
            (self.active_games_path, "current/active-games.json"),
            (self.connection_events_path, "current/connection-events.jsonl"),
            (self.paths.runtime_root / "status.json", "current/status.json"),
        ):
            if local.exists():
                files.append((local, remote))
        return files

    def mirror_current_state(self) -> list[dict[str, Any]]:
        with self._mirror_lock:
            return [
                self.mirror.upload_verified(local, remote)
                for local, remote in self._current_mirror_files()
            ]

    def _game_mirror_files(self, game_id: str) -> list[tuple[Path, str]]:
        files: list[tuple[Path, str]] = []
        sgf_path = self.games_dir / f"{game_id}.sgf"
        if sgf_path.exists():
            files.append((sgf_path, f"games/{game_id}.sgf"))
        files.extend(
            [
                (
                    self.games_dir / f"{game_id}.service.jsonl",
                    f"games/{game_id}.service.jsonl",
                ),
                (
                    self.games_dir / f"{game_id}.effects.jsonl",
                    f"games/{game_id}.effects.jsonl",
                ),
                (
                    self.games_dir / f"{game_id}.evidence.json",
                    f"games/{game_id}.evidence.json",
                ),
            ]
        )
        return files

    def _mirror_file_metadata(
        self,
        local: Path,
        remote_rel: str,
    ) -> dict[str, Any]:
        data = local.read_bytes()
        return {
            "local": str(local),
            "remote": (
                self.mirror.remote_root + "/" + remote_rel.lstrip("/")
            ).replace(":s3:", "s3://", 1),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }

    def mirror_game(
        self,
        game_id: str,
        *,
        include_current: bool = True,
    ) -> dict[str, Any]:
        with self._mirror_lock:
            files = self._game_mirror_files(game_id)

            uploaded = [
                self.mirror.upload_verified(local, remote)
                for local, remote in files
            ]
            manifest = {
                "schema": "skatai.v2.external-iss-evidence-mirror.v1",
                "game_id": game_id,
                "source_commit": self.source_commit,
                "files": uploaded,
            }
            manifest_path = self.games_dir / f"{game_id}.mirror.json"
            _atomic_json(manifest_path, manifest)
            uploaded_manifest = self.mirror.upload_verified(
                manifest_path, f"manifests/{game_id}.json"
            )
            manifest["manifest"] = uploaded_manifest
            if include_current:
                current_token = None
                if self.mirror_current_dirty_path.exists():
                    current_token = self.mirror_current_dirty_path.read_bytes()
                manifest["current"] = self.mirror_current_state()
                if (
                    current_token is not None
                    and self.mirror_current_dirty_path.exists()
                    and self.mirror_current_dirty_path.read_bytes() == current_token
                ):
                    self.mirror_current_dirty_path.unlink(missing_ok=True)
            return manifest

    def mirror_batch(self, *, limit: int | None = None) -> dict[str, Any]:
        with self._mirror_lock:
            entries = self.pending_mirror_entries()
            selected = entries if limit is None else entries[: max(0, int(limit))]
            current_token = None
            if self.mirror_current_dirty_path.exists():
                current_token = self.mirror_current_dirty_path.read_bytes()

            files: list[tuple[Path, str]] = []
            mirrored: list[str] = []
            for _, payload in selected:
                game_id = str(payload["game_id"])
                self._verify_outbox_artifacts(
                    game_id,
                    payload.get("artifacts") or [],
                )
                game_files = self._game_mirror_files(game_id)
                manifest = {
                    "schema": "skatai.v2.external-iss-evidence-mirror.v1",
                    "game_id": game_id,
                    "source_commit": payload["source_commit"],
                    "files": [
                        self._mirror_file_metadata(local, remote)
                        for local, remote in game_files
                    ],
                }
                manifest_path = self.games_dir / f"{game_id}.mirror.json"
                _atomic_json(manifest_path, manifest)
                files.extend(game_files)
                files.append(
                    (manifest_path, f"manifests/{game_id}.json")
                )
                mirrored.append(game_id)

            current_files = []
            if selected or current_token is not None:
                current_files = self._current_mirror_files()
                files.extend(current_files)

            uploaded = self.mirror.upload_batch_verified(files)

            for marker, _ in selected:
                marker.unlink(missing_ok=True)

            if current_token is not None and self.mirror_current_dirty_path.exists():
                if self.mirror_current_dirty_path.read_bytes() == current_token:
                    self.mirror_current_dirty_path.unlink(missing_ok=True)

            result = {
                "mirrored_games": mirrored,
                "uploaded_files": len(uploaded),
                "current_files": len(current_files),
                **self.mirror_backlog_status(),
            }
            self._write_mirror_status(
                state="OK",
                last_success_unix_ns=time.time_ns(),
                mirrored_games=len(mirrored),
                uploaded_files=len(uploaded),
            )
            return result


class MirrorWriteBehind:
    def __init__(
        self,
        evidence: GateEvidence,
        *,
        policy: MirrorPolicy,
    ) -> None:
        policy.validate()
        self.evidence = evidence
        self.policy = policy
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _current_dirty_age_s(self) -> float | None:
        path = self.evidence.mirror_current_dirty_path
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        dirty_ns = int(
            payload.get(
                "first_dirty_unix_ns",
                payload.get("generation_unix_ns"),
            )
        )
        return max(0.0, (time.time_ns() - dirty_ns) / 1e9)

    def _due(self) -> bool:
        backlog = self.evidence.mirror_backlog_status()
        pending = int(backlog["pending_games"])
        if pending >= self.policy.batch_games:
            return True
        oldest = backlog["oldest_pending_age_s"]
        if oldest is not None and float(oldest) >= self.policy.max_delay_s:
            return True
        current_age = self._current_dirty_age_s()
        return (
            current_age is not None
            and current_age >= self.policy.max_delay_s
        )

    def _run(self) -> None:
        self.evidence._write_mirror_status(state="RUNNING")
        while not self._stop.is_set():
            try:
                if self._due():
                    self.evidence.mirror_batch(
                        limit=self.policy.batch_games
                    )
                self._stop.wait(self.policy.poll_interval_s)
            except Exception as exc:
                self.evidence._write_mirror_status(
                    state="DEGRADED",
                    last_error=f"{type(exc).__name__}:{exc}",
                    last_error_unix_ns=time.time_ns(),
                )
                self._stop.wait(self.policy.retry_delay_s)
        self.evidence._write_mirror_status(state="STOPPED")

    def backpressure_required(self) -> bool:
        backlog = self.evidence.mirror_backlog_status()
        oldest = backlog["oldest_pending_age_s"]
        return (
            int(backlog["pending_games"]) >= self.policy.max_pending_games
            or int(backlog["pending_bytes"]) >= self.policy.max_pending_bytes
            or (
                oldest is not None
                and float(oldest) >= self.policy.max_backlog_age_s
            )
        )

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="iss-mirror-writebehind",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, flush: bool) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            if flush:
                thread.join()
            else:
                thread.join(timeout=1.0)
        self._thread = None
        if flush:
            while True:
                backlog = self.evidence.mirror_backlog_status()
                if (
                    int(backlog["pending_games"]) == 0
                    and not bool(backlog["current_dirty"])
                ):
                    break
                self.evidence.mirror_batch(
                    limit=self.policy.batch_games
                )


def _private_table_credentials() -> tuple[str, str]:
    # Official client rules: ID 3..8 chars, first alphabetic; password >=3
    # printable ASCII. These are ephemeral and intentionally never persisted.
    return "AI" + secrets.token_hex(3), "p" + secrets.token_hex(6)


class ExternalGateWorker:
    def __init__(self, paths: GatePaths) -> None:
        self.paths = paths
        verified = verify_deployment_assets(paths)
        self.identities = verified["deployment_identities"]
        self.source_commit = _source_commit(paths.repo_root)

        b0_ai = build_b0_skat_ai(paths.skatzero_root, paths.skatzero_python)
        b1_ai = build_b1_skat_ai(
            paths.b1_model,
            paths.skatzero_root,
            paths.skatzero_python,
            threshold=0.5,
        )
        self.switch = RecordingSwitchProvider(
            {
                "B0": ISSSkatAIDecisionProvider(
                    b0_ai, release_id=self.identities["B0"]["release_id"]
                ),
                "B1": ISSSkatAIDecisionProvider(
                    b1_ai, release_id=self.identities["B1"]["release_id"]
                ),
            }
        )
        self.evidence = GateEvidence(
            paths=paths,
            identities=self.identities,
            source_commit=self.source_commit,
        )
        self.mirror_policy = mirror_policy_from_environment()
        self.mirror_writebehind = MirrorWriteBehind(
            self.evidence,
            policy=self.mirror_policy,
        )
        self.effect_guard = ISSAuthorityGuard(
            ISSEffectJournal(self.evidence.effect_journal)
        )
        self.client: ISSClientCore | None = None
        self.password: str | None = None
        self.assignment_by_game: dict[tuple[str, int], ActiveGame] = {}
        self.desired_stack: str | None = None
        self.table_id: str | None = None
        self._expected_new_table_id: str | None = None
        self._table_password: str | None = None
        self._transport_failure_streak = 0
        self._mirror_pause_requested = False

    def _restore_active_game_authority(self) -> None:
        restored = self.evidence.restore_active_games(source_commit=self.source_commit)
        restored, _ = self.evidence.reconcile_terminal_active_games(
            restored,
            source_commit=self.source_commit,
        )
        self.assignment_by_game = restored
        if not restored:
            return
        (table_id, _), active = next(iter(restored.items()))
        self.table_id = table_id
        self._expected_new_table_id = None
        self.desired_stack = active.assignment.stack
        self.switch.set_arm(active.assignment.arm)

    def _persist_active_game_authority(self) -> None:
        self.evidence.persist_active_games(
            self.assignment_by_game,
            source_commit=self.source_commit,
        )

    def campaign_status(self) -> dict[str, Any]:
        rows = self.evidence.scored_rows()
        target, gate = current_target(rows)
        payload = {
            "schema": SCHEMA,
            "source_commit": self.source_commit,
            "scored_games": len(rows),
            "next_per_arm_target": target,
            "gate": gate,
            "next_stack": (
                None if target is None else next_underfilled_stack(rows, per_arm=target)
            ),
        }
        _atomic_json(self.paths.runtime_root / "status.json", payload)
        return payload

    def _create_next_table(self) -> None:
        if self.client is None:
            raise ISSGateWorkerError("CLIENT_NOT_CONNECTED")
        status = self.campaign_status()
        target = status["next_per_arm_target"]
        if target is None:
            return
        stack = status["next_stack"]
        if stack is None:
            raise ISSGateWorkerError("NO_UNDERFILLED_STACK_WITH_OPEN_GATE")
        name, password = _private_table_credentials()
        self.desired_stack = str(stack)
        self._expected_new_table_id = name
        self._table_password = password
        self.client.send_service_command(
            command_create_table(
                players=3,
                table_name=name,
                table_password=password,
            )
        )

    def _admitted_table_ids(self) -> set[str]:
        admitted = {table_id for table_id, _ in self.assignment_by_game}
        if self.table_id is not None:
            admitted.add(self.table_id)
        if self._expected_new_table_id is not None:
            admitted.add(self._expected_new_table_id)
        return admitted

    def _event_is_admitted(self, event) -> bool:
        table_id = event.fields.get("table_id")
        if table_id is None:
            return True
        return str(table_id) in self._admitted_table_ids()

    def _on_create(self, event) -> None:
        if self.client is None or not bool(event.fields.get("is_player")):
            return
        if self.desired_stack is None:
            return
        incoming_table_id = str(event.fields["table_id"])
        if (
            self.table_id == incoming_table_id
            and any(key[0] == incoming_table_id for key in self.assignment_by_game)
        ):
            # Reconnect replay for an already-started game. Do not re-invite
            # opponents or send READY while that game is in progress.
            return
        if incoming_table_id != self._expected_new_table_id:
            # ISS replays table directory/create state after reconnect. A
            # fresh campaign epoch must never adopt a table merely because
            # SkatAI is still listed there from an older worker/epoch.
            return
        self.table_id = incoming_table_id
        self._expected_new_table_id = None
        viewer = str(event.fields["viewer_name"])
        for opponent in self.desired_stack.split("+"):
            self.client.send_service_command(
                command_invite(self.table_id, viewer, opponent)
            )
        self.client.send_service_command(command_ready(self.table_id, viewer))

    def _on_start_preapply(self, line: str) -> None:
        event = parse_service_line(line)
        if event.kind != "table_start":
            return
        meta = parse_table_start_payload(str(event.fields.get("payload") or ""))
        players = tuple(meta["players"])
        viewer = str(event.fields["viewer_name"])
        seat = player_seat(players, viewer)
        stack = canonical_opponent_stack(players, skatai_seat=seat)
        key = (str(event.fields["table_id"]), int(meta["game_num"]))
        existing = self.assignment_by_game.get(key)
        if existing is not None:
            if existing.assignment.stack != stack or existing.assignment.seat != seat:
                raise ISSGateWorkerError(
                    f"RECONNECT_ACTIVE_GAME_IDENTITY_MISMATCH:{key}"
                )
            self.switch.set_arm(existing.assignment.arm)
            return

        rows = self.evidence.scored_rows()
        target, gate = current_target(rows)
        if target is None:
            assignment = GameAssignment("B0", stack, seat, LOOKS_PER_ARM[-1], False)
        else:
            assignment = choose_arm_for_stratum(
                rows,
                per_arm=target,
                stack=stack,
                seat=seat,
            )
        self.switch.set_arm(assignment.arm)
        self.assignment_by_game[key] = ActiveGame(
            assignment=assignment,
            protocol_offset=self.evidence._file_size(self.evidence.protocol_journal),
            effect_offset=self.evidence._file_size(self.evidence.effect_journal),
        )
        # This executes before client.handle_line(start), therefore before any
        # decision produced from the new game can be sent.
        self._persist_active_game_authority()

    def _on_end(self, event, table) -> bool:
        if self.client is None:
            raise ISSGateWorkerError("CLIENT_NOT_CONNECTED")
        key = (table.table_id, table.game_sequence)
        active = self.assignment_by_game.get(key)
        if active is None:
            raise ISSGateWorkerError(f"MISSING_GAME_ASSIGNMENT:{key}")
        assignment = active.assignment
        if not table.game_sgf:
            raise ISSGateWorkerError("TABLE_END_WITHOUT_SGF")

        game_id = f"iss:{table.table_id}:{table.game_sequence}"
        p50, p95 = self.switch.latency_summary(game_id)
        stored = self.evidence.append_game(
            assignment=assignment,
            viewer_name=table.viewer_name,
            sgf=table.game_sgf,
            latency_p50=p50,
            latency_p95=p95,
            protocol_offset=active.protocol_offset,
            effect_offset=active.effect_offset,
            table_id=table.table_id,
            game_sequence=table.game_sequence,
        )
        status = self.campaign_status()
        # Local terminal evidence is already fsync-durable. Queue remote
        # persistence and immediately continue ISS gameplay; the background
        # mirror batches immutable games and current-state snapshots.
        self.evidence.enqueue_mirror_game(stored["result"]["game_id"])
        self.assignment_by_game.pop(key, None)
        self._persist_active_game_authority()
        self._transport_failure_streak = 0

        target = status["next_per_arm_target"]
        if target is None:
            self.client.send_service_command(
                command_leave(table.table_id, table.viewer_name)
            )
            return False

        # Leave at the game boundary. Never perform remote I/O or wait for it
        # on the ISS protocol thread. The destroy event confirms that this
        # table can be retired before the worker pauses between sessions.
        if self.mirror_writebehind.backpressure_required():
            _atomic_json(
                self.paths.runtime_root / "mirror-departure-pending.json",
                {
                    "schema": "skatai.v2.iss-mirror-departure-pending.v2",
                    "source_commit": self.source_commit,
                    "table_id": table.table_id,
                    "viewer_name": table.viewer_name,
                    "game_sequence": table.game_sequence,
                    "game_id": stored["result"]["game_id"],
                    "protocol_offset": self.evidence._file_size(
                        self.evidence.protocol_journal
                    ),
                },
            )
            self._mirror_pause_requested = True
            self.evidence._write_mirror_status(state="BACKPRESSURE")
            self.client.send_service_command(
                command_leave(table.table_id, table.viewer_name)
            )
            self.desired_stack = None
            self._table_password = None
            return True

        # Diagnostic/wrong-stack games never pin the campaign to that table.
        rows = self.evidence.scored_rows()
        next_stack = next_underfilled_stack(rows, per_arm=target)
        if (not assignment.primary) or next_stack != assignment.stack:
            self.client.send_service_command(
                command_leave(table.table_id, table.viewer_name)
            )
            # Keep the departing table admitted until ISS confirms its
            # destruction. The destroy event clears table_id and creates the
            # next table when desired_stack is None.
            self.desired_stack = None
            self._table_password = None
            return True

        self.client.send_service_command(
            command_ready(table.table_id, table.viewer_name)
        )
        return True

    def _on_table_error(self, event, table) -> None:
        if self.client is None:
            raise ISSGateWorkerError("CLIENT_NOT_CONNECTED")

        key = (table.table_id, table.game_sequence)
        active = self.assignment_by_game.get(key)
        error_text = str(event.fields.get("text") or "").strip()
        if active is None:
            raise ISSGateWorkerError(
                f"ISS_TABLE_ERROR_WITHOUT_ACTIVE_GAME:{table.table_id}:"
                f"{table.game_sequence}:{error_text}"
            )

        reason = f"ISS_TABLE_ERROR:{error_text or 'UNSPECIFIED'}"
        for state in self.effect_guard.journal.pending_for_game(
            table.table_id, table.game_sequence
        ):
            self.effect_guard.journal.abort_stale(
                state.effect_id,
                reason=reason,
            )

        stored = self.evidence.append_failure_without_terminal(
            assignment=active.assignment,
            table_id=table.table_id,
            game_sequence=table.game_sequence,
            protocol_offset=active.protocol_offset,
            effect_offset=active.effect_offset,
            status="PROTOCOL_FAILURE",
            failure_reason=reason,
        )

        # Remove authority before mirroring so authoritative current state
        # cannot claim that a failed game is still active after this point.
        self.assignment_by_game.pop(key, None)
        self._persist_active_game_authority()
        self.table_id = None
        self.desired_stack = None
        self._expected_new_table_id = None
        self._table_password = None

        self.evidence.mirror_game(stored["game_id"])
        self.client.send_service_command(
            command_leave(table.table_id, table.viewer_name)
        )
        raise ISSGateWorkerError(
            f"ISS_TABLE_ERROR_DURING_ACTIVE_GAME:{table.table_id}:"
            f"{table.game_sequence}:{error_text}"
        )

    def _on_destroy(self, destroyed_table_id: str) -> bool:
        if self._mirror_pause_requested:
            marker = self.paths.runtime_root / "mirror-departure-pending.json"
            pending = json.loads(marker.read_text(encoding="utf-8"))
            if pending["table_id"] != destroyed_table_id:
                raise ISSGateWorkerError("MIRROR_DEPARTURE_TABLE_MISMATCH")
            # A replayed destroy is the only evidence that resolves this
            # outbound LEAVE. Keep the marker on any earlier interruption.
            marker.unlink()
        if self.table_id == destroyed_table_id:
            self.table_id = None
        if self._expected_new_table_id == destroyed_table_id:
            self._expected_new_table_id = None
        if self.desired_stack is None:
            if self._mirror_pause_requested or self.mirror_writebehind.backpressure_required():
                self._mirror_pause_requested = False
                self.evidence._write_mirror_status(state="BACKPRESSURE")
                return True
            self._create_next_table()
        return False

    def _wait_for_mirror_capacity(self) -> None:
        # A restart must not bypass the same admission limit that paused the
        # previous session. Active games are reconciled first and must resume
        # promptly; this wait applies only between games.
        if self.assignment_by_game:
            return
        while self.mirror_writebehind.backpressure_required():
            self.evidence._write_mirror_status(state="BACKPRESSURE")
            time.sleep(self.mirror_policy.poll_interval_s)

    def _run_connected_session(
        self,
        *,
        client_policy: ISSClientPolicy,
    ) -> dict[str, Any] | None:
        client, password = client_from_environment(
            journal_path=self.evidence.protocol_journal,
            policy=client_policy,
            move_provider=self.switch,
            effect_guard=self.effect_guard,
        )
        self.client = client
        self.password = password
        keepalive_stop = threading.Event()
        keepalive_error: list[BaseException] = []

        def keepalive_loop() -> None:
            while not keepalive_stop.wait(20.0):
                try:
                    client.send_service_command(command_keepalive())
                except BaseException as exc:
                    keepalive_error.append(exc)
                    keepalive_stop.set()
                    return

        keepalive_thread = None
        connected_at = None
        try:
            actual_client_id = client.connect_and_login(password)
            connected_at = time.monotonic()
            self.password = None
            password = ""
            self.evidence.append_connection_event(
                "CONNECTED",
                client_id=actual_client_id,
                transport_failure_streak=self._transport_failure_streak,
            )
            keepalive_thread = threading.Thread(
                target=keepalive_loop,
                name="iss-keepalive",
                daemon=True,
            )
            keepalive_thread.start()
            if self.table_id is None:
                self._create_next_table()
            while True:
                if keepalive_error:
                    exc = keepalive_error[0]
                    if isinstance(exc, ISSTransportError):
                        raise exc
                    raise ISSGateWorkerError(
                        f"KEEPALIVE_FAILED:{type(exc).__name__}"
                    ) from exc
                line = client.transport.read_line()
                preview = parse_service_line(line)
                if not self._event_is_admitted(preview):
                    # Preserve the inbound protocol evidence, but do not apply
                    # foreign/pre-existing table state to the client and never
                    # let it reach the move provider/effect guard.
                    if client.journal is not None:
                        client.journal.write("in", line)
                    self.evidence.append_connection_event(
                        "FOREIGN_TABLE_EVENT_IGNORED",
                        event_kind=preview.kind,
                        table_id=str(preview.fields.get("table_id")),
                    )
                    continue
                self._on_start_preapply(line)
                event = client.handle_line(line)
                if event.kind == "create":
                    self._on_create(event)
                elif event.kind == "table_error":
                    table = client.state.tables[str(event.fields["table_id"])]
                    self._on_table_error(event, table)
                elif event.kind == "table_end":
                    table = client.state.tables[str(event.fields["table_id"])]
                    keep_running = self._on_end(event, table)
                    if not keep_running:
                        return self.campaign_status()
                elif event.kind == "destroy":
                    if self._on_destroy(str(event.fields["table_id"])):
                        return {"mirror_paused": True}
        finally:
            keepalive_stop.set()
            if keepalive_thread is not None:
                keepalive_thread.join(timeout=2.0)
            elapsed = (
                None if connected_at is None else time.monotonic() - connected_at
            )
            self.evidence.append_connection_event(
                "CONNECTION_CLOSED",
                connected_duration_s=elapsed,
            )
            self.password = None
            password = ""
            client.close()
            self.client = None

    def run(self) -> dict[str, Any]:
        departure_marker = self.paths.runtime_root / "mirror-departure-pending.json"
        if departure_marker.exists() and not reconcile_mirror_departure(
            departure_marker,
            self.paths.runtime_root / "service.jsonl",
            source_commit=self.source_commit,
        ):
            raise ISSGateWorkerError("MIRROR_DEPARTURE_OUTCOME_UNKNOWN")
        ready = readiness(self.paths)
        _atomic_json(self.paths.runtime_root / "readiness.json", ready)
        if not ready["ready"]:
            raise ISSGateWorkerError(
                "ISS_GATE_NOT_READY:" + ",".join(ready["blockers"])
            )

        storage = self.evidence.mirror.probe()
        _atomic_json(self.paths.runtime_root / "object-storage-readiness.json", storage)
        if not storage["ok"]:
            raise ISSGateWorkerError("HETZNER_EVIDENCE_MIRROR_NOT_READY")

        self._restore_active_game_authority()
        self.mirror_writebehind.start()
        client_policy = ISSClientPolicy(
            accept_invitations=False,
            ready_when_joined=False,
            ready_after_game=False,
        )
        reconnect = reconnect_policy_from_environment()
        attempt_in_cycle = 0

        try:
            self._wait_for_mirror_capacity()
            while True:
                try:
                    result = self._run_connected_session(
                        client_policy=client_policy
                    )
                    if result is not None and result.get("mirror_paused"):
                        # No game is active and the old table's destruction
                        # was observed. The transfer thread alone drains the
                        # queue; a storage outage cannot block protocol I/O.
                        self._wait_for_mirror_capacity()
                        continue
                    if result is not None:
                        # A scientific gate is not complete until every queued
                        # game and the final current-state snapshot are remotely
                        # verified. This blocking flush is off the gameplay hot
                        # path because no more gate games are needed.
                        self.mirror_writebehind.stop(flush=True)
                        return result
                except ISSTransportError as exc:
                    if self._mirror_pause_requested:
                        raise ISSGateWorkerError(
                            "MIRROR_DEPARTURE_OUTCOME_UNKNOWN"
                        ) from exc
                    if not recoverable_transport_error(exc):
                        raise
                    self._transport_failure_streak += 1
                    attempt_in_cycle += 1
                    if attempt_in_cycle >= reconnect.attempts_per_cycle:
                        delay = reconnect.cooldown_s
                        self.evidence.append_connection_event(
                            "RECONNECT_CYCLE_COOLDOWN",
                            reason=str(exc),
                            failure_streak=self._transport_failure_streak,
                            attempts_in_cycle=attempt_in_cycle,
                            delay_s=delay,
                        )
                        attempt_in_cycle = 0
                    else:
                        delay = reconnect_delay_s(reconnect, attempt_in_cycle)
                        self.evidence.append_connection_event(
                            "RECONNECT_WAIT",
                            reason=str(exc),
                            failure_streak=self._transport_failure_streak,
                            attempt_in_cycle=attempt_in_cycle,
                            delay_s=delay,
                        )
                    time.sleep(delay)
                    continue
        finally:
            # On an abnormal stop the durable queue is intentionally retained
            # for restart reconciliation; do not block shutdown on Hetzner.
            self.mirror_writebehind.stop(flush=False)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--check",
        action="store_true",
        help="Verify frozen gate/runtime prerequisites without connecting to ISS.",
    )
    args = p.parse_args()
    paths = GatePaths.defaults()
    if args.check:
        payload = {
            "readiness": readiness(paths),
            "object_storage": object_storage_readiness(paths),
            "assets": verify_deployment_assets(paths),
            "identities": load_identities(paths.repo_root),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    result = ExternalGateWorker(paths).run()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
