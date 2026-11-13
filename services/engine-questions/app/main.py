"""Question engine: proposes the open questions that matter, typed by what they demand, with a suggested resolution."""
from __future__ import annotations

import json

from fastapi import Depends, Request
from pydantic import ValidationError

from sdp_common.auth import Caller, caller_from
from sdp_common.clients import ServiceError, ledger
from sdp_common.contracts import EngineRunRequest, EngineRunResponse, OpenItemProposal
from sdp_common.engine_kit import Run, brief_text, engine_dir, load_prompt, render
from sdp_common.service import create_app

ENGINE = "engine-questions"
app = create_app(ENGINE, "Proposes open questions that demand a number, constraint, owner, decision or artefact.")
PROMPT, PROMPT_VERSION = load_prompt(engine_dir(__file__), "questions_v2")


def caller_dep(request: Request) -> Caller:
    return caller_from(request)


@app.post("/v1/run", response_model=EngineRunResponse, tags=["engine"])
async def run_endpoint(req: EngineRunRequest, c: Caller = Depends(caller_dep)):
    eid = req.engagementId
    run = Run(ENGINE, "questions", PROMPT_VERSION, eid, req.actor, req.correlationId)
    L = ledger()
    ctx = await L.get(f"/v1/engagements/{eid}/context", actor=run.agent)
    rules = "\n".join(f"- {r['category']} ({r['label']}): {r['status']}" for r in ctx["rules"])
    claims = "\n".join(
        f"[{x['id']}] ({x['question']}/{x['kind']}) {x['statement']}" + (f" | number: {x['number']['value']:g} {x['number'].get('unit','')}" if x.get("number") else "")
        for x in ctx["claims"][-250:]) or "none yet"
    existing = "\n".join(f"- {o['question']}" for o in ctx["openItems"]) or "none"
    packs = ctx["brief"]["packs"]
    mustknow = "\n".join(f"- {m} ({p['label']})" for p in packs for m in p.get("mustKnow") or []) or "none"
    askabout = "\n".join(f"- {a} ({p['label']})" for p in packs for a in p.get("askAbout") or []) or "none"
    prompt = render(PROMPT, profile=json.dumps(ctx["engagement"].get("profile") or {}), brief=brief_text(ctx),
                    mustknow=mustknow, askabout=askabout, rules=rules, claims=claims, existing=existing)
    try:
        out = await run.model(prompt, tier="default")
    except ServiceError as e:
        return await run.finish(accepted=0, rejected=0, input_summary="context", output_summary="Model call failed.", error=str(e.detail)[:400])
    items, rejected = [], []
    for raw in (out.get("items") or []) if isinstance(out, dict) else []:
        try:
            items.append(OpenItemProposal.model_validate({**{k: raw.get(k) for k in ("question", "kind", "outcome", "impact", "askRole", "relatedClaimIds") if raw.get(k) is not None},
                                                          "runId": run.id}).model_dump(mode="json"))
        except (ValidationError, AttributeError):
            rejected.append({"raw": str(raw)[:200], "reason": "Malformed open item."})
    result = await L.post(f"/v1/engagements/{eid}/open-items/batch", json={"items": items}, actor=run.agent) if items else {"accepted": []}
    return await run.finish(accepted=len(result["accepted"]), rejected=len(rejected), rejected_samples=rejected,
                            input_summary=f"{len(ctx['claims'])} confirmed claims, {len(ctx['openItems'])} open items, packs: {', '.join(ctx['brief']['appliedPacks'])}",
                            output_summary=f"{len(result['accepted'])} new open items proposed")
