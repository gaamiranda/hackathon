"""HTTP layer in mock mode (TestClient, no network)."""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procureai.api.app import app

ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC = ROOT / "data" / "synthetic"
DOCS = ["supplier_a_apex.pdf", "supplier_b_borealis.xlsx", "supplier_c_cobalt.eml.txt"]
# required_by is relative so Apex (13 d lead time) stays eligible whatever day the suite runs (created_at is "now").
REQUEST = {"product": "Product X", "quantity": 2000, "required_by": (date.today() + timedelta(days=14)).isoformat(),
           "budget": "30000.00", "currency": "USD"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def upload(client: TestClient, run_id: str, names: list[str]):
    files = [("files", (n, (SYNTHETIC / n).read_bytes())) for n in names]
    return client.post(f"/runs/{run_id}/documents", files=files)


def create(client: TestClient) -> str:
    r = client.post("/runs", json={"request": REQUEST})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["run"]["state"] == "CREATED" and body["run"]["config"]["weights"]["price"] == 0.3
    return body["run_id"]


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"mode": "mock", "llm_gateway": "unconfigured", "llm_backend": "gateway", "openclaw": "unconfigured",
                        "openclaw_tasks": [], "guardrail_judge": "mock", "judge_model": None, "runs": 0, "runs_persisted": False}


def test_full_mismatch_flow_over_http(client):
    run_id = create(client)
    assert client.get("/runs").json()[0]["run_id"] == run_id

    r = upload(client, run_id, DOCS)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["state"] == "EXTRACTED" and len(run["documents"]) == 3
    assert {d["source"] for d in run["documents"]} == {"pdf", "xlsx", "email"}
    xlsx_text = next(d["text"] for d in run["documents"] if d["source"] == "xlsx")
    assert "Supplier: Borealis Manufacturing AS" in xlsx_text and "TOTAL (USD): 25338" in xlsx_text
    assert all(len(d["text"]) < 3000 for d in run["documents"])

    run = client.post(f"/runs/{run_id}/evaluate").json()
    assert run["state"] == "CALC_MISMATCH"
    assert run["pending_human"]["kind"] == "calc_mismatch"
    assert run["pending_human"]["quote_ids"] == ["APX-Q-26091"]

    r = client.post(f"/runs/{run_id}/quotes/APX-Q-26091/confirm-math", json={"use_computed": True})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["state"] == "RECOMMENDED"
    assert run["recommendation"]["recommended_supplier_id"] == "sup_b"
    assert [c["supplier_id"] for c in run["scorecards"]] == ["sup_b", "sup_c", "sup_a"]
    assert client.get(f"/runs/{run_id}").json()["state"] == "RECOMMENDED"

    events = client.get(f"/runs/{run_id}/events").json()
    assert [e["seq"] for e in events] == list(range(len(events)))
    later = client.get(f"/runs/{run_id}/events", params={"since": 10}).json()
    assert later and all(e["seq"] > 10 for e in later) and len(later) == len(events) - 11
    assert client.get("/health").json()["runs"] == 1


def test_run_summary_over_http(client):
    """GET /runs/{id}/summary (T16): the header strip's figures, straight from the Run, at every stage."""
    run_id = create(client)
    s = client.get(f"/runs/{run_id}/summary").json()
    assert s == {"run_id": run_id, "state": "CREATED", "product": "Product X", "quantity": 2000, "version": 1,
                 "recommended_supplier_id": None, "recommended_name": None, "total_score": None, "landed_cost": None,
                 "pending_human_kind": None, "po_number": None,
                 "counts": {"documents": 0, "quotes": 0, "events": 1, "negotiations": 0}}

    upload(client, run_id, DOCS)
    client.post(f"/runs/{run_id}/evaluate")
    s = client.get(f"/runs/{run_id}/summary").json()
    assert s["state"] == "CALC_MISMATCH" and s["pending_human_kind"] == "calc_mismatch"
    assert s["counts"]["documents"] == 3 and s["counts"]["quotes"] == 3
    assert s["counts"]["events"] == len(client.get(f"/runs/{run_id}/events").json())

    client.post(f"/runs/{run_id}/quotes/APX-Q-26091/confirm-math", json={"use_computed": True})
    run = client.get(f"/runs/{run_id}").json()
    s = client.get(f"/runs/{run_id}/summary").json()
    top = next(c for c in run["scorecards"] if c["supplier_id"] == "sup_b")
    assert s["state"] == "RECOMMENDED" and s["pending_human_kind"] is None
    assert s["recommended_supplier_id"] == "sup_b" and s["recommended_name"] == "Borealis Manufacturing AS"
    assert s["total_score"] == top["total_score"] and s["landed_cost"] == top["landed_cost"]

    client.post(f"/runs/{run_id}/request-po")
    assert client.get(f"/runs/{run_id}/summary").json()["pending_human_kind"] == "po_approval"
    run = client.post(f"/runs/{run_id}/approve-po", json={"approved_by": "judge"}).json()
    s = client.get(f"/runs/{run_id}/summary").json()
    assert s["state"] == "PO_GENERATED" and s["po_number"] == run["purchase_order"]["po_number"]
    assert s["pending_human_kind"] is None
    assert client.get("/runs/nope/summary").status_code == 404


