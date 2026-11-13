"""Brief engine: drafts the Brief (purpose, value, measures, scope, non-goals, shape) from confirmed claims.

It never agrees anything. Slots without confirmed claims behind them stay blank, with the gaps listed, and an
agreed slot is only ever offered a suggestion. The shape it proposes picks which packs (question sets, FROM
checklists, practices) apply to the engagement.
"""
from __future__ import annotations

from fastapi import Depends, Request
from pydantic import ValidationError

from sdp_common.auth import Caller, caller_from
from sdp_common.clients import ServiceError, ledger
from sdp_common.contracts import BriefProposal, BriefSlotProposal, EngineRunRequest, EngineRunResponse
from sdp_common.engine_kit import Run, engine_dir, load_prompt, render
from sdp_common.service import create_app

ENGINE = "engine-brief"
app = create_app(ENGINE, "Drafts the engagement Brief and its shape from confirmed claims.")
PROMPT, PROMPT_VERSION = load_prompt(engine_dir(__file__), "brief_v1")


def caller_dep(request: Request) -> Caller:
    return caller_from(request)


@app.post("/v1/run", response_model=EngineRunResponse, tags=["engine"])
async def run_endpoint(req: EngineRunRequest, c: Caller = Depends(caller_dep)):
    eid = req.engagementId
    run = Run(ENGINE, "brief", PROMPT_VERSION, eid, req.actor, req.correlationId)
    L = ledger()
    ctx = await L.get(f"/v1/engagements/{eid}/context", actor=run.agent)
    claims = ctx["claims"]
    if not claims:
        return await run.finish(accepted=0, rejected=0, input_summary="0 confirmed claims",
                                output_summary="Nothing is confirmed yet, so the Brief stays blank.")
    packs = await L.get("/v1/packs", actor=run.agent)
    packs_txt = "\n".join(f"{p['key']}: {p['label']} | {p.get('description', '')} | signals: {', '.join(p.get('signals') or [])}"
                          for p in packs if p["key"] != "core")
    brief_txt = "\n".join(
        f"{s['key']} ({s['status'].upper()}): {s['text'] or '-'}" + (f" | shapes: {', '.join(s['shapes'])}" if s.get("shapes") else "")
        for s in ctx["brief"]["slots"])
    claims_txt = "\n".join(f"[{x['id']}] ({x['question']}/{x['kind']}) {x['statement']}" for x in claims[-300:])
    prompt = render(PROMPT, packs=packs_txt, brief=brief_txt, claims=claims_txt)
    try:
        out = await run.model(prompt, tier="complex")
    except ServiceError as e:
        return await run.finish(accepted=0, rejected=0, input_summary=f"{len(claims)} claims", output_summary="Model call failed.",
                                error=str(e.detail)[:400])
    confirmed = {x["id"] for x in claims}
    pack_keys = {p["key"] for p in packs} - {"core"}
    slots, rejected = [], []
    for raw in (out.get("slots") or []) if isinstance(out, dict) else []:
        try:
            s = BriefSlotProposal.model_validate({k: raw.get(k) for k in ("key", "text", "claimIds", "shapes", "gaps") if raw.get(k) is not None})
        except (ValidationError, AttributeError) as e:
            rejected.append({"raw": str(raw)[:200], "reason": f"Malformed slot: {str(e)[:120]}"})
            continue
        invented = [x for x in s.claimIds if x not in confirmed]
        s.claimIds = [x for x in s.claimIds if x in confirmed]
        s.shapes = [k for k in s.shapes if k in pack_keys]
        if invented:
            rejected.append({"key": s.key.value, "reason": f"Cited claims that are not confirmed: {', '.join(invented[:5])}"})
        slots.append(s)
    proposal = BriefProposal(slots=slots, runId=run.id, promptVersion=PROMPT_VERSION, model=run.model_name)
    result = await L.post(f"/v1/engagements/{eid}/brief/proposals", json=proposal.model_dump(mode="json"), actor=run.agent)
    rejected += result["rejected"]
    filled = [s["key"] for s in result["brief"]["slots"] if s["status"] != "empty"]
    return await run.finish(accepted=len(result["accepted"]), rejected=len(rejected), rejected_samples=rejected,
                            input_summary=f"{len(claims)} confirmed claims",
                            output_summary=f"{len(result['accepted'])} slot(s) drafted or suggested; {len(filled)} of 6 now filled; "
                                           f"shape: {', '.join(result['brief']['appliedPacks'][1:]) or 'not yet known'}",
                            details={"accepted": result["accepted"], "appliedPacks": result["brief"]["appliedPacks"]})
