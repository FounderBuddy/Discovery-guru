"""End-to-end test against a running platform (python scripts/dev.py --fresh, mock model).

Drives a whole discovery through the api-gateway as four different people, and calls the ledger directly
with engine credentials to prove the governance rules hold at the store, not just in the UI.

    pytest -q tests/e2e_test.py
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

GW = "http://127.0.0.1:8000"
LEDGER = "http://127.0.0.1:8001"
TOKENS = json.loads((Path(__file__).resolve().parent.parent / "data" / "tokens.json").read_text())

TRANSCRIPT = """Discovery call, Northwind Telecom (fictional), 3 March
[00:01:10] Dana Ruiz: Producing a customer report takes about 2 weeks and it is all done by hand in spreadsheets.
[00:03:40] Dana Ruiz: We want a single view of each customer so that sales and finance stop arguing about the numbers.
[00:05:02] Omar Haddad: Today the warehouse is Oracle and holds about 2 TB of data across six source systems.
[00:07:15] Omar Haddad: All production workloads must run on a multi-cloud footprint, that is a board policy.
[00:09:30] Dana Ruiz: Customer data must stay in Canada for residency and our SOC 2 audit.
[00:12:45] Omar Haddad: Actually the warehouse is closer to 9 TB once you count the archive tables we currently keep.
"""


def as_user(uid: str) -> httpx.Client:
    return httpx.Client(base_url=GW, headers={"X-Dev-User": uid}, timeout=120)


def ok(r: httpx.Response, code: int = 200) -> dict | list:
    assert r.status_code == code, f"{r.request.method} {r.request.url} -> {r.status_code}: {r.text[:400]}"
    return r.json() if r.content else None


@pytest.fixture(scope="module")
def world():
    owner, alex, priya, sam = as_user("u_yesser"), as_user("u_alex"), as_user("u_priya"), as_user("u_sam")
    e = ok(owner.post("/api/engagements", json={"name": "Northwind data platform", "client": "Northwind Telecom (fictional)",
                                                 "sector": "Telecommunications", "aiBudgetUsd": 10}), 201)
    eid = e["id"]
    ok(owner.put(f"/api/engagements/{eid}/members/u_alex", json={"role": "contributor"}))
    ok(owner.put(f"/api/engagements/{eid}/members/u_priya", json={"role": "reviewer"}))
    ok(owner.put(f"/api/engagements/{eid}/members/u_sam", json={"role": "observer"}))
    return {"eid": eid, "owner": owner, "alex": alex, "priya": priya, "sam": sam, "state": {}}


def test_1_ingest_and_extract(world):
    eid, alex = world["eid"], world["alex"]
    src = ok(alex.post(f"/api/engagements/{eid}/sources/text", json={"title": "Discovery call 1", "kind": "transcript", "text": TRANSCRIPT}), 201)
    world["state"]["source"] = src["id"]
    run = ok(alex.post(f"/api/engagements/{eid}/run/extraction", json={"sourceId": src["id"]}))
    assert run["status"] == "completed" and run["accepted"] >= 5
    assert run["rejected"] >= 1, "the citation check should have discarded the mock's invented quote"
    claims = ok(alex.get(f"/api/engagements/{eid}/claims", params={"status": "proposed"}))
    assert all(c["quote"] and c["sourceId"] == src["id"] for c in claims)


def test_2_agents_cannot_confirm_or_impersonate(world):
    eid = world["eid"]
    claim = ok(world["alex"].get(f"/api/engagements/{eid}/claims", params={"status": "proposed"}))[0]
    engine = {"Authorization": f"Bearer {TOKENS['engine-extraction']}", "X-Actor-Type": "agent", "X-Actor-Id": "engine-extraction"}
    r = httpx.post(f"{LEDGER}/v1/engagements/{eid}/claims/{claim['id']}/confirm", json={}, headers=engine)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "human_only"
    impersonate = {**engine, "X-Actor-Type": "user", "X-Actor-Id": "u_yesser"}
    r = httpx.post(f"{LEDGER}/v1/engagements/{eid}/claims/{claim['id']}/confirm", json={}, headers=impersonate)
    assert r.status_code == 403, "an engine must not be able to act as a person"
    bogus = {"claims": [{"statement": "x", "quote": "words that are nowhere in the transcript at all", "question": "from",
                         "kind": "estate_fact", "sourceId": world["state"]["source"]}]}
    r = httpx.post(f"{LEDGER}/v1/engagements/{eid}/claims/batch", json=bogus, headers=engine)
    assert r.json()["rejected"] and not r.json()["accepted"], "the ledger re-checks citations itself"


def test_3_roles(world):
    eid = world["eid"]
    claim = ok(world["sam"].get(f"/api/engagements/{eid}/claims", params={"status": "proposed"}))[0]
    r = world["sam"].post(f"/api/engagements/{eid}/claims/{claim['id']}/confirm", json={})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "role"
    r = as_user("u_stranger").get(f"/api/engagements/{eid}")
    assert r.status_code == 403


def test_4_confirm_and_mark_hard_rule(world):
    eid, alex = world["eid"], world["alex"]
    n = ok(alex.post(f"/api/engagements/{eid}/claims/confirm-high"))
    assert n["confirmed"] >= 5
    rules = ok(alex.get(f"/api/engagements/{eid}/rules"))
    multi = [r for cat in rules for r in cat["rules"] if "multi-cloud" in r["statement"].lower()]
    assert multi, "the multi-cloud policy should be a confirmed guardrail"
    ok(alex.patch(f"/api/engagements/{eid}/claims/{multi[0]['id']}", json={"strength": "hard"}))
    ok(alex.put(f"/api/engagements/{eid}/rules/strategic", json={"status": "yes", "notes": "Board policy"}))


def test_5_divergence_and_supersession(world):
    eid, alex, owner = world["eid"], world["alex"], world["owner"]
    run = ok(alex.post(f"/api/engagements/{eid}/run/divergence"))
    assert run["accepted"] >= 1, run
    div = ok(owner.get(f"/api/engagements/{eid}/divergences"))[0]
    r = alex.post(f"/api/engagements/{eid}/divergences/{div['id']}/resolve", json={"resolution": "b_correct"})
    assert r.status_code == 403, "only the solution owner resolves divergences"
    res = ok(owner.post(f"/api/engagements/{eid}/divergences/{div['id']}/resolve", json={"resolution": "b_correct", "note": "Archive tables count."}))
    dropped = ok(owner.get(f"/api/engagements/{eid}/claims/{div['claimA']}")) if False else None
    claims = {c["id"]: c for c in ok(owner.get(f"/api/engagements/{eid}/claims"))}
    assert claims[div["claimA"]]["status"] == "superseded" and claims[div["claimA"]]["supersededBy"] == div["claimB"]


def test_6_questions_and_ageing(world):
    eid, alex, owner = world["eid"], world["alex"], world["owner"]
    run = ok(alex.post(f"/api/engagements/{eid}/run/questions"))
    assert run["accepted"] >= 1
    item = ok(alex.get(f"/api/engagements/{eid}/open-items"))[0]
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    ok(alex.patch(f"/api/engagements/{eid}/open-items/{item['id']}", json={"dueDate": yesterday, "ownerId": "u_alex", "outcome": "estimate"}))
    assert alex.post(f"/api/engagements/{eid}/run/resolution").status_code == 403, "ageing is the solution owner's"
    run = ok(owner.post(f"/api/engagements/{eid}/run/resolution"))
    assert run["accepted"] >= 1
    item = next(o for o in ok(owner.get(f"/api/engagements/{eid}/open-items")) if o["id"] == item["id"])
    assert item["escalationLevel"] == 1


def test_7_recommendation_conflict_is_escalated(world):
    eid, alex, owner, priya = world["eid"], world["alex"], world["owner"], world["priya"]
    run = ok(alex.post(f"/api/engagements/{eid}/run/recommendation", json={"path": "Data platform"}))
    rec_id = run["details"]["recommendationId"]
    assert run["details"]["escalatedDecisions"], "a hard multi-cloud rule against a cloud-native practice must escalate"
    rec = next(r for r in ok(owner.get(f"/api/engagements/{eid}/recommendations")) if r["id"] == rec_id)
    assert rec["status"] == "blocked"
    r = priya.post(f"/api/engagements/{eid}/recommendations/{rec_id}/approve")
    assert r.status_code == 409, "a blocked recommendation can't be approved"
    escalated = run["details"]["escalatedDecisions"]
    assert alex.post(f"/api/engagements/{eid}/decisions/{escalated[0]}/decide", json={"decision": "x", "rationale": "y"}).status_code == 403
    for i, did in enumerate(escalated):
        if i == 0 and len(escalated) > 1:  # still blocked while any conflict is undecided
            ok(owner.post(f"/api/engagements/{eid}/decisions/{did}/decide", json={"decision": "Follow the client rule", "rationale": "Board policy."}))
            assert priya.post(f"/api/engagements/{eid}/recommendations/{rec_id}/approve").status_code == 409
            continue
        ok(owner.post(f"/api/engagements/{eid}/decisions/{did}/decide",
                      json={"decision": "Follow the client rule", "rationale": "Board policy outranks our default; recorded as a deviation."}))
    rec = ok(priya.post(f"/api/engagements/{eid}/recommendations/{rec_id}/approve"))
    assert rec["status"] == "approved"
    world["state"]["rec"] = rec_id


def test_8_lineage_outputs_audit_events(world):
    eid, owner = world["eid"], world["owner"]
    lin = ok(owner.get(f"/api/engagements/{eid}/lineage/recommendation/{world['state']['rec']}"))
    assert lin["upstream"]["claims"] and lin["upstream"]["run"]["promptVersion"] == "recommend_v1"
    assert any(h["action"] == "approved" for h in lin["history"])
    md = owner.get(f"/api/engagements/{eid}/outputs/discovery-summary")
    assert md.status_code == 200 and "## RULES" in md.text and "multi-cloud" in md.text
    csv = owner.get(f"/api/engagements/{eid}/outputs/claims-register")
    assert csv.text.startswith("id,status,question")
    runs = ok(owner.get(f"/api/engagements/{eid}/runs"))
    assert {r["engine"] for r in runs} >= {"engine-extraction", "engine-divergence", "engine-questions", "engine-recommendation", "engine-resolution"}
    assert all(r["promptVersion"] for r in runs)
    events = ok(owner.get(f"/api/engagements/{eid}/events"))
    types = {e["type"] for e in events}
    assert {"sdp.source.added.v1", "sdp.agent.run.completed.v1", "sdp.decision.escalated.v1"} <= types
    assert all(e["specversion"] == "1.0" for e in events)
    b = ok(owner.get(f"/api/engagements/{eid}/budget"))
    assert b["budgetUsd"] == 10
