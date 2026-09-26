#!/usr/bin/env python3
"""Deterministic current-main portability audit for the verified JSkat adapter branch."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path("/workspace/skatai-v2")
BRANCH = "feat/jskat-runtime-adapter-v1"
EXPECTED_HEAD = "575858100ffbfc2fc24cb0d5713038eb930adc9a"

ALLOWED_EXACT = {
    "provenance/JSKAT_INSTALLED_RUNTIME_WHEEL_GATE_20260925.json",
    "provenance/JSKAT_RUNTIME_INTEGRATION_V1_20260925.json",
    "src/skatai/runtime/host_service.py",
    "tests/test_host_service.py",
}
ALLOWED_PREFIX = "integrations/jskat-adapter/"


def run(*argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(
        list(argv),
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
    )
    if check and cp.returncode != 0:
        raise RuntimeError(f"command failed {argv!r}: {cp.stderr.strip()}")
    return cp


def main() -> int:
    head = run("git", "rev-parse", "HEAD").stdout.strip()
    branch_head = run("git", "rev-parse", BRANCH).stdout.strip()
    if branch_head != EXPECTED_HEAD:
        raise SystemExit("JSKAT_ADAPTER_BRANCH_IDENTITY_DRIFT")

    base = run("git", "merge-base", head, branch_head).stdout.strip()
    mt = run("git", "merge-tree", "--write-tree", head, branch_head, check=False)
    if mt.returncode != 0:
        print(json.dumps({
            "status": "CONFLICT",
            "main_head": head,
            "branch_head": branch_head,
            "merge_base": base,
            "stderr": mt.stderr[-4000:],
        }, sort_keys=True))
        return 2
    merged_tree = mt.stdout.strip().splitlines()[0].strip()

    changed = []
    cp = run("git", "diff-tree", "--no-commit-id", "--name-status", "-r", head, merged_tree)
    for line in cp.stdout.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        path = fields[-1]
        changed.append({"status": fields[0], "path": path})

    unexpected = [
        row for row in changed
        if row["path"] not in ALLOWED_EXACT and not row["path"].startswith(ALLOWED_PREFIX)
    ]
    if unexpected:
        print(json.dumps({
            "status": "UNEXPECTED_PATHS",
            "main_head": head,
            "branch_head": branch_head,
            "merge_base": base,
            "merged_tree": merged_tree,
            "unexpected": unexpected,
        }, sort_keys=True))
        return 3

    out = {
        "status": "PASS_CLEAN_MERGE_TREE",
        "main_head": head,
        "branch_head": branch_head,
        "merge_base": base,
        "merged_tree": merged_tree,
        "changed_path_count": len(changed),
        "changed_paths": changed,
        "runtime_java_available": bool(subprocess.run(
            ["sh", "-c", "command -v java >/dev/null 2>&1 && command -v javac >/dev/null 2>&1"],
            cwd=ROOT,
        ).returncode == 0),
        "claim_limit": "portability only; no current-main Java/JSkat runtime acceptance without deployment-matched JDK revalidation",
    }
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
