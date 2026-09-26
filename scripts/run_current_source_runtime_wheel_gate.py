#!/usr/bin/env python3
"""Build the exact committed SkatAI wheel twice and verify an installed runtime."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

REPO = Path("/workspace/skatai-v2")
RUNTIME_ROOT = Path("/workspace/skatai-v2-runtime")
PRODUCT_PYTHON = RUNTIME_ROOT / "runtime/b0-venv/bin/python"
OUT_ROOT = RUNTIME_ROOT / "releases/current-source-wheel"
UV = Path("/usr/bin/uv")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(REPO), *args], text=True).strip()


def run(argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    subprocess.run(argv, cwd=cwd, env=env, check=True, timeout=300)


def verify_runtime() -> dict[str, str]:
    code = (
        "import json,sys,torch,numpy,packaging;"
        "print(json.dumps({'python':sys.version.split()[0],"
        "'torch':torch.__version__,'numpy':numpy.__version__,"
        "'packaging':packaging.__version__},sort_keys=True))"
    )
    versions = json.loads(subprocess.check_output([str(PRODUCT_PYTHON), "-c", code], text=True))
    if not versions["python"].startswith("3.11."):
        raise RuntimeError("BAD_PRODUCT_PYTHON")
    if (
        versions["torch"] != "2.1.2+cpu"
        or versions["numpy"] != "1.26.4"
        or versions["packaging"] != "26.3"
    ):
        raise RuntimeError("BAD_PRODUCT_RUNTIME_IDENTITY")
    return versions


def build_once(source: Path, out: Path, epoch: str) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/tmp",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "SOURCE_DATE_EPOCH": epoch,
        "PYTHONHASHSEED": "0",
        "UV_CACHE_DIR": "/workspace/.cache/uv",
        "UV_NO_PROGRESS": "1",
    }
    run([str(UV), "build", "--wheel", "--out-dir", str(out)], cwd=source, env=env)
    wheels = list(out.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("WHEEL_BUILD_COUNT_INVALID")
    return wheels[0]


def main() -> int:
    if git("status", "--porcelain"):
        raise RuntimeError("REPO_DIRTY")
    commit = git("rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("BAD_SOURCE_COMMIT")
    epoch = git("show", "-s", "--format=%ct", commit)
    runtime_identity = verify_runtime()
    final_dir = OUT_ROOT / commit
    result_path = final_dir / "result.json"
    final_wheel = final_dir / "skatai_v2-0.0.1-py3-none-any.whl"

    if result_path.is_file() and final_wheel.is_file():
        result = json.loads(result_path.read_text())
        if (
            result.get("source_commit") == commit
            and result.get("wheel_sha256") == sha256_file(final_wheel)
            and result.get("byte_reproducible") is True
            and result.get("installed_import_without_pythonpath") is True
        ):
            OUT_ROOT.mkdir(parents=True, exist_ok=True)
            latest = {
                "schema": "skatai.v2.current-source-runtime-wheel-latest.v1",
                "source_commit": commit,
                "result_path": str(result_path),
                "wheel_path": str(final_wheel),
                "wheel_sha256": result["wheel_sha256"],
            }
            (OUT_ROOT / "LATEST.json").write_text(json.dumps(latest, indent=2, sort_keys=True) + "\n")
            print(json.dumps({"status": "REUSED_VERIFIED", **result}, sort_keys=True))
            return 0

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".wheel-{commit[:12]}.", dir=OUT_ROOT))
    try:
        archive = work / "source.tar"
        run(["git", "-C", str(REPO), "archive", "--format=tar", commit, "-o", str(archive)])
        src1 = work / "src1"
        src2 = work / "src2"
        src1.mkdir(); src2.mkdir()
        with tarfile.open(archive, "r:") as tf:
            tf.extractall(src1)
        with tarfile.open(archive, "r:") as tf:
            tf.extractall(src2)
        wheel1 = build_once(src1, work / "dist1", epoch)
        wheel2 = build_once(src2, work / "dist2", epoch)
        sha1 = sha256_file(wheel1)
        sha2 = sha256_file(wheel2)
        if sha1 != sha2 or wheel1.read_bytes() != wheel2.read_bytes():
            raise RuntimeError("WHEEL_NOT_BYTE_REPRODUCIBLE")

        final_dir.mkdir(parents=True, exist_ok=True)
        tmp_wheel = final_dir / f".{final_wheel.name}.tmp-{os.getpid()}"
        shutil.copyfile(wheel1, tmp_wheel)
        os.chmod(tmp_wheel, 0o444)
        os.replace(tmp_wheel, final_wheel)

        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("SKATAI_V2_PYTHONPATH", None)
        env["UV_CACHE_DIR"] = "/workspace/.cache/uv"
        env["UV_NO_PROGRESS"] = "1"
        run([str(UV), "pip", "install", "--python", str(PRODUCT_PYTHON), "--reinstall", str(final_wheel)], env=env)
        code = (
            "import json,skatai,skatai.runtime.host_service as h;"
            "print(json.dumps({'skatai':skatai.__file__,'host_service':h.__file__},sort_keys=True))"
        )
        imported = json.loads(subprocess.check_output(
            [str(PRODUCT_PYTHON), "-I", "-c", code], text=True, env=env
        ))
        if "/site-packages/" not in imported["skatai"]:
            raise RuntimeError("INSTALLED_IMPORT_NOT_FROM_SITE_PACKAGES")

        result = {
            "schema": "skatai.v2.current-source-runtime-wheel.v1",
            "source_commit": commit,
            "source_date_epoch": int(epoch),
            "wheel_name": final_wheel.name,
            "wheel_sha256": sha256_file(final_wheel),
            "wheel_bytes": final_wheel.stat().st_size,
            "independent_builds": 2,
            "byte_reproducible": True,
            "installed_import_without_pythonpath": True,
            "installed_skatai_path": imported["skatai"],
            "installed_host_service_path": imported["host_service"],
            "runtime_python": str(PRODUCT_PYTHON),
            "runtime_identity": runtime_identity,
            "promotion_claim": False,
        }
        tmp_result = final_dir / f".result.json.tmp-{os.getpid()}"
        tmp_result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        os.replace(tmp_result, result_path)
        latest = {
            "schema": "skatai.v2.current-source-runtime-wheel-latest.v1",
            "source_commit": commit,
            "result_path": str(result_path),
            "wheel_path": str(final_wheel),
            "wheel_sha256": result["wheel_sha256"],
        }
        (OUT_ROOT / "LATEST.json").write_text(json.dumps(latest, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"status": "PASS", **result}, sort_keys=True))
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