def test_email_json_upload_and_correct(client):
    run_id = create(client)
    text = (SYNTHETIC / "supplier_c_cobalt.eml.txt").read_text()
    r = client.post(f"/runs/{run_id}/documents", json={"email_text": text, "filename": "supplier_c_cobalt.eml.txt"})
    assert r.status_code == 200, r.text
    assert r.json()["documents"][0]["source"] == "email"
    # correct is only legal while NEEDS_HUMAN_EXTRACTION
    r = client.post(f"/runs/{run_id}/quotes/CI-Q-7731/correct", json={"patch": {"unit_price": "13.40"}})
    assert r.status_code == 409 and r.json()["code"] == "illegal_transition"


def test_replace_document(client):
    run_id = create(client)
    run = upload(client, run_id, ["supplier_b_borealis.xlsx"]).json()
    doc_id = run["documents"][0]["doc_id"]
    assert run["quotes"][0]["doc_id"] == doc_id
    files = [("files", ("supplier_c_cobalt.eml.txt", (SYNTHETIC / "supplier_c_cobalt.eml.txt").read_bytes()))]
    r = client.post(f"/runs/{run_id}/documents/{doc_id}/replace", files=files)
    assert r.status_code == 200, r.text
    run = r.json()
    assert [d["filename"] for d in run["documents"]] == ["supplier_c_cobalt.eml.txt"]
    assert [q["supplier_id"] for q in run["quotes"]] == ["sup_c"]
    assert run["quotes"][0]["doc_id"] == run["documents"][0]["doc_id"]
    assert run["state"] == "EXTRACTED"
    assert client.post(f"/runs/{run_id}/documents/nope/replace", files=files).status_code == 404


def test_lowconf_filename_triggers_extraction_gate(client):
    """Mock knob: a filename containing "lowconf" gets 0.5 confidence on unit_price/lead_time_days (G6)."""
    run_id = create(client)
    files = [("files", ("supplier_c_lowconf.eml.txt", (SYNTHETIC / "supplier_c_cobalt.eml.txt").read_bytes())),
             ("files", ("supplier_b_borealis.xlsx", (SYNTHETIC / "supplier_b_borealis.xlsx").read_bytes()))]
    r = client.post(f"/runs/{run_id}/documents", files=files)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["state"] == "NEEDS_HUMAN_EXTRACTION"
    assert run["pending_human"]["kind"] == "extraction"
    assert run["pending_human"]["quote_ids"] == ["CI-Q-7731"]
    assert run["pending_human"]["details"]["fields"] == {"CI-Q-7731": ["unit_price", "lead_time_days"]}
    quote = next(q for q in run["quotes"] if q["quote_id"] == "CI-Q-7731")
    assert quote["field_confidence"]["unit_price"] == 0.5 and quote["field_confidence"]["lead_time_days"] == 0.5
    assert quote["doc_id"] == next(d["doc_id"] for d in run["documents"] if "lowconf" in d["filename"])

    r = client.post(f"/runs/{run_id}/quotes/CI-Q-7731/correct", json={"patch": {"unit_price": "13.40", "lead_time_days": 9}})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["state"] == "RECOMMENDED" and run["pending_human"] is None
    assert run["recommendation"]["recommended_supplier_id"] == "sup_b"
    types = [e["type"] for e in client.get(f"/runs/{run_id}/events").json()]
    assert types.index("extraction.needs_human") < types.index("quote.corrected") < types.index("extraction.resumed")


