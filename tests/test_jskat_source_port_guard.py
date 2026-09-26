import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/port_jskat_adapter_source.py'
spec = importlib.util.spec_from_file_location('source_port_guard', SCRIPT)
port = importlib.util.module_from_spec(spec)
spec.loader.exec_module(port)


def test_known_untracked_artifact_requires_exact_hash(tmp_path, monkeypatch):
    rel = 'provenance/cutover.json'
    f = tmp_path / rel
    f.parent.mkdir()
    f.write_bytes(b'frozen')
    monkeypatch.setattr(port, 'REPO', tmp_path)
    monkeypatch.setattr(port, 'ALLOWED_PREEXISTING_UNTRACKED_SHA256',
                        {rel: hashlib.sha256(b'frozen').hexdigest()})
    monkeypatch.setattr(port, 'git', lambda *args: SimpleNamespace(stdout=f'?? {rel}\n'))
    port.assert_worktree_safe()
    f.write_bytes(b'changed')
    with pytest.raises(SystemExit, match='JSKAT_PORT_UNRELATED_DIRTY_WORKTREE'):
        port.assert_worktree_safe()


def test_target_or_new_untracked_file_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(port, 'REPO', tmp_path)
    monkeypatch.setattr(port, 'ALLOWED_PREEXISTING_UNTRACKED_SHA256', {})
    monkeypatch.setattr(port, 'git', lambda *args: SimpleNamespace(stdout='?? unexpected.txt\n'))
    with pytest.raises(SystemExit, match='unexpected.txt'):
        port.assert_worktree_safe()
