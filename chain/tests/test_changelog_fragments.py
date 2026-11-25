#!/usr/bin/env python3
"""scripts/changelog.py: entry files are validated, and build folds them under [Unreleased] and deletes them."""
import importlib.util
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "changelog.py"
_spec = importlib.util.spec_from_file_location("changelog_script", SCRIPT)
changelog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(changelog)

BASE = """# Changelog

## [Unreleased]

### Added
- Older added entry.

### Fixed
- Older fix.

## [0.1.0] - 2026-01-01

### Added
- First release.
"""


def make(root: Path, text: str = BASE) -> Path:
    (root / "changes").mkdir(parents=True)
    (root / "CHANGELOG.md").write_text(text, encoding="utf-8")
    (root / "changes" / "README.md").write_text("How to add an entry.\n", encoding="utf-8")
    return root


def entry(root: Path, name: str, text: str) -> None:
    (root / "changes" / name).write_text(text, encoding="utf-8")


def test_build_folds_entries_first_and_deletes_files(tmp: Path) -> None:
    root = make(tmp / "a")
    entry(root, "9.fixed.md", "- Fix nine (#9).\n")
    entry(root, "10.fixed.md", "- Fix ten (#10).\n")
    entry(root, "pr-chain-report.added.md", "- New report.\n  Second line.\n")
    assert changelog.build(root) == 3
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert text.index("- New report.") < text.index("- Older added entry.")
    assert text.index("- Fix ten (#10).") < text.index("- Fix nine (#9).") < text.index("- Older fix.")
    assert "  Second line." in text
    assert text.index("## [0.1.0]") > text.index("- Fix ten (#10).")
    assert list((root / "changes").glob("*.md")) == [root / "changes" / "README.md"]


def test_check_refuses_bad_names_and_bodies(tmp: Path) -> None:
    root = make(tmp / "b")
    entry(root, "bogus.md", "- x\n")
    entry(root, "1.added.md", "plain text, no bullet\n")
    found = changelog.problems(root)
    assert any("bogus.md" in line for line in found), found
    assert any("1.added.md" in line and "bullet" in line for line in found), found


def test_check_passes_on_valid_entries(tmp: Path) -> None:
    root = make(tmp / "c")
    entry(root, "tighten-defaults.changed.md", "- A change.\n")
    assert changelog.problems(root) == []


def test_fold_adds_missing_section_in_order() -> None:
    out = changelog.fold(BASE, {"security": ["- Keys stay out of logs."]})
    assert out.index("### Security") > out.index("### Fixed"), out
    assert "- Keys stay out of logs." in out


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        test_build_folds_entries_first_and_deletes_files(base / "1")
        test_check_refuses_bad_names_and_bodies(base / "2")
        test_check_passes_on_valid_entries(base / "3")
    test_fold_adds_missing_section_in_order()
    print("all changelog fragment checks passed")
    sys.exit(0)
