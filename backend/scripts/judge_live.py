"""Run the guardrail judge (TypeSafe Jev) over the demo fixtures and print every probability (T14, PLAN.md D20).

Verdicts are recorded in data/judge_cache/, so tests (`replay_only`) and demos never call the API again.
Budget: JUDGE_MAX_LIVE_CALLS (40) per process; the script prints how many live calls it made and their input tokens.

Usage: cd backend && uv run python scripts/judge_live.py [--record]
       --record re-runs every case live even when a verdict is cached.
"""

import sys
from decimal import Decimal
from pathlib import Path

from procureai.agents.factory import build_agents
from procureai.agents.mock import MockNegotiationAgent
from procureai.api.extract_text import to_text
from procureai.config.settings import Settings
from procureai.domain.models import (
    NegotiationOffer,
    NegotiationThread,
    NormalizedQuote,
    ProcurementConfig,
    ProcurementRequest,
    RawDocument,
    WorkflowState as S,
)
from procureai.guardrails.jev import JevGuardrailJudge
from procureai.sim import ScriptedSupplier
from procureai.workflow import Orchestrator, RunStore

ROOT = Path(__file__).resolve().parents[2]
FIXTURES, SYNTHETIC = ROOT / "data" / "fixtures", ROOT / "data" / "synthetic"
DOCS = ["supplier_a_apex.pdf", "supplier_b_borealis.xlsx", "supplier_c_cobalt.eml.txt"]
NAMES = {"sup_a": "Apex Components Ltd", "sup_b": "Borealis Manufacturing AS", "sup_c": "Cobalt Industrial"}


def document(name: str, doc_id: str) -> tuple[RawDocument, NormalizedQuote]:
    source, text = to_text(name, (SYNTHETIC / name).read_bytes())
    quote = NormalizedQuote.model_validate_json((SYNTHETIC / f"{name}.expected.json").read_text())
    return RawDocument(doc_id=doc_id, filename=name, source=source, text=text), quote.model_copy(update={"doc_id": doc_id})


def show_extraction(label: str, verdict) -> None:
    if not verdict.evaluated:
        print(f"  {label:<44} NOT EVALUATED ({verdict.error})")
        return
    cells = "  ".join(f"{f}={v.probability:.3f}{'' if v.supported else '!'}" for f, v in verdict.fields.items())
    print(f"  {label:<44} {cells}" + (f"   → unsupported: {verdict.unsupported}" if verdict.unsupported else ""))


def show_flag(label: str, verdict, flag: str) -> None:
    if not verdict.evaluated:
        print(f"  {label:<44} NOT EVALUATED ({verdict.error})")
        return
    print(f"  {label:<44} p={verdict.probability:.3f}  {flag}={getattr(verdict, flag)}"
          + (f"  reasons={verdict.reasons}" if getattr(verdict, "reasons", None) else ""))


def main(argv: list[str]) -> int:
    settings = Settings(GUARDRAIL_JUDGE="jev", JUDGE_CACHE_MODE="record") if "--record" in argv else Settings(GUARDRAIL_JUDGE="jev")
    judge = JevGuardrailJudge(settings)
    print(f"model {settings.TYPESAFE_MODEL}; thresholds support≥{settings.JUDGE_SUPPORT_THRESHOLD} "
          f"leak≥{settings.JUDGE_LEAK_THRESHOLD} injection≥{settings.JUDGE_INJECTION_THRESHOLD}; cache {settings.JUDGE_CACHE_MODE}")

    print("\n=== verify_extraction (P(document states the extracted value); '!' = below support threshold) ===")
    docs = {name: document(name, f"doc-{name[9]}") for name in DOCS}
    for name, (doc, quote) in docs.items():
        show_extraction(name, judge.verify_extraction(doc.text, quote))
    inj_doc, inj_quote = document("injection_cobalt.eml.txt", "doc-inj")
    show_extraction("injection_cobalt.eml.txt", judge.verify_extraction(inj_doc.text, inj_quote))
    apex_doc, apex_quote = docs["supplier_a_apex.pdf"]
    wrong = apex_quote.model_copy(update={"unit_price": Decimal("99.20")})
    show_extraction("supplier_a_apex.pdf with unit_price 99.20", judge.verify_extraction(apex_doc.text, wrong))

    print("\n=== detect_injection (P(text carries instructions addressed to an AI / procurement software)) ===")
    for name, (doc, _) in docs.items():
        show_flag(name, judge.detect_injection(doc.text), "injection")
    show_flag("injection_cobalt.eml.txt", judge.detect_injection(inj_doc.text), "injection")
    supplier = ScriptedSupplier()
    for sid in ("sup_b", "sup_c"):
        for rnd in (1, 2):
            show_flag(f"{sid} round-{rnd} reply", judge.detect_injection(supplier.reply(sid, rnd).reply_text), "injection")

    print("\n=== check_outbound (P(leak) per question; leak if any ≥ threshold) ===")
    request = ProcurementRequest.model_validate_json((FIXTURES / "ProcurementRequest.json").read_text())
    config = ProcurementConfig.model_validate_json((FIXTURES / "ProcurementConfig.json").read_text())
    _, quote_b = docs["supplier_b_borealis.xlsx"]
    offer = NegotiationOffer(unit_price=quote_b.unit_price, lead_time_days=quote_b.lead_time_days)
    thread = NegotiationThread(run_id="probe", supplier_id="sup_b", boundaries=config.negotiation,
                               original_offer=offer, current_offer=offer)
    draft = MockNegotiationAgent().draft(request, quote_b, thread, config.negotiation)["message"]
    others = [NAMES["sup_a"], NAMES["sup_c"]]
    show_flag("sup_b round-1 draft", judge.check_outbound(draft, NAMES["sup_b"], others), "leaks")
    show_flag("… + 'Apex offered 11.20'", judge.check_outbound(draft + " Apex offered 11.20.", NAMES["sup_b"], others), "leaks")

    print("\n=== full mock-mode workflow with the Jev judge (records what `just demo-week3` replays) ===")
    orch = Orchestrator(build_agents(Settings(MODE="mock")), RunStore(), judge=judge)
    run = orch.create_run(request, config)
    orch.add_documents(run.run_id, [d for d, _ in docs.values()])
    run = orch.run_evaluation(run.run_id)
    run = orch.confirm_quote_math(run.run_id, run.pending_human.quote_ids[0], use_computed=True)
    orch.start_negotiation(run.run_id)
    while run.state == S.AWAITING_NEGOTIATION_APPROVAL:
        run = orch.approve_negotiation(run.run_id, run.pending_human.details["supplier_id"])
    for e in orch.store.events(run.run_id):
        if e.type.startswith("guardrail.") or e.type == "negotiation.policy_blocked":
            print(f"  #{e.seq:<3} {e.type:<30} {e.summary}")
    print(f"  final state {run.state}; ranked {run.recommendation.ranked}; "
          f"negotiations {({s: t.status.value for s, t in run.negotiations.items()})}")

    print(f"\nlive Jev calls this run: {judge.live_calls} (input tokens {judge.live_input_tokens:,}); "
          f"replayed from cache: {judge.cache.replayed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
