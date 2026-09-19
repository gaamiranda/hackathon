"""Week 2 tracer bullet over HTTP: the Week 1 path, then the negotiation loop with every pending draft
auto-approved unedited (PLAN.md D17, D21). Assumes the API is running (`just backend`).

Prints each draft as it is approved, each supplier reply verbatim (untrusted text, G4), the settled threads
and the final change explanation.

Usage: cd backend && uv run python scripts/demo_week2.py [http://127.0.0.1:8000]
"""

import sys
import textwrap

from demo_week1 import main as week1


def indent(text: str, prefix: str = "      ") -> str:
    return textwrap.indent(textwrap.fill(text, 100), prefix)


def main(base_url: str) -> None:
    c, run_id, run = week1(base_url)
    before = {card["supplier_id"]: card for card in run["scorecards"]}

    run = c.post(f"/runs/{run_id}/negotiate").raise_for_status().json()
    print(f"\nnegotiate: state={run['state']}")
    while run["state"] == "AWAITING_NEGOTIATION_APPROVAL":
        d = run["pending_human"]["details"]
        sid, round_no, target = d["supplier_id"], d["round"], d["target_offer"]
        print(f"\n  [{sid} round {round_no}] draft (ask {target['unit_price']}/unit, {target['lead_time_days']} d):")
        print(indent(d["draft"]))
        run = c.post(f"/runs/{run_id}/negotiation/{sid}/approve", json={}).raise_for_status().json()
        thread = run["negotiations"][sid]
        reply = next(t for t in reversed(thread["turns"]) if t["role"] == "supplier")
        offer = reply["offer"]
        counter = f"counter {offer['unit_price']}/unit, {offer['lead_time_days']} d" if offer else "no counter-offer"
        print(f"  [{sid} round {round_no}] approved & sent → supplier reply ({counter}):")
        print(indent(reply["message"]))
        if thread["status"] != "open":
            cur = thread["current_offer"]
            print(f"  [{sid}] thread {thread['status']}: {cur['unit_price']}/unit, {cur['lead_time_days']} d")

    print(f"\nfinal state: {run['state']}; recommended: {run['recommendation']['recommended_supplier_id']}\n")
    print(f"{'supplier':<8} {'landed before':>14} {'landed after':>14} {'score before':>13} {'score after':>12}")
    for card in run["scorecards"]:
        b = before[card["supplier_id"]]
        print(f"{card['supplier_id']:<8} {float(b['landed_cost']):>14,.2f} {float(card['landed_cost']):>14,.2f} "
              f"{b['total_score']:>13.2f} {card['total_score']:>12.2f}")
    print(f"\nchange explanation: {run['recommendation']['change_explanation']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000")
