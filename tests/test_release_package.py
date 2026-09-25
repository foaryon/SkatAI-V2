from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import skatai.artifacts.release as release_module

from skatai.artifacts.release import (
    ReleaseComponent,
    ReleasePackageError,
    build_release_package,
    materialize_release_package,
    validate_release_package,
)


def _release():
    return {
        "release_id": "SkatAI-test-v1",
        "source_commit": "a" * 40,
        "parent_lineage": {"parent": "V2-B0"},
        "model_hashes": {"model": "b" * 64},
        "bidding_identity": {"kind": "test"},
        "cardplay_identity": {"kind": "test"},
        "belief_value_identity": None,
        "search_config": None,
        "rule_engine_identity": {"sha256": "c" * 64},
        "runtime_version": {"python": "3.11"},
        "deployment_config": {"mode": "cpu"},
        "acceptance_evidence": [{"id": "baseline"}],
        "benchmark_identity": {"id": "B0"},
    }


def test_release_package_is_byte_reproducible_and_validated(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"alpha\x00payload")
    b.write_bytes(b"beta payload")

    components = [
        ReleaseComponent(b, "models/b.bin", "model"),
        ReleaseComponent(a, "source/a.bin", "source"),
    ]
    p1 = tmp_path / "one.skatmodel"
    p2 = tmp_path / "two.skatmodel"

    r1 = build_release_package(p1, release=_release(), components=components)
    r2 = build_release_package(p2, release=_release(), components=list(reversed(components)))

    assert p1.read_bytes() == p2.read_bytes()
    assert r1["package_sha256"] == r2["package_sha256"]
    checked = validate_release_package(p1)
    assert checked["release_id"] == "SkatAI-test-v1"
    assert checked["package_sha256"] == hashlib.sha256(p1.read_bytes()).hexdigest()
    assert [x["path"] for x in checked["manifest"]["components"]] == [
        "models/b.bin",
        "source/a.bin",
    ]


def test_release_package_detects_component_tampering(tmp_path):
    source = tmp_path / "payload.bin"
    source.write_bytes(b"UNIQUE-COMPONENT-PAYLOAD-12345")
    package = tmp_path / "release.skatmodel"
    build_release_package(
        package,
        release=_release(),
        components=[ReleaseComponent(source, "models/payload.bin", "model")],
    )
    raw = package.read_bytes()
    assert b"UNIQUE-COMPONENT-PAYLOAD-12345" in raw
    package.write_bytes(
        raw.replace(
            b"UNIQUE-COMPONENT-PAYLOAD-12345",
            b"UNIQUE-COMPONENT-PAYLOAD-12346",
            1,
        )
    )

    with pytest.raises(ReleasePackageError, match="COMPONENT_HASH_MISMATCH"):
        validate_release_package(package)


@pytest.mark.parametrize(
    "archive_path",
    ["/absolute.bin", "../escape.bin", "a/../escape.bin", r"a\windows.bin"],
)
def test_release_package_rejects_unsafe_component_paths(tmp_path, archive_path):
    source = tmp_path / "payload.bin"
    source.write_bytes(b"x")
    with pytest.raises(ReleasePackageError, match="ARCHIVE_PATH"):
        build_release_package(
            tmp_path / "release.skatmodel",
            release=_release(),
            components=[ReleaseComponent(source, archive_path, "model")],
        )


def test_release_package_requires_complete_release_identity(tmp_path):
    source = tmp_path / "payload.bin"
    source.write_bytes(b"x")
    release = _release()
    del release["acceptance_evidence"]

    with pytest.raises(ReleasePackageError, match="RELEASE_METADATA_MISSING"):
        build_release_package(
            tmp_path / "release.skatmodel",
            release=release,
            components=[ReleaseComponent(source, "payload.bin", "model")],
        )


def test_release_package_materialization_is_validated_and_atomic(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"alpha")
    b.write_bytes(b"beta")
    package = tmp_path / "release.skatmodel"
    build_release_package(
        package,
        release=_release(),
        components=[
            ReleaseComponent(a, "source/a.bin", "source"),
            ReleaseComponent(b, "models/b.bin", "model"),
        ],
    )

    destination = tmp_path / "materialized"
    result = materialize_release_package(package, destination)

    assert result["release_id"] == "SkatAI-test-v1"
    assert (destination / "source/a.bin").read_bytes() == b"alpha"
    assert (destination / "models/b.bin").read_bytes() == b"beta"
    assert (destination / "manifest.json").is_file()

    with pytest.raises(ReleasePackageError, match="DESTINATION_EXISTS"):
        materialize_release_package(package, destination)


def test_release_package_does_not_replace_existing_release(tmp_path):
    source = tmp_path / "model.bin"
    source.write_bytes(b"accepted model")
    output = tmp_path / "release.skatmodel"
    output.write_bytes(b"preexisting release")

    with pytest.raises(ReleasePackageError, match="OUTPUT_EXISTS"):
        build_release_package(
            output,
            release=_release(),
            components=[ReleaseComponent(source, "models/model.bin", "model")],
        )
    assert output.read_bytes() == b"preexisting release"


def test_release_package_does_not_publish_failed_validation(tmp_path, monkeypatch):
    source = tmp_path / "model.bin"
    source.write_bytes(b"accepted model")
    output = tmp_path / "release.skatmodel"
    real_hash = release_module.sha256_file
    monkeypatch.setattr(
        release_module,
        "sha256_file",
        lambda path: "0" * 64 if Path(path) == source else real_hash(path),
    )

    with pytest.raises(ReleasePackageError, match="COMPONENT_HASH_MISMATCH"):
        build_release_package(
            output,
            release=_release(),
            components=[ReleaseComponent(source, "models/model.bin", "model")],
        )
    assert not output.exists()


def test_release_package_requires_full_source_commit_and_reserved_fields(tmp_path):
    source = tmp_path / "model.bin"
    source.write_bytes(b"accepted model")
    component = ReleaseComponent(source, "models/model.bin", "model")
    short_commit = _release() | {"source_commit": "a" * 7}
    with pytest.raises(ReleasePackageError, match="BAD_SOURCE_COMMIT"):
        build_release_package(tmp_path / "short.skatmodel", release=short_commit, components=[component])

    reserved = _release() | {"components": []}
    with pytest.raises(ReleasePackageError, match="RESERVED_FIELD"):
        build_release_package(tmp_path / "reserved.skatmodel", release=reserved, components=[component])
