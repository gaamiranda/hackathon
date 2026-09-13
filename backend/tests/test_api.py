"""HTTP layer in mock mode (TestClient, no network)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procureai.api.app import app

ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC = ROOT / "data" / "synthetic"
DOCS = ["supplier_a_apex.pdf", "supplier_b_borealis.xlsx", "supplier_c_cobalt.eml.txt"]
REQUEST = {"product": "Product X", "quantity": 2000, "required_by": "2026-09-29", "budget": "30000.00", "currency": "USD"}


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
    assert r.json() == {"mode": "mock", "llm_gateway": "unconfigured", "openclaw": "unconfigured", "runs": 0}


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
    files = [("files", ("supplier_c_cobalt.eml.txt", (SYNTHETIC / "supplier_c_cobalt.eml.txt").read_bytes()))]
    r = client.post(f"/runs/{run_id}/documents/{doc_id}/replace", files=files)
    assert r.status_code == 200, r.text
    run = r.json()
    assert [d["filename"] for d in run["documents"]] == ["supplier_c_cobalt.eml.txt"]
    assert [q["supplier_id"] for q in run["quotes"]] == ["sup_c"]
    assert run["state"] == "EXTRACTED"
    assert client.post(f"/runs/{run_id}/documents/nope/replace", files=files).status_code == 404


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
    assert [b["event"] for b in received] == [e["type"] for e in expected]
    assert json.loads(received[-1]["data"])["type"] == "recommendation.ready"
    assert received[-1]["event"] == "recommendation.ready"
