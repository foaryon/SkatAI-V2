from __future__ import annotations

import io
from pathlib import Path
import tarfile

import pytest

from skatai.artifacts.release import ReleasePackageError
from skatai.runtime.release_loader import _extract_frozen_source


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
