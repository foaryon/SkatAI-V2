from __future__ import annotations

import io
from pathlib import Path
import sys
import tarfile
from types import ModuleType

import pytest

from skatai.artifacts.release import ReleasePackageError
import skatai.runtime.release_loader as release_loader
from skatai.runtime.release_loader import _extract_frozen_source, _verify_frozen_source


@pytest.mark.parametrize(
    ("release_id", "source_commit", "accepted"),
    [
        ("V2-B0-package-v1", "a" * 40, True),
        ("V2-B0-package-v2", "a" * 40, True),
        ("V2-B0-package-v3", "a" * 40, True),
        ("V2-B0-package-v4-" + "a" * 40, "a" * 40, True),
        ("V2-B0-package-v4-" + "a" * 40, "b" * 40, False),
        ("V2-B0-package-v4-" + "a" * 39, "a" * 39, False),
        ("V2-B0-package-v4-" + "A" * 40, "A" * 40, False),
        ("V2-B0-package-v4-" + "g" * 40, "g" * 40, False),
        ("V2-B0-package-v4-" + "a" * 40 + "-extra", "a" * 40, False),
        ("V2-B0-package-v5-" + "a" * 40, "a" * 40, False),
    ],
)
def test_loader_accepts_only_supported_source_bound_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    release_id: str, source_commit: str, accepted: bool,
):
    manifest = {
        "release_id": release_id,
        "source_commit": source_commit,
        "release_status": "BASELINE_PACKAGE_STAGED",
    }
    monkeypatch.setattr(release_loader, "validate_release_package", lambda _: {"manifest": manifest})
    monkeypatch.setattr(release_loader, "_verify_materialized", lambda *_: {})
    sentinel = object()
    backend = ModuleType("skatai.runtime.skatzero_backend")
    backend.build_b0_skat_ai = lambda *_: sentinel
    monkeypatch.setitem(sys.modules, backend.__name__, backend)
    if accepted:
        assert release_loader.load_model(
            tmp_path / "package.skatmodel", materialize_to=tmp_path,
            python_executable=Path(__file__),
        ) is sentinel
    else:
        with pytest.raises(ReleasePackageError, match="UNSUPPORTED_RELEASE_IDENTITY"):
            release_loader.load_model(
                tmp_path / "package.skatmodel", materialize_to=tmp_path,
                python_executable=Path(__file__),
            )


@pytest.mark.parametrize("name", ["../escape.py", "/absolute.py", "a/../escape.py"])
def test_frozen_source_rejects_path_escape(tmp_path: Path, name: str):
    archive = tmp_path / "source.tar"
    payload = b"unsafe"
    with tarfile.open(archive, "w") as tf:
        member = tarfile.TarInfo(name)
        member.size = len(payload)
        tf.addfile(member, io.BytesIO(payload))

    with pytest.raises(ReleasePackageError, match="UNSAFE_FROZEN_SOURCE_MEMBER"):
        _extract_frozen_source(archive, tmp_path / "extracted")
    assert not (tmp_path / "escape.py").exists()


def test_frozen_source_rejects_links(tmp_path: Path):
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w") as tf:
        member = tarfile.TarInfo("api.py")
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc/passwd"
        tf.addfile(member)

    with pytest.raises(ReleasePackageError, match="UNSAFE_FROZEN_SOURCE_MEMBER"):
        _extract_frozen_source(archive, tmp_path / "extracted")


def test_frozen_source_extracts_regular_file(tmp_path: Path):
    archive = tmp_path / "source.tar"
    payload = b"print('ok')\n"
    with tarfile.open(archive, "w") as tf:
        member = tarfile.TarInfo("api.py")
        member.size = len(payload)
        tf.addfile(member, io.BytesIO(payload))

    _extract_frozen_source(archive, tmp_path / "extracted")
    assert (tmp_path / "extracted/api.py").read_bytes() == payload


def test_reused_frozen_source_checks_auxiliary_files(tmp_path: Path):
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w") as tf:
        for name, payload in (("api.py", b"import helper\n"), ("helper.py", b"value = 1\n")):
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            tf.addfile(member, io.BytesIO(payload))
    extracted = tmp_path / "extracted"
    _extract_frozen_source(archive, extracted)
    _verify_frozen_source(archive, extracted)
    (extracted / "helper.py").write_bytes(b"value = 2\n")
    with pytest.raises(ReleasePackageError, match="B0_RUNTIME_SOURCE_MISMATCH:helper.py"):
        _verify_frozen_source(archive, extracted)


def test_reused_frozen_source_rejects_unexpected_module(tmp_path: Path):
    archive = tmp_path / "source.tar"
    payload = b"print('ok')\n"
    with tarfile.open(archive, "w") as tf:
        member = tarfile.TarInfo("api.py")
        member.size = len(payload)
        tf.addfile(member, io.BytesIO(payload))
    extracted = tmp_path / "extracted"
    _extract_frozen_source(archive, extracted)
    (extracted / "random.py").write_text("unexpected = True\n")
    with pytest.raises(ReleasePackageError, match="B0_RUNTIME_SOURCE_UNEXPECTED:random.py"):
        _verify_frozen_source(archive, extracted)
