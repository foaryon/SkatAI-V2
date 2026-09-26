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
    'provenance/AUTOMATED_TRAIN_EVAL_PROMOTE_ACCEPTANCE_20260926.json': 'abbd8703a6d4d9ad42c40cc06a62bc882d9ed0a118911968edf83ec0004a65b4',
    'provenance/WEAKNESS_MINING_ACCEPTANCE_20260926.json': '38c8ceab7fc415fb1cb2e467f5d9ba8d59a9d30e647fcf564c4e242a0ca80236',
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


FROZEN_PORT_TARGET_SHA256 = {
    'integrations/jskat-adapter/.gitignore': '8c14e5ce2a4b785f38e12036540e8838090de3a962d6e91132c34bf1f129d2a2',
    'integrations/jskat-adapter/README.md': '9a1d843e38391abfef114c90d2471d6dd4d7cd8efbd792fc846f130effc57072',
    'integrations/jskat-adapter/build.gradle.kts': '43a6b35453795ac9d0ce89db34ca3695c04adf226e0da6806ba94e8321d2d4b9',
    'integrations/jskat-adapter/patches/.gitattributes': '39599d875411ccdbdf08dabc3ea21a90c69d3587e027dc7103a43ff607b042c5',
    'integrations/jskat-adapter/patches/jskat-skatai-player.patch': '805031765843b621c98d9a0ddda25a052f09d7a2f6f4e4fdaad34dff9f30f894',
    'integrations/jskat-adapter/settings.gradle.kts': 'bb903bb300e1ed45881108a6a5f95cae098ae20cc3d410cf70f8304b2894c414',
    'integrations/jskat-adapter/src/main/java/org/skatai/v2/jskat/ContractMapper.java': '7ec70e4154a4ac12d4cc903e46082175848264efcdbad0d0f6c82d193151714f',
    'integrations/jskat-adapter/src/main/java/org/skatai/v2/jskat/HostClient.java': '3185b96a4b7b8f32e2459bfd215fc0d247776985ec6c7c5fbfdafbe1269d3e2b',
    'integrations/jskat-adapter/src/main/java/org/skatai/v2/jskat/JsonLineHostClient.java': 'bd4dc8d845f305803f5562e70ed8a8503d86ede28d1c14d242cb3b008412dedd',
    'integrations/jskat-adapter/src/main/java/org/skatai/v2/jskat/ProtocolIdentity.java': '691d8ba867def758f47a9e6bf6e600bc7c1065c52f8ceafa663b859b9d0dfa86',
    'integrations/jskat-adapter/src/main/java/org/skatai/v2/jskat/SkatAIJSkatPlayer.java': '198c68549573f6e07597dd3845d67385485a1fed68f13d5eb89567b40562d1d7',
    'integrations/jskat-adapter/src/test/java/org/skatai/v2/jskat/ContractMapperTest.java': '8b218057967788435886a0e4ab83e9b27ef755a225230a58217e5588284b9c4d',
    'integrations/jskat-adapter/src/test/java/org/skatai/v2/jskat/JsonLineHostClientIntegrationTest.java': '3f073d7128394d05877a300de1c189b0f07d24413a9de666af4179b9e03439e3',
    'integrations/jskat-adapter/src/test/java/org/skatai/v2/jskat/ProtocolIdentityTest.java': '54b79656edbd5feb39e6f68cf869770e0d79e9defe868de5cf696c03358b891c',
    'integrations/jskat-adapter/src/test/java/org/skatai/v2/jskat/SkatAIJSkatPlayerTest.java': 'b167af0551d42aabcee4ebd88fea3726309b663c43f48b9040b5bc40ffc2e622',
    'provenance/JSKAT_INSTALLED_RUNTIME_WHEEL_GATE_20260925.json': '3b109366bfcc687ac4b15fb5fcfb51b84afeb9a408f82f73a6a8a39968f621af',
    'provenance/JSKAT_RUNTIME_INTEGRATION_V1_20260925.json': '85680ab6a90f8c1b1b3ff936a9095ee88f7019c77358e90ff7f8b60a3125a9ed',
    'src/skatai/runtime/host_service.py': '317f4436f43477167360a4c05f8b624af8a3fd3855b8a58676c530f10aa38ed7',
    'tests/test_host_service.py': '016fb7471339c97ad81f525775cb1805097adc1d51943440c5eefcfd3ae22892',
}


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
        invalid = [rel for rel in changed if rel not in FROZEN_PORT_TARGET_SHA256
                   or not (REPO / rel).is_file()
                   or hashlib.sha256((REPO / rel).read_bytes()).hexdigest() != FROZEN_PORT_TARGET_SHA256[rel]]
        if invalid:
            raise SystemExit("JSKAT_PORT_TARGET_HISTORY_CHANGED:" + ",".join(invalid))


def assert_worktree_safe(tree: str) -> None:
    dirty = []
    for row in git("status", "--porcelain=v1", "--untracked-files=all").stdout.splitlines():
        if not row:
            continue
        path = row[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path in TARGETS:
            entry = tree_entry(tree, path)
            candidate = REPO / path
            if entry is not None and candidate.is_file() and not candidate.is_symlink():
                mode, data = entry
                if candidate.read_bytes() == data and bool(candidate.stat().st_mode & 0o111) == (mode == "100755"):
                    continue
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
    tree = merged_tree()
    assert_worktree_safe(tree)

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
