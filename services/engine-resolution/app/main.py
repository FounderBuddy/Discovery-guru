"""Resolution engine: ageing. Overdue open items escalate one level: owner, then solution owner, then client sponsor.

Cloud Scheduler calls /v1/tick daily on GCP; a person can also run it for one engagement.
No model involved: this engine applies policy, so its runs record promptVersion "policy-v1".
"""
from __future__ import annotations

from datetime import date

from fastapi import Depends, Request

from sdp_common.auth import Caller, caller_from
from sdp_common.clients import ledger
from sdp_common.contracts import Actor, ActorType, EngineRunRequest, EngineRunResponse
from sdp_common.engine_kit import Run
from sdp_common.service import create_app

ENGINE = "engine-resolution"
app = create_app(ENGINE, "Applies the ageing policy to open items.")
LEVELS = ["item owner", "solution owner", "client sponsor"]


def caller_dep(request: Request) -> Caller:
    return caller_from(request)


async def age(engagement_id: str, requested_by: Actor, correlation_id: str | None) -> EngineRunResponse:
    run = Run(ENGINE, "ageing", "policy-v1", engagement_id, requested_by, correlation_id)
    L = ledger()
    items = await L.get(f"/v1/engagements/{engagement_id}/open-items", actor=run.agent)
    overdue = [o for o in items if o["overdue"] and o["escalationLevel"] < 2]
    moved = []
    for o in overdue:
        r = await L.post(f"/v1/engagements/{engagement_id}/open-items/{o['id']}/escalate", actor=run.agent)
        moved.append(f"{o['question'][:60]} -> {LEVELS[r['escalationLevel']]}")
    return await run.finish(accepted=len(moved), rejected=0, input_summary=f"{len(items)} open items checked on {date.today().isoformat()}",
                            output_summary=f"{len(moved)} overdue item(s) escalated" + (": " + "; ".join(moved[:5]) if moved else ""))


@app.post("/v1/run", response_model=EngineRunResponse, tags=["engine"])
async def run_endpoint(req: EngineRunRequest, c: Caller = Depends(caller_dep)):
    return await age(req.engagementId, req.actor, req.correlationId)


@app.post("/v1/tick", tags=["engine"])
async def tick(c: Caller = Depends(caller_dep)):
    system = Actor(type=ActorType.system, id="scheduler")
    engagements = await ledger().get("/v1/engagements", actor=Actor(type=ActorType.agent, id=ENGINE))
    results = [(await age(e["id"], system, None)).model_dump() for e in engagements]
    return {"engagements": len(results), "escalated": sum(r["accepted"] for r in results)}
