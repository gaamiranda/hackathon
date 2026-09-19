"""Week 3 tracer bullet over HTTP: the Week 2 path (negotiated, Borealis recommended), then the interrupt
"quantity 2,000 → 5,000, budget 75,000" (PLAN.md §1 step 8, D22, §15). Assumes the API is running (`just backend`).

Prints the replan events as they were logged, the per-supplier impact table and the change explanation.
Expected end state: Cobalt recommended, Borealis ineligible on capacity.

Usage: cd backend && uv run python scripts/demo_week3.py [http://127.0.0.1:8000]
"""

import sys
import textwrap

import httpx

from demo_week2 import main as week2

INTERRUPT = {"quantity": 5000, "budget": "75000.00", "reason": "Customer order upsized"}


def main(base_url: str) -> None:
    week2(base_url)
    c = httpx.Client(base_url=base_url, timeout=30)
    run_id = c.get("/runs").json()[-1]["run_id"]
    since = c.get(f"/runs/{run_id}/events").json()[-1]["seq"]

    print(f"\n=== INTERRUPT: quantity {INTERRUPT['quantity']:,}, budget {INTERRUPT['budget']} ({INTERRUPT['reason']}) ===")
    run = c.post(f"/runs/{run_id}/interrupt", json=INTERRUPT).raise_for_status().json()
    for e in c.get(f"/runs/{run_id}/events", params={"since": since}).json():
        arrow = f"  {e['state_before']} → {e['state_after']}" if e["state_before"] != e["state_after"] else ""
        print(f"  #{e['seq']:<3} {e['actor']:<8} {e['type']:<28} {e['summary'][:90]}{arrow}")

    impact = run["replan_impact"]
    print(f"\nstate: {run['state']}; request v{run['request']['version']} "
          f"(history: {[f'v{h['version']}' for h in run['request_history']]})")
    print("changed: " + "; ".join(f"{k} {v['before']} → {v['after']}" for k, v in impact["changes"].items()))
    print(f"\n{'supplier':<8} {'elig before':>11} {'elig after':>10} {'landed before':>14} {'landed after':>13} "
          f"{'score before':>13} {'score after':>12}  reasons")
    for s in impact["per_supplier"]:
        print(f"{s['supplier_id']:<8} {str(s['eligible_before']):>11} {str(s['eligible_after']):>10} "
              f"{float(s['landed_before']):>14,.2f} {float(s['landed_after']):>13,.2f} "
              f"{s['score_before']:>13.2f} {s['score_after']:>12.2f}  {'; '.join(s['reasons'])}")
    print(f"\nrecommended: {impact['recommended_before']} → {impact['recommended_after']}")
    rec = run["recommendation"]
    if rec["escalation"]:
        print(f"ESCALATION: {rec['escalation']['reason']}")
    print("\nchange explanation:")
    print(textwrap.indent(textwrap.fill(rec["change_explanation"], 100), "  "))
    assert rec["recommended_supplier_id"] == "sup_c", rec
    assert any(s["supplier_id"] == "sup_b" and not s["eligible_after"] for s in impact["per_supplier"]), impact


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000")
