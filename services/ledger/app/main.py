"""Ledger service: the only store. Every other service reads and writes through this API.

Claims are never overwritten: status changes and edits are recorded in the append-only history table,
and supersession links a claim to its replacement so lineage survives every correction.
"""
from __future__ import annotations

import json
import os
from datetime import date
from typing import Any, Literal, Optional

from fastapi import BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from sdp_common.auth import Caller, caller_from
from sdp_common.contracts import (ActorType, AgentRunRecord, BriefProposal, BriefSlotKey, ClaimProposal, DivergenceProposal,
                                  OpenItemProposal, RecommendationProposal, RuleCategory,
                                  StakeholderProposal)
from sdp_common.events import make_event, publisher
from sdp_common.service import create_app
from sdp_common.text import verify_quote

from .authz import require, require_practice_admin
from .db import (AgentRun, BriefSlot, Claim, Decision, Divergence, Engagement, History, Member, OpenItem,
                 Practice, Recommendation, RuleStatus, SessionLocal, Source, SourceChunk,
                 Stakeholder, User, init_db, new_id, now, snake)

SERVICE = "ledger"
app = create_app(SERVICE, "The only store: engagements, evidence, claims, decisions and their lineage.")
CHUNK = 60000
BRIEF_KEYS = [k.value for k in BriefSlotKey]
BRIEF_LABELS = {"purpose": "Purpose", "value": "Business value", "measures": "Success measures",
                "scope": "In scope", "nonGoals": "Out of scope", "shape": "Engagement shape"}


def load_packs() -> dict:
    import yaml
    path = os.getenv("SDP_PACKS") or os.path.join(os.path.dirname(__file__), "../../../config/packs.yaml")
    with open(path) as f:
        return yaml.safe_load(f)["packs"]


PACKS = load_packs()
RULE_LABELS = {
    "regulatory": "Regulatory and compliance", "security": "Security policy",
    "strategic": "Strategic positions", "architecture": "Architectural standards",
    "organisational": "Organisational and governance", "commercial": "Commercial",
    "operational": "Operational",
}


# ------------------------------------------------------------------ plumbing

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def caller_dep(request: Request) -> Caller:
    return caller_from(request)


class In(BaseModel):
    model_config = ConfigDict(extra="forbid")


def hist(db: Session, c: Caller, eng: str | None, entity: str, entity_id: str, action: str, payload: Any = None):
    db.add(History(engagement_id=eng, entity=entity, entity_id=entity_id, action=action,
                   actor_type=c.actor.type.value, actor_id=c.actor.id, via_service=c.service,
                   payload=payload or {}))


def emit(bg: BackgroundTasks, etype: str, data: dict, eng: str, c: Caller, subject: str | None = None):
    bg.add_task(publisher.publish, make_event(etype, SERVICE, data, engagement_id=eng, actor=c.actor,
                                              correlation_id=c.correlation_id, subject=subject))


def is_user(c: Caller) -> bool:
    return c.actor.type == ActorType.user


def get_or_404(db: Session, model, key, eng: str | None = None, what: str = "Item"):
    obj = db.get(model, key)
    if obj is None or (eng is not None and getattr(obj, "engagement_id", eng) != eng):
        raise HTTPException(404, {"code": "not_found", "message": f"{what} not found."})
    return obj


def apply(obj, data: dict):
    for k, v in data.items():
        setattr(obj, snake(k), v)


@app.on_event("startup")
def startup():
    init_db()
    db = SessionLocal()
    try:
        if db.scalar(select(func.count()).select_from(User)) == 0 and os.getenv("SDP_DEV_USERS"):
            for u in json.loads(os.environ["SDP_DEV_USERS"]):
                db.add(User(id=u["id"], name=u.get("name", ""), email=u.get("email", ""),
                            is_practice_admin=bool(u.get("isPracticeAdmin"))))
        seed = os.getenv("SDP_SEED_PRACTICES")
        if seed and os.path.exists(seed) and db.scalar(select(func.count()).select_from(Practice)) == 0:
            for p in json.load(open(seed)):
                db.add(Practice(id=new_id("prc"), domain=p["domain"], title=p["title"], statement=p["statement"],
                                rationale=p.get("rationale", ""), status=p.get("status", "draft"),
                                applies_to=p.get("appliesTo", []), updated_by="seed"))
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------ users (directory)

class UserIn(In):
    id: str
    name: str = ""
    email: str = ""
    isPracticeAdmin: Optional[bool] = None


@app.get("/v1/users", tags=["users"])
def search_users(q: str = "", db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    stmt = select(User)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(User.name).like(like), func.lower(User.email).like(like)))
    return [u.to_api() for u in db.scalars(stmt.order_by(User.name).limit(8))]


