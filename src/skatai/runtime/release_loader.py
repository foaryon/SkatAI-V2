"""Load a validated B0 release behind the stable SkatAI product interface."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile

from skatai.artifacts.release import (
    ReleasePackageError,
    materialize_release_package,
    sha256_file,
    validate_release_package,
)
from skatai.runtime.interface import SkatAI


def _extract_frozen_source(archive: Path, destination: Path) -> None:
    destination.mkdir()
    with tarfile.open(archive, "r:") as tf:
        for member in tf:
            name = member.name
            parts = PurePosixPath(name).parts
            if (
                not name
                or name.startswith("/")
                or "\\" in name
                or any(part in ("", ".", "..") for part in parts)
                or PurePosixPath(name).as_posix() != name
                or not (member.isfile() or member.isdir())
            ):
                raise ReleasePackageError(f"UNSAFE_FROZEN_SOURCE_MEMBER:{name!r}")
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            content = tf.extractfile(member)
            if content is None:
                raise ReleasePackageError(f"FROZEN_SOURCE_MEMBER_UNREADABLE:{name}")
            with target.open("xb") as out:
                shutil.copyfileobj(content, out, length=1024 * 1024)
            os.chmod(target, 0o644)


def _verify_frozen_source(archive: Path, root: Path) -> None:
    """Check every extracted source file before reusing a materialization."""

    expected: set[str] = set()
    with tarfile.open(archive, "r:") as tf:
        for member in tf:
            name = member.name
            parts = PurePosixPath(name).parts
            if (
                not name
                or name.startswith("/")
                or "\\" in name
                or any(part in ("", ".", "..") for part in parts)
                or PurePosixPath(name).as_posix() != name
                or not (member.isfile() or member.isdir())
            ):
                raise ReleasePackageError(f"UNSAFE_FROZEN_SOURCE_MEMBER:{name!r}")
            expected.add(name)
            installed = root.joinpath(*parts)
            if installed.is_symlink():
                raise ReleasePackageError(f"B0_RUNTIME_SOURCE_LINK:{name}")
            if member.isdir():
                if not installed.is_dir():
                    raise ReleasePackageError(f"B0_RUNTIME_SOURCE_DIR_MISMATCH:{name}")
                continue
            if not installed.is_file() or installed.stat().st_size != member.size:
                raise ReleasePackageError(f"B0_RUNTIME_SOURCE_MISMATCH:{name}")
            original = tf.extractfile(member)
            if original is None:
                raise ReleasePackageError(f"FROZEN_SOURCE_MEMBER_UNREADABLE:{name}")
            original_hash = hashlib.sha256()
            installed_hash = hashlib.sha256()
            with installed.open("rb") as copy:
                while chunk := original.read(1024 * 1024):
                    original_hash.update(chunk)
                while chunk := copy.read(1024 * 1024):
                    installed_hash.update(chunk)
            if original_hash.digest() != installed_hash.digest():
                raise ReleasePackageError(f"B0_RUNTIME_SOURCE_MISMATCH:{name}")
    for installed in root.rglob("*"):
        relative = installed.relative_to(root)
        if installed.is_symlink():
            raise ReleasePackageError(f"B0_RUNTIME_SOURCE_LINK:{relative}")
        if "__pycache__" in relative.parts:
            if installed.is_dir() or installed.suffix == ".pyc":
                continue
        if relative.as_posix() not in expected:
            raise ReleasePackageError(f"B0_RUNTIME_SOURCE_UNEXPECTED:{relative}")


def _verify_materialized(root: Path, manifest: dict) -> dict:
    bundle = root / "bundle"
    if json.loads((bundle / "manifest.json").read_text()) != manifest:
        raise ReleasePackageError("MATERIALIZED_MANIFEST_MISMATCH")
    for row in manifest["components"]:
        component = bundle / row["path"]
        if component.stat().st_size != row["bytes"] or sha256_file(component) != row["sha256"]:
            raise ReleasePackageError(f"MATERIALIZED_COMPONENT_MISMATCH:{row['path']}")
    baseline = json.loads((bundle / "provenance/B0_SKATZERO_BASELINE.json").read_text())
    if manifest["model_hashes"] != baseline["pretrained_models"]:
        raise ReleasePackageError("B0_RELEASE_MODEL_IDENTITY_MISMATCH")
    if sha256_file(bundle / "source/skatzero-source.tar") != baseline["git_archive_sha256"]:
        raise ReleasePackageError("B0_SOURCE_HASH_MISMATCH")
    skatzero_root = root / "skatzero"
    _verify_frozen_source(bundle / "source/skatzero-source.tar", skatzero_root)
    if sha256_file(skatzero_root / "api.py") != baseline["implementation_hashes"]["inference_api_py"]:
        raise ReleasePackageError("B0_INFERENCE_API_HASH_MISMATCH")
    for name, expected in sorted(baseline["pretrained_models"].items()):
        if sha256_file(skatzero_root / "models/latest" / name) != expected:
            raise ReleasePackageError(f"B0_RUNTIME_MODEL_HASH_MISMATCH:{name}")
    return baseline


def _supported_release_identity(manifest: dict) -> bool:
    release_id = manifest["release_id"]
    if release_id in {"V2-B0-package-v1", "V2-B0-package-v2", "V2-B0-package-v3"}:
        return True
    source_commit = manifest.get("source_commit")
    return (
        isinstance(release_id, str)
        and isinstance(source_commit, str)
        and re.fullmatch(r"V2-B0-package-v4-[0-9a-f]{40}", release_id) is not None
        and release_id == f"V2-B0-package-v4-{source_commit}"
    )


def load_model(
    package: Path,
    *,
    materialize_to: Path,
    python_executable: Path,
) -> SkatAI:
    """Validate and atomically materialize a B0 package, then return its SkatAI."""

    package = Path(package)
    destination = Path(materialize_to)
    python_executable = Path(python_executable)
    if not python_executable.is_file():
        raise ReleasePackageError("RELEASE_PYTHON_MISSING")
    validation = validate_release_package(package)
    manifest = validation["manifest"]
    if (
        not _supported_release_identity(manifest)
        or manifest.get("release_status") != "BASELINE_PACKAGE_STAGED"
    ):
        raise ReleasePackageError("UNSUPPORTED_RELEASE_IDENTITY")
    if destination.exists():
        _verify_materialized(destination, manifest)
        from skatai.runtime.skatzero_backend import build_b0_skat_ai

        return build_b0_skat_ai(destination / "skatzero", python_executable)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    if staging.exists():
        raise ReleasePackageError(f"TEMP_DESTINATION_ALREADY_EXISTS:{staging}")
    staging.mkdir()
    try:
        bundle = staging / "bundle"
        materialize_release_package(package, bundle)
        baseline = json.loads(
            (bundle / "provenance/B0_SKATZERO_BASELINE.json").read_text()
        )
        frozen_source = bundle / "source/skatzero-source.tar"
        if sha256_file(frozen_source) != baseline["git_archive_sha256"]:
            raise ReleasePackageError("B0_SOURCE_HASH_MISMATCH")
        if manifest["model_hashes"] != baseline["pretrained_models"]:
            raise ReleasePackageError("B0_RELEASE_MODEL_IDENTITY_MISMATCH")

        skatzero_root = staging / "skatzero"
        _extract_frozen_source(frozen_source, skatzero_root)
        if (
            sha256_file(skatzero_root / "api.py")
            != baseline["implementation_hashes"]["inference_api_py"]
        ):
            raise ReleasePackageError("B0_INFERENCE_API_HASH_MISMATCH")
        model_dir = skatzero_root / "models/latest"
        model_dir.mkdir(parents=True, exist_ok=True)
        for name, expected in sorted(baseline["pretrained_models"].items()):
            if manifest["release_id"] == "V2-B0-package-v1":
                if sha256_file(bundle / "models" / name) != expected:
                    raise ReleasePackageError(f"B0_MODEL_HASH_MISMATCH:{name}")
            installed = model_dir / name
            if installed.exists():
                if sha256_file(installed) != expected:
                    raise ReleasePackageError(f"B0_EMBEDDED_MODEL_HASH_MISMATCH:{name}")
            else:
                model = bundle / "models" / name
                if manifest["release_id"] != "V2-B0-package-v1":
                    raise ReleasePackageError(f"B0_EMBEDDED_MODEL_MISSING:{name}")
                if sha256_file(model) != expected:
                    raise ReleasePackageError(f"B0_MODEL_HASH_MISMATCH:{name}")
                os.link(model, installed)

        _verify_materialized(staging, manifest)

        os.replace(staging, destination)
        parent_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    from skatai.runtime.skatzero_backend import build_b0_skat_ai

    return build_b0_skat_ai(destination / "skatzero", python_executable)
