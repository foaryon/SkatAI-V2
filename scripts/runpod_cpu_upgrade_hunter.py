#!/usr/bin/env python3
"""RunPod CPU capacity hunter for the SkatAI V2 main pod.

Polls RunPod REST API v2 for an exact 16C/32GB or 8C/16GB CPU flavor in
the current network-volume data center.  When --claim is enabled, the first
matching capacity is provisioned as a replacement pod that mounts the same
network volume and starts through the fail-closed upgrade entrypoint.

The script never stops or deletes the current source pod.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

API_BASE = "https://api.runpod.io/v2"
DEFAULT_STATE_DIR = Path("/workspace/sentinelx-host/runpod-cpu-upgrade-hunter")
DEFAULT_LOG = Path("/workspace/sentinelx-host/logs/runpod-cpu-upgrade-hunter.log")
DEFAULT_UPGRADE_ENTRYPOINT = "/workspace/sentinelx-host/runpod-upgrade-entrypoint.sh"
DEFAULT_TARGETS = ((16, 32), (8, 16))
NAME_PREFIX = "skatai-v2-cpu-upgrade"
TERMINAL_STATUSES = {"EXITED", "STOPPED", "TERMINATED", "DELETED", "FAILED"}
UNAVAILABLE_MARKERS = {
    "NONE",
    "UNAVAILABLE",
    "OUT_OF_STOCK",
    "OUT OF STOCK",
    "NO_CAPACITY",
    "NO CAPACITY",
    "ZERO",
}
PID1_KEYS = {
    "RUNPOD_API_KEY",
    "RUNPOD_POD_ID",
    "RUNPOD_DC_ID",
    "RUNPOD_CPU_COUNT",
    "RUNPOD_MEM_GB",
    "RUNPOD_VOLUME_ID",
}


@dataclass(frozen=True)
class Target:
    vcpu: int
    memory_gb: int

    @property
    def ram_per_vcpu(self) -> float:
        return self.memory_gb / self.vcpu

    @property
    def label(self) -> str:
        return f"{self.vcpu}c-{self.memory_gb}g"


@dataclass(frozen=True)
class Config:
    source_pod_id: str
    data_center_id: str
    network_volume_id: str
    mount_path: str
    upgrade_entrypoint: str
    poll_seconds: int
    hold_seconds: int
    targets: tuple[Target, ...]
    state_dir: Path
    log_path: Path


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"RunPod API HTTP {status}: {message}")
        self.status = status
        self.message = message

    @property
    def looks_like_capacity(self) -> bool:
        text = self.message.lower()
        needles = (
            "capacity",
            "available",
            "availability",
            "no longer any instances",
            "no instances",
            "out of stock",
            "could not find",
            "unable to find",
            "not enough",
            "insufficient",
        )
        return self.status in {409, 422, 429, 503} or any(n in text for n in needles)


class RunpodApi:
    def __init__(self, api_key: str, *, base_url: str = API_BASE, timeout: int = 20):
        if not api_key:
            raise ValueError("RUNPOD_API_KEY is missing")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self.base_url + path
        if params:
            query = urllib.parse.urlencode(
                [(k, v) for k, value in params.items() for v in (value if isinstance(value, (list, tuple)) else [value])]
            )
            url += "?" + query
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "skatai-v2-runpod-cpu-hunter/1",
        }
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
                if not raw:
                    return {}
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise RuntimeError("RunPod API returned a non-object JSON response")
                return parsed
        except urllib.error.HTTPError as exc:
            raw = exc.read(65536)
            raise ApiError(exc.code, _safe_api_error(raw)) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"RunPod API transport error: {exc.reason}") from exc


def _safe_api_error(raw: bytes) -> str:
    """Return only non-secret error fields; never echo request payloads."""
    try:
        data = json.loads(raw)
    except Exception:
        text = raw.decode("utf-8", errors="replace").strip()
        return text[:500] if text else "empty error response"
    if isinstance(data, dict):
        for key in ("message", "error", "detail", "title"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:500]
        errors = data.get("errors")
        if isinstance(errors, list) and errors:
            return "; ".join(str(x)[:200] for x in errors[:3])
    return "request rejected"


def _read_pid1_environment() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        raw = Path("/proc/1/environ").read_bytes()
    except OSError:
        return values
    for item in raw.split(b"\0"):
        if b"=" not in item:
            continue
        key_b, value_b = item.split(b"=", 1)
        key = key_b.decode("utf-8", errors="ignore")
        if key in PID1_KEYS:
            values[key] = value_b.decode("utf-8", errors="ignore")
    return values


def runtime_environment() -> dict[str, str]:
    values = {key: os.environ[key] for key in PID1_KEYS if os.environ.get(key)}
    if PID1_KEYS - values.keys():
        pid1 = _read_pid1_environment()
        for key in PID1_KEYS:
            if key not in values and pid1.get(key):
                values[key] = pid1[key]
    return values


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_line(path: Path, event: str, **fields: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = [utc_now(), event]
    for key in sorted(fields):
        value = fields[key]
        if value is None:
            continue
        parts.append(f"{key}={value}")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(" ".join(parts) + "\n")


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _list_from_response(data: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    nested = data.get("data")
    if isinstance(nested, dict):
        for key in keys:
            value = nested.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def pod_status(pod: dict[str, Any]) -> str:
    return str(
        pod.get("status")
        or pod.get("desiredStatus")
        or pod.get("desired_status")
        or "UNKNOWN"
    ).upper()


def normalize_env(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    result: dict[str, str] = {}
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                key = item.get("key")
                val = item.get("value")
                if key is not None and val is not None:
                    result[str(key)] = str(val)
            elif isinstance(item, str) and "=" in item:
                key, val = item.split("=", 1)
                result[key] = val
    return result


def _first(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return None


def source_create_fields(source: dict[str, Any]) -> dict[str, Any]:
    """Extract only v2 create-compatible, non-compute source settings."""
    body: dict[str, Any] = {}

    image = _first(source, "image", "imageName")
    template_id = _first(source, "templateId", "template_id")
    if image:
        body["image"] = image
    if template_id:
        body["templateId"] = template_id
    if not image and not template_id:
        raise RuntimeError("source pod response exposes neither image nor templateId")

    disk = _first(source, "disk", "containerDiskInGb", "container_disk_in_gb")
    if isinstance(disk, (int, float)) and int(disk) > 0:
        body["disk"] = int(disk)

    ports = source.get("ports")
    if isinstance(ports, str):
        body["ports"] = [x.strip() for x in ports.split(",") if x.strip()]
    elif isinstance(ports, list):
        body["ports"] = ports

    env = normalize_env(source.get("env"))
    if env:
        body["env"] = env

    cloud = _first(source, "cloud", "cloudType", "cloud_type")
    if isinstance(cloud, str) and cloud.upper() in {"SECURE", "COMMUNITY"}:
        body["cloud"] = cloud.upper()

    start_ssh = _first(source, "startSsh", "startSSH")
    if isinstance(start_ssh, bool):
        body["startSsh"] = start_ssh
    else:
        body["startSsh"] = True
    return body


def _vcpu_supported(cpu: dict[str, Any], requested: int) -> bool:
    spec = cpu.get("vcpu")
    if isinstance(spec, dict):
        lo = spec.get("min")
        hi = spec.get("max")
        if isinstance(lo, (int, float)) and requested < int(lo):
            return False
        if isinstance(hi, (int, float)) and requested > int(hi):
            return False
    return True


def _dc_entry(cpu: dict[str, Any], dc: str) -> dict[str, Any] | None:
    entries = cpu.get("dataCenters") or cpu.get("datacenters") or cpu.get("data_centers")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_id = _first(entry, "id", "dataCenterId", "data_center_id")
        if str(entry_id or "").upper() == dc.upper():
            return entry
    return {}


def _availability_value(cpu: dict[str, Any], dc: str) -> str:
    entry = _dc_entry(cpu, dc)
    if entry == {}:
        return "NONE"
    if isinstance(entry, dict):
        value = _first(entry, "availability", "stockStatus", "stock_status")
        if value is not None:
            return str(value).upper()
    value = _first(cpu, "availability", "stockStatus", "stock_status")
    return str(value or "UNKNOWN").upper()


def candidate_cpu_types(
    cpu_types: Iterable[dict[str, Any]], target: Target, data_center_id: str
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for cpu in cpu_types:
        flavor_id = _first(cpu, "id", "cpuFlavorId", "cpu_flavor_id")
        if not flavor_id or not _vcpu_supported(cpu, target.vcpu):
            continue
        ram_ratio = _first(cpu, "ramGbPerVcpu", "ramGBPerVcpu", "ram_gb_per_vcpu")
        try:
            ratio = float(ram_ratio)
        except (TypeError, ValueError):
            continue
        if abs(ratio - target.ram_per_vcpu) > 1e-9:
            continue
        availability = _availability_value(cpu, data_center_id)
        if availability in UNAVAILABLE_MARKERS:
            continue
        item = dict(cpu)
        item["_availability"] = availability
        matches.append(item)

    rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "AVAILABLE": 0, "UNKNOWN": 3}
    matches.sort(
        key=lambda x: (
            rank.get(str(x.get("_availability", "UNKNOWN")).upper(), 3),
            str(_first(x, "id", "cpuFlavorId", "cpu_flavor_id")),
        )
    )
    return matches


def cpu_catalog(api: RunpodApi, target: Target) -> list[dict[str, Any]]:
    data = api.request(
        "GET",
        "/catalog/cpus",
        params={"include": "AVAILABILITY", "product": "POD", "vcpuCount": target.vcpu},
    )
    return _list_from_response(data, "cpus", "items", "cpuTypes")


def list_pods(api: RunpodApi) -> list[dict[str, Any]]:
    return _list_from_response(api.request("GET", "/pods"), "pods", "items")


def get_pod(api: RunpodApi, pod_id: str) -> dict[str, Any] | None:
    try:
        return api.request("GET", f"/pods/{urllib.parse.quote(pod_id, safe='')}")
    except ApiError as exc:
        if exc.status == 404:
            return None
        raise


def recover_existing_candidate(api: RunpodApi, source_pod_id: str) -> dict[str, Any] | None:
    for pod in list_pods(api):
        name = str(pod.get("name") or "")
        if not name.startswith(NAME_PREFIX + "-"):
            continue
        if str(pod.get("id") or "") == source_pod_id:
            continue
        if pod_status(pod) not in TERMINAL_STATUSES:
            return pod
    return None


def build_create_body(
    source: dict[str, Any],
    *,
    cfg: Config,
    target: Target,
    cpu_flavor_id: str,
) -> dict[str, Any]:
    body = source_create_fields(source)
    body.update(
        {
            "name": f"{NAME_PREFIX}-{target.label}",
            "args": cfg.upgrade_entrypoint,
            "dataCenterIds": [cfg.data_center_id],
            "mounts": {
                "network": [
                    {"volumeId": cfg.network_volume_id, "path": cfg.mount_path}
                ]
            },
            "cpu": {"id": cpu_flavor_id, "vcpuCount": target.vcpu},
        }
    )
    env = dict(body.get("env") or {})
    env["SKATAI_UPGRADE_SOURCE_POD_ID"] = cfg.source_pod_id
    env["SKATAI_UPGRADE_WAIT_SECONDS"] = str(cfg.hold_seconds)
    env["SKATAI_RUNPOD_HUNTER_MANAGED"] = "1"
    body["env"] = env
    return body


def _state_path(cfg: Config) -> Path:
    return cfg.state_dir / "state.json"


def _record_state(cfg: Config, **updates: Any) -> dict[str, Any]:
    path = _state_path(cfg)
    state = read_state(path)
    state.update(updates)
    state["updated_at"] = utc_now()
    atomic_json(path, state)
    return state


def verify_candidate(
    api: RunpodApi, candidate_id: str, cfg: Config, target: Target
) -> tuple[bool, dict[str, Any] | None]:
    pod = get_pod(api, candidate_id)
    if not pod:
        return False, None
    status = pod_status(pod)
    if status in TERMINAL_STATUSES:
        return False, pod

    dc = _first(pod, "dataCenterId", "data_center_id")
    if dc and str(dc).upper() != cfg.data_center_id.upper():
        raise RuntimeError(
            f"claimed candidate landed in unexpected data center {dc!r}"
        )

    vcpu = _first(pod, "vcpuCount", "vcpu_count")
    memory = _first(pod, "memoryInGb", "memoryGb", "memory_gb")
    if vcpu is not None and int(vcpu) != target.vcpu:
        raise RuntimeError(f"candidate vCPU mismatch: expected {target.vcpu}, got {vcpu}")
    if memory is not None and int(memory) != target.memory_gb:
        raise RuntimeError(
            f"candidate memory mismatch: expected {target.memory_gb}, got {memory}"
        )
    return True, pod


def run_iteration(api: RunpodApi, cfg: Config, *, claim: bool) -> dict[str, Any]:
    state = read_state(_state_path(cfg))
    candidate_id = state.get("candidate_pod_id")
    if isinstance(candidate_id, str) and candidate_id:
        candidate = get_pod(api, candidate_id)
        if candidate and pod_status(candidate) not in TERMINAL_STATUSES:
            return {
                "outcome": "candidate-held",
                "candidate_pod_id": candidate_id,
                "status": pod_status(candidate),
                "target": state.get("target"),
            }
        _record_state(
            cfg,
            candidate_pod_id=None,
            claim_pending=False,
            outcome="candidate-gone",
        )
        state = read_state(_state_path(cfg))

    # The pod-scoped RunPod credential on the live main pod can read itself and
    # the hardware catalog but is forbidden from account-wide GET /pods.
    # Therefore crash recovery cannot safely discover an unknown just-created
    # pod by listing. A durable pre-POST marker closes the duplicate-spend
    # window: if a POST's transport outcome is unknown, stop claiming until an
    # operator resolves that uncertainty.
    if state.get("claim_pending") and not state.get("candidate_pod_id"):
        return _record_state(cfg, outcome="claim-uncertain")

    source = get_pod(api, cfg.source_pod_id)
    if not source:
        raise RuntimeError(f"source pod {cfg.source_pod_id} not found")
    if pod_status(source) in TERMINAL_STATUSES:
        return _record_state(
            cfg,
            source_pod_id=cfg.source_pod_id,
            outcome="source-not-running",
            source_status=pod_status(source),
        )

    available: list[dict[str, Any]] = []
    catalog_observed: list[dict[str, Any]] = []
    for target in cfg.targets:
        catalog = cpu_catalog(api, target)
        catalog_observed.append(
            {
                "target": target.label,
                "types": [
                    {
                        "id": _first(cpu, "id", "cpuFlavorId", "cpu_flavor_id"),
                        "ram_gb_per_vcpu": _first(
                            cpu,
                            "ramGbPerVcpu",
                            "ramGBPerVcpu",
                            "ram_gb_per_vcpu",
                        ),
                        "dc_availability": _availability_value(
                            cpu, cfg.data_center_id
                        ),
                    }
                    for cpu in catalog
                ],
            }
        )
        types = candidate_cpu_types(catalog, target, cfg.data_center_id)
        for cpu in types:
            cpu_id = str(_first(cpu, "id", "cpuFlavorId", "cpu_flavor_id"))
            available.append(
                {
                    "target": target.label,
                    "cpu_flavor_id": cpu_id,
                    "availability": cpu.get("_availability"),
                }
            )
            if not claim:
                continue

            body = build_create_body(
                source, cfg=cfg, target=target, cpu_flavor_id=cpu_id
            )
            _record_state(
                cfg,
                source_pod_id=cfg.source_pod_id,
                candidate_pod_id=None,
                claim_pending=True,
                target=target.label,
                cpu_flavor_id=cpu_id,
                data_center_id=cfg.data_center_id,
                network_volume_id=cfg.network_volume_id,
                outcome="claiming",
                claim_started_at=utc_now(),
            )
            try:
                created = api.request("POST", "/pods", payload=body)
            except ApiError as exc:
                # An HTTP response is authoritative: the create was rejected,
                # so clearing claim_pending cannot hide a successfully created
                # pod. Transport failures intentionally leave it set because
                # the request outcome is then unknowable without list access.
                _record_state(
                    cfg,
                    candidate_pod_id=None,
                    claim_pending=False,
                    outcome="claim-rejected",
                    claim_http_status=exc.status,
                )
                if exc.looks_like_capacity:
                    log_line(
                        cfg.log_path,
                        "claim-race-lost",
                        target=target.label,
                        cpu=cpu_id,
                        http=exc.status,
                    )
                    continue
                raise

            new_id = str(created.get("id") or "")
            if not new_id:
                # A successful HTTP response without an id is ambiguous for
                # billing/idempotence purposes. Keep claim_pending set.
                raise RuntimeError("RunPod create response did not contain a pod id")

            # Persist the provider id before any follow-up request. From here on,
            # a restart can always check the exact pod without account-wide list
            # permission and therefore cannot duplicate the reservation.
            _record_state(
                cfg,
                source_pod_id=cfg.source_pod_id,
                candidate_pod_id=new_id,
                claim_pending=False,
                target=target.label,
                cpu_flavor_id=cpu_id,
                data_center_id=cfg.data_center_id,
                network_volume_id=cfg.network_volume_id,
                outcome="claimed-unverified",
                claimed_at=utc_now(),
                hold_seconds=cfg.hold_seconds,
            )

            # v2 creation is asynchronous. The authoritative initial check is
            # existence/configuration; runtime readiness is owned by the
            # replacement entrypoint and later handoff.
            verified, pod = verify_candidate(api, new_id, cfg, target)
            if not verified:
                raise RuntimeError(
                    f"new candidate {new_id} immediately entered terminal state "
                    f"{pod_status(pod or {})}"
                )

            state = _record_state(
                cfg,
                outcome="claimed",
                candidate_status=pod_status(pod or {}),
            )
            log_line(
                cfg.log_path,
                "claimed",
                candidate=new_id,
                target=target.label,
                cpu=cpu_id,
                dc=cfg.data_center_id,
                hold_s=cfg.hold_seconds,
            )
            return state

    state = _record_state(
        cfg,
        source_pod_id=cfg.source_pod_id,
        outcome="waiting",
        available=available,
        catalog_observed=catalog_observed,
        last_probe_at=utc_now(),
    )
    return state


def parse_targets(text: str) -> tuple[Target, ...]:
    targets: list[Target] = []
    for token in text.split(","):
        token = token.strip().lower()
        if not token:
            continue
        if "/" in token:
            cpu_s, mem_s = token.split("/", 1)
        elif ":" in token:
            cpu_s, mem_s = token.split(":", 1)
        else:
            raise ValueError(f"invalid target {token!r}; use vcpu/memory")
        target = Target(int(cpu_s), int(mem_s))
        if target.vcpu <= 0 or target.memory_gb <= 0:
            raise ValueError("target values must be positive")
        targets.append(target)
    if not targets:
        raise ValueError("at least one target is required")
    return tuple(targets)


def config_from_args(args: argparse.Namespace, env: dict[str, str]) -> Config:
    source = args.source_pod_id or env.get("RUNPOD_POD_ID", "")
    dc = args.data_center_id or env.get("RUNPOD_DC_ID", "")
    volume = args.network_volume_id or env.get("RUNPOD_VOLUME_ID", "")
    missing = [
        name
        for name, value in (
            ("source pod id", source),
            ("data center id", dc),
            ("network volume id", volume),
        )
        if not value
    ]
    if missing:
        raise RuntimeError("missing " + ", ".join(missing))
    return Config(
        source_pod_id=source,
        data_center_id=dc,
        network_volume_id=volume,
        mount_path=args.mount_path,
        upgrade_entrypoint=args.upgrade_entrypoint,
        poll_seconds=args.poll_seconds,
        hold_seconds=args.hold_minutes * 60,
        targets=parse_targets(args.targets),
        state_dir=Path(args.state_dir),
        log_path=Path(args.log_path),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claim", action="store_true", help="create a replacement pod when capacity appears")
    parser.add_argument("--once", action="store_true", help="run one probe/claim iteration and exit")
    parser.add_argument("--source-pod-id")
    parser.add_argument("--data-center-id")
    parser.add_argument("--network-volume-id")
    parser.add_argument("--mount-path", default="/workspace")
    parser.add_argument("--upgrade-entrypoint", default=DEFAULT_UPGRADE_ENTRYPOINT)
    parser.add_argument("--targets", default="16/32,8/16")
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument(
        "--hold-minutes",
        type=int,
        default=180,
        help="replacement waits this long for source shutdown before self-termination",
    )
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--log-path", default=str(DEFAULT_LOG))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.poll_seconds < 10:
        raise SystemExit("--poll-seconds must be >= 10")
    if args.hold_minutes < 30:
        raise SystemExit("--hold-minutes must be >= 30")

    env = runtime_environment()
    api_key = env.get("RUNPOD_API_KEY", "")
    if not api_key:
        raise SystemExit(
            "RUNPOD_API_KEY unavailable; run as root on the RunPod or export it explicitly"
        )
    cfg = config_from_args(args, env)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = cfg.state_dir / "hunter.lock"
    lock_handle = lock_path.open("a+")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("runpod_cpu_hunter: another instance holds the lock", file=sys.stderr)
        return 0

    api = RunpodApi(api_key)
    log_line(
        cfg.log_path,
        "hunter-start",
        source=cfg.source_pod_id,
        dc=cfg.data_center_id,
        volume=cfg.network_volume_id,
        claim=int(args.claim),
        targets=",".join(t.label for t in cfg.targets),
    )

    while True:
        try:
            state = run_iteration(api, cfg, claim=args.claim)
            outcome = str(state.get("outcome") or "unknown")
            if args.once:
                public = {
                    key: state.get(key)
                    for key in (
                        "outcome",
                        "source_pod_id",
                        "candidate_pod_id",
                        "candidate_status",
                        "target",
                        "cpu_flavor_id",
                        "data_center_id",
                        "network_volume_id",
                        "last_probe_at",
                    )
                    if state.get(key) is not None
                }
                print(json.dumps(public, sort_keys=True))
                return 0
            if outcome in {"claimed", "candidate-held", "candidate-recovered"}:
                # Capacity is reserved. Keep checking so a timed-out/self-terminated
                # candidate is noticed and hunting resumes without spawning duplicates.
                time.sleep(cfg.poll_seconds)
            else:
                time.sleep(cfg.poll_seconds)
        except KeyboardInterrupt:
            log_line(cfg.log_path, "hunter-stop", reason="keyboard-interrupt")
            return 130
        except Exception as exc:  # daemon must survive transient provider failures
            log_line(
                cfg.log_path,
                "iteration-error",
                error=type(exc).__name__,
                message=str(exc).replace("\n", " ")[:500],
            )
            if args.once:
                raise
            time.sleep(max(cfg.poll_seconds, 30))


if __name__ == "__main__":
    raise SystemExit(main())
