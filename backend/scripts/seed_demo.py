"""Seed a fresh demo run to any stage over the HTTP API (T18): local backend or the Lightsail box.

    cd backend && uv run python scripts/seed_demo.py STAGE [--scenario a|b] [--base URL] [--quiet]
    just seed STAGE [--scenario b] [--base URL]

STAGE (each includes the ones before it; every human gate is auto-approved exactly as docs/DEMO.md does):
    created      the request, nothing uploaded
    extracted    + the synthetic quotes uploaded and extracted (state EXTRACTED)
    mismatch     + evaluated: stopped at the quote whose printed total is wrong (CALC_MISMATCH, beat 3)
    recommended  + computed total accepted → first recommendation (RECOMMENDED, beat 4)
    negotiated   + negotiation started, every draft approved unedited, re-scored (RECOMMENDED, beat 6)
    replanned    + the scenario's interrupt → the recommendation flips (RECOMMENDED, beat 7)
    po           + PO requested and approved as demo-user (PO_GENERATED, beat 8)

SCENARIO (--scenario, default a):
    a  PLAN.md §15: 2,000 × Product X in 14 days, 30,000 USD. Apex's total is wrong; Borealis recommended;
       the interrupt raises quantity to 5,000 / budget to 75,000 and Borealis fails capacity → Cobalt.
    b  PLAN.md §11 T22: 10,000 × M8 stainless hex bolts in 21 days, 9,500 USD. Granite's total is wrong;
       Delta is blacklisted; Eiger recommended; the interrupt cuts the budget to 8,700 and Eiger goes
       over budget → Fjord. Same engine, different story (data/synthetic/scenario_b/README.md).

Always creates a new run (never touches existing ones) and prints its War Room URL last. With the box's cache
warm every stage takes well under 15 s; a cache miss adds one live LLM call (~5 s) per decision moment.
The default base is the box's API; use --base http://127.0.0.1:8000 for `just run`.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC = ROOT / "data" / "synthetic"
STAGES = ["created", "extracted", "mismatch", "recommended", "negotiated", "replanned", "po"]
BOX_API = "http://47.129.120.76/api"
APPROVER = "demo-user"

SCENARIOS: dict[str, dict] = {
    # delivery_window_days matches the UI preset: required_by is derived from today in UTC, so the
    # Decision Agent's prompt (and therefore its cache key) is the same from any timezone.
    "a": {
        "dir": SYNTHETIC,
        "docs": ["supplier_a_apex.pdf", "supplier_b_borealis.xlsx", "supplier_c_cobalt.eml.txt"],
        "request": {"product": "Product X", "quantity": 2000, "budget": "30000.00", "currency": "USD"},
        "delivery_window_days": 14,  # PLAN.md §15
        "interrupt": {"quantity": 5000, "budget": "75000.00", "reason": "Customer order upsized"},  # beat 7, D22
        "recommended": ("sup_b", "Borealis"),
        "replanned": ("sup_c", "Cobalt"),
    },
    "b": {
        "dir": SYNTHETIC / "scenario_b",
        "docs": ["supplier_d_delta.xlsx", "supplier_e_eiger.pdf",
                 "supplier_f_fjord.eml.txt", "supplier_g_granite.pdf"],
        "request": {"product": "M8 stainless hex bolts", "quantity": 10000, "budget": "9500.00", "currency": "USD"},
        "delivery_window_days": 21,  # PLAN.md §11 T22
        "interrupt": {"budget": "8700.00", "reason": "Budget cut by finance"},  # quantity unchanged
        "recommended": ("sup_e", "Eiger"),
        "replanned": ("sup_f", "Fjord"),
    },
}


def demo_request(scenario: str) -> dict:
    """The UI preset: required_by = today in UTC + the scenario's window, the clock the backend stamps
    created_at with, so the delivery window — part of the Decision Agent's prompt and cache key — is stable."""
    spec = SCENARIOS[scenario]
    required_by = (datetime.now(timezone.utc).date() + timedelta(days=spec["delivery_window_days"])).isoformat()
    return {**spec["request"], "required_by": required_by}


def war_room_url(base: str, run_id: str) -> str:
    """http://host/api → http://host/runs/<id>; a bare backend (:8000) has no UI, so point at the Vite dev server."""
    if base.endswith("/api"):
        return f"{base[: -len('/api')]}/runs/{run_id}"
    return f"http://localhost:5173/runs/{run_id}"


