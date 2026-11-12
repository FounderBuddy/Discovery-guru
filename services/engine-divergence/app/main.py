"""Divergence engine: finds confirmed claims that cannot both be true. Proposes; the solution owner resolves."""
from __future__ import annotations

from fastapi import Depends, Request
from pydantic import ValidationError

from sdp_common.auth import Caller, caller_from
from sdp_common.clients import ServiceError, ledger
from sdp_common.contracts import DivergenceProposal, EngineRunRequest, EngineRunResponse
from sdp_common.engine_kit import Run, engine_dir, load_prompt, render
from sdp_common.service import create_app

ENGINE = "engine-divergence"
app = create_app(ENGINE, "Detects contradictions between confirmed claims across sources.")
PROMPT, PROMPT_VERSION = load_prompt(engine_dir(__file__), "diverge_v1")
MAX_CLAIMS = 300


def caller_dep(request: Request) -> Caller:
    return caller_from(request)


def claim_line(c: dict, sources: dict) -> str:
    num = f" | number: {c['number']['value']:g} {c['number'].get('unit', '')}" if c.get("number") else ""
    src = sources.get(c.get("sourceId"), "entered by a person")
    return f"[{c['id']}] ({c['question']}/{c['kind']}) {c['statement']}{num} | quote: \"{c.get('quote', '')[:240]}\" | source: {src} | speaker: {c.get('speaker') or 'unknown'}"


@app.post("/v1/run", response_model=EngineRunResponse, tags=["engine"])
async def run_endpoint(req: EngineRunRequest, c: Caller = Depends(caller_dep)):
    eid = req.engagementId
    run = Run(ENGINE, "divergence", PROMPT_VERSION, eid, req.actor, req.correlationId)
    L = ledger()
    ctx = await L.get(f"/v1/engagements/{eid}/context", actor=run.agent)
    sources = {s["id"]: s["title"] for s in await L.get(f"/v1/engagements/{eid}/sources", actor=run.agent)}
    claims = ctx["claims"][-MAX_CLAIMS:]
    if len(claims) < 2:
        return await run.finish(accepted=0, rejected=0, input_summary=f"{len(claims)} confirmed claims",
                                output_summary="Nothing to compare yet: confirm at least two claims.")
    known = "\n".join(f"{d['claimA']} / {d['claimB']}" for d in ctx["divergences"]) or "none"
    try:
        out = await run.model(render(PROMPT, known=known, claims="\n".join(claim_line(x, sources) for x in claims)), tier="complex")
    except ServiceError as e:
        return await run.finish(accepted=0, rejected=0, input_summary=f"{len(claims)} claims", output_summary="Model call failed.", error=str(e.detail)[:400])
    proposals, rejected = [], []
    for raw in (out.get("divergences") or []) if isinstance(out, dict) else []:
        try:
            proposals.append(DivergenceProposal(claimA=raw["a"], claimB=raw["b"], type=raw["type"], severity=raw["severity"],
                                                explanation=raw["explanation"], runId=run.id).model_dump())
        except (KeyError, ValidationError, TypeError):
            rejected.append({"raw": str(raw)[:200], "reason": "Malformed divergence."})
    result = await L.post(f"/v1/engagements/{eid}/divergences/batch", json={"divergences": proposals}, actor=run.agent) if proposals else {"accepted": [], "rejected": []}
    rejected += result["rejected"]
    return await run.finish(accepted=len(result["accepted"]), rejected=len(rejected), rejected_samples=rejected,
                            input_summary=f"{len(claims)} confirmed claims compared",
                            output_summary=f"{len(result['accepted'])} new divergences for the solution owner to resolve")
