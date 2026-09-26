#!/usr/bin/env python3
"""Build and smoke the exact committed source-bound B0 product package."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

REPO = Path("/workspace/skatai-v2")
RUNTIME_ROOT = Path("/workspace/skatai-v2-runtime")
UPSTREAM_SOURCE = RUNTIME_ROOT / "releases/V2-B0/skatzero-source.tar"
PRODUCT_PYTHON = RUNTIME_ROOT / "runtime/b0-venv/bin/python"
OUT_ROOT = RUNTIME_ROOT / "releases/current-source-host-smoke"
EXPECTED_UPSTREAM_SHA = "40eff4a1f03aa0a0c0665a11b733505130a79e8ac646195c8dc51c61b6fb3f2d"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(REPO), *args], text=True).strip()


def verify_runtime() -> dict[str, str]:
    code = (
        "import json,sys,torch,numpy,packaging;"
        "print(json.dumps({'python':sys.version.split()[0],"
        "'torch':torch.__version__,'numpy':numpy.__version__,"
        "'packaging':packaging.__version__},sort_keys=True))"
    )
    raw = subprocess.check_output([str(PRODUCT_PYTHON), "-c", code], text=True)
    versions = json.loads(raw)
    if not versions["python"].startswith("3.11."):
        raise RuntimeError("BAD_PRODUCT_PYTHON")
    if versions["torch"] != "2.1.2+cpu" or versions["numpy"] != "1.26.4":
        raise RuntimeError("BAD_PRODUCT_RUNTIME_IDENTITY")
    return versions


def valid_existing(result_path: Path, package_path: Path, commit: str) -> dict | None:
    if not result_path.is_file() or not package_path.is_file():
        return None
    result = json.loads(result_path.read_text())
    expected_release = f"V2-B0-package-v4-{commit}"
    if (
        result.get("source_commit") != commit
        or result.get("release_id") != expected_release
        or result.get("response_count") != 5
        or result.get("phases") != ["BID", "DECLARATION", "DISCARD", "PLAY_CARD", "PICKUP_PLAN"]
        or result.get("accepted_release_claim") is not False
        or result.get("actual_user_host_integration_claim") is not False
        or result.get("package_sha256") != sha256_file(package_path)
    ):
        return None
    return result


def main() -> int:
    if git("status", "--porcelain"):
        raise RuntimeError("REPO_DIRTY")
    commit = git("rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("BAD_SOURCE_COMMIT")
    if not UPSTREAM_SOURCE.is_file() or sha256_file(UPSTREAM_SOURCE) != EXPECTED_UPSTREAM_SHA:
        raise RuntimeError("UPSTREAM_SOURCE_IDENTITY_MISMATCH")
    runtime = verify_runtime()
    release_id = f"V2-B0-package-v4-{commit}"
    final_dir = OUT_ROOT / commit
    result_path = final_dir / "result.json"
    package_path = final_dir / f"{release_id}.skatmodel"
    existing = valid_existing(result_path, package_path, commit)
    if existing is not None:
        OUT_ROOT.mkdir(parents=True, exist_ok=True)
        latest = {
            "schema": "skatai.v2.current-source-host-smoke-latest.v1",
            "source_commit": commit,
            "release_id": existing["release_id"],
            "result_path": str(result_path),
            "artifact_path": str(package_path),
            "package_sha256": existing["package_sha256"],
        }
        (OUT_ROOT / "LATEST.json").write_text(json.dumps(latest, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"status": "REUSED_VERIFIED", **existing}, sort_keys=True))
        return 0

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{commit[:12]}.", dir=OUT_ROOT))
    scratch = work / "smoke"
    try:
        sys.path.insert(0, str(REPO / "src"))
        sys.path.insert(0, str(REPO / "scripts"))
        from smoke_b0_host_release import run_smoke
        from skatai.artifacts.release import validate_release_package

        result = run_smoke(REPO, UPSTREAM_SOURCE, PRODUCT_PYTHON, scratch)
        built_package = scratch / "V2-B0-current-source.skatmodel"
        if result.get("source_commit") != commit or result.get("release_id") != release_id:
            raise RuntimeError("SMOKE_SOURCE_IDENTITY_MISMATCH")
        if result.get("response_count") != 5:
            raise RuntimeError("SMOKE_RESPONSE_COUNT_MISMATCH")
        if result.get("phases") != ["BID", "DECLARATION", "DISCARD", "PLAY_CARD", "PICKUP_PLAN"]:
            raise RuntimeError("SMOKE_PHASE_SET_MISMATCH")
        if result.get("accepted_release_claim") is not False or result.get("actual_user_host_integration_claim") is not False:
            raise RuntimeError("SMOKE_CLAIM_BOUNDARY_VIOLATION")
        if result.get("package_sha256") != sha256_file(built_package):
            raise RuntimeError("SMOKE_PACKAGE_HASH_MISMATCH")
        validation = validate_release_package(built_package)
        if validation["manifest"].get("release_id") != release_id:
            raise RuntimeError("SMOKE_MANIFEST_RELEASE_ID_MISMATCH")

        final_dir.mkdir(parents=True, exist_ok=True)
        tmp_package = final_dir / f".{package_path.name}.tmp-{os.getpid()}"
        shutil.move(str(built_package), tmp_package)
        os.chmod(tmp_package, 0o444)
        os.replace(tmp_package, package_path)

        result = {
            **result,
            "schema": "skatai.v2.current-source-host-smoke.v2",
            "upstream_source_sha256": EXPECTED_UPSTREAM_SHA,
            "product_runtime": runtime,
            "artifact_path": str(package_path),
            "package_sha256": sha256_file(package_path),
            "reused": False,
        }
        tmp_result = final_dir / f".result.json.tmp-{os.getpid()}"
        tmp_result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        with tmp_result.open("rb") as f:
            os.fsync(f.fileno())
        os.replace(tmp_result, result_path)
        latest = {
            "schema": "skatai.v2.current-source-host-smoke-latest.v1",
            "source_commit": commit,
            "release_id": release_id,
            "result_path": str(result_path),
            "artifact_path": str(package_path),
            "package_sha256": result["package_sha256"],
        }
        (OUT_ROOT / "LATEST.json").write_text(json.dumps(latest, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"status": "PASS", **result}, sort_keys=True))
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
