"""Synthetic quote generator: files exist, ground truth validates, text stays small."""

import sys
from pathlib import Path

import pytest

from procureai.domain.models import NormalizedQuote

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))
from generate_synthetic_quotes import (  # noqa: E402
    INJECTION_LINE,
    MAX_TEXT_CHARS,
    generate,
    pdf_text,
    xlsx_text,
)

DOCS = ["supplier_a_apex.pdf", "supplier_b_borealis.xlsx", "supplier_c_cobalt.eml.txt"]


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("synthetic")
    generate(d)
    generate(d)  # idempotent: second run overwrites cleanly
    return d


def _text(out_dir: Path, name: str) -> str:
    path = out_dir / name
    if name.endswith(".pdf"):
        return pdf_text(path)
    if name.endswith(".xlsx"):
        return xlsx_text(path)
    return path.read_text()


def test_generator_writes_six_files_and_readme(out_dir):
    for name in DOCS:
        assert (out_dir / name).exists()
        assert (out_dir / f"{name}.expected.json").exists()
    assert (out_dir / "README.md").exists()


@pytest.mark.parametrize("name", DOCS)
def test_expected_json_is_a_normalized_quote(out_dir, name):
    quote = NormalizedQuote.model_validate_json((out_dir / f"{name}.expected.json").read_text())
    assert quote.quantity_quoted == 2000
    assert quote.currency == "USD"


def test_pdf_shows_wrong_grand_total(out_dir):
    text = pdf_text(out_dir / "supplier_a_apex.pdf")
    assert "22,040.00" in text
    assert "22,400.00" in text  # line total is still correct; only the grand total is wrong


def test_email_contains_injection_line(out_dir):
    assert INJECTION_LINE in (out_dir / "supplier_c_cobalt.eml.txt").read_text()


@pytest.mark.parametrize("name", DOCS)
def test_document_text_is_under_limit(out_dir, name):
    assert len(_text(out_dir, name)) < MAX_TEXT_CHARS


def test_committed_expected_json_matches_generator(out_dir):
    committed = BACKEND.parent / "data" / "synthetic"
    for name in DOCS:
        fn = f"{name}.expected.json"
        assert (committed / fn).read_text() == (out_dir / fn).read_text(), f"{fn} stale: rerun generator"
