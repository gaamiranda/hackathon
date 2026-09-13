"""Deterministic mock agents backed by fixtures (PLAN.md D3). No LLM, no network."""

import json
from pathlib import Path

from procureai.agents.base import Explanation
from procureai.domain.models import (
    NormalizedQuote,
    ProcurementRequest,
    RawDocument,
    Scorecard,
    SupplierProfile,
    ValidatedQuote,
)

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
SYNTHETIC_DIR = DATA_DIR / "synthetic"
SUPPLIER_HISTORY = DATA_DIR / "supplier_history.json"


# Demo knob: a filename containing this token gets low confidence on these fields (→ NEEDS_HUMAN_EXTRACTION, G6).
LOWCONF_TOKEN = "lowconf"
LOWCONF_FIELDS = ("unit_price", "lead_time_days")
LOWCONF_VALUE = 0.5


class MockDocumentAgent:
    """Returns data/synthetic/<filename>.expected.json for a known filename.

    `<name>_lowconf<ext>` (e.g. supplier_c_lowconf.eml.txt) maps to the matching supplier's ground truth
    with `unit_price` and `lead_time_days` confidence lowered to 0.5, to demo the extraction gate."""

    def __init__(self, synthetic_dir: Path = SYNTHETIC_DIR) -> None:
        self.synthetic_dir = synthetic_dir

    def extract(self, doc: RawDocument) -> NormalizedQuote:
        name = Path(doc.filename).name
        lowconf = LOWCONF_TOKEN in name
        expected = self._ground_truth(name)
        if expected is None:
            known = sorted(p.name.removesuffix(".expected.json") for p in self.synthetic_dir.glob("*.expected.json"))
            raise FileNotFoundError(
                f"MockDocumentAgent has no ground truth for '{doc.filename}'. Known files: {known}"
            )
        quote = NormalizedQuote.model_validate_json(expected.read_text())
        confidence = quote.field_confidence
        if lowconf:
            confidence = {**confidence, **{f: LOWCONF_VALUE for f in LOWCONF_FIELDS}}
        return quote.model_copy(update={"doc_id": doc.doc_id, "field_confidence": confidence})

    def _ground_truth(self, name: str) -> Path | None:
        direct = self.synthetic_dir / f"{name}.expected.json"
        if direct.exists():
            return direct
        if LOWCONF_TOKEN not in name:
            return None
        # supplier_c_cobalt_lowconf.eml.txt → supplier_c_cobalt.eml.txt; supplier_c_lowconf.eml.txt → supplier_c_*.eml.txt
        stripped = self.synthetic_dir / f"{name.replace('_' + LOWCONF_TOKEN, '').replace(LOWCONF_TOKEN, '')}.expected.json"
        if stripped.exists():
            return stripped
        prefix, _, suffix = name.partition(LOWCONF_TOKEN)
        matches = sorted(self.synthetic_dir.glob(f"{prefix}*{suffix}.expected.json"))
        return matches[0] if len(matches) == 1 else None


class MockSupplierIntelAgent:
    """Reads the read-only seed data/supplier_history.json."""

    def __init__(self, history_path: Path = SUPPLIER_HISTORY) -> None:
        rows = json.loads(history_path.read_text())
        self._profiles = {p.supplier_id: p for p in (SupplierProfile.model_validate(r) for r in rows)}

    def get_profile(self, supplier_id: str) -> SupplierProfile | None:
        return self._profiles.get(supplier_id)


class MockDecisionAgent:
    """Templated, deterministic rationale built from engine output only."""

    def explain(
        self,
        request: ProcurementRequest,
        scorecards: list[Scorecard],
        validated: list[ValidatedQuote],
        diff_lines: list[str] | None = None,
    ) -> Explanation:
        names = {vq.supplier_id: vq.supplier_name for vq in validated}
        eligible = [c for c in scorecards if c.eligible]
        parts: list[str] = []

        if eligible:
            top = eligible[0]
            bd = ", ".join(f"{k} {v:.1f}" for k, v in top.score_breakdown.items())
            parts.append(
                f"Recommended: {names.get(top.supplier_id, top.supplier_id)} ({top.supplier_id}) with "
                f"{top.total_score:.1f}/100 (breakdown: {bd}). Landed cost {top.landed_cost:,.2f} "
                f"{request.currency} for {request.quantity:,} units, lead time {top.lead_time_days} days."
            )
            for c in eligible[1:]:
                parts.append(
                    f"{names.get(c.supplier_id, c.supplier_id)} scored {c.total_score:.1f} "
                    f"(landed cost {c.landed_cost:,.2f}, {c.lead_time_days} days)."
                )
        else:
            parts.append("No eligible supplier: every quote failed at least one policy check.")

        for c in scorecards:
            if not c.eligible:
                parts.append(f"{names.get(c.supplier_id, c.supplier_id)} is ineligible: {'; '.join(c.ineligibility_reasons)}.")

        change = " ".join(diff_lines) if diff_lines else None
        return Explanation(rationale=" ".join(parts), change_explanation=change)
