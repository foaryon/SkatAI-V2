from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
from typing import Any, Mapping, Sequence

RELEASE_SCHEMA = "skatai.v2.release-package.v1"
MANIFEST_PATH = "manifest.json"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024

REQUIRED_RELEASE_FIELDS = (
    "release_id",
    "source_commit",
    "parent_lineage",
    "model_hashes",
    "bidding_identity",
    "cardplay_identity",
    "belief_value_identity",
    "search_config",
    "rule_engine_identity",
    "runtime_version",
    "deployment_config",
    "acceptance_evidence",
    "benchmark_identity",
)


class ReleasePackageError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseComponent:
    source: Path
    archive_path: str
    kind: str


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb", buffering=1024 * 1024) as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(value),
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _validated_archive_path(raw: str) -> str:
    value = str(raw)
    if not value or "\\" in value or value.startswith("/"):
        raise ReleasePackageError(f"UNSAFE_ARCHIVE_PATH:{value!r}")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ReleasePackageError(f"UNSAFE_ARCHIVE_PATH:{value!r}")
    normalized = path.as_posix()
    if normalized != value:
        raise ReleasePackageError(f"NONCANONICAL_ARCHIVE_PATH:{value!r}")
    return normalized


def _validate_release_metadata(release: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(release)
    missing = [field for field in REQUIRED_RELEASE_FIELDS if field not in payload]
    if missing:
        raise ReleasePackageError(f"RELEASE_METADATA_MISSING:{','.join(missing)}")

    release_id = str(payload["release_id"]).strip()
    source_commit = str(payload["source_commit"]).strip().lower()
    if not release_id:
        raise ReleasePackageError("EMPTY_RELEASE_ID")
    if len(source_commit) != 40 or any(c not in "0123456789abcdef" for c in source_commit):
        raise ReleasePackageError("BAD_SOURCE_COMMIT")
    payload["release_id"] = release_id
    payload["source_commit"] = source_commit

    if not isinstance(payload["parent_lineage"], (dict, list)):
        raise ReleasePackageError("BAD_PARENT_LINEAGE")
    if not isinstance(payload["model_hashes"], dict):
        raise ReleasePackageError("BAD_MODEL_HASHES")
    if not isinstance(payload["acceptance_evidence"], list):
        raise ReleasePackageError("BAD_ACCEPTANCE_EVIDENCE")
    if not isinstance(payload["benchmark_identity"], (dict, list, str)):
        raise ReleasePackageError("BAD_BENCHMARK_IDENTITY")
    return payload


def _tarinfo(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.size = int(size)
    info.mode = 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    return info


def build_release_package(
    output: Path,
    *,
    release: Mapping[str, Any],
    components: Sequence[ReleaseComponent],
) -> dict[str, Any]:
    """Build a byte-reproducible, content-addressed SkatAI release package."""

    output = Path(output)
    if "schema" in release or "components" in release:
        raise ReleasePackageError("RELEASE_METADATA_CONTAINS_RESERVED_FIELD")
    metadata = _validate_release_metadata(release)
    if not components:
        raise ReleasePackageError("RELEASE_REQUIRES_COMPONENTS")

    component_rows: list[dict[str, Any]] = []
    by_archive_path: dict[str, ReleaseComponent] = {}
    for component in components:
        source = Path(component.source)
        archive_path = _validated_archive_path(component.archive_path)
        kind = str(component.kind).strip()
        if archive_path == MANIFEST_PATH:
            raise ReleasePackageError("COMPONENT_CANNOT_REPLACE_MANIFEST")
        if archive_path in by_archive_path:
            raise ReleasePackageError(f"DUPLICATE_COMPONENT_PATH:{archive_path}")
        if not kind:
            raise ReleasePackageError(f"EMPTY_COMPONENT_KIND:{archive_path}")
        if not source.is_file():
            raise ReleasePackageError(f"COMPONENT_NOT_FILE:{source}")
        by_archive_path[archive_path] = ReleaseComponent(source, archive_path, kind)
        component_rows.append(
            {
                "path": archive_path,
                "kind": kind,
                "bytes": source.stat().st_size,
                "sha256": sha256_file(source),
            }
        )

    component_rows.sort(key=lambda row: row["path"])
    manifest = {
        "schema": RELEASE_SCHEMA,
        **metadata,
        "components": component_rows,
    }
    manifest_bytes = _canonical_json_bytes(manifest)

    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    if tmp.exists():
        raise ReleasePackageError(f"TEMP_OUTPUT_ALREADY_EXISTS:{tmp}")

    try:
        with tarfile.open(tmp, mode="w", format=tarfile.USTAR_FORMAT) as tf:
            tf.addfile(_tarinfo(MANIFEST_PATH, len(manifest_bytes)), io.BytesIO(manifest_bytes))
            for row in component_rows:
                component = by_archive_path[row["path"]]
                with component.source.open("rb") as f:
                    tf.addfile(_tarinfo(row["path"], row["bytes"]), f)
        os.chmod(tmp, 0o644)
        # Validate the complete staged archive before publishing any release path.
        result = validate_release_package(tmp)
        with tmp.open("rb") as staged:
            os.fsync(staged.fileno())
        try:
            os.link(tmp, output)
        except FileExistsError as exc:
            raise ReleasePackageError(f"OUTPUT_EXISTS:{output}") from exc
        directory_fd = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if tmp.exists():
            tmp.unlink()

    return {
        "schema": RELEASE_SCHEMA,
        "release_id": manifest["release_id"],
        "package_path": str(output),
        "package_sha256": result["package_sha256"],
        "package_bytes": result["package_bytes"],
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "components": component_rows,
    }


def _read_member_bytes(tf: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    if member.size > MAX_MANIFEST_BYTES and member.name == MANIFEST_PATH:
        raise ReleasePackageError("MANIFEST_TOO_LARGE")
    f = tf.extractfile(member)
    if f is None:
        raise ReleasePackageError(f"MEMBER_NOT_READABLE:{member.name}")
    return f.read()


def validate_release_package(package: Path) -> dict[str, Any]:
    package = Path(package)
    if not package.is_file():
        raise ReleasePackageError(f"PACKAGE_NOT_FILE:{package}")

    try:
        tf = tarfile.open(package, mode="r:")
    except tarfile.TarError as exc:
        raise ReleasePackageError("INVALID_RELEASE_TAR") from exc

    with tf:
        members = tf.getmembers()
        if not members:
            raise ReleasePackageError("EMPTY_RELEASE_PACKAGE")

        by_name: dict[str, tarfile.TarInfo] = {}
        for member in members:
            name = _validated_archive_path(member.name)
            if name in by_name:
                raise ReleasePackageError(f"DUPLICATE_ARCHIVE_MEMBER:{name}")
            if not member.isfile():
                raise ReleasePackageError(f"NONFILE_ARCHIVE_MEMBER:{name}")
            by_name[name] = member

        manifest_member = by_name.get(MANIFEST_PATH)
        if manifest_member is None:
            raise ReleasePackageError("RELEASE_MANIFEST_MISSING")
        try:
            manifest = json.loads(_read_member_bytes(tf, manifest_member).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReleasePackageError("BAD_RELEASE_MANIFEST_JSON") from exc
        if not isinstance(manifest, dict) or manifest.get("schema") != RELEASE_SCHEMA:
            raise ReleasePackageError("RELEASE_SCHEMA_MISMATCH")
        _validate_release_metadata(manifest)

        component_rows = manifest.get("components")
        if not isinstance(component_rows, list) or not component_rows:
            raise ReleasePackageError("BAD_RELEASE_COMPONENTS")

        expected_names = {MANIFEST_PATH}
        seen_components: set[str] = set()
        for row in component_rows:
            if not isinstance(row, dict):
                raise ReleasePackageError("BAD_COMPONENT_ROW")
            name = _validated_archive_path(str(row.get("path") or ""))
            if name in seen_components or name == MANIFEST_PATH:
                raise ReleasePackageError(f"DUPLICATE_COMPONENT_PATH:{name}")
            seen_components.add(name)
            expected_names.add(name)
            member = by_name.get(name)
            if member is None:
                raise ReleasePackageError(f"COMPONENT_MISSING:{name}")
            try:
                expected_bytes = int(row["bytes"])
                expected_sha = str(row["sha256"]).lower()
            except (KeyError, TypeError, ValueError) as exc:
                raise ReleasePackageError(f"BAD_COMPONENT_METADATA:{name}") from exc
            if member.size != expected_bytes:
                raise ReleasePackageError(f"COMPONENT_SIZE_MISMATCH:{name}")
            h = hashlib.sha256()
            f = tf.extractfile(member)
            if f is None:
                raise ReleasePackageError(f"COMPONENT_NOT_READABLE:{name}")
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
            if h.hexdigest() != expected_sha:
                raise ReleasePackageError(f"COMPONENT_HASH_MISMATCH:{name}")

        extra = sorted(set(by_name) - expected_names)
        missing = sorted(expected_names - set(by_name))
        if extra:
            raise ReleasePackageError(f"UNDECLARED_ARCHIVE_MEMBER:{extra[0]}")
        if missing:
            raise ReleasePackageError(f"DECLARED_ARCHIVE_MEMBER_MISSING:{missing[0]}")

    return {
        "schema": RELEASE_SCHEMA,
        "release_id": str(manifest["release_id"]),
        "manifest": manifest,
        "package_sha256": sha256_file(package),
        "package_bytes": package.stat().st_size,
    }


def materialize_release_package(package: Path, destination: Path) -> dict[str, Any]:
    """Validate then atomically materialize a release without tar path traversal."""

    package = Path(package)
    destination = Path(destination)
    validation = validate_release_package(package)
    if destination.exists():
        raise ReleasePackageError(f"DESTINATION_EXISTS:{destination}")

    tmp = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    if tmp.exists():
        raise ReleasePackageError(f"TEMP_DESTINATION_ALREADY_EXISTS:{tmp}")
    tmp.mkdir(parents=True)

    try:
        with tarfile.open(package, mode="r:") as tf:
            manifest = validation["manifest"]
            for row in manifest["components"]:
                name = _validated_archive_path(row["path"])
                member = tf.getmember(name)
                target = tmp / PurePosixPath(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tf.extractfile(member)
                if source is None:
                    raise ReleasePackageError(f"COMPONENT_NOT_READABLE:{name}")
                with target.open("wb") as out:
                    shutil.copyfileobj(source, out, length=1024 * 1024)
                os.chmod(target, 0o644)
                if (
                    target.stat().st_size != int(row["bytes"])
                    or sha256_file(target) != str(row["sha256"]).lower()
                ):
                    raise ReleasePackageError(f"MATERIALIZED_COMPONENT_MISMATCH:{name}")

        manifest_path = tmp / MANIFEST_PATH
        manifest_path.write_bytes(_canonical_json_bytes(validation["manifest"]))
        os.chmod(manifest_path, 0o644)
        os.replace(tmp, destination)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)

    return {
        "schema": RELEASE_SCHEMA,
        "release_id": validation["release_id"],
        "destination": str(destination),
        "package_sha256": validation["package_sha256"],
    }