@app.get("/v1/users/{user_id}", tags=["users"])
def get_user(user_id: str, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    return get_or_404(db, User, user_id, what="User").to_api()


@app.post("/v1/users", tags=["users"])
def upsert_user(body: UserIn, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    if c.service != "api-gateway":
        raise HTTPException(403, {"code": "forbidden", "message": "Only the api-gateway registers users."})
    u = db.get(User, body.id) or User(id=body.id)
    u.name, u.email = body.name or u.name, body.email or u.email
    if body.isPracticeAdmin is not None:
        u.is_practice_admin = body.isPracticeAdmin
    db.merge(u)
    db.commit()
    return db.get(User, body.id).to_api()


# ------------------------------------------------------------------ engagements & members

class EngagementIn(In):
    name: str
    client: str = ""
    sector: str = ""
    description: str = ""
    aiBudgetUsd: float = 25.0


class EngagementPatch(In):
    name: Optional[str] = None
    client: Optional[str] = None
    sector: Optional[str] = None
    description: Optional[str] = None
    aiBudgetUsd: Optional[float] = None


class ProfileIn(In):
    market: Literal["b2b", "b2c", "both", ""] = ""
    audiences: str = ""
    notes: str = ""


class MemberIn(In):
    role: Literal["owner", "contributor", "reviewer", "observer"]


def engagement_view(db: Session, e: Engagement, c: Caller) -> dict:
    out = e.to_api()
    members = db.scalars(select(Member).where(Member.engagement_id == e.id)).all()
    out["members"] = [m.to_api() for m in members]
    out["myRole"] = next((m.role for m in members if is_user(c) and m.user_id == c.actor.id), None)
    return out


@app.get("/v1/engagements", tags=["engagements"])
def list_engagements(db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    stmt = select(Engagement).order_by(Engagement.created_at.desc())
    if is_user(c):
        stmt = stmt.join(Member, Member.engagement_id == Engagement.id).where(Member.user_id == c.actor.id)
    else:
        require(db, c, None, "read")
    return [engagement_view(db, e, c) for e in db.scalars(stmt)]


@app.post("/v1/engagements", tags=["engagements"], status_code=201)
def create_engagement(body: EngagementIn, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    if not is_user(c):
        raise HTTPException(403, {"code": "human_only", "message": "Only a person opens an engagement."})
    require(db, c, None, "read")
    e = Engagement(id=new_id("eng"), name=body.name, client=body.client, sector=body.sector,
                   description=body.description, ai_budget_usd=body.aiBudgetUsd,
                   owner_id=c.actor.id, created_by=c.actor.id, profile={})
    db.add(e)
    db.add(Member(engagement_id=e.id, user_id=c.actor.id, role="owner", added_by=c.actor.id))
    for cat in RULE_LABELS:
        db.add(RuleStatus(engagement_id=e.id, category=cat, status="unknown", source_ids=[]))
    for key in BRIEF_KEYS:  # the Brief starts blank
        db.add(BriefSlot(id=new_id("brf"), engagement_id=e.id, key=key))
    hist(db, c, e.id, "engagement", e.id, "created", body.model_dump())
    db.commit()
    return engagement_view(db, e, c)


@app.get("/v1/engagements/{eid}", tags=["engagements"])
def get_engagement(eid: str, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "read")
    return engagement_view(db, get_or_404(db, Engagement, eid, what="Engagement"), c)


@app.patch("/v1/engagements/{eid}", tags=["engagements"])
def patch_engagement(eid: str, body: EngagementPatch, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "engagement.update")
    e = get_or_404(db, Engagement, eid, what="Engagement")
    data = body.model_dump(exclude_none=True)
    apply(e, data)
    hist(db, c, eid, "engagement", eid, "updated", data)
    db.commit()
    return engagement_view(db, e, c)


@app.put("/v1/engagements/{eid}/profile", tags=["engagements"])
def put_profile(eid: str, body: ProfileIn, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "rules.write")
    e = get_or_404(db, Engagement, eid, what="Engagement")
    e.profile = body.model_dump()
    hist(db, c, eid, "engagement", eid, "profile_updated", e.profile)
    db.commit()
    return engagement_view(db, e, c)


@app.put("/v1/engagements/{eid}/members/{user_id}", tags=["engagements"])
def put_member(eid: str, user_id: str, body: MemberIn, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "members.manage")
    e = get_or_404(db, Engagement, eid, what="Engagement")
    get_or_404(db, User, user_id, what="User")
    if body.role == "owner" and e.owner_id != user_id:  # one solution owner: hand over
        prev = db.get(Member, {"engagement_id": eid, "user_id": e.owner_id})
        if prev:
            prev.role = "contributor"
        e.owner_id = user_id
    m = db.get(Member, {"engagement_id": eid, "user_id": user_id})
    if m is None:
        m = Member(engagement_id=eid, user_id=user_id, role=body.role, added_by=c.actor.id)
        db.add(m)
    else:
        if m.role == "owner" and body.role != "owner":
            raise HTTPException(409, {"code": "owner_required", "message": "Hand ownership to someone else first."})
        m.role = body.role
    hist(db, c, eid, "member", user_id, "role_set", {"role": body.role})
    db.commit()
    return engagement_view(db, e, c)


@app.delete("/v1/engagements/{eid}/members/{user_id}", tags=["engagements"])
def delete_member(eid: str, user_id: str, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "members.manage")
    e = get_or_404(db, Engagement, eid, what="Engagement")
    if e.owner_id == user_id:
        raise HTTPException(409, {"code": "owner_required", "message": "The solution owner can't be removed. Hand ownership over first."})
    m = db.get(Member, {"engagement_id": eid, "user_id": user_id})
    if m:
        db.delete(m)
        hist(db, c, eid, "member", user_id, "removed")
        db.commit()
    return engagement_view(db, e, c)


# ------------------------------------------------------------------ stakeholders

class StakeholderIn(In):
    name: str
    title: str = ""
    organisation: str = ""
    type: str = ""
    decides: str = ""
    status: Optional[Literal["proposed", "confirmed"]] = None


@app.get("/v1/engagements/{eid}/stakeholders", tags=["people"])
def list_stakeholders(eid: str, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "read")
    return [s.to_api() for s in db.scalars(select(Stakeholder).where(Stakeholder.engagement_id == eid).order_by(Stakeholder.name))]


@app.post("/v1/engagements/{eid}/stakeholders", tags=["people"], status_code=201)
def add_stakeholders(eid: str, body: list[StakeholderProposal] | StakeholderIn, db: Session = Depends(get_db),
                     c: Caller = Depends(caller_dep)):
    if is_user(c):
        require(db, c, eid, "stakeholders.write")
        s = Stakeholder(id=new_id("stk"), engagement_id=eid, origin="human", status="confirmed",
                        **{k: v for k, v in body.model_dump().items() if k != "status"})
        db.add(s)
        hist(db, c, eid, "stakeholder", s.id, "added")
        db.commit()
        return [s.to_api()]
    require(db, c, eid, "stakeholders.propose")
    existing = {s.name.strip().lower() for s in db.scalars(select(Stakeholder).where(Stakeholder.engagement_id == eid))}
    added = []
    for p in body if isinstance(body, list) else []:
        key = p.name.strip().lower()
        if not key or key in existing:
            continue
        existing.add(key)
        s = Stakeholder(id=new_id("stk"), engagement_id=eid, name=p.name.strip(), title=p.title,
                        organisation=p.organisation, type=p.type, status="proposed", origin="agent",
                        source_id=p.sourceId)
        db.add(s)
        hist(db, c, eid, "stakeholder", s.id, "proposed")
        added.append(s)
    db.commit()
    return [s.to_api() for s in added]


@app.patch("/v1/engagements/{eid}/stakeholders/{sid}", tags=["people"])
def patch_stakeholder(eid: str, sid: str, body: StakeholderIn, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "stakeholders.write")
    s = get_or_404(db, Stakeholder, sid, eid, "Stakeholder")
    data = body.model_dump(exclude_none=True)
    apply(s, data)
    hist(db, c, eid, "stakeholder", sid, "updated", data)
    db.commit()
    return s.to_api()


@app.delete("/v1/engagements/{eid}/stakeholders/{sid}", tags=["people"], status_code=204)
def delete_stakeholder(eid: str, sid: str, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "stakeholders.write")
    db.delete(get_or_404(db, Stakeholder, sid, eid, "Stakeholder"))
    hist(db, c, eid, "stakeholder", sid, "deleted")
    db.commit()


# ------------------------------------------------------------------ sources

class SourceIn(In):
    title: str
    kind: str = "other"
    ruleCategory: Optional[RuleCategory] = None
    fileName: str = ""
    text: str = Field(min_length=1)


class SourceStatusIn(In):
    status: Literal["new", "extracting", "extracted", "failed"]
    proposedClaims: Optional[int] = None
    rejectedClaims: Optional[int] = None


def source_text(db: Session, sid: str) -> str:
    chunks = db.scalars(select(SourceChunk).where(SourceChunk.source_id == sid).order_by(SourceChunk.idx))
    return "".join(ch.text for ch in chunks)


@app.post("/v1/engagements/{eid}/sources", tags=["evidence"], status_code=201)
def add_source(eid: str, body: SourceIn, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "sources.write")
    if len(body.text) > 2_000_000:
        raise HTTPException(413, {"code": "too_large", "message": "Sources are limited to 2 million characters. Split the file."})
    s = Source(id=new_id("src"), engagement_id=eid, title=body.title, kind=body.kind,
               rule_category=body.ruleCategory.value if body.ruleCategory else None,
               file_name=body.fileName, chars=len(body.text), created_by=c.actor.id)
    db.add(s)
    for i in range(0, len(body.text), CHUNK):
        db.add(SourceChunk(source_id=s.id, idx=i // CHUNK, text=body.text[i:i + CHUNK]))
    hist(db, c, eid, "source", s.id, "added", {"title": s.title, "chars": s.chars})
    db.commit()
    return s.to_api()


@app.get("/v1/engagements/{eid}/sources", tags=["evidence"])
def list_sources(eid: str, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "read")
    return [s.to_api() for s in db.scalars(select(Source).where(Source.engagement_id == eid).order_by(Source.created_at.desc()))]


@app.get("/v1/engagements/{eid}/sources/{sid}", tags=["evidence"])
def get_source(eid: str, sid: str, includeText: bool = False, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "read")
    out = get_or_404(db, Source, sid, eid, "Source").to_api()
    if includeText:
        out["text"] = source_text(db, sid)
    return out


@app.patch("/v1/engagements/{eid}/sources/{sid}/status", tags=["evidence"])
def set_source_status(eid: str, sid: str, body: SourceStatusIn, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "sources.write" if is_user(c) else "sources.status")
    s = get_or_404(db, Source, sid, eid, "Source")
    apply(s, body.model_dump(exclude_none=True))
    db.commit()
    return s.to_api()


@app.delete("/v1/engagements/{eid}/sources/{sid}", tags=["evidence"], status_code=204)
def delete_source(eid: str, sid: str, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "sources.delete")
    s = get_or_404(db, Source, sid, eid, "Source")
    cited = db.scalar(select(func.count()).select_from(Claim).where(Claim.source_id == sid, Claim.status == "confirmed"))
    if cited:
        raise HTTPException(409, {"code": "cited", "message": f"{cited} confirmed claims cite this source. Deleting it would break their lineage."})
    for ch in db.scalars(select(SourceChunk).where(SourceChunk.source_id == sid)):
        db.delete(ch)
    for cl in db.scalars(select(Claim).where(Claim.source_id == sid, Claim.status == "proposed")):
        db.delete(cl)
    db.delete(s)
    hist(db, c, eid, "source", sid, "deleted", {"title": s.title})
    db.commit()


# ------------------------------------------------------------------ claims

class HumanClaimIn(In):
    statement: str
    question: str
    kind: str
    ruleCategory: Optional[RuleCategory] = None
    number: Optional[dict] = None
    speaker: str = ""
    quote: str = ""
    sourceId: Optional[str] = None
    strength: Optional[Literal["hard", "soft"]] = None


class ClaimBatchIn(In):
    claims: list[ClaimProposal]


class ConfirmIn(In):
    strength: Optional[Literal["hard", "soft"]] = None


class RejectIn(In):
    reason: str = ""


class ClaimPatch(In):
    statement: Optional[str] = None
    question: Optional[str] = None
    kind: Optional[str] = None
    ruleCategory: Optional[RuleCategory] = None
    strength: Optional[Literal["hard", "soft"]] = None
    number: Optional[dict] = None


class SupersedeIn(In):
    byClaimId: Optional[str] = None
    reason: str


def _verify_citation(db: Session, eid: str, source_id: str | None, quote: str) -> Optional[str]:
    if not source_id:
        return "Agent claims must cite a source."
    src = db.get(Source, source_id)
    if not src or src.engagement_id != eid:
        return "Cited source does not exist in this engagement."
    if not verify_quote(quote, source_text(db, source_id)):
        return "Quote not found verbatim in the cited source."
    return None


@app.get("/v1/engagements/{eid}/claims", tags=["claims"])
def list_claims(eid: str, status: Optional[str] = None, question: Optional[str] = None,
                sourceId: Optional[str] = None, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "read")
    stmt = select(Claim).where(Claim.engagement_id == eid)
    if status:
        stmt = stmt.where(Claim.status.in_(status.split(",")))
    if question:
        stmt = stmt.where(Claim.question == question)
    if sourceId:
        stmt = stmt.where(Claim.source_id == sourceId)
    return [cl.to_api() for cl in db.scalars(stmt.order_by(Claim.created_at))]


@app.post("/v1/engagements/{eid}/claims", tags=["claims"], status_code=201)
def add_human_claim(eid: str, body: HumanClaimIn, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    if not is_user(c):
        raise HTTPException(403, {"code": "forbidden", "message": "Agents propose claims through /claims/batch."})
    require(db, c, eid, "claims.propose")
    evidence = "none"
    if body.sourceId or body.quote:
        err = _verify_citation(db, eid, body.sourceId, body.quote)
        if err:
            raise HTTPException(422, {"code": "citation_not_found", "message": err})
        evidence = "cited"
    cl = Claim(id=new_id("clm"), engagement_id=eid, statement=body.statement, quote=body.quote,
               question=body.question, kind=body.kind,
               rule_category=body.ruleCategory.value if body.ruleCategory else None,
               number=body.number, speaker=body.speaker, confidence="high", strength=body.strength,
               status="confirmed", origin="human", evidence=evidence, source_id=body.sourceId,
               created_by=c.actor.id, confirmed_by=c.actor.id, confirmed_at=now())
    db.add(cl)
    hist(db, c, eid, "claim", cl.id, "entered", {"evidence": evidence})
    db.commit()
    return cl.to_api()


@app.post("/v1/engagements/{eid}/claims/batch", tags=["claims"])
def propose_claims(eid: str, body: ClaimBatchIn, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    """Agents propose. The ledger re-checks every citation itself rather than trusting the engine."""
    require(db, c, eid, "claims.propose")
    accepted, rejected = [], []
    for p in body.claims:
        err = _verify_citation(db, eid, p.sourceId, p.quote)
        if err:
            rejected.append({"statement": p.statement, "quote": p.quote, "reason": err})
            continue
        cl = Claim(id=new_id("clm"), engagement_id=eid, statement=p.statement, quote=p.quote,
                   question=p.question.value, kind=p.kind.value,
                   rule_category=p.ruleCategory.value if p.ruleCategory else None,
                   number=p.number.model_dump() if p.number else None, speaker=p.speaker,
                   confidence=p.confidence, status="proposed",
                   origin="agent" if not is_user(c) else "human", evidence="cited",
                   source_id=p.sourceId, part=p.part, run_id=p.runId, prompt_version=p.promptVersion,
                   model=p.model, provider=p.provider, created_by=c.actor.id)
        db.add(cl)
        hist(db, c, eid, "claim", cl.id, "proposed", {"runId": p.runId})
        accepted.append(cl.id)
    db.commit()
    return {"accepted": accepted, "rejected": rejected}


def _review(db, c, eid, cid) -> Claim:
    require(db, c, eid, "claims.review")
    return get_or_404(db, Claim, cid, eid, "Claim")


@app.post("/v1/engagements/{eid}/claims/{cid}/confirm", tags=["claims"])
def confirm_claim(eid: str, cid: str, body: ConfirmIn, bg: BackgroundTasks, db: Session = Depends(get_db),
                  c: Caller = Depends(caller_dep)):
    cl = _review(db, c, eid, cid)
    if cl.status not in ("proposed", "rejected"):
        raise HTTPException(409, {"code": "state", "message": f"A {cl.status} claim can't be confirmed."})
    cl.status, cl.confirmed_by, cl.confirmed_at = "confirmed", c.actor.id, now()
    if body.strength:
        cl.strength = body.strength
    if cl.kind == "guardrail" and not cl.strength:
        cl.strength = "soft"  # no document behind it yet means soft until shown otherwise
    hist(db, c, eid, "claim", cid, "confirmed", body.model_dump(exclude_none=True))
    db.commit()
    emit(bg, "sdp.claim.confirmed.v1", {"claimId": cid, "status": "confirmed"}, eid, c, cid)
    return cl.to_api()


@app.post("/v1/engagements/{eid}/claims/confirm-high", tags=["claims"])
def confirm_high(eid: str, sourceId: Optional[str] = None, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "claims.review")
    stmt = select(Claim).where(Claim.engagement_id == eid, Claim.status == "proposed", Claim.confidence == "high")
    if sourceId:
        stmt = stmt.where(Claim.source_id == sourceId)
    n = 0
    for cl in db.scalars(stmt):
        cl.status, cl.confirmed_by, cl.confirmed_at = "confirmed", c.actor.id, now()
        if cl.kind == "guardrail" and not cl.strength:
            cl.strength = "soft"
        hist(db, c, eid, "claim", cl.id, "confirmed", {"bulk": True})
        n += 1
    db.commit()
    return {"confirmed": n}


@app.post("/v1/engagements/{eid}/claims/{cid}/reject", tags=["claims"])
def reject_claim(eid: str, cid: str, body: RejectIn, bg: BackgroundTasks, db: Session = Depends(get_db),
                 c: Caller = Depends(caller_dep)):
    cl = _review(db, c, eid, cid)
    if cl.status != "proposed":
        raise HTTPException(409, {"code": "state", "message": "Only a proposed claim can be rejected. Supersede a confirmed one instead."})
    cl.status, cl.rejected_reason = "rejected", body.reason
    hist(db, c, eid, "claim", cid, "rejected", body.model_dump())
    db.commit()
    emit(bg, "sdp.claim.rejected.v1", {"claimId": cid, "status": "rejected"}, eid, c, cid)
    return cl.to_api()


@app.patch("/v1/engagements/{eid}/claims/{cid}", tags=["claims"])
def patch_claim(eid: str, cid: str, body: ClaimPatch, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    cl = _review(db, c, eid, cid)
    data = body.model_dump(exclude_none=True, mode="json")
    before = {k: getattr(cl, snake(k)) for k in data}
    apply(cl, data)
    hist(db, c, eid, "claim", cid, "edited", {"before": before, "after": data})
    db.commit()
    return cl.to_api()


def _flag_downstream(db: Session, c: Caller, eid: str, claim_id: str, reason: str) -> dict:
    flagged = {"openItems": [], "recommendations": [], "decisions": [], "brief": []}
    for b in db.scalars(select(BriefSlot).where(BriefSlot.engagement_id == eid)):
        if claim_id in (b.claim_ids or []) and b.status in ("draft", "agreed"):
            b.status, b.review_reason = "needs_review", reason
            flagged["brief"].append(b.key)
            hist(db, c, eid, "brief", b.id, "needs_review", {"claimId": claim_id, "slot": b.key})
    for oi in db.scalars(select(OpenItem).where(OpenItem.engagement_id == eid)):
        if claim_id in (oi.related_claim_ids or []):
            oi.needs_review = True
            flagged["openItems"].append(oi.id)
            hist(db, c, eid, "openitem", oi.id, "needs_review", {"claimId": claim_id})
    for r in db.scalars(select(Recommendation).where(Recommendation.engagement_id == eid)):
        if claim_id in (r.claim_ids or []):
            r.status, r.review_reason = "needs_review", reason
            flagged["recommendations"].append(r.id)
            hist(db, c, eid, "recommendation", r.id, "needs_review", {"claimId": claim_id})
    for d in db.scalars(select(Decision).where(Decision.engagement_id == eid)):
        if claim_id in (d.related_claim_ids or []):
            d.needs_review = True
            flagged["decisions"].append(d.id)
            hist(db, c, eid, "decision", d.id, "needs_review", {"claimId": claim_id})
    return flagged


def _supersede(db, c, eid, cl: Claim, by: str | None, reason: str, bg: BackgroundTasks) -> dict:
    if cl.status != "confirmed":
        raise HTTPException(409, {"code": "state", "message": "Only a confirmed claim can be superseded."})
    if by:
        get_or_404(db, Claim, by, eid, "Replacement claim")
    cl.status, cl.superseded_by, cl.superseded_reason = "superseded", by, reason
    hist(db, c, eid, "claim", cl.id, "superseded", {"by": by, "reason": reason})
    flagged = _flag_downstream(db, c, eid, cl.id, f"Rests on a superseded claim: {reason}")
    emit(bg, "sdp.claim.superseded.v1", {"claimId": cl.id, "status": "superseded", "supersededBy": by}, eid, c, cl.id)
    return flagged


@app.post("/v1/engagements/{eid}/claims/{cid}/supersede", tags=["claims"])
def supersede_claim(eid: str, cid: str, body: SupersedeIn, bg: BackgroundTasks, db: Session = Depends(get_db),
                    c: Caller = Depends(caller_dep)):
    require(db, c, eid, "claims.supersede")
    flagged = _supersede(db, c, eid, get_or_404(db, Claim, cid, eid, "Claim"), body.byClaimId, body.reason, bg)
    db.commit()
    return {"claim": db.get(Claim, cid).to_api(), "flaggedForReview": flagged}


# ------------------------------------------------------------------ rules (guardrails checklist)

class RuleIn(In):
    status: Literal["unknown", "yes", "partial", "no"]
    gapDecision: Optional[Literal["establish", "accept"]] = None
    notes: str = ""
    sourceIds: list[str] = Field(default_factory=list)


@app.get("/v1/engagements/{eid}/rules", tags=["rules"])
def get_rules(eid: str, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "read")
    rows = {r.category: r for r in db.scalars(select(RuleStatus).where(RuleStatus.engagement_id == eid))}
    claims = db.scalars(select(Claim).where(Claim.engagement_id == eid, Claim.kind == "guardrail",
                                            Claim.status == "confirmed")).all()
    out = []
    for cat, label in RULE_LABELS.items():
        r = rows.get(cat)
        item = r.to_api() if r else {"category": cat, "status": "unknown", "gapDecision": None, "notes": "", "sourceIds": []}
        item["label"] = label
        item["rules"] = [cl.to_api() for cl in claims if cl.rule_category == cat]
        item["needsGapDecision"] = item["status"] in ("no", "partial") and not item.get("gapDecision")
        out.append(item)
    return out


@app.put("/v1/engagements/{eid}/rules/{category}", tags=["rules"])
def put_rule(eid: str, category: RuleCategory, body: RuleIn, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "rules.write")
    r = db.get(RuleStatus, {"engagement_id": eid, "category": category.value}) or RuleStatus(engagement_id=eid, category=category.value)
    r.status, r.gap_decision, r.notes, r.source_ids = body.status, body.gapDecision, body.notes, list(body.sourceIds)
    r.updated_by, r.updated_at = c.actor.id, now()
    db.merge(r)
    hist(db, c, eid, "rule", category.value, "updated", body.model_dump())
    db.commit()
    return get_rules(eid, db, c)


# ------------------------------------------------------------------ open items

class OpenItemIn(In):
    question: str
    kind: Literal["number", "constraint", "owner", "decision", "artefact"] = "decision"
    outcome: str = "unclassified"
    ownerId: Optional[str] = None
    dueDate: Optional[str] = None
    impact: str = ""
    defaultIfSilent: str = ""
    askRole: str = ""
    relatedClaimIds: list[str] = Field(default_factory=list)


class OpenItemPatch(In):
    question: Optional[str] = None
    kind: Optional[Literal["number", "constraint", "owner", "decision", "artefact"]] = None
    outcome: Optional[Literal["unclassified", "insist", "estimate", "risk", "pending", "design_around"]] = None
    ownerId: Optional[str] = None
    dueDate: Optional[str] = None
    impact: Optional[str] = None
    defaultIfSilent: Optional[str] = None
    askRole: Optional[str] = None
    status: Optional[Literal["open", "answered", "closed"]] = None
    answer: Optional[str] = None
    needsReview: Optional[bool] = None


class OpenItemBatchIn(In):
    items: list[OpenItemProposal]


@app.get("/v1/engagements/{eid}/open-items", tags=["open items"])
def list_open_items(eid: str, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "read")
    today = date.today().isoformat()
    out = []
    for oi in db.scalars(select(OpenItem).where(OpenItem.engagement_id == eid).order_by(OpenItem.created_at)):
        d = oi.to_api()
        d["overdue"] = bool(oi.status == "open" and oi.due_date and oi.due_date < today)
        out.append(d)
    return out


@app.post("/v1/engagements/{eid}/open-items", tags=["open items"], status_code=201)
def add_open_item(eid: str, body: OpenItemIn, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    if not is_user(c):
        raise HTTPException(403, {"code": "forbidden", "message": "Agents propose open items through /open-items/batch."})
    require(db, c, eid, "openitems.write")
    oi = OpenItem(id=new_id("oi"), engagement_id=eid, origin="human", created_by=c.actor.id,
                  **{snake(k): v for k, v in body.model_dump().items()})
    db.add(oi)
    hist(db, c, eid, "openitem", oi.id, "created")
    db.commit()
    return oi.to_api()


@app.post("/v1/engagements/{eid}/open-items/batch", tags=["open items"])
def propose_open_items(eid: str, body: OpenItemBatchIn, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "openitems.propose")
    known = {cid for (cid,) in db.execute(select(Claim.id).where(Claim.engagement_id == eid))}
    existing = {oi.question.strip().lower() for oi in db.scalars(select(OpenItem).where(OpenItem.engagement_id == eid))}
    added = []
    for p in body.items:
        if p.question.strip().lower() in existing:
            continue
        oi = OpenItem(id=new_id("oi"), engagement_id=eid, question=p.question, kind=p.kind.value,
                      outcome=p.outcome.value, impact=p.impact, ask_role=p.askRole,
                      related_claim_ids=[x for x in p.relatedClaimIds if x in known],
                      origin="agent", run_id=p.runId, created_by=c.actor.id)
        db.add(oi)
        hist(db, c, eid, "openitem", oi.id, "proposed", {"runId": p.runId})
        added.append(oi.id)
    db.commit()
    return {"accepted": added}


@app.patch("/v1/engagements/{eid}/open-items/{oid}", tags=["open items"])
def patch_open_item(eid: str, oid: str, body: OpenItemPatch, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "openitems.write")
    oi = get_or_404(db, OpenItem, oid, eid, "Open item")
    data = body.model_dump(exclude_none=True)
    apply(oi, data)
    hist(db, c, eid, "openitem", oid, "updated", data)
    db.commit()
    return oi.to_api()


@app.post("/v1/engagements/{eid}/open-items/{oid}/escalate", tags=["open items"])
def escalate_open_item(eid: str, oid: str, bg: BackgroundTasks, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "openitems.escalate")
    oi = get_or_404(db, OpenItem, oid, eid, "Open item")
    if oi.status != "open" or oi.escalation_level >= 2:
        return oi.to_api()
    oi.escalation_level += 1
    hist(db, c, eid, "openitem", oid, "escalated", {"level": oi.escalation_level})
    db.commit()
    emit(bg, "sdp.openitem.escalated.v1", {"openItemId": oid, "level": oi.escalation_level}, eid, c, oid)
    return oi.to_api()


@app.delete("/v1/engagements/{eid}/open-items/{oid}", tags=["open items"], status_code=204)
def delete_open_item(eid: str, oid: str, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "delete")
    db.delete(get_or_404(db, OpenItem, oid, eid, "Open item"))
    hist(db, c, eid, "openitem", oid, "deleted")
    db.commit()


# ------------------------------------------------------------------ divergences

class DivergenceBatchIn(In):
    divergences: list[DivergenceProposal]


class ResolveIn(In):
    resolution: Literal["a_correct", "b_correct", "both_stand", "ask_client"]
    note: str = ""


@app.get("/v1/engagements/{eid}/divergences", tags=["divergences"])
def list_divergences(eid: str, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "read")
    return [d.to_api() for d in db.scalars(select(Divergence).where(Divergence.engagement_id == eid).order_by(Divergence.created_at))]


@app.post("/v1/engagements/{eid}/divergences/batch", tags=["divergences"])
def propose_divergences(eid: str, body: DivergenceBatchIn, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "divergences.propose")
    live = {cl.id for cl in db.scalars(select(Claim).where(Claim.engagement_id == eid, Claim.status == "confirmed"))}
    pairs = {frozenset((d.claim_a, d.claim_b)) for d in db.scalars(select(Divergence).where(Divergence.engagement_id == eid))}
    accepted, rejected = [], []
    for p in body.divergences:
        pair = frozenset((p.claimA, p.claimB))
        if p.claimA == p.claimB or not {p.claimA, p.claimB} <= live:
            rejected.append({"pair": [p.claimA, p.claimB], "reason": "Both claims must be distinct confirmed claims."})
            continue
        if pair in pairs:
            continue
        pairs.add(pair)
        d = Divergence(id=new_id("div"), engagement_id=eid, claim_a=p.claimA, claim_b=p.claimB, type=p.type,
                       severity=p.severity, explanation=p.explanation, run_id=p.runId,
                       origin="agent" if not is_user(c) else "human")
        db.add(d)
        hist(db, c, eid, "divergence", d.id, "proposed", {"runId": p.runId})
        accepted.append(d.id)
    db.commit()
    return {"accepted": accepted, "rejected": rejected}


@app.post("/v1/engagements/{eid}/divergences/{did}/resolve", tags=["divergences"])
def resolve_divergence(eid: str, did: str, body: ResolveIn, bg: BackgroundTasks, db: Session = Depends(get_db),
                       c: Caller = Depends(caller_dep)):
    require(db, c, eid, "divergences.resolve")
    d = get_or_404(db, Divergence, did, eid, "Divergence")
    if d.status != "open":
        raise HTTPException(409, {"code": "state", "message": "This divergence is already resolved."})
    result: dict[str, Any] = {}
    if body.resolution in ("a_correct", "b_correct"):
        keep, drop = (d.claim_a, d.claim_b) if body.resolution == "a_correct" else (d.claim_b, d.claim_a)
        reason = body.note or f"Contradicted by {keep}, resolved as correct."
        result["flaggedForReview"] = _supersede(db, c, eid, db.get(Claim, drop), keep, reason, bg)
        d.status = "resolved"
    elif body.resolution == "ask_client":
        a, b = db.get(Claim, d.claim_a), db.get(Claim, d.claim_b)
        oi = OpenItem(id=new_id("oi"), engagement_id=eid, kind="decision", outcome="pending",
                      question=f"Which is right? “{a.statement}” or “{b.statement}”",
                      impact=d.explanation, related_claim_ids=[a.id, b.id], origin="human", created_by=c.actor.id)
        db.add(oi)
        hist(db, c, eid, "openitem", oi.id, "created", {"fromDivergence": did})
        result["openItemId"] = oi.id
        d.status = "with_client"
    else:
        d.status = "resolved"
    d.resolution, d.resolution_note, d.resolved_by, d.resolved_at = body.resolution, body.note, c.actor.id, now()
    hist(db, c, eid, "divergence", did, "resolved", body.model_dump())
    db.commit()
    return {"divergence": d.to_api(), **result}


# ------------------------------------------------------------------ decisions (ADR log)

class DecisionIn(In):
    title: str
    context: str = ""
    options: list[str] = Field(default_factory=list)
    decision: str = ""
    rationale: str = ""
    status: Literal["proposed", "accepted"] = "proposed"
    source: Literal["ours", "client"] = "ours"
    relatedClaimIds: list[str] = Field(default_factory=list)


class EscalationIn(In):
    title: str
    context: str
    options: list[str]
    relatedClaimIds: list[str] = Field(default_factory=list)
    practiceId: Optional[str] = None
    recommendationId: Optional[str] = None


class DecideIn(In):
    decision: str
    rationale: str


def _next_adr(db: Session, eid: str) -> int:
    return (db.scalar(select(func.max(Decision.number)).where(Decision.engagement_id == eid)) or 0) + 1


@app.get("/v1/engagements/{eid}/decisions", tags=["decisions"])
def list_decisions(eid: str, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "read")
    return [d.to_api() for d in db.scalars(select(Decision).where(Decision.engagement_id == eid).order_by(Decision.number))]


@app.post("/v1/engagements/{eid}/decisions", tags=["decisions"], status_code=201)
def add_decision(eid: str, body: DecisionIn, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    if not is_user(c):
        raise HTTPException(403, {"code": "forbidden", "message": "Agents can only escalate, through /decisions/escalate."})
    require(db, c, eid, "decisions.write")
    d = Decision(id=new_id("adr"), engagement_id=eid, number=_next_adr(db, eid), origin="human", created_by=c.actor.id,
                 **{snake(k): v for k, v in body.model_dump().items()})
    if d.status == "accepted":
        d.decided_by, d.decided_at = c.actor.id, now()
    db.add(d)
    hist(db, c, eid, "decision", d.id, "created", {"status": d.status})
    db.commit()
    return d.to_api()


@app.post("/v1/engagements/{eid}/decisions/escalate", tags=["decisions"], status_code=201)
def escalate_decision(eid: str, body: EscalationIn, bg: BackgroundTasks, db: Session = Depends(get_db),
                      c: Caller = Depends(caller_dep)):
    """A conflict between a Datatonic practice and a client rule. Never resolved by an agent: the solution owner decides."""
    require(db, c, eid, "decisions.escalate")
    d = Decision(id=new_id("adr"), engagement_id=eid, number=_next_adr(db, eid), title=body.title,
                 context=body.context, options=body.options, status="escalated", source="ours",
                 related_claim_ids=body.relatedClaimIds, practice_id=body.practiceId,
                 recommendation_id=body.recommendationId, origin="agent", created_by=c.actor.id)
    db.add(d)
    hist(db, c, eid, "decision", d.id, "escalated")
    db.commit()
    emit(bg, "sdp.decision.escalated.v1", {"decisionId": d.id, "title": d.title, "recommendationId": d.recommendation_id}, eid, c, d.id)
    return d.to_api()


@app.post("/v1/engagements/{eid}/decisions/{did}/decide", tags=["decisions"])
def decide(eid: str, did: str, body: DecideIn, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    d = get_or_404(db, Decision, did, eid, "Decision")
    require(db, c, eid, "decisions.decide" if d.status == "escalated" else "decisions.write")
    if d.status not in ("escalated", "proposed"):
        raise HTTPException(409, {"code": "state", "message": f"A {d.status} decision can't be decided again."})
    d.status, d.decision, d.rationale, d.decided_by, d.decided_at = "accepted", body.decision, body.rationale, c.actor.id, now()
    d.needs_review = False
    hist(db, c, eid, "decision", did, "decided", body.model_dump())
    db.flush()  # the count below must see this decision as accepted
    if d.recommendation_id:
        rec = db.get(Recommendation, d.recommendation_id)
        pending = db.scalar(select(func.count()).select_from(Decision).where(
            Decision.recommendation_id == d.recommendation_id, Decision.status == "escalated"))
        if rec and rec.status == "blocked" and pending == 0:
            rec.status, rec.review_reason = "needs_review", "Escalated conflicts decided. Review before approving."
            hist(db, c, eid, "recommendation", rec.id, "unblocked")
    db.commit()
    return d.to_api()


# ------------------------------------------------------------------ recommendations

@app.get("/v1/engagements/{eid}/recommendations", tags=["recommendations"])
def list_recs(eid: str, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "read")
    return [r.to_api() for r in db.scalars(select(Recommendation).where(Recommendation.engagement_id == eid).order_by(Recommendation.created_at.desc()))]


@app.post("/v1/engagements/{eid}/recommendations", tags=["recommendations"], status_code=201)
def add_rec(eid: str, body: RecommendationProposal, requestedBy: str = "", db: Session = Depends(get_db),
            c: Caller = Depends(caller_dep)):
    require(db, c, eid, "recs.propose")
    claim_ids = {cid for (cid,) in db.execute(select(Claim.id).where(Claim.engagement_id == eid))}
    practice_ids = {pid for (pid,) in db.execute(select(Practice.id))}
    data = body.model_dump(mode="json")
    r = Recommendation(id=new_id("rec"), engagement_id=eid, path=body.path, recommendation=body.recommendation,
                       rationale=body.rationale, level=body.level.value,
                       practice_ids=[p for p in body.practiceIds if p in practice_ids],
                       claim_ids=[x for x in body.claimIds if x in claim_ids],
                       alternatives=data["alternatives"], assumptions=body.assumptions, conflicts=data["conflicts"],
                       status="blocked" if body.conflicts else "draft",
                       review_reason="Conflicts with Datatonic practice escalated to the solution owner." if body.conflicts else "",
                       run_id=body.runId, prompt_version=body.promptVersion, model=body.model,
                       requested_by=requestedBy or c.actor.id)
    db.add(r)
    hist(db, c, eid, "recommendation", r.id, "drafted", {"runId": body.runId, "conflicts": len(body.conflicts)})
    db.commit()
    return r.to_api()


@app.post("/v1/engagements/{eid}/recommendations/{rid}/approve", tags=["recommendations"])
def approve_rec(eid: str, rid: str, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "recs.approve")
    r = get_or_404(db, Recommendation, rid, eid, "Recommendation")
    if r.status == "blocked":
        raise HTTPException(409, {"code": "blocked", "message": "Escalated conflicts need the solution owner's decision first."})
    r.status, r.approved_by, r.approved_at, r.review_reason = "approved", c.actor.id, now(), ""
    hist(db, c, eid, "recommendation", rid, "approved")
    db.commit()
    return r.to_api()


@app.delete("/v1/engagements/{eid}/recommendations/{rid}", tags=["recommendations"], status_code=204)
def delete_rec(eid: str, rid: str, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "delete")
    db.delete(get_or_404(db, Recommendation, rid, eid, "Recommendation"))
    hist(db, c, eid, "recommendation", rid, "deleted")
    db.commit()


# ------------------------------------------------------------------ the Brief and packs

def _slots(db: Session, eid: str) -> list[BriefSlot]:
    """The six slots, in order. Created on first read for engagements opened before the Brief existed."""
    have = {b.key: b for b in db.scalars(select(BriefSlot).where(BriefSlot.engagement_id == eid))}
    missing = [k for k in BRIEF_KEYS if k not in have]
    for key in missing:
        have[key] = BriefSlot(id=new_id("brf"), engagement_id=eid, key=key, status="empty", text="",
                              shapes=[], claim_ids=[], gaps=[], version=0)
        db.add(have[key])
    if missing:
        db.commit()  # slot ids must be stable for lineage
    return [have[k] for k in BRIEF_KEYS]


def _slot_api(b: BriefSlot) -> dict:
    return {**b.to_api(), "label": BRIEF_LABELS.get(b.key, b.key)}


def _brief_view(db: Session, eid: str) -> dict:
    slots = _slots(db, eid)
    shape = next(b for b in slots if b.key == "shape")
    shapes = [k for k in (shape.shapes or []) if k in PACKS and k != "core"]
    shape_state = "agreed" if shape.status == "agreed" else "tentative" if shapes else "unknown"
    applied = ["core"] + shapes
    return {
        "slots": [_slot_api(b) for b in slots],
        "shapeState": shape_state,                # packs apply tentatively from a draft shape, firmly once agreed
        "appliedPacks": applied,
        "packs": [{"key": k, **{f: PACKS[k].get(f) for f in ("label", "description", "mustKnow", "askAbout", "paths")}} for k in applied],
        "agreed": len([b for b in slots if b.status == "agreed"]),
        "total": len(slots),
    }


class BriefEditIn(In):
    text: str = ""
    claimIds: list[str] = Field(default_factory=list)
    shapes: list[str] = Field(default_factory=list)
    note: str = ""


def _confirmed_ids(db: Session, eid: str) -> set[str]:
    return {cid for (cid,) in db.execute(select(Claim.id).where(Claim.engagement_id == eid, Claim.status == "confirmed"))}


def _emit_brief(bg: BackgroundTasks, b: BriefSlot, eid: str, c: Caller):
    emit(bg, "sdp.brief.changed.v1", {"slot": b.key, "status": b.status, "version": b.version}, eid, c, b.id)


@app.get("/v1/packs", tags=["brief"])
def list_packs(c: Caller = Depends(caller_dep)):
    return [{"key": k, **v} for k, v in PACKS.items()]


@app.get("/v1/engagements/{eid}/brief", tags=["brief"])
def get_brief(eid: str, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "read")
    out = _brief_view(db, eid)
    db.commit()
    return out


@app.post("/v1/engagements/{eid}/brief/proposals", tags=["brief"])
def propose_brief(eid: str, body: BriefProposal, bg: BackgroundTasks, db: Session = Depends(get_db),
                  c: Caller = Depends(caller_dep)):
    """An engine's draft. Every slot must rest on confirmed claims. Agreed slots are never overwritten:
    a different draft is kept beside them as a suggestion for the owner."""
    require(db, c, eid, "brief.propose")
    confirmed = _confirmed_ids(db, eid)
    slots = {b.key: b for b in _slots(db, eid)}
    accepted, rejected = [], []
    for p in body.slots:
        b = slots[p.key.value]
        ids = [x for x in p.claimIds if x in confirmed]
        shapes = [k for k in p.shapes if k in PACKS and k != "core"] if p.key.value == "shape" else []
        b.gaps = p.gaps[:10]
        if not p.text.strip() or not ids or (p.key.value == "shape" and not shapes):
            rejected.append({"key": p.key.value, "reason": "No text, no confirmed claims behind it, or no known pack."})
            continue
        draft = {"text": p.text.strip(), "claimIds": ids, "shapes": shapes, "runId": body.runId}
        if b.status == "agreed":
            if p.text.strip() != (b.text or "") or set(shapes) != set(b.shapes or []):
                b.suggestion = draft
                hist(db, c, eid, "brief", b.id, "suggested", draft)
                accepted.append(b.key)
            continue
        b.text, b.claim_ids, b.shapes, b.run_id = draft["text"], ids, shapes, body.runId
        b.status, b.version, b.drafted_by, b.review_reason, b.suggestion, b.updated_at = "draft", (b.version or 0) + 1, c.actor.id, "", None, now()
        hist(db, c, eid, "brief", b.id, "drafted", {**draft, "promptVersion": body.promptVersion, "model": body.model})
        _emit_brief(bg, b, eid, c)
        accepted.append(b.key)
    db.commit()
    return {"accepted": accepted, "rejected": rejected, "brief": _brief_view(db, eid)}


def _slot_or_404(db: Session, eid: str, key: str) -> BriefSlot:
    if key not in BRIEF_KEYS:
        raise HTTPException(404, {"code": "not_found", "message": f"No Brief slot called {key}."})
    return next(b for b in _slots(db, eid) if b.key == key)


@app.put("/v1/engagements/{eid}/brief/{key}", tags=["brief"])
def edit_brief(eid: str, key: str, body: BriefEditIn, bg: BackgroundTasks, db: Session = Depends(get_db),
               c: Caller = Depends(caller_dep)):
    """A person rewrites a slot. It goes back to draft: agreement is always on the exact words."""
    require(db, c, eid, "brief.edit")
    b = _slot_or_404(db, eid, key)
    confirmed = _confirmed_ids(db, eid)
    bad = [x for x in body.claimIds if x not in confirmed]
    if bad:
        raise HTTPException(422, {"code": "claims", "message": f"Only confirmed claims can back the Brief: {', '.join(bad)}"})
    shapes = [k for k in body.shapes if k in PACKS and k != "core"]
    if key == "shape" and len(shapes) != len([k for k in body.shapes if k != "core"]):
        raise HTTPException(422, {"code": "packs", "message": "Unknown pack in shapes."})
    before = _slot_api(b)
    b.text, b.claim_ids, b.shapes = body.text.strip(), body.claimIds, shapes if key == "shape" else []
    empty = not b.text and not b.shapes
    b.status = "empty" if empty else "draft"
    b.version, b.drafted_by, b.run_id, b.review_reason, b.suggestion, b.updated_at = (b.version or 0) + 1, c.actor.id, None, "", None, now()
    b.agreed_by = b.agreed_at = None
    hist(db, c, eid, "brief", b.id, "edited", {"before": {k: before[k] for k in ("text", "claimIds", "shapes", "status")},
                                                "after": body.model_dump(), "note": body.note})
    _emit_brief(bg, b, eid, c)
    db.commit()
    return _slot_api(b)


@app.post("/v1/engagements/{eid}/brief/{key}/agree", tags=["brief"])
def agree_brief(eid: str, key: str, bg: BackgroundTasks, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "brief.agree")
    b = _slot_or_404(db, eid, key)
    if b.status == "empty" or not (b.text or b.shapes):
        raise HTTPException(409, {"code": "state", "message": "There is nothing to agree yet."})
    confirmed = _confirmed_ids(db, eid)
    live = [x for x in (b.claim_ids or []) if x in confirmed]
    if not live:
        raise HTTPException(409, {"code": "uncited", "message": "Link at least one confirmed claim before agreeing: the Brief must rest on evidence."})
    if len(live) != len(b.claim_ids or []):
        raise HTTPException(409, {"code": "stale", "message": "Some claims behind this slot are no longer confirmed. Edit it first."})
    b.status, b.agreed_by, b.agreed_at, b.review_reason, b.updated_at = "agreed", c.actor.id, now(), "", now()
    hist(db, c, eid, "brief", b.id, "agreed", {"version": b.version, "text": b.text, "shapes": b.shapes})
    _emit_brief(bg, b, eid, c)
    db.commit()
    return _slot_api(b)


@app.post("/v1/engagements/{eid}/brief/{key}/dismiss-suggestion", tags=["brief"])
def dismiss_suggestion(eid: str, key: str, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "brief.edit")
    b = _slot_or_404(db, eid, key)
    hist(db, c, eid, "brief", b.id, "suggestion_dismissed", b.suggestion)
    b.suggestion = None
    db.commit()
    return _slot_api(b)


# ------------------------------------------------------------------ practices (Datatonic, global)

class PracticeIn(In):
    domain: str
    title: str
    statement: str
    rationale: str = ""
    status: Literal["draft", "active", "retired"] = "active"
    appliesTo: list[str] = Field(default_factory=list)


@app.get("/v1/practices", tags=["practices"])
def list_practices(status: Optional[str] = None, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    stmt = select(Practice).order_by(Practice.domain, Practice.title)
    if status:
        stmt = stmt.where(Practice.status.in_(status.split(",")))
    return [p.to_api() for p in db.scalars(stmt)]


@app.post("/v1/practices", tags=["practices"], status_code=201)
def add_practice(body: PracticeIn, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require_practice_admin(db, c)
    data = body.model_dump()
    p = Practice(id=new_id("prc"), updated_by=c.actor.id, applies_to=data.pop("appliesTo"), **data)
    db.add(p)
    hist(db, c, None, "practice", p.id, "created", body.model_dump())
    db.commit()
    return p.to_api()


@app.patch("/v1/practices/{pid}", tags=["practices"])
def patch_practice(pid: str, body: PracticeIn, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require_practice_admin(db, c)
    p = get_or_404(db, Practice, pid, what="Practice")
    before = p.to_api()
    apply(p, body.model_dump())  # apply() maps appliesTo -> applies_to
    p.version, p.updated_by, p.updated_at = p.version + 1, c.actor.id, now()
    hist(db, c, None, "practice", pid, "updated", {"before": before, "after": body.model_dump()})
    db.commit()
    return p.to_api()


# ------------------------------------------------------------------ agent runs, budget

@app.post("/v1/engagements/{eid}/runs", tags=["governance"], status_code=201)
def record_run(eid: str, body: AgentRunRecord, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "runs.write")
    r = AgentRun(engagement_id=eid, **{snake(k): v for k, v in body.model_dump().items()})
    db.merge(r)
    hist(db, c, eid, "run", body.id, "recorded", {"engine": body.engine, "accepted": body.accepted, "rejected": body.rejected})
    db.commit()
    return db.get(AgentRun, body.id).to_api()


@app.get("/v1/engagements/{eid}/runs", tags=["governance"])
def list_runs(eid: str, limit: int = 100, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "read")
    return [r.to_api() for r in db.scalars(select(AgentRun).where(AgentRun.engagement_id == eid).order_by(AgentRun.started_at.desc()).limit(limit))]


@app.get("/v1/engagements/{eid}/budget", tags=["governance"])
def budget(eid: str, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "read")
    e = get_or_404(db, Engagement, eid, what="Engagement")
    spent = db.scalar(select(func.coalesce(func.sum(AgentRun.cost_usd), 0.0)).where(AgentRun.engagement_id == eid)) or 0.0
    return {"budgetUsd": e.ai_budget_usd, "spentUsd": round(spent, 4), "remainingUsd": round(e.ai_budget_usd - spent, 4)}


@app.get("/v1/engagements/{eid}/history", tags=["governance"])
def history(eid: str, entityId: Optional[str] = None, limit: int = 200, db: Session = Depends(get_db),
            c: Caller = Depends(caller_dep)):
    require(db, c, eid, "read")
    stmt = select(History).where(History.engagement_id == eid)
    if entityId:
        stmt = stmt.where(History.entity_id == entityId)
    return [h.to_api() for h in db.scalars(stmt.order_by(History.id.desc()).limit(limit))]


# ------------------------------------------------------------------ context, summary, lineage

@app.get("/v1/engagements/{eid}/context", tags=["engines"])
def context(eid: str, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    """Everything an engine needs in one packet: what is confirmed, the rules, practices and open questions."""
    require(db, c, eid, "read")
    e = get_or_404(db, Engagement, eid, what="Engagement")
    brief = _brief_view(db, eid)
    applied = set(brief["appliedPacks"])
    return {
        "engagement": e.to_api(),
        "brief": brief,
        "claims": [cl.to_api() for cl in db.scalars(select(Claim).where(Claim.engagement_id == eid, Claim.status == "confirmed"))],
        "rules": get_rules(eid, db, c),
        "practices": [p.to_api() for p in db.scalars(select(Practice).where(Practice.status == "active"))
                      if not p.applies_to or applied & set(p.applies_to)],
        "openItems": [oi.to_api() for oi in db.scalars(select(OpenItem).where(OpenItem.engagement_id == eid, OpenItem.status == "open"))],
        "divergences": [d.to_api() for d in db.scalars(select(Divergence).where(Divergence.engagement_id == eid))],
        "stakeholders": [s.to_api() for s in db.scalars(select(Stakeholder).where(Stakeholder.engagement_id == eid))],
    }


@app.get("/v1/engagements/{eid}/summary", tags=["engagements"])
def summary(eid: str, db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    require(db, c, eid, "read")
    claims = db.scalars(select(Claim).where(Claim.engagement_id == eid)).all()
    confirmed = [cl for cl in claims if cl.status == "confirmed"]
    by_q = {q: [cl for cl in confirmed if cl.question == q] for q in ("why", "what", "rules", "from", "how")}
    why_needing_baseline = [cl for cl in by_q["why"] if cl.kind in ("driver", "pain_point") and not cl.number]
    ois = list_open_items(eid, db, c)
    rules = get_rules(eid, db, c)
    decisions = db.scalars(select(Decision).where(Decision.engagement_id == eid)).all()
    recs = db.scalars(select(Recommendation).where(Recommendation.engagement_id == eid)).all()
    divs = db.scalars(select(Divergence).where(Divergence.engagement_id == eid)).all()
    return {
        "questions": {q: len(v) for q, v in by_q.items()},
        "assumptions": len([cl for cl in confirmed if cl.evidence == "none"]),
        "whyWithoutBaseline": len(why_needing_baseline),
        "proposedClaims": len([cl for cl in claims if cl.status == "proposed"]),
        "openDivergences": len([d for d in divs if d.status == "open"]),
        "openItems": len([o for o in ois if o["status"] == "open"]),
        "overdueItems": len([o for o in ois if o["overdue"]]),
        "openItemsByOutcome": {k: len([o for o in ois if o["status"] == "open" and o["outcome"] == k])
                               for k in ("unclassified", "insist", "estimate", "risk", "pending", "design_around")},
        "rulesUnknown": len([r for r in rules if r["status"] == "unknown"]),
        "rulesNeedingGapDecision": len([r for r in rules if r["needsGapDecision"]]),
        "escalatedDecisions": len([d for d in decisions if d.status == "escalated"]),
        "recommendationsNeedingReview": len([r for r in recs if r.status in ("needs_review", "blocked")]),
        "needsReview": len([o for o in ois if o["needsReview"]]) + len([d for d in decisions if d.needs_review]),
        "brief": {st: len([b for b in _slots(db, eid) if b.status == st]) for st in ("empty", "draft", "agreed", "needs_review")},
        "briefNeedsReview": len([b for b in _slots(db, eid) if b.status == "needs_review"]),
    }


def _claim_brief(db, cid):
    cl = db.get(Claim, cid)
    if not cl:
        return None
    src = db.get(Source, cl.source_id) if cl.source_id else None
    return {"id": cl.id, "statement": cl.statement, "quote": cl.quote, "status": cl.status, "speaker": cl.speaker,
            "evidence": cl.evidence, "source": {"id": src.id, "title": src.title} if src else None}


@app.get("/v1/engagements/{eid}/lineage/{entity}/{item_id}", tags=["lineage"])
def lineage(eid: str, entity: Literal["claim", "recommendation", "openitem", "decision", "divergence", "brief"], item_id: str,
            db: Session = Depends(get_db), c: Caller = Depends(caller_dep)):
    """Up to the evidence, down to everything that rests on it, plus the item's full history."""
    require(db, c, eid, "read")
    up: dict[str, Any] = {}
    down: dict[str, Any] = {}
    if entity == "claim":
        cl = get_or_404(db, Claim, item_id, eid, "Claim")
        item = cl.to_api()
        src = db.get(Source, cl.source_id) if cl.source_id else None
        up["source"] = src.to_api() if src else None
        up["run"] = db.get(AgentRun, cl.run_id).to_api() if cl.run_id and db.get(AgentRun, cl.run_id) else None
        up["supersedes"] = [x.id for x in db.scalars(select(Claim).where(Claim.superseded_by == item_id))]
        down["supersededBy"] = _claim_brief(db, cl.superseded_by) if cl.superseded_by else None
        down["openItems"] = [o.to_api() for o in db.scalars(select(OpenItem).where(OpenItem.engagement_id == eid)) if item_id in (o.related_claim_ids or [])]
        down["recommendations"] = [r.to_api() for r in db.scalars(select(Recommendation).where(Recommendation.engagement_id == eid)) if item_id in (r.claim_ids or [])]
        down["decisions"] = [d.to_api() for d in db.scalars(select(Decision).where(Decision.engagement_id == eid)) if item_id in (d.related_claim_ids or [])]
        down["divergences"] = [d.to_api() for d in db.scalars(select(Divergence).where(Divergence.engagement_id == eid, or_(Divergence.claim_a == item_id, Divergence.claim_b == item_id)))]
        down["brief"] = [_slot_api(b) for b in _slots(db, eid) if item_id in (b.claim_ids or [])]
    elif entity == "brief":
        b = get_or_404(db, BriefSlot, item_id, eid, "Brief slot")
        item = _slot_api(b)
        up["claims"] = [x for x in (_claim_brief(db, cid) for cid in b.claim_ids or []) if x]
        up["run"] = db.get(AgentRun, b.run_id).to_api() if b.run_id and db.get(AgentRun, b.run_id) else None
    elif entity == "recommendation":
        r = get_or_404(db, Recommendation, item_id, eid, "Recommendation")
        item = r.to_api()
        up["claims"] = [b for b in (_claim_brief(db, x) for x in r.claim_ids or []) if b]
        up["practices"] = [p.to_api() for p in (db.get(Practice, x) for x in r.practice_ids or []) if p]
        up["run"] = db.get(AgentRun, r.run_id).to_api() if r.run_id and db.get(AgentRun, r.run_id) else None
        down["decisions"] = [d.to_api() for d in db.scalars(select(Decision).where(Decision.recommendation_id == item_id))]
    elif entity == "openitem":
        o = get_or_404(db, OpenItem, item_id, eid, "Open item")
        item = o.to_api()
        up["claims"] = [b for b in (_claim_brief(db, x) for x in o.related_claim_ids or []) if b]
    elif entity == "decision":
        d = get_or_404(db, Decision, item_id, eid, "Decision")
        item = d.to_api()
        up["claims"] = [b for b in (_claim_brief(db, x) for x in d.related_claim_ids or []) if b]
        up["practice"] = db.get(Practice, d.practice_id).to_api() if d.practice_id and db.get(Practice, d.practice_id) else None
        up["recommendation"] = db.get(Recommendation, d.recommendation_id).to_api() if d.recommendation_id and db.get(Recommendation, d.recommendation_id) else None
    else:
        d = get_or_404(db, Divergence, item_id, eid, "Divergence")
        item = d.to_api()
        up["claims"] = [b for b in (_claim_brief(db, x) for x in (d.claim_a, d.claim_b)) if b]
    hist_rows = db.scalars(select(History).where(History.entity_id == item_id).order_by(History.id)).all()
    return {"entity": entity, "item": item, "upstream": up, "downstream": down,
            "history": [h.to_api() for h in hist_rows]}
