"""Extraction engine: source text in, proposed claims out. Every claim must quote the source verbatim.

Triggered by a person (POST /v1/run) or by sdp.source.added.v1 on the bus when AUTO_EXTRACT=true.
It checks every citation itself, and the ledger checks again: no citation, no claim.
"""
from __future__ import annotations

import os

from fastapi import BackgroundTasks, Depends, Request
from pydantic import ValidationError

from sdp_common.auth import Caller, caller_from
from sdp_common.clients import ServiceError, ledger
from sdp_common.contracts import Actor, ActorType, ClaimProposal, EngineRunRequest, EngineRunResponse, StakeholderProposal
from sdp_common.engine_kit import Run, engine_dir, load_prompt, render
from sdp_common.events import CloudEvent
from sdp_common.service import create_app
from sdp_common.text import split_parts, verify_quote

ENGINE = "engine-extraction"
app = create_app(ENGINE, "Extracts cited claims from a source. Proposes only; people confirm.")
PROMPT, PROMPT_VERSION = load_prompt(engine_dir(__file__), "extract_v1")
PART_SIZE = int(os.getenv("EXTRACT_PART_CHARS", "18000"))
FIELDS = ("statement", "quote", "question", "kind", "ruleCategory", "number", "speaker", "confidence")


def caller_dep(request: Request) -> Caller:
    return caller_from(request)


async def extract(engagement_id: str, source_id: str, requested_by: Actor, correlation_id: str | None) -> EngineRunResponse:
    run = Run(ENGINE, "extraction", PROMPT_VERSION, engagement_id, requested_by, correlation_id)
    L = ledger()
    src = await L.get(f"/v1/engagements/{engagement_id}/sources/{source_id}", params={"includeText": "true"}, actor=run.agent)
    eng = await L.get(f"/v1/engagements/{engagement_id}", actor=run.agent)
    await L.patch(f"/v1/engagements/{engagement_id}/sources/{source_id}/status", json={"status": "extracting"}, actor=run.agent)
    parts = split_parts(src["text"], PART_SIZE)
    proposals: list[dict] = []
    rejected: list[dict] = []
    people: list[dict] = []
    try:
        for i, part in enumerate(parts, start=1):
            prompt = render(PROMPT, engagement=f"{eng['name']} for {eng['client']}", title=src["title"],
                            kind=src["kind"], part=i, parts=len(parts), text=part)
            out = await run.model(prompt, tier="default")
            for raw in (out.get("claims") or []) if isinstance(out, dict) else []:
                if not isinstance(raw, dict):
                    continue
                raw = {k: raw[k] for k in FIELDS if raw.get(k) not in (None, "")}  # models add stray keys; keep the contract's
                try:
                    p = ClaimProposal.model_validate({**raw, "sourceId": source_id, "part": i, "runId": run.id,
                                                      "promptVersion": PROMPT_VERSION, "model": run.model_name,
                                                      "provider": run.provider})
                except ValidationError as e:
                    rejected.append({"statement": str(raw.get("statement", ""))[:200], "reason": f"Malformed claim: {e.errors()[0]['msg']}"})
                    continue
                if not verify_quote(p.quote, part):  # first check here; the ledger checks again
                    rejected.append({"statement": p.statement[:200], "quote": p.quote[:200], "reason": "Quote not found in the source part."})
                    continue
                proposals.append(p.model_dump(mode="json"))
            for person in (out.get("people") or []) if isinstance(out, dict) else []:
                if isinstance(person, dict) and person.get("name"):
                    people.append(StakeholderProposal.model_validate({**{k: person.get(k, "") or "" for k in ("name", "title", "organisation", "type")},
                                                                      "sourceId": source_id}).model_dump())
        result = {"accepted": [], "rejected": []}
        if proposals:
            result = await L.post(f"/v1/engagements/{engagement_id}/claims/batch", json={"claims": proposals}, actor=run.agent)
        rejected += result["rejected"]
        if people:
            await L.post(f"/v1/engagements/{engagement_id}/stakeholders", json=people, actor=run.agent)
        accepted = len(result["accepted"])
        await L.patch(f"/v1/engagements/{engagement_id}/sources/{source_id}/status",
                      json={"status": "extracted", "proposedClaims": accepted, "rejectedClaims": len(rejected)}, actor=run.agent)
        return await run.finish(accepted=accepted, rejected=len(rejected), rejected_samples=rejected,
                                input_summary=f"{src['title']}: {src['chars']} characters in {len(parts)} part(s)",
                                output_summary=f"{accepted} claims proposed for review, {len(rejected)} discarded by the citation check, {len(people)} people noted",
                                details={"sourceId": source_id})
    except ServiceError as e:
        await L.patch(f"/v1/engagements/{engagement_id}/sources/{source_id}/status", json={"status": "failed"}, actor=run.agent)
        return await run.finish(accepted=0, rejected=len(rejected), input_summary=src["title"],
                                output_summary="Extraction failed.", error=str(e.detail)[:400])


@app.post("/v1/run", response_model=EngineRunResponse, tags=["engine"])
async def run_endpoint(req: EngineRunRequest, c: Caller = Depends(caller_dep)):
    return await extract(req.engagementId, req.params["sourceId"], req.actor, req.correlationId)


@app.post("/v1/events", status_code=202, tags=["engine"])
async def on_event(event: CloudEvent, bg: BackgroundTasks, c: Caller = Depends(caller_dep)):
    if event.type == "sdp.source.added.v1" and os.getenv("AUTO_EXTRACT", "false") == "true":
        actor = Actor(type=ActorType(event.sdpactortype or "system"), id=event.sdpactorid or "event-bus")
        bg.add_task(extract, event.sdpengagement, event.data["sourceId"], actor, event.sdpcorrelation)
        return {"queued": True}
    return {"queued": False}
