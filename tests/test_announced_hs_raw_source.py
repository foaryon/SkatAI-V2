import bz2
import io
from pathlib import Path
import runpy

from skatai.data.sgf import parse_sgf_line


_module = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "audit_announced_hs_raw_source.py"))
_fixtures = runpy.run_path(str(Path(__file__).parent / "test_sgf.py"))


def test_raw_reparse_maps_legacy_identity_without_changing_gameplay_fields():
    raw = _fixtures["PLAYED"]
    legacy = parse_sgf_line("legacy", raw)
    legacy["semantic_sha256"] = "0" * 64
    targets = {legacy["raw_sha256"]: (0, legacy)}
    audited = _module["audit_stream"](
        io.BytesIO(bz2.compress(raw + b"\n")), targets, 2,
    )
    assert audited["targets"] == 1
    assert audited["raw_lines_scanned"] == 1
    row = audited["found"][0]
    assert row["core_field_differences"] == []
    assert row["v2_projection_identity_equal"]
    assert row["legacy_semantic_sha256"] != row["v2_semantic_sha256"]
