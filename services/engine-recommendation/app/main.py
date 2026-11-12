"""Recommendation engine: drafts one recommendation per path.

Authority: Datatonic practices first, then best practice, with hard client rules respected. Any conflict
between a practice and a client rule is escalated to the solution owner as a decision; the engine never
picks a side, and the recommendation stays blocked until the owner decides.
"""
from __future__ import annotations

import json

from fastapi import Depends, Request
from pydantic import ValidationError

from sdp_common.auth import Caller, caller_from
from sdp_common.clients import ServiceError, ledger
from sdp_common.contracts import EngineRunRequest, EngineRunResponse, RecommendationProposal
from sdp_common.engine_kit import Run, engine_dir, load_prompt, render
from sdp_common.events import make_event, publisher
from sdp_common.service import create_app

ENGINE = "engine-recommendation"
app = create_app(ENGINE, "Drafts recommendations per path; escalates practice-versus-client conflicts.")
PROMPT, PROMPT_VERSION = load_prompt(engine_dir(__file__), "recommend_v1")
FIELDS = ("recommendation", "rationale", "level", "practiceIds", "claimIds", "alternatives", "assumptions", "conflicts")


def caller_dep(request: Request) -> Caller:
    return caller_from(request)


@app.post("/v1/run", response_model=EngineRunResponse, tags=["engine"])
async def run_endpoint(req: EngineRunRequest, c: Caller = Depends(caller_dep)):
    eid, path = req.engagementId, str(req.params.get("path", "")).strip()
    run = Run(ENGINE, "recommendation", PROMPT_VERSION, eid, req.actor, req.correlationId)
    L = ledger()
    ctx = await L.get(f"/v1/engagements/{eid}/context", actor=run.agent)
    rules = [r for cat in ctx["rules"] for r in cat["rules"]]
    rules_txt = "\n".join(f"[{r['id']}] ({r.get('strength') or 'soft'}) {r['statement']}" for r in rules) or "none recorded"
    practices_txt = "\n".join(f"[{p['id']}] {p['domain']} - {p['title']}: {p['statement']}" for p in ctx["practices"]) or "none"
    claims_txt = "\n".join(f"[{x['id']}] ({x['question']}/{x['kind']}) {x['statement']}" for x in ctx["claims"][-250:]) or "none"
    prompt = render(PROMPT, path=path, profile=json.dumps(ctx["engagement"].get("profile") or {}),
                    rules=rules_txt, practices=practices_txt, claims=claims_txt)
    try:
        out = await run.model(prompt, tier="complex")
        proposal = RecommendationProposal.model_validate({**{k: out[k] for k in FIELDS if k in out}, "path": path,
                                                          "runId": run.id, "promptVersion": PROMPT_VERSION, "model": run.model_name})
    except ServiceError as e:
        return await run.finish(accepted=0, rejected=0, input_summary=path, output_summary="Model call failed.", error=str(e.detail)[:400])
    except (ValidationError, TypeError, AttributeError) as e:
        return await run.finish(accepted=0, rejected=1, input_summary=path, output_summary="The model's answer broke the contract.",
                                rejected_samples=[{"reason": str(e)[:300]}], error="contract")
    # keep only conflicts that name a real practice and a real client rule
    practice_ids = {p["id"] for p in ctx["practices"]}
    rule_ids = {r["id"] for r in rules}
    proposal.conflicts = [cf for cf in proposal.conflicts if cf.practiceId in practice_ids and cf.claimId in rule_ids]
    rec = await L.post(f"/v1/engagements/{eid}/recommendations", json=proposal.model_dump(mode="json"),
                       params={"requestedBy": req.actor.id}, actor=run.agent)
    practices = {p["id"]: p for p in ctx["practices"]}
    rule_by_id = {r["id"]: r for r in rules}
    escalated = []
    for cf in proposal.conflicts:
        p, r = practices[cf.practiceId], rule_by_id[cf.claimId]
        d = await L.post(f"/v1/engagements/{eid}/decisions/escalate", actor=run.agent, json={
            "title": f"{path}: client rule versus Datatonic practice",
            "context": f"{cf.description}\n\nClient rule ({r.get('strength') or 'soft'}): {r['statement']}\nDatatonic practice: {p['title']}: {p['statement']}",
            "options": [f"Follow the client rule: {r['statement'][:160]}", f"Follow the Datatonic practice: {p['title']}", "Another way, recorded in the rationale"],
            "relatedClaimIds": [cf.claimId], "practiceId": cf.practiceId, "recommendationId": rec["id"]})
        escalated.append(d["id"])
    return await run.finish(accepted=1, rejected=0, input_summary=f"Path: {path}; {len(ctx['claims'])} claims, {len(rules)} rules, {len(ctx['practices'])} practices",
                            output_summary=f"Recommendation {rec['id']} drafted at level {proposal.level.value}; {len(escalated)} conflict(s) escalated to the solution owner",
                            details={"recommendationId": rec["id"], "escalatedDecisions": escalated})
