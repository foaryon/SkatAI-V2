"""Load a validated B0 release behind the stable SkatAI product interface."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
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
        manifest["release_id"] not in {"V2-B0-package-v1", "V2-B0-package-v2"}
        or manifest.get("release_status") != "BASELINE_PACKAGE_STAGED"
    ):
        raise ReleasePackageError("UNSUPPORTED_RELEASE_IDENTITY")
    if destination.exists():
        raise ReleasePackageError(f"DESTINATION_EXISTS:{destination}")
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
