"""Run synthetic documents through the live Document Agent and check them against ground truth (T5).

Live gateway calls are recorded in data/llm_cache/, so re-running costs nothing.
Usage: cd backend && uv run python scripts/extract_live.py [FILE ...] [--record] [--scenario b]
       FILE is a name in data/synthetic/ (or its scenario_b/ subdirectory) or a path;
       no FILE means that scenario's demo documents.
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
SEARCH_DIRS = [SYNTHETIC, SYNTHETIC / "scenario_b"]
DOCS = ["supplier_a_apex.pdf", "supplier_b_borealis.xlsx", "supplier_c_cobalt.eml.txt"]
DOCS_B = ["supplier_d_delta.xlsx", "supplier_e_eiger.pdf", "supplier_f_fjord.eml.txt", "supplier_g_granite.pdf"]
CHECKED = (*CRITICAL_FIELDS, "llm_stated_total", "supplier_id")


def locate(name: str) -> Path:
    """A path as given, else the first match in data/synthetic/ or data/synthetic/scenario_b/."""
    if Path(name).exists():
        return Path(name)
    return next((d / name for d in SEARCH_DIRS if (d / name).exists()), SYNTHETIC / name)


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
    scenario = argv[argv.index("--scenario") + 1] if "--scenario" in argv else "a"
    names = [a for a in argv if not a.startswith("-") and a not in ("a", "b")] or (DOCS_B if scenario == "b" else DOCS)
    settings = Settings(MODE="live", LLM_CACHE_MODE="record") if "--record" in argv else Settings(MODE="live")
    agent = LiveDocumentAgent(build_llm_client(settings))

    failures: list[str] = []
    for name in names:
        path = locate(name)
        quote = agent.extract(as_document(path))
        print(f"\n=== {path.name} ===")
        print(quote.model_dump_json(indent=2, exclude={"raw_excerpt"}))
        for problem in compare(quote, path.parent / f"{path.name}.expected.json"):
            failures.append(f"{path.name} {problem}")

    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    if not failures:
        print(f"\n{len(names)} document(s) extracted; critical fields and stated totals match ground truth")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
