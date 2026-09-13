"""Week 1 tracer bullet over HTTP. Assumes the API is running (uv run uvicorn procureai.api.app:app).

Usage: cd backend && uv run python scripts/demo_week1.py [http://127.0.0.1:8000]
"""

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
FIXTURES, SYNTHETIC = ROOT / "data" / "fixtures", ROOT / "data" / "synthetic"
DOCS = ["supplier_a_apex.pdf", "supplier_b_borealis.xlsx", "supplier_c_cobalt.eml.txt"]


def main(base_url: str) -> None:
    c = httpx.Client(base_url=base_url, timeout=30)
    print("health:", c.get("/health").json())

    fixture = json.loads((FIXTURES / "ProcurementRequest.json").read_text())
    body = {"request": {k: fixture[k] for k in ("product", "quantity", "required_by", "budget", "currency")}}
    created = c.post("/runs", json=body).raise_for_status().json()
    run_id = created["run_id"]
    print(f"run {run_id} created: state={created['run']['state']}")

    files = [("files", (name, (SYNTHETIC / name).read_bytes())) for name in DOCS]
    run = c.post(f"/runs/{run_id}/documents", files=files).raise_for_status().json()
    print(f"uploaded {len(run['documents'])} documents: state={run['state']}, quotes={[q['quote_id'] for q in run['quotes']]}")

    run = c.post(f"/runs/{run_id}/evaluate").raise_for_status().json()
    print(f"evaluate: state={run['state']}")
    pending = run.get("pending_human")
    if pending:
        print(f"  pending human ({pending['kind']}): {pending['message']}")
        for qid, d in pending["details"].items():
            print(f"    {qid} [{d['supplier_id']}]: computed {d['computed_pre_tax_total']} vs stated {d['stated_total']}")
        for qid in list(pending["quote_ids"]):
            run = c.post(f"/runs/{run_id}/quotes/{qid}/confirm-math", json={"use_computed": True}).raise_for_status().json()
            print(f"  confirmed {qid} with computed total → state={run['state']}")

    rec = run["recommendation"]
    print("\nranking:")
    for card in run["scorecards"]:
        flag = "" if card["eligible"] else f"  (ineligible: {'; '.join(card['ineligibility_reasons'])})"
        print(f"  {card['supplier_id']:<6} {card['total_score']:>6.1f}  landed {card['landed_cost']:>10}  {card['lead_time_days']:>2} d{flag}")
    print(f"\nrecommended: {rec['recommended_supplier_id']}")
    print(f"rationale: {rec['rationale']}")
    events = c.get(f"/runs/{run_id}/events").json()
    print(f"\n{len(events)} events logged; stream with: curl -N {base_url}/runs/{run_id}/events/stream")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000")
