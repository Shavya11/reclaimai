"""The evidence table in the README has to stay true.

That table is the first thing a reader checks, and every row of it names a test.
A rename or a deletion turns one of those rows into a claim with nothing behind
it — silently, because prose does not fail to compile.

`recoup` does the same thing for its red-team panel and calls it out for the same
reason: "each of these is a passing test" is a claim made out loud, and this is
what stops a refactor turning it into a lie.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
TESTS = ROOT / "tests"


def _collected_test_names() -> set[str]:
    names: set[str] = set()
    for path in TESTS.glob("test_*.py"):
        for match in re.finditer(r"^def (test_[A-Za-z0-9_]+)", path.read_text(
                encoding="utf-8"), re.MULTILINE):
            names.add(match.group(1))
    return names


def _cited_names() -> list[str]:
    """Every `test_...` in a backticked cell of the evidence table."""
    text = README.read_text(encoding="utf-8")
    return re.findall(r"`(test_[A-Za-z0-9_]+)`", text)


def test_the_evidence_table_cites_tests_that_exist():
    cited = _cited_names()
    assert cited, "the evidence table cites no tests - has it been removed?"

    missing = sorted(set(cited) - _collected_test_names())
    assert not missing, (
        f"README cites {len(missing)} test(s) that do not exist: "
        f"{', '.join(missing)}. Either the test was renamed and the table is "
        f"now a claim with nothing behind it, or the row should go."
    )


def test_the_evidence_table_cites_files_that_exist():
    """The other half of a citation. A path that has moved is the same failure
    as a test that has been renamed."""
    text = README.read_text(encoding="utf-8")
    table = text.split("## Every claim, and how to check it", 1)[-1]
    table = table.split("## What is real", 1)[0]

    paths = re.findall(r"\]\((reclaim/[^)]+)\)", table)
    assert paths, "the evidence table links to no source files"

    missing = sorted(p for p in set(paths) if not (ROOT / p).exists())
    assert not missing, f"README links to missing file(s): {', '.join(missing)}"


def test_the_real_versus_modelled_table_is_present():
    """Deleting the limitations is the one edit to this README that would make
    it dishonest rather than merely stale, so it is asserted rather than
    trusted."""
    # Whitespace-normalised: the README is hard-wrapped, so a phrase can be
    # split across a line break and still read correctly to a human.
    text = " ".join(README.read_text(encoding="utf-8").split())
    assert "## What is real, and what is modelled" in text
    for phrase in ("Self-cure is modelled", "stated estimates", "RISK_DECLINE"):
        assert phrase in text, f"the limitations no longer mention {phrase!r}"