def test_errors(client):
    run_id = create(client)
    r = client.post(f"/runs/{run_id}/evaluate")
    assert r.status_code == 409 and r.json()["code"] == "illegal_transition"
    assert client.get("/runs/run-nope").status_code == 404
    assert client.post("/runs/run-nope/evaluate").json()["code"] == "run_not_found"
    assert client.get("/runs/run-nope/events").status_code == 404

    big = "x" * 6001
    r = client.post(f"/runs/{run_id}/documents", json={"email_text": big, "filename": "big.txt"})
    assert r.status_code == 413
    r = client.post(f"/runs/{run_id}/documents", files=[("files", ("quote.docx", b"zzz"))])
    assert r.status_code == 415
    r = client.post(f"/runs/{run_id}/documents", files=[("files", ("broken.pdf", b"not a pdf"))])
    assert r.status_code == 422
    assert client.post(f"/runs/{run_id}/documents", json={"filename": "x.txt"}).status_code == 422
    assert client.post("/runs", json={"request": {**REQUEST, "quantity": 0}}).status_code == 422


def test_sse_replays_events(client):
    run_id = create(client)
    upload(client, run_id, ["supplier_b_borealis.xlsx", "supplier_c_cobalt.eml.txt"])
    client.post(f"/runs/{run_id}/evaluate")
    expected = client.get(f"/runs/{run_id}/events", params={"since": 3}).json()

    received = []
    # follow=false: TestClient cannot close an endless stream (Starlette waits for the app task), so replay only
    with client.stream("GET", f"/runs/{run_id}/events/stream", params={"since": 3, "follow": "false"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        block: dict[str, str] = {}
        for line in r.iter_lines():
            if line == "":
                if block:
                    received.append(block)
                    block = {}
                continue
            key, _, value = line.partition(": ")
            block[key] = value

    assert [int(b["id"]) for b in received] == [e["seq"] for e in expected]
    # no `event:` line (T8b): every message reaches EventSource.onmessage; the type travels inside the JSON
    assert all(set(b) == {"id", "data"} for b in received)
    assert [json.loads(b["data"])["type"] for b in received] == [e["type"] for e in expected]
    assert json.loads(received[-1]["data"])["type"] == "recommendation.ready"


# --------------------------------------------------------------------------- negotiation (T12)


def recommended(client: TestClient) -> str:
    """Demo path up to RECOMMENDED over HTTP (A's wrong total confirmed with the computed value)."""
    run_id = create(client)
    assert upload(client, run_id, DOCS).status_code == 200
    run = client.post(f"/runs/{run_id}/evaluate").json()
    assert run["state"] == "CALC_MISMATCH"
    run = client.post(f"/runs/{run_id}/quotes/APX-Q-26091/confirm-math", json={"use_computed": True}).json()
    assert run["state"] == "RECOMMENDED"
    return run_id


def test_negotiate_before_recommended_is_409(client):
    run_id = create(client)
    r = client.post(f"/runs/{run_id}/negotiate")
    assert r.status_code == 409 and r.json()["code"] == "illegal_transition"
    r = client.post(f"/runs/{run_id}/negotiation/sup_b/approve", json={})
    assert r.status_code == 409 and r.json()["code"] == "illegal_transition"
    assert client.post("/runs/run-nope/negotiate").status_code == 404
    assert client.get("/runs/run-nope/negotiations").status_code == 404
    assert client.get(f"/runs/{run_id}/negotiations").json() == {}


def test_full_negotiation_loop_over_http(client):
    run_id = recommended(client)
    r = client.post(f"/runs/{run_id}/negotiate")
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["state"] == "AWAITING_NEGOTIATION_APPROVAL"
    pending = run["pending_human"]
    assert pending["kind"] == "negotiation_approval"
    assert set(pending["details"]) == {"supplier_id", "round", "draft", "target_offer", "boundaries"}
    assert pending["details"]["supplier_id"] == "sup_b" and pending["details"]["round"] == 1

    # approving for the wrong supplier is refused; the pending draft stays
    r = client.post(f"/runs/{run_id}/negotiation/sup_c/approve")
    assert r.status_code == 409 and r.json()["code"] == "not_pending"

    approved: list[tuple[str, int]] = []
    while run["state"] == "AWAITING_NEGOTIATION_APPROVAL":
        d = run["pending_human"]["details"]
        approved.append((d["supplier_id"], d["round"]))
        r = client.post(f"/runs/{run_id}/negotiation/{d['supplier_id']}/approve", json={})
        assert r.status_code == 200, r.text
        run = r.json()
    assert approved == [("sup_b", 1), ("sup_b", 2), ("sup_c", 1), ("sup_c", 2)]

    assert run["state"] == "RECOMMENDED" and run["pending_human"] is None
    assert run["recommendation"]["recommended_supplier_id"] == "sup_b"
    assert run["recommendation"]["change_explanation"]
    assert "Landed cost of sup_b changed from 27,618.42 to 26,763.86" in run["recommendation"]["change_explanation"]
    threads = run["negotiations"]
    assert set(threads) == {"sup_b", "sup_c"}
    assert all(t["status"] == "accepted" and len(t["turns"]) == 4 for t in threads.values())
    assert all(t["turns"][0]["approved_by_human"] and not t["turns"][1]["approved_by_human"] for t in threads.values())
    assert threads["sup_c"]["current_offer"] == {"unit_price": "12.45", "lead_time_days": 9}
    assert client.get(f"/runs/{run_id}/negotiations").json() == threads
    assert client.get(f"/runs/{run_id}").json()["negotiations"] == threads
    assert {q["supplier_id"]: q["negotiated_offer"] for q in run["quotes"]} == {
        "sup_a": None,
        "sup_b": {"unit_price": "12.40", "lead_time_days": 10},
        "sup_c": {"unit_price": "12.45", "lead_time_days": 9},
    }
    # settled threads cannot be reopened
    assert client.post(f"/runs/{run_id}/negotiate").json()["code"] == "nothing_to_negotiate"


def test_edited_message_naming_competitor_is_422(client):
    run_id = recommended(client)
    run = client.post(f"/runs/{run_id}/negotiate").json()
    before = client.get(f"/runs/{run_id}").json()
    n_events = len(client.get(f"/runs/{run_id}/events").json())

    edited = run["pending_human"]["details"]["draft"] + " Apex offered us better terms."
    r = client.post(f"/runs/{run_id}/negotiation/sup_b/approve", json={"message": edited})
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "policy_violation"
    assert body["violations"] and all(isinstance(v, str) for v in body["violations"])
    assert any("Apex" in v for v in body["violations"])

    after = client.get(f"/runs/{run_id}").json()
    assert after["state"] == "AWAITING_NEGOTIATION_APPROVAL"
    assert after["pending_human"] == before["pending_human"] and after["negotiations"] == before["negotiations"]
    assert after["negotiations"]["sup_b"]["turns"] == []
    new_events = client.get(f"/runs/{run_id}/events").json()[n_events:]
    assert [e["type"] for e in new_events] == ["negotiation.policy_blocked"]
    assert new_events[0]["payload"]["source"] == "human_edit"

    # an edit that keeps to the supplier's own figures goes through and is marked edited
    ok = "Dear Borealis team, could you do USD 11.78 per unit within 8 days?"
    r = client.post(f"/runs/{run_id}/negotiation/sup_b/approve", json={"message": ok})
    assert r.status_code == 200, r.text
    turns = r.json()["negotiations"]["sup_b"]["turns"]
    assert turns[0]["message"] == ok and turns[0]["approved_by_human"] and turns[0]["role"] == "buyer"
    assert turns[1]["role"] == "supplier" and not turns[1]["approved_by_human"]


def test_sse_replays_negotiation_events(client):
    run_id = recommended(client)
    run = client.post(f"/runs/{run_id}/negotiate").json()
    while run["state"] == "AWAITING_NEGOTIATION_APPROVAL":
        run = client.post(f"/runs/{run_id}/negotiation/{run['pending_human']['details']['supplier_id']}/approve").json()
    assert run["state"] == "RECOMMENDED"

    types = []
    with client.stream("GET", f"/runs/{run_id}/events/stream", params={"follow": "false"}) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("data: "):
                types.append(json.loads(line[len("data: "):])["type"])
    for t in ("negotiation.started", "negotiation.drafted", "negotiation.awaiting_approval", "negotiation.sent",
              "supplier.counter_offer", "negotiation.round_completed", "negotiation.closed", "rescoring.started"):
        assert t in types, t
    assert types.count("supplier.counter_offer") == 4 and types.count("negotiation.closed") == 2
    assert types[-1] == "recommendation.ready"


def negotiated(client: TestClient) -> str:
    """recommended() plus the whole negotiation loop auto-approved (B and C settled, B still first)."""
    run_id = recommended(client)
    run = client.post(f"/runs/{run_id}/negotiate").json()
    while run["state"] == "AWAITING_NEGOTIATION_APPROVAL":
        run = client.post(f"/runs/{run_id}/negotiation/{run['pending_human']['details']['supplier_id']}/approve", json={}).json()
    assert run["state"] == "RECOMMENDED" and run["recommendation"]["recommended_supplier_id"] == "sup_b"
    return run_id


def test_interrupt_over_http_flips_recommendation(client):
    run_id = negotiated(client)
    body = {"quantity": 5000, "budget": "75000", "reason": "Customer order upsized"}
    r = client.post(f"/runs/{run_id}/interrupt", json=body)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["state"] == "RECOMMENDED" and run["request"]["version"] == 2 and run["request"]["quantity"] == 5000
    assert run["request"]["budget"] == "75000.00" and run["pending_human"] is None
    assert [h["version"] for h in run["request_history"]] == [1] and run["request_history"][0]["quantity"] == 2000
    assert run["recommendation"]["recommended_supplier_id"] == "sup_c"
    assert [c["supplier_id"] for c in run["scorecards"]] == ["sup_c", "sup_a", "sup_b"]
    b = next(c for c in run["scorecards"] if c["supplier_id"] == "sup_b")
    assert not b["eligible"] and "capacity" in b["ineligibility_reasons"][0]
    impact = run["replan_impact"]
    assert impact["from_version"] == 1 and impact["to_version"] == 2
    assert impact["recommended_before"] == "sup_b" and impact["recommended_after"] == "sup_c"
    assert impact["changes"] == {"quantity": {"before": 2000, "after": 5000}, "budget": {"before": "30000.00", "after": "75000.00"}}
    assert {s["supplier_id"]: s["eligible_after"] for s in impact["per_supplier"]} == {"sup_a": True, "sup_b": False, "sup_c": True}
    assert "capacity" in run["recommendation"]["change_explanation"]
    assert next(q for q in run["quotes"] if q["supplier_id"] == "sup_b")["negotiated_offer"] == {"unit_price": "12.40", "lead_time_days": 10}
    assert client.get(f"/runs/{run_id}").json()["replan_impact"] == impact

    types = [e["type"] for e in client.get(f"/runs/{run_id}/events").json()]
    order = [t for t in types if t in ("requirement.changed", "replan.started", "replan.completed")]
    assert order == ["requirement.changed", "replan.started", "replan.completed"]
    assert types[-1] == "replan.completed"
    changed = next(e for e in client.get(f"/runs/{run_id}/events").json() if e["type"] == "requirement.changed")
    assert changed["actor"] == "human" and changed["payload"]["reason"] == "Customer order upsized"


def test_interrupt_errors_over_http(client):
    run_id = create(client)
    r = client.post(f"/runs/{run_id}/interrupt", json={"quantity": 5000})
    assert r.status_code == 409 and r.json()["code"] == "illegal_transition"
    assert client.post("/runs/run-nope/interrupt", json={"quantity": 5000}).status_code == 404
    assert client.post(f"/runs/{run_id}/interrupt", json={"quantity": 0}).status_code == 422

    run_id = recommended(client)
    for body in ({}, {"quantity": 2000}, {"reason": "nothing"}, {"budget": "30000.00"}):
        r = client.post(f"/runs/{run_id}/interrupt", json=body)
        assert r.status_code == 409 and r.json()["code"] == "no_change", body
    assert client.get(f"/runs/{run_id}").json()["request"]["version"] == 1


def test_interrupt_while_awaiting_approval_over_http(client):
    run_id = recommended(client)
    run = client.post(f"/runs/{run_id}/negotiate").json()
    assert run["state"] == "AWAITING_NEGOTIATION_APPROVAL"
    r = client.post(f"/runs/{run_id}/interrupt", json={"quantity": 5000, "budget": "75000"})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["state"] == "RECOMMENDED" and run["pending_human"] is None
    assert run["negotiations"]["sup_b"]["status"] == "closed"
    types = [e["type"] for e in client.get(f"/runs/{run_id}/events").json()]
    assert "negotiation.discarded" in types and types.index("negotiation.discarded") < types.index("replan.started")
    assert run["recommendation"]["recommended_supplier_id"] == "sup_c"


def test_interrupt_without_budget_escalates(client):
    run_id = negotiated(client)
    run = client.post(f"/runs/{run_id}/interrupt", json={"quantity": 5000}).json()
    assert run["state"] == "RECOMMENDED" and run["recommendation"]["recommended_supplier_id"] is None
    assert run["recommendation"]["escalation"]["reason"] == "no_eligible_supplier"
    assert all(not c["eligible"] for c in run["scorecards"])


# ----------------------------------------------------------------------------- PO gate (T15, G5)


def replanned(client: TestClient) -> str:
    """negotiated() plus the §15 interrupt: 5,000 units / 75,000 budget → Cobalt recommended."""
    run_id = negotiated(client)
    run = client.post(f"/runs/{run_id}/interrupt", json={"quantity": 5000, "budget": "75000"}).json()
    assert run["state"] == "RECOMMENDED" and run["recommendation"]["recommended_supplier_id"] == "sup_c"
    return run_id


def test_po_gate_over_http(client):
    run_id = replanned(client)
    assert client.get(f"/runs/{run_id}/po").status_code == 404
    assert client.get(f"/runs/{run_id}/po.pdf").status_code == 404

    r = client.post(f"/runs/{run_id}/request-po")
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["state"] == "AWAITING_PO_APPROVAL" and run["purchase_order"] is None
    preview = run["po_preview"]
    assert preview["po_number"] is None and preview["approved_by"] is None
    assert preview["supplier"] == {"supplier_id": "sup_c", "name": "Cobalt Industrial"}
    assert preview["line_items"] == [{"description": "Product X", "quantity": 5000, "unit_price": "12.45", "line_total": "62250.00"}]
    assert preview["totals"] == {"subtotal": "62250.00", "discount": "0.00", "shipping": "0.00", "tax": "5602.50", "total": "67852.50"}
    assert preview["negotiated"] is True and preview["lead_time_days"] == 9 and preview["request_version"] == 2
    c = next(card for card in run["scorecards"] if card["supplier_id"] == "sup_c")
    assert preview["totals"]["total"] == c["landed_cost"]
    pending = run["pending_human"]
    assert pending["kind"] == "po_approval"
    assert pending["details"] == {"supplier_id": "sup_c", "supplier_name": "Cobalt Industrial", "totals": preview["totals"],
                                  "unit_price": "12.45", "lead_time_days": 9, "negotiated": True, "request_version": 2}
    assert client.get(f"/runs/{run_id}/po").status_code == 404  # preview is not a PO

    assert client.post(f"/runs/{run_id}/approve-po", json={}).status_code == 422
    r = client.post(f"/runs/{run_id}/approve-po", json={"approved_by": "demo-user"})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["state"] == "PO_GENERATED" and run["po_preview"] is None and run["pending_human"] is None
    po = run["purchase_order"]
    # D25: PO-<YYYYMMDD>-<the 8 hex chars of the run id after "run-">
    assert po["po_number"].startswith("PO-") and po["po_number"].endswith(run_id.removeprefix("run-"))
    assert len(po["po_number"]) == 3 + 8 + 1 + 8
    assert po["approved_by"] == "demo-user" and po["approved_at"]
    assert po["totals"] == preview["totals"] and po["line_items"] == preview["line_items"]
    assert client.get(f"/runs/{run_id}/po").json() == po

    r = client.get(f"/runs/{run_id}/po.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and po["po_number"] in r.headers["content-disposition"]

    events = client.get(f"/runs/{run_id}/events").json()
    assert [e["type"] for e in events][-2:] == ["po.requested", "po.generated"]
    generated = events[-1]
    assert generated["actor"] == "human" and generated["state_after"] == "PO_GENERATED"
    assert generated["payload"]["purchase_order"]["po_number"] == po["po_number"]
    assert client.get("/runs").json()[-1]["state"] == "PO_GENERATED"

    # terminal
    for path, body in (("interrupt", {"quantity": 6000}), ("negotiate", None), ("request-po", None),
                       ("approve-po", {"approved_by": "x"}), ("reject-po", {"reason": "late"}), ("evaluate", None)):
        r = client.post(f"/runs/{run_id}/{path}", json=body)
        assert r.status_code == 409 and r.json()["code"] == "illegal_transition", (path, r.text)
    assert client.get(f"/runs/{run_id}").json()["state"] == "PO_GENERATED"


def test_po_gate_errors_over_http(client):
    assert client.post("/runs/run-nope/request-po").status_code == 404
    assert client.get("/runs/run-nope/po").status_code == 404
    run_id = replanned(client)
    # approve straight from RECOMMENDED: no path around the gate
    r = client.post(f"/runs/{run_id}/approve-po", json={"approved_by": "demo-user"})
    assert r.status_code == 409 and r.json()["code"] == "illegal_transition"
    assert client.get(f"/runs/{run_id}").json()["purchase_order"] is None

    # ineligible recommendation (no budget increase → every supplier over budget)
    run_id = negotiated(client)
    run = client.post(f"/runs/{run_id}/interrupt", json={"quantity": 5000}).json()
    assert run["recommendation"]["recommended_supplier_id"] is None
    r = client.post(f"/runs/{run_id}/request-po")
    assert r.status_code == 409 and r.json()["code"] == "no_recommendation"


def test_reject_po_and_interrupt_from_po_gate_over_http(client):
    run_id = replanned(client)
    assert client.post(f"/runs/{run_id}/request-po").json()["state"] == "AWAITING_PO_APPROVAL"
    r = client.post(f"/runs/{run_id}/reject-po", json={"reason": "Ask Apex first"})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["state"] == "RECOMMENDED" and run["po_preview"] is None and run["pending_human"] is None
    rejected = client.get(f"/runs/{run_id}/events").json()[-1]
    assert rejected["type"] == "po.rejected" and rejected["actor"] == "human" and rejected["payload"]["reason"] == "Ask Apex first"
    # negotiation still callable afterwards (Apex has no thread yet)
    run = client.post(f"/runs/{run_id}/negotiate").json()
    assert run["state"] == "AWAITING_NEGOTIATION_APPROVAL" and run["pending_human"]["details"]["supplier_id"] == "sup_a"

    run_id = replanned(client)
    client.post(f"/runs/{run_id}/request-po")
    since = client.get(f"/runs/{run_id}/events").json()[-1]["seq"]
    run = client.post(f"/runs/{run_id}/interrupt", json={"quantity": 4000}).json()
    assert run["state"] == "RECOMMENDED" and run["request"]["version"] == 3 and run["po_preview"] is None
    types = [e["type"] for e in client.get(f"/runs/{run_id}/events", params={"since": since}).json()]
    assert types[:3] == ["requirement.changed", "po.discarded", "replan.started"] and types[-1] == "replan.completed"


def test_approve_po_totals_changed_over_http(client, monkeypatch):
    run_id = replanned(client)
    client.post(f"/runs/{run_id}/request-po")
    orch = client.app.state.orchestrator
    real = orch.engine

    def drifted(*args, **kwargs):
        result = real(*args, **kwargs)
        return result.model_copy(update={"validated": [v.model_copy(update={"tax": v.tax + 1}) for v in result.validated]})

    monkeypatch.setattr(orch, "engine", drifted)
    r = client.post(f"/runs/{run_id}/approve-po", json={"approved_by": "demo-user"})
    assert r.status_code == 409 and r.json()["code"] == "totals_changed"
    assert client.get(f"/runs/{run_id}").json()["state"] == "AWAITING_PO_APPROVAL"
    assert client.get(f"/runs/{run_id}/po").status_code == 404
