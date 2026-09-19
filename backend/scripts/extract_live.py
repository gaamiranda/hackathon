"""Run synthetic documents through the live Document Agent and check them against ground truth (T5).

Live gateway calls are recorded in data/llm_cache/, so re-running costs nothing.
Usage: cd backend && uv run python scripts/extract_live.py [FILE ...] [--record]
       FILE is a name in data/synthetic/ or a path; no FILE means all three demo documents.
"""

import sys
from pathlib import Path

from procureai.agents.base import CRITICAL_FIELDS
from procureai.agents.document import LiveDocumentAgent
from procureai.api.extract_text import to_text
from procureai.config.settings import Settings
from procureai.domain.models import NormalizedQuote, RawDocument
from procureai.llm.factory import build_llm_client

ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC = ROOT / "data" / "synthetic"
DOCS = ["supplier_a_apex.pdf", "supplier_b_borealis.xlsx", "supplier_c_cobalt.eml.txt"]
CHECKED = (*CRITICAL_FIELDS, "llm_stated_total", "supplier_id")


def as_document(path: Path) -> RawDocument:
    source, text = to_text(path.name, path.read_bytes())
    return RawDocument(doc_id=f"doc_{path.stem}", filename=path.name, source=source, text=text)


def compare(quote: NormalizedQuote, expected_path: Path) -> list[str]:
    if not expected_path.exists():
        return []
    expected = NormalizedQuote.model_validate_json(expected_path.read_text())
    return [
        f"{field}: got {getattr(quote, field)!r}, expected {getattr(expected, field)!r}"
        for field in CHECKED
        if getattr(quote, field) != getattr(expected, field)
    ]


def main(argv: list[str]) -> int:
    names = [a for a in argv if not a.startswith("-")] or DOCS
    settings = Settings(MODE="live", LLM_CACHE_MODE="record") if "--record" in argv else Settings(MODE="live")
    agent = LiveDocumentAgent(build_llm_client(settings))

    failures: list[str] = []
    for name in names:
        path = Path(name) if Path(name).exists() else SYNTHETIC / name
        quote = agent.extract(as_document(path))
        print(f"\n=== {path.name} ===")
        print(quote.model_dump_json(indent=2, exclude={"raw_excerpt"}))
        for problem in compare(quote, SYNTHETIC / f"{path.name}.expected.json"):
            failures.append(f"{path.name} {problem}")

    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    if not failures:
        print(f"\n{len(names)} document(s) extracted; critical fields and stated totals match ground truth")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
