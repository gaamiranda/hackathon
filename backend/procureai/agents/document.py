"""LLM-backed Document Agent (PLAN.md §3, D18: exactly one gateway call per document).

Extraction only: the model reads the document and reports what it says. Totals, eligibility and
ranking are the engine's job (G1), and instructions found inside the document are ignored (G4).
A document the model cannot read comes back as a zero-confidence quote, which routes the run to
NEEDS_HUMAN_EXTRACTION instead of failing it (G6).
"""

import json
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import ValidationError

from procureai.agents.base import CRITICAL_FIELDS
from procureai.agents.prompts.document import EXPECTED_KEYS, SYSTEM, build_user_message
from procureai.domain.models import NormalizedQuote, RawDocument
from procureai.llm.base import LLMClient

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
SUPPLIER_ALIASES = DATA_DIR / "supplier_aliases.json"

TASK = "extract"
MAX_TOKENS = 600
MAX_TEXT_CHARS = 5000
EXCERPT_CHARS = 300

UNKNOWN_CURRENCY = "XXX"  # ISO-4217 "no currency": honest placeholder, never a fabricated USD


def slugify(name: str) -> str:
    """"Apex Components Ltd." → "apex-components-ltd". Deterministic; no LLM involved."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.strip().lower())).strip("-")


def load_aliases(path: Path = SUPPLIER_ALIASES) -> dict[str, str]:
    return json.loads(path.read_text())["aliases"]


class LiveDocumentAgent:
    """One document in, one NormalizedQuote out, one LLM call in between."""

    def __init__(
        self,
        llm: LLMClient,
        *,
        aliases: dict[str, str] | None = None,
        max_text_chars: int = MAX_TEXT_CHARS,
    ) -> None:
        self.llm = llm
        self.aliases = load_aliases() if aliases is None else aliases
        self.max_text_chars = max_text_chars

    def extract(self, doc: RawDocument) -> NormalizedQuote:
        text = doc.text
        if len(text) > self.max_text_chars:
            log.warning(
                "%s: text truncated from %d to %d chars before extraction", doc.filename, len(text), self.max_text_chars
            )
            text = text[: self.max_text_chars]

        result = self.llm.complete(
            TASK, SYSTEM, build_user_message(text), max_tokens=MAX_TOKENS, json_mode=True
        )
        if result.parsed_json is None:
            log.warning("%s: model returned no JSON; escalating to human extraction", doc.filename)
            return self._unreadable(doc, text)

        try:
            return self._to_quote(doc, text, result.parsed_json)
        except (ValidationError, InvalidOperation, ValueError, TypeError) as exc:
            log.warning("%s: extraction did not validate (%s); escalating to human extraction", doc.filename, exc)
            return self._unreadable(doc, text)

    # ---------------------------------------------------------------- mapping

    def _to_quote(self, doc: RawDocument, text: str, payload: dict) -> NormalizedQuote:
        fields = {k: payload.get(k) for k in EXPECTED_KEYS}
        confidence = payload.get("confidence") or {}
        supplier_name = str(fields["supplier_name"] or "").strip() or doc.filename
        reference = str(fields["quote_reference"] or "").strip()

        return NormalizedQuote(
            quote_id=reference or f"{doc.doc_id}-q",
            doc_id=doc.doc_id,
            supplier_id=self.supplier_id_for(supplier_name),
            supplier_name=supplier_name,
            source=doc.source,
            unit_price=_decimal(fields["unit_price"]),
            currency=str(fields["currency"] or UNKNOWN_CURRENCY).strip().upper(),
            quantity_quoted=_int(fields["quantity_quoted"]),
            moq=_int(fields["moq"]),
            lead_time_days=_int(fields["lead_time_days"]),
            payment_terms=_text(fields["payment_terms"]),
            shipping_cost=_decimal(fields["shipping_cost"]),
            discount_pct=_decimal(fields["discount_pct"]),
            validity_date=_iso_date(fields["validity_date"]),
            capacity_units=_optional_int(fields["capacity_units"]),
            llm_stated_total=_optional_decimal(fields["llm_stated_total"]),
            field_confidence=_confidence(confidence),
            raw_excerpt=text[:EXCERPT_CHARS],
        )

    def supplier_id_for(self, supplier_name: str) -> str:
        """Alias table first, slug otherwise. An unmapped supplier has no profile and so is
        ineligible (D13) — better than guessing which known supplier was meant."""
        slug = slugify(supplier_name)
        return self.aliases.get(slug, slug or "unknown")

    def _unreadable(self, doc: RawDocument, text: str) -> NormalizedQuote:
        """Valid-but-empty quote with zero confidence on every critical field, so the orchestrator
        stops at NEEDS_HUMAN_EXTRACTION and a human fills the form (G6)."""
        return NormalizedQuote(
            quote_id=f"{doc.doc_id}-q",
            doc_id=doc.doc_id,
            supplier_id=f"unknown-{doc.doc_id}",
            supplier_name=f"Unreadable document ({doc.filename})",
            source=doc.source,
            unit_price=Decimal("0"),
            currency=UNKNOWN_CURRENCY,
            quantity_quoted=1,  # the contract requires > 0; confidence 0 marks it as unknown
            moq=0,
            lead_time_days=0,
            field_confidence={f: 0.0 for f in CRITICAL_FIELDS},
            raw_excerpt=text[:EXCERPT_CHARS],
        )


# ---------------------------------------------------------------------- coercion
# The model returns JSON numbers; Decimal(str(x)) keeps "11.2" out of binary-float territory.
# It sometimes returns what the page printed instead ("USD 22,040.00"), so digits are salvaged
# rather than failing the whole document over formatting.

NUMERIC_NOISE = re.compile(r"[^0-9.eE+-]")


def _decimal(value: object) -> Decimal:
    return Decimal("0") if value is None else _optional_decimal(value)


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    cleaned = NUMERIC_NOISE.sub("", str(value))
    if not cleaned:
        raise InvalidOperation(f"no number in {value!r}")
    return Decimal(cleaned)


def _int(value: object) -> int:
    return 0 if value is None else int(_optional_decimal(value))


def _optional_int(value: object) -> int | None:
    return None if value is None else int(_optional_decimal(value))


def _iso_date(value: object) -> date | None:
    """A malformed date must not cost us the whole extraction: it is not a critical field."""
    text = _text(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        log.warning("dropping unparseable validity_date %r", text)
        return None


def _text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _confidence(raw: object) -> dict[str, float]:
    """One 0–1 float per extraction field; anything the model omitted counts as 0 (G6)."""
    values = raw if isinstance(raw, dict) else {}
    out: dict[str, float] = {}
    for key in EXPECTED_KEYS:
        try:
            out[key] = min(1.0, max(0.0, float(values.get(key, 0.0))))
        except (TypeError, ValueError):
            out[key] = 0.0
    return out