def seed(base: str, stage: str, *, scenario: str = "a", log=print) -> str:
    """Drive the run to `stage`; returns the run id. Raises on any unexpected state (a seed must never lie)."""
    if stage not in STAGES:
        raise SystemExit(f"unknown stage {stage!r}; one of {', '.join(STAGES)}")
    if scenario not in SCENARIOS:
        raise SystemExit(f"unknown scenario {scenario!r}; one of {', '.join(SCENARIOS)}")
    spec = SCENARIOS[scenario]
    target = STAGES.index(stage)
    c = httpx.Client(base_url=base, timeout=60)
    t0 = time.perf_counter()

    def step(name: str, run: dict, expect: str | None = None) -> None:
        log(f"  {time.perf_counter() - t0:5.1f}s  {name:<14} state={run['state']}")
        if expect and run["state"] != expect:
            raise SystemExit(f"{name}: expected {expect}, got {run['state']} ({run.get('pending_human')})")

    def expect_recommended(run: dict, key: str) -> None:
        want, label = spec[key]
        got = run["recommendation"]["recommended_supplier_id"]
        if got != want:
            raise SystemExit(f"expected {label} ({want}) recommended, got {got}")

    created = c.post("/runs", json={"request": demo_request(scenario)}).raise_for_status().json()
    run_id, run = created["run_id"], created["run"]
    step("created", run, "CREATED")
    if target == 0:
        return run_id

    files = [("files", (name, (spec["dir"] / name).read_bytes())) for name in spec["docs"]]
    run = c.post(f"/runs/{run_id}/documents", files=files).raise_for_status().json()
    step("extracted", run, "EXTRACTED")
    if target == 1:
        return run_id

    run = c.post(f"/runs/{run_id}/evaluate").raise_for_status().json()
    step("evaluated", run, "CALC_MISMATCH")
    if target == 2:
        return run_id

    for qid in list(run["pending_human"]["quote_ids"]):  # beat 3: "Use computed total"
        run = c.post(f"/runs/{run_id}/quotes/{qid}/confirm-math", json={"use_computed": True}).raise_for_status().json()
    step("recommended", run, "RECOMMENDED")
    expect_recommended(run, "recommended")
    if target == 3:
        return run_id

    run = c.post(f"/runs/{run_id}/negotiate").raise_for_status().json()
    rounds = 0
    while run["state"] == "AWAITING_NEGOTIATION_APPROVAL":  # beats 5–6: approve every draft unedited
        sid = run["pending_human"]["details"]["supplier_id"]
        run = c.post(f"/runs/{run_id}/negotiation/{sid}/approve", json={}).raise_for_status().json()
        rounds += 1
    step(f"negotiated ×{rounds}", run, "RECOMMENDED")
    if target == 4:
        return run_id

    run = c.post(f"/runs/{run_id}/interrupt", json=spec["interrupt"]).raise_for_status().json()
    step("replanned", run, "RECOMMENDED")
    expect_recommended(run, "replanned")
    if target == 5:
        return run_id

    run = c.post(f"/runs/{run_id}/request-po").raise_for_status().json()
    step("po requested", run, "AWAITING_PO_APPROVAL")
    run = c.post(f"/runs/{run_id}/approve-po", json={"approved_by": APPROVER}).raise_for_status().json()
    step("po", run, "PO_GENERATED")
    log(f"  PO {run['purchase_order']['po_number']} for {run['purchase_order']['supplier']['name']}")
    return run_id


def llm_routes(base: str, run_id: str) -> dict[str, int]:
    """How each agent.finished was served (replay / openclaw / gateway / template…) — the cache-warm check."""
    events = httpx.get(f"{base}/runs/{run_id}/events", timeout=30).raise_for_status().json()
    counts: dict[str, int] = {}
    for e in events:
        if e["type"] == "agent.finished":
            via = (e.get("payload") or {}).get("backend") or "-"
            counts[via] = counts.get(via, 0) + 1
        elif e["type"] == "agent.failed":
            counts["FAILED"] = counts.get("FAILED", 0) + 1
    return counts


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=STAGES)
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="a", help="a = Product X (default), b = M8 bolts")
    ap.add_argument("--base", default=BOX_API, help=f"API base URL (default {BOX_API}; local: http://127.0.0.1:8000)")
    ap.add_argument("--quiet", action="store_true", help="print only the run URL")
    args = ap.parse_args(argv)
    base = args.base.rstrip("/")
    log = (lambda *a, **k: None) if args.quiet else print

    health = httpx.get(f"{base}/health", timeout=10).raise_for_status().json()
    log(f"seeding '{args.stage}' (scenario {args.scenario}) on {base} "
        f"(mode {health['mode']}, llm {health.get('llm', '?')}, judge {health['guardrail_judge']})")
    t0 = time.perf_counter()
    run_id = seed(base, args.stage, scenario=args.scenario, log=log)
    elapsed = time.perf_counter() - t0
    routes = llm_routes(base, run_id)
    log(f"done in {elapsed:.1f}s; agent.finished via {json.dumps(routes)}")
    print(war_room_url(base, run_id))


if __name__ == "__main__":
    main(sys.argv[1:])
