"""Run the guardrail judge (TypeSafe Jev) over the demo fixtures and print every probability (T14, PLAN.md D20).

Verdicts are recorded in data/judge_cache/, so tests (`replay_only`) and demos never call the API again.
Budget: JUDGE_MAX_LIVE_CALLS (40) per process; the script prints how many live calls it made and their input tokens.

Usage: cd backend && uv run python scripts/judge_live.py [--scenario a|b] [--record]
       --record re-runs every case live even when a verdict is cached.
       --scenario b runs the same battery over the second demo story (T22, data/synthetic/scenario_b/):
       four documents, a blacklisted supplier and an injection hidden in a polite postscript.
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
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

SCENARIOS: dict[str, dict] = {
    "a": {
        "dir": SYNTHETIC,
        "docs": ["supplier_a_apex.pdf", "supplier_b_borealis.xlsx", "supplier_c_cobalt.eml.txt"],
        "names": {"sup_a": "Apex Components Ltd", "sup_b": "Borealis Manufacturing AS", "sup_c": "Cobalt Industrial"},
        "negotiated": ["sup_b", "sup_c"],
        "draft_doc": "supplier_b_borealis.xlsx",
        "draft_supplier": "sup_b",
        "leak_suffix": " Apex offered 11.20.",
        "tamper": ("supplier_a_apex.pdf", "unit_price", Decimal("99.20")),
        "extra_doc": "injection_cobalt.eml.txt",  # hand-written adversarial copy, not generated
    },
    "b": {
        "dir": SYNTHETIC / "scenario_b",
        "docs": ["supplier_d_delta.xlsx", "supplier_e_eiger.pdf",
                 "supplier_f_fjord.eml.txt", "supplier_g_granite.pdf"],
        "names": {"sup_d": "Delta Trading", "sup_e": "Eiger Metallwerk GmbH",
                  "sup_f": "Fjord Components AS", "sup_g": "Granite Fastener Co"},
        "negotiated": ["sup_e", "sup_f"],
        "draft_doc": "supplier_e_eiger.pdf",
        "draft_supplier": "sup_e",
        "leak_suffix": " Fjord offered 0.79.",
        "tamper": ("supplier_e_eiger.pdf", "unit_price", Decimal("0.41")),
        "extra_doc": None,
        "request": {"id": "req-demo-b", "product": "M8 stainless hex bolts", "quantity": 10000,
                    "budget": Decimal("9500.00"), "currency": "USD", "window_days": 21},
    },
}


def document(spec: dict, name: str, doc_id: str) -> tuple[RawDocument, NormalizedQuote]:
    directory = spec["dir"] if (spec["dir"] / name).exists() else SYNTHETIC
    source, text = to_text(name, (directory / name).read_bytes())
    quote = NormalizedQuote.model_validate_json((directory / f"{name}.expected.json").read_text())
    return RawDocument(doc_id=doc_id, filename=name, source=source, text=text), quote.model_copy(update={"doc_id": doc_id})


def demo_request(spec: dict) -> ProcurementRequest:
    """Scenario A reuses the committed fixture; scenario B builds its own request (T22)."""
    if "request" not in spec:
        return ProcurementRequest.model_validate_json((FIXTURES / "ProcurementRequest.json").read_text())
    r = dict(spec["request"])
    created = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)
    return ProcurementRequest(
        **{k: v for k, v in r.items() if k != "window_days"},
        created_at=created,
        required_by=(created + timedelta(days=r["window_days"])).date(),
    )


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
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="a")
    ap.add_argument("--record", action="store_true", help="re-run every case live even when a verdict is cached")
    args = ap.parse_args(argv)
    spec = SCENARIOS[args.scenario]
    names, doc_names = spec["names"], spec["docs"]

    settings = Settings(GUARDRAIL_JUDGE="jev", JUDGE_CACHE_MODE="record") if args.record else Settings(GUARDRAIL_JUDGE="jev")
    judge = JevGuardrailJudge(settings)
    print(f"scenario {args.scenario}; model {settings.TYPESAFE_MODEL}; thresholds support≥{settings.JUDGE_SUPPORT_THRESHOLD} "
          f"leak≥{settings.JUDGE_LEAK_THRESHOLD} injection≥{settings.JUDGE_INJECTION_THRESHOLD}; cache {settings.JUDGE_CACHE_MODE}")

    print("\n=== verify_extraction (P(document states the extracted value); '!' = below support threshold) ===")
    docs = {name: document(spec, name, f"doc-{i}") for i, name in enumerate(doc_names)}
    for name, (doc, quote) in docs.items():
        show_extraction(name, judge.verify_extraction(doc.text, quote))
    if spec["extra_doc"]:
        extra_doc, extra_quote = document(spec, spec["extra_doc"], "doc-inj")
        show_extraction(spec["extra_doc"], judge.verify_extraction(extra_doc.text, extra_quote))
    tamper_doc, field, value = spec["tamper"]
    base_doc, base_quote = docs[tamper_doc]
    wrong = base_quote.model_copy(update={field: value})
    show_extraction(f"{tamper_doc} with {field} {value}", judge.verify_extraction(base_doc.text, wrong))

    print("\n=== detect_injection (P(text carries instructions addressed to an AI / procurement software)) ===")
    for name, (doc, _) in docs.items():
        show_flag(name, judge.detect_injection(doc.text), "injection")
    if spec["extra_doc"]:
        show_flag(spec["extra_doc"], judge.detect_injection(extra_doc.text), "injection")
    supplier = ScriptedSupplier()
    for sid in spec["negotiated"]:
        for rnd in (1, 2):
            show_flag(f"{sid} round-{rnd} reply", judge.detect_injection(supplier.reply(sid, rnd).reply_text), "injection")

    print("\n=== check_outbound (P(leak) per question; leak if any ≥ threshold) ===")
    request = demo_request(spec)
    config = ProcurementConfig.model_validate_json((FIXTURES / "ProcurementConfig.json").read_text())
    own_id = spec["draft_supplier"]
    _, own_quote = docs[spec["draft_doc"]]
    offer = NegotiationOffer(unit_price=own_quote.unit_price, lead_time_days=own_quote.lead_time_days)
    thread = NegotiationThread(run_id="probe", supplier_id=own_id, boundaries=config.negotiation,
                               original_offer=offer, current_offer=offer)
    draft = MockNegotiationAgent().draft(request, own_quote, thread, config.negotiation)["message"]
    others = [n for sid, n in names.items() if sid != own_id]
    show_flag(f"{own_id} round-1 draft", judge.check_outbound(draft, names[own_id], others), "leaks")
    show_flag(f"… +'{spec['leak_suffix'].strip()}'", judge.check_outbound(draft + spec["leak_suffix"], names[own_id], others), "leaks")

    print("\n=== full mock-mode workflow with the Jev judge (records what the demo replays) ===")
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
