"""Synthetic quote generator: files exist, ground truth validates, text stays small, output is stable."""

import sys
from pathlib import Path

import pytest

from procureai.domain.models import NormalizedQuote

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))
from generate_synthetic_quotes import (  # noqa: E402
    DOCS_A,
    DOCS_B,
    INJECTION_LINE,
    INJECTION_LINE_B,
    MAX_TEXT_CHARS,
    SCENARIOS,
    document_text,
    generate,
    pdf_text,
)

COMMITTED = BACKEND.parent / "data" / "synthetic"
QUANTITY = {"a": 2000, "b": 10000}
ALL_DOCS = [("a", name) for name in DOCS_A] + [("b", name) for name in DOCS_B]


@pytest.fixture(scope="module")
def out_dirs(tmp_path_factory) -> dict[str, Path]:
    """Both scenarios generated twice into a temp tree: the second run must overwrite cleanly."""
    root = tmp_path_factory.mktemp("synthetic")
    dirs = {"a": root, "b": root / "scenario_b"}
    for scenario, directory in dirs.items():
        generate(directory, scenario)
        generate(directory, scenario)
    return dirs


def _path(out_dirs: dict[str, Path], scenario: str, name: str) -> Path:
    return out_dirs[scenario] / name


def test_generator_writes_every_document_with_ground_truth_and_a_readme(out_dirs):
    for scenario, name in ALL_DOCS:
        assert _path(out_dirs, scenario, name).exists()
        assert _path(out_dirs, scenario, f"{name}.expected.json").exists()
    for directory in out_dirs.values():
        assert (directory / "README.md").exists()


@pytest.mark.parametrize(("scenario", "name"), ALL_DOCS)
def test_expected_json_is_a_normalized_quote(out_dirs, scenario, name):
    quote = NormalizedQuote.model_validate_json(_path(out_dirs, scenario, f"{name}.expected.json").read_text())
    assert quote.quantity_quoted == QUANTITY[scenario]
    assert quote.currency == "USD"


def test_pdf_shows_wrong_grand_total(out_dirs):
    text = pdf_text(_path(out_dirs, "a", "supplier_a_apex.pdf"))
    assert "22,040.00" in text
    assert "22,400.00" in text  # line total is still correct; only the grand total is wrong


def test_scenario_b_pdf_shows_the_transposed_total(out_dirs):
    text = pdf_text(_path(out_dirs, "b", "supplier_g_granite.pdf"))
    assert "8,560.00" in text  # 0.84 × 10,000 + 250.00 is 8,650.00
    assert "8,650.00" not in text


def test_email_contains_injection_line(out_dirs):
    assert INJECTION_LINE in _path(out_dirs, "a", "supplier_c_cobalt.eml.txt").read_text()


def test_scenario_b_email_hides_the_injection_in_a_postscript(out_dirs):
    text = _path(out_dirs, "b", "supplier_f_fjord.eml.txt").read_text()
    assert text.rstrip().endswith(INJECTION_LINE_B)
    assert "SYSTEM NOTE" not in text


def test_scenario_b_workbook_has_two_sheets(out_dirs):
    """Delta's commercial terms live on a second sheet, away from the prices."""
    text = document_text(_path(out_dirs, "b", "supplier_d_delta.xlsx"))
    assert "## Offer" in text and "## Terms" in text
    assert "Lead time (days): 12" in text


def test_scenario_b_letter_has_no_table(out_dirs):
    """Eiger's figures sit inside sentences; nothing to align on."""
    text = pdf_text(_path(out_dirs, "b", "supplier_e_eiger.pdf"))
    assert "unit price of USD 0.83" in text
    assert "Unit Price" not in text and "|" not in text


@pytest.mark.parametrize(("scenario", "name"), ALL_DOCS)
def test_document_text_is_under_limit(out_dirs, scenario, name):
    assert len(document_text(_path(out_dirs, scenario, name))) < MAX_TEXT_CHARS


@pytest.mark.parametrize(("scenario", "name"), ALL_DOCS)
def test_committed_files_match_the_generator(out_dirs, scenario, name):
    """Documents and ground truth are byte-reproducible, so `just regen` never dirties the tree."""
    committed = SCENARIOS[scenario]["dir"]
    assert committed.is_relative_to(COMMITTED)
    for filename in (name, f"{name}.expected.json"):
        assert (committed / filename).read_bytes() == _path(out_dirs, scenario, filename).read_bytes(), (
            f"{filename} stale: rerun `just regen`"
        )
