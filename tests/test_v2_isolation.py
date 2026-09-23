from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Runtime/config source must never make these Legacy locations active dependencies.
FORBIDDEN_RUNTIME_REFERENCES = (
    "/workspace/skatai/",
    "/workspace/skatai\\",
    "github.com/foaryon/Skat",
    "foaryon/Skat.git",
)

TEXT_SUFFIXES = {".py", ".toml", ".yaml", ".yml", ".json", ".sh"}


def _runtime_files():
    for base in (ROOT / "src", ROOT / "configs", ROOT / "scripts"):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                yield path


def test_v2_runtime_has_no_direct_v1_dependency_strings():
    violations = []
    for path in _runtime_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for forbidden in FORBIDDEN_RUNTIME_REFERENCES:
            if forbidden in text:
                violations.append(
                    f"{path.relative_to(ROOT)} contains forbidden runtime reference {forbidden!r}"
                )
    assert not violations, "\n".join(violations)


def test_v2_runtime_packages_resolve_under_clean_v2_tree():
    import skatai

    resolved = Path(skatai.__file__).resolve()
    assert ROOT / "src" in resolved.parents
    assert "/workspace/skatai/" not in str(resolved)
