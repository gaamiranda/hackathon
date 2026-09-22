# ProcureAI — Slide Outline (segments A and D of docs/DEMO.md; ~10 slides)

1. **Title.** ProcureAI: The Autonomous Procurement War Room. Team names. One line: "An AI procurement team that does the work and asks a human only when it matters."
2. **The buyer's half hour.** Three messy quotes (show the real PDF, xlsx, email thumbnails). Manual today: 15–30 min per comparison, no negotiation on small orders, errors nobody catches.
3. **The pre-mortem.** Four ways an AI buyer fails in front of a CFO: math hallucination (2,000 × 10.50 = 2,100), infinite negotiation, leaking Supplier A's price to Supplier B, obeying instructions hidden in a supplier email. "We designed against these first."
4. **Three layers, one rule.** Diagram: OpenClaw runs the agents · Python calculates the truth · the LLM explains. Arrows: documents → Document Agent → deterministic engine → Decision Agent → Negotiation Agent → human gates. Callout: "The LLM never touches a number that matters."
5. **The team.** Four agents with responsibilities and what each is forbidden to do (from PLAN.md §3), plus the Guardrail Judge (Jev) and the Engine. One row each.
6. **Human in the loop, by design.** Four gates: extraction form, math mismatch, negotiation approval, final PO. "Autonomous for everything non-consequential; 100% human for everything consequential."
   → Live demo (segments B and C)
7. **What you just saw, measured.** The evidence table from docs/DEMO.md: math guard, number guard catches (2 of 3, then 3 of 3 via OpenClaw, then 4 of 4 in negotiation before prompt fixes), Jev probabilities, policy blocks, replan < 1 s, manual mode identical numbers.
8. **How it is built.** Stack line (FastAPI, React, OpenClaw on Lightsail, Claude Sonnet 4.5 via the AWS gateway, Jev), 313 tests, replay cache so demos never depend on the network, 7 failure drills. Screenshot of the engine formula chain or the number guard (from the code walk).
9. **What we would do next.** Real email/ERP connectors, DynamoDB supplier history, multi-product requests, learning negotiation boundaries from history. Keep it to four bullets.
10. **Thank you / Q&A.** URL, repo, the one-line pitch again.

Design notes: dark theme matching the War Room; screenshots from docs/screenshots/ (01 lanes, 02 mismatch, 03 injection, 04 replan, 05 PO) as full-bleed images between slides 6 and 7 if a transition is needed; no bullet longer than one line.
