#!/usr/bin/env python3
"""Deterministically materialize the audited JSkat adapter merge tree.

This script never commits, stages, fetches, merges refs, or changes authority.
It writes only the fixed path set previously accepted by the portability gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

REPO = Path("/workspace/skatai-v2")
AUDITED_MAIN = "c256ac9980d4952476d23e096582f4f6c0f1f154"
BRANCH = "575858100ffbfc2fc24cb0d5713038eb930adc9a"
# Cutover artifacts were present before this source port. Their byte identities
# are fixed here; changed or additional dirty files fail closed.
ALLOWED_PREEXISTING_UNTRACKED_SHA256 = {
    'provenance/AUTOMATED_TRAIN_EVAL_PROMOTE_ACCEPTANCE_20260926.json': '37978212ab4986c63c3c4455a830f56cea7dd41e47b2de54bda40aceaac0b95c',
    'provenance/DATA_SPLIT_LEAKAGE_ACCEPTANCE_20260926.json': '29055f23ef6e331c7f47a4301af8e9108b14688bfd4625fcd203446d29f709ea',
    'provenance/JSKAT_ADAPTER_SOURCE_PORT_20260926.json': 'f15aaccb7e852c2412e6c0a4af3d2936fdf3215f43bcf8e364a839513d4bb43b',
    'provenance/WEAKNESS_MINING_ACCEPTANCE_20260926.json': '7e0dcb689418b2ae50104267bd2cec7209f4d601bcd700e4cadb9ad5808a46b9',
    'scripts/audit_data_split_leakage.py': '927554c68e3658a9b31ec1ef8c1573e49bfc9286ec50264beeb67f99d8ffd9eb',
    'scripts/run_jskat_adapter_source_port_tests.py': 'b8852526a96423abe0ddf447ff551679112409dab883a006ad885927fad139ce',
}

TARGETS = (
    "integrations/jskat-adapter/.gitignore",
    "integrations/jskat-adapter/README.md",
    "integrations/jskat-adapter/build.gradle.kts",
    "integrations/jskat-adapter/patches/.gitattributes",
    "integrations/jskat-adapter/patches/jskat-skatai-player.patch",
    "integrations/jskat-adapter/settings.gradle.kts",
    "integrations/jskat-adapter/src/main/java/org/skatai/v2/jskat/ContractMapper.java",
    "integrations/jskat-adapter/src/main/java/org/skatai/v2/jskat/HostClient.java",
    "integrations/jskat-adapter/src/main/java/org/skatai/v2/jskat/JsonLineHostClient.java",
    "integrations/jskat-adapter/src/main/java/org/skatai/v2/jskat/ProtocolIdentity.java",
    "integrations/jskat-adapter/src/main/java/org/skatai/v2/jskat/SkatAIJSkatPlayer.java",
    "integrations/jskat-adapter/src/test/java/org/skatai/v2/jskat/ContractMapperTest.java",
    "integrations/jskat-adapter/src/test/java/org/skatai/v2/jskat/JsonLineHostClientIntegrationTest.java",
    "integrations/jskat-adapter/src/test/java/org/skatai/v2/jskat/ProtocolIdentityTest.java",
    "integrations/jskat-adapter/src/test/java/org/skatai/v2/jskat/SkatAIJSkatPlayerTest.java",
    "provenance/JSKAT_INSTALLED_RUNTIME_WHEEL_GATE_20260925.json",
    "provenance/JSKAT_RUNTIME_INTEGRATION_V1_20260925.json",
    "src/skatai/runtime/host_service.py",
    "tests/test_host_service.py",
)


def git(*args: str, check: bool = True, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        check=check,
        capture_output=True,
        text=text,
        timeout=60,
    )


def fixed_branch_diff() -> tuple[str, ...]:
    base = git("merge-base", AUDITED_MAIN, BRANCH).stdout.strip()
    changed = tuple(
        row.strip()
        for row in git("diff", "--name-only", f"{base}..{BRANCH}", "--", *TARGETS).stdout.splitlines()
        if row.strip()
    )
    if set(changed) != set(TARGETS):
        raise SystemExit("JSKAT_PORT_BRANCH_PATH_SET_MISMATCH")
    return changed


def assert_target_history_unchanged() -> None:
    changed = [
        row.strip()
        for row in git("diff", "--name-only", f"{AUDITED_MAIN}..HEAD", "--", *TARGETS).stdout.splitlines()
        if row.strip()
    ]
    if changed:
        raise SystemExit("JSKAT_PORT_TARGET_HISTORY_CHANGED:" + ",".join(changed))


def assert_worktree_safe() -> None:
    dirty = []
    for row in git("status", "--porcelain=v1", "--untracked-files=all").stdout.splitlines():
        if not row:
            continue
        path = row[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        expected = ALLOWED_PREEXISTING_UNTRACKED_SHA256.get(path)
        if row.startswith("?? ") and expected is not None:
            candidate = REPO / path
            if candidate.is_file() and not candidate.is_symlink() and hashlib.sha256(candidate.read_bytes()).hexdigest() == expected:
                continue
        dirty.append(path)
    if dirty:
        raise SystemExit("JSKAT_PORT_UNRELATED_DIRTY_WORKTREE:" + ",".join(sorted(dirty)))


def merged_tree() -> str:
    cp = git("merge-tree", "--write-tree", "HEAD", BRANCH, check=False)
    if cp.returncode != 0:
        raise SystemExit("JSKAT_PORT_MERGE_TREE_CONFLICT:" + (cp.stdout + cp.stderr)[-4000:])
    tree = cp.stdout.strip().splitlines()[0] if cp.stdout.strip() else ""
    if not re.fullmatch(r"[0-9a-f]{40}", tree):
        raise SystemExit("JSKAT_PORT_MERGE_TREE_INVALID")
    return tree


def tree_entry(tree: str, rel: str) -> tuple[str, bytes] | None:
    cp = git("ls-tree", tree, "--", rel)
    line = cp.stdout.rstrip("\n")
    if not line:
        return None
    meta, path = line.split("\t", 1)
    mode, typ, oid = meta.split()
    if path != rel or typ != "blob" or mode not in {"100644", "100755"}:
        raise SystemExit("JSKAT_PORT_TREE_ENTRY_UNSUPPORTED:" + rel)
    blob = subprocess.run(
        ["git", "-C", str(REPO), "cat-file", "blob", oid],
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout
    return mode, blob


def verify(tree: str) -> list[dict[str, object]]:
    rows = []
    for rel in TARGETS:
        entry = tree_entry(tree, rel)
        path = REPO / rel
        if entry is None:
            if path.exists() or path.is_symlink():
                raise SystemExit("JSKAT_PORT_VERIFY_EXPECTED_ABSENT:" + rel)
            rows.append({"path": rel, "present": False})
            continue
        mode, expected = entry
        if not path.is_file() or path.read_bytes() != expected:
            raise SystemExit("JSKAT_PORT_VERIFY_CONTENT_MISMATCH:" + rel)
        executable = bool(path.stat().st_mode & 0o111)
        if executable != (mode == "100755"):
            raise SystemExit("JSKAT_PORT_VERIFY_MODE_MISMATCH:" + rel)
        rows.append({"path": rel, "present": True, "bytes": len(expected), "mode": mode})
    return rows


def apply(tree: str) -> list[dict[str, object]]:
    # If all targets already match, this is safely idempotent.
    try:
        return verify(tree)
    except SystemExit:
        pass
    for rel in TARGETS:
        entry = tree_entry(tree, rel)
        path = REPO / rel
        if entry is None:
            if path.exists() or path.is_symlink():
                path.unlink()
            continue
        mode, data = entry
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".skatai-port-", dir=path.parent)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o755 if mode == "100755" else 0o644)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    return verify(tree)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("plan", "apply", "verify"), required=True)
    args = ap.parse_args()

    if git("rev-parse", BRANCH).stdout.strip() != BRANCH:
        raise SystemExit("JSKAT_PORT_BRANCH_IDENTITY_MISMATCH")
    fixed_branch_diff()
    assert_target_history_unchanged()
    assert_worktree_safe()
    tree = merged_tree()

    if args.mode == "plan":
        rows = [{"path": rel} for rel in TARGETS]
    elif args.mode == "apply":
        rows = apply(tree)
    else:
        rows = verify(tree)

    print(json.dumps({
        "schema": "skatai.v2.jskat-adapter-source-port.v1",
        "status": "PASS",
        "mode": args.mode,
        "audited_main": AUDITED_MAIN,
        "current_head": git("rev-parse", "HEAD").stdout.strip(),
        "branch_head": BRANCH,
        "merged_tree": tree,
        "path_count": len(TARGETS),
        "paths": rows,
        "java_validation": "NOT_RUN",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
