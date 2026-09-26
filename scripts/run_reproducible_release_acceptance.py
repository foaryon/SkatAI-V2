#!/usr/bin/env python3
"""Deterministic acceptance precheck for reproducible/versioned release artifacts."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path("/workspace/skatai-v2")
PKG = ROOT / "provenance/CURRENT_SOURCE_PACKAGE_HOST_SMOKE_20260926.json"
WHEEL = ROOT / "provenance/CURRENT_SOURCE_RUNTIME_WHEEL_20260926.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    pkg = load(PKG)
    wheel = load(WHEEL)
    if pkg.get("classification") != "ACCEPT":
        raise SystemExit("PACKAGE_GATE_NOT_ACCEPT")
    if wheel.get("classification") != "ACCEPT":
        raise SystemExit("WHEEL_GATE_NOT_ACCEPT")

    pv = pkg["verification"]
    wv = wheel["verification"]
    source = str(pv["source_commit"])
    if source != str(wv["source_commit"]):
        raise SystemExit("SOURCE_COMMIT_MISMATCH")
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise SystemExit("SOURCE_COMMIT_INVALID")

    presult = pv["result"]
    wresult = wv["result"]
    expected_release = f"V2-B0-package-v4-{source}"
    if presult.get("release_id") != expected_release:
        raise SystemExit("PACKAGE_RELEASE_ID_NOT_SOURCE_BOUND")
    if presult.get("source_commit") != source:
        raise SystemExit("PACKAGE_SOURCE_IDENTITY_MISMATCH")
    if presult.get("response_count") != 5:
        raise SystemExit("PACKAGE_HOST_SMOKE_RESPONSE_COUNT_INVALID")
    if presult.get("phases") != ["BID", "DECLARATION", "DISCARD", "PLAY_CARD", "PICKUP_PLAN"]:
        raise SystemExit("PACKAGE_HOST_PHASES_INVALID")
    if presult.get("accepted_release_claim") is not False:
        raise SystemExit("PACKAGE_GATE_PROMOTION_CLAIM_UNEXPECTED")

    if wresult.get("source_commit") != source:
        raise SystemExit("WHEEL_SOURCE_IDENTITY_MISMATCH")
    if wresult.get("byte_reproducible") is not True or int(wresult.get("independent_builds", 0)) < 2:
        raise SystemExit("WHEEL_NOT_BYTE_REPRODUCIBLE")
    if wresult.get("installed_import_without_pythonpath") is not True:
        raise SystemExit("WHEEL_INSTALLED_IMPORT_NOT_VERIFIED")
    if wresult.get("promotion_claim") is not False:
        raise SystemExit("WHEEL_GATE_PROMOTION_CLAIM_UNEXPECTED")

    package_sha = str(pv.get("package_sha256") or presult.get("package_sha256") or "")
    wheel_sha = str(wresult.get("wheel_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", package_sha):
        raise SystemExit("PACKAGE_SHA_INVALID")
    if not re.fullmatch(r"[0-9a-f]{64}", wheel_sha):
        raise SystemExit("WHEEL_SHA_INVALID")

    upload = pv.get("artifact_upload") or {}
    if upload.get("sha256") != package_sha or not str(upload.get("remote") or "").startswith("s3://skatai-v2/"):
        raise SystemExit("PACKAGE_DURABILITY_EVIDENCE_INVALID")

    cp = subprocess.run(
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", source, "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
    )
    if cp.returncode != 0:
        raise SystemExit("RELEASE_SOURCE_NOT_ANCESTOR_OF_CURRENT_MAIN")

    out = {
        "status": "PASS",
        "source_commit": source,
        "release_id": expected_release,
        "package_sha256": package_sha,
        "wheel_sha256": wheel_sha,
        "wheel_byte_reproducible": True,
        "package_host_response_count": 5,
        "durable_package_remote": upload["remote"],
        "scope": "reproducible/versioned release artifact capability; no model promotion or user-host deployment claim",
    }
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
