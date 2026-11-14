"""Discovery Plan engine: turns what we know so far (an RFP, a call transcript, notes) into a plan of
sessions: who attends on both sides, what to ask, how long, which day.

Code builds the skeleton from the chosen format (config/discovery_formats.yaml) and the engagement's packs
(config/packs.yaml): which sessions, durations, days, attendees and nominations. The budget is enforced here
and again by the ledger. The model then tailors each session to the evidence: purpose, agenda, questions,
pre-reads. If the model call fails, the skeleton is still a valid plan.
"""
from __future__ import annotations

import json
import math
import re

from fastapi import Depends, Request

from sdp_common.auth import Caller, caller_from
from sdp_common.clients import ServiceError, ledger
from sdp_common.contracts import (EngineRunRequest, EngineRunResponse, PlanAttendee, PlanNomination, PlanProposal,
                                  PlanQuestion, PlanSession, PlanTeamMember)
from sdp_common.engine_kit import Run, brief_text, engine_dir, load_prompt, render
from sdp_common.service import create_app
from sdp_common.text import signal_hits

ENGINE = "engine-plan"
app = create_app(ENGINE, "Builds the Discovery Plan: sessions, attendees, questions, durations.")
PROMPT, PROMPT_VERSION = load_prompt(engine_dir(__file__), "plan_v1")
DOC_BUDGET = 40000          # characters of raw documents given to the model
PREP_FACTOR = 1.0           # an hour of preparation and write-up per hour in the room
DAY_MINUTES = 300           # most client time we put in one day


def caller_dep(request: Request) -> Caller:
    return caller_from(request)


# ------------------------------------------------------------------ shape and format

def infer_shapes(packs: list[dict], texts: list[str]) -> list[str]:
    """Tentative shape from pack signals, used only when the Brief has no shape yet."""
    blob = "\n".join(texts).lower()
    scores = {}
    for p in packs:
        if p["key"] == "core":
            continue
        n = signal_hits(p.get("signals") or [], blob)
        if n >= 2:
            scores[p["key"]] = n
    return [k for k, _ in sorted(scores.items(), key=lambda kv: -kv[1])][:3]


def recommend_format(sources: list[dict], doc_chars: int, shapes: list[str]) -> tuple[str, str]:
    kinds = {s.get("kind") for s in sources}
    titles = " ".join(s.get("title", "") for s in sources).lower()
    if not sources:
        return "single-call", "Nothing has been shared yet, so start with one call to understand the requirement."
    if kinds & {"rfp", "proposal"} or re.search(r"\b(rfp|rfi|tender|request for proposal)\b", titles):
        return "workshop-day", "An RFP is in hand: a workshop day clarifies requirements and assumptions before we respond."
    if doc_chars < 3000 and len(sources) <= 1:
        return "single-call", "We only have a short description so far: one call to understand the requirement and qualify it."
    if len(shapes) >= 3:
        return "two-week", f"The evidence points to a mixed engagement ({', '.join(shapes)}): each part needs its own deep dives."
    if len(shapes) == 2:
        return "one-week", f"Two kinds of work ({', '.join(shapes)}): a week covers both with deep dives where the risk is."
    return "half-day", "One kind of work and a qualified need: a half day covers context, current state and constraints."


# ------------------------------------------------------------------ skeleton

def _templates(packs: dict, applied: list[str]) -> list[dict]:
    out = []
    for k in applied:
        for t in packs[k].get("sessions") or []:
            out.append({**t, "pack": k})
    return out


def _priority(t: dict) -> int:
    """Lower is kept first when the budget is tight."""
    if t["key"] == "kickoff":
        return 0
    if t.get("last"):
        return 1
    base = {2: 10, 3: 30}.get(t.get("minDepth", 2), 20)
    return base + (0 if t["pack"] != "core" else 5) + (20 if t["key"] == "midpoint" else 0)


def _round15(m: float) -> int:
    return max(30, int(round(m / 15.0)) * 15)


def build_skeleton(fmt: dict, packs: dict, applied: list[str], open_items: list[dict], brief: dict) -> list[dict]:
    depth, days = fmt["depth"], fmt["days"]
    temps = _templates(packs, applied)
    gaps = {s["key"]: s.get("gaps") or [] for s in brief["slots"] if s["status"] != "agreed"}
    must = {k: list(packs[k].get("mustKnow") or []) for k in applied}

    if depth == 1:  # one call: every relevant template folds into a time-boxed agenda
        minutes = fmt["sessionMax"]
        sections = ["Introductions and the goal of the call (5 min)"]
        kick = next(t for t in temps if t["key"] == "kickoff")
        sections += kick["agenda"]
        for k in applied[1:]:
            first = next((t for t in packs[k].get("sessions") or []), None)
            if first:
                sections.append(f"{packs[k]['label']}: {first['agenda'][0].lower()}")
        sections.append("Next steps and who else we should meet (5 min)")
        qs = [PlanQuestion(text=g, covers=_covers_for_slot(slot)) for slot, gs in gaps.items() for g in gs[:1]]
        for k in applied:
            qs += [PlanQuestion(text=m, mustKnow=m, covers="from") for m in must[k][:2]]
        client = ["sponsor", "business-owner", "it-lead"] + [packs[k]["sessions"][0]["clientRoles"][0] for k in applied[1:] if packs[k].get("sessions")]
        ours = ["account-lead", "solution-architect"] + [packs[k]["ourRoles"][0] for k in applied[1:] if packs[k].get("ourRoles")]
        if len(qs) < 6:
            qs += [PlanQuestion(text=q, covers=c) for c, q in STARTERS.values()][: 8 - len(qs)]
        return [{"key": "discovery-call", "title": "Discovery call", "day": 1, "minutes": minutes,
                 "covers": ["why", "what", "rules", "from"], "clientRoles": _uniq(client)[:5], "ourRoles": _uniq(ours)[:3],
                 "agenda": sections[:8], "questions": qs[:10], "pack": "core"}]

    temps = [t for t in temps if t.get("minDepth", 2) <= depth and t.get("minDays", 1) <= days]
    temps.sort(key=_priority)
    temps = temps[: fmt["maxSessions"]]
    for t in temps:
        t["minutes"] = min(t.get("minutes", 60), fmt["sessionMax"])
    while sum(t["minutes"] for t in temps) > fmt["maxMinutes"]:  # shrink long sessions first, then drop the least important
        longest = max(temps, key=lambda t: t["minutes"])
        if longest["minutes"] > 45:
            longest["minutes"] = _round15(longest["minutes"] - 15)
        else:
            temps.pop()
    # running order: kickoff, everything else by depth and pack, mid-point in the middle, readout last
    kickoff = [t for t in temps if t["key"] == "kickoff"]
    last = [t for t in temps if t.get("last")]
    mid = [t for t in temps if t["key"] == "midpoint"]
    body = sorted([t for t in temps if t not in kickoff + last + mid],
                  key=lambda t: (t.get("minDepth", 2), applied.index(t["pack"]) if t["pack"] != "core" else -1))
    half = math.ceil(len(body) / 2)
    order = kickoff + body[:half] + mid + body[half:] + last
    _assign_days(order, days)

    # questions: the pack's must-know list spread over its sessions that cover FROM, Brief gaps to the kickoff,
    # open items to the session whose client attendees match who we should ask
    for t in order:
        t["questions"] = []
    for k in applied:
        holders = [t for t in order if t["pack"] == k and "from" in t.get("covers", [])] or \
                  [t for t in order if t["key"] == "current-state"] or order[:1]
        for i, m in enumerate(must[k]):
            holders[i % len(holders)]["questions"].append(PlanQuestion(text=m, mustKnow=m, covers="from"))
    for slot, gs in gaps.items():
        target = next((t for t in order if _covers_for_slot(slot) in t.get("covers", [])), order[0])
        target["questions"] += [PlanQuestion(text=g, covers=_covers_for_slot(slot)) for g in gs[:2]]
    for oi in open_items:
        if oi.get("status") != "open":
            continue
        target = next((t for t in order if any(r.replace("-", " ") in (oi.get("askRole") or "").lower() for r in t["clientRoles"])), None) \
            or next((t for t in order if t["key"] == "current-state"), order[0])
        target["questions"].append(PlanQuestion(text=oi["question"], openItemId=oi["id"]))
    for k in applied:  # the pack's always-ask topics go to its own sessions
        own = [t for t in order if t["pack"] == k] or order[:1]
        for i, a in enumerate(packs[k].get("askAbout") or []):
            own[i % len(own)]["questions"].append(PlanQuestion(text=a, covers="from"))
    for slot, (cov, q) in STARTERS.items():  # Brief parts with nothing yet get a starter question
        if not any(s["key"] == slot and (s["status"] != "empty" or s.get("gaps")) for s in brief["slots"]):
            target = next((t for t in order if cov in t.get("covers", [])), order[0])
            target["questions"].append(PlanQuestion(text=q, covers=cov))
    for t in order:  # playbacks confirm what we heard; every other session gets at least its agenda as questions
        if t.get("last") or t["key"] == "midpoint":
            t["questions"] = [PlanQuestion(text=q, covers=c) for c, q in PLAYBACK] + t["questions"][:2]
        elif not t["questions"]:
            t["questions"] = [PlanQuestion(text=f"Walk us through: {a[0].lower() + a[1:]}", covers=(t.get("covers") or ["from"])[0])
                              for a in (t.get("agenda") or [])[:3]]
        t["questions"] = t["questions"][:10]
    return order


PLAYBACK = [
    ("why", "Have we described why you are doing this, and why now, correctly?"),
    ("what", "Are these the right success measures, with the right baselines?"),
    ("how", "Which of the open decisions can you take now, and who takes the rest?"),
]

STARTERS = {  # Brief slot -> (covers, starter question)
    "purpose": ("why", "What is driving this now, and what happens if nothing changes?"),
    "value": ("why", "What does today's problem cost you, in time, money or risk?"),
    "measures": ("what", "How will you judge success in a year, and what is today's baseline?"),
    "scope": ("from", "Which teams, systems and processes are in scope?"),
    "nonGoals": ("from", "What is explicitly out of scope for this phase?"),
}


def _assign_days(order: list[dict], days: int) -> None:
    """Kickoff on day 1, readout on the last day, mid-point half way; the rest spread evenly between."""
    for t in order:
        t["day"] = 1
    if days == 1:
        return
    last = [t for t in order if t.get("last")]
    for t in last:
        t["day"] = days
    mid = next((t for t in order if t["key"] == "midpoint"), None)
    body = [t for t in order[1:] if not t.get("last") and t is not mid]
    if mid:
        mid_day = max(2, days // 2)
        mid["day"] = mid_day
        cut = order.index(mid)
        _spread([t for t in body if order.index(t) < cut], 1, mid_day - 1)
        _spread([t for t in body if order.index(t) > cut], mid_day + 1, days - 1)
    else:
        _spread(body, 1, days - 1)


def _spread(items: list[dict], first: int, last: int) -> None:
    span = max(1, last - first + 1)
    load: dict[int, int] = {}
    for i, t in enumerate(items):
        d = first + min(span - 1, int(i * span / max(1, len(items))))
        while load.get(d, 0) + t["minutes"] > DAY_MINUTES and d < last:
            d += 1
        t["day"] = d
        load[d] = load.get(d, 0) + t["minutes"]


def _covers_for_slot(slot: str) -> str:
    return {"purpose": "why", "value": "why", "measures": "what", "scope": "from", "nonGoals": "from", "shape": "what"}.get(slot, "what")


def _uniq(xs: list[str]) -> list[str]:
    return list(dict.fromkeys(x for x in xs if x))


# ------------------------------------------------------------------ people

def match_stakeholder(role: str, roles: dict, stakeholders: list[dict]) -> dict | None:
    words = roles["client"].get(role, {}).get("match") or []
    best = None
    for s in stakeholders:
        hay = f" {s.get('title', '')} {s.get('type', '')} ".lower()
        if any(re.search(r"\b" + re.escape(w) + r"\b", hay) for w in words):
            if best is None or (s.get("status") == "confirmed" and best.get("status") != "confirmed"):
                best = s
    return best


def staff(order: list[dict], roles: dict, stakeholders: list[dict]):
    sponsor = match_stakeholder("sponsor", roles, stakeholders)
    ask_who = sponsor["name"] if sponsor else "the client's main contact"
    noms: dict[str, list[str]] = {}
    for t in order:
        att = []
        for r in t.get("clientRoles") or []:
            s = match_stakeholder(r, roles, stakeholders)
            if s:
                att.append(PlanAttendee(role=r, stakeholderId=s["id"], name=s["name"]))
            else:
                att.append(PlanAttendee(role=r))
                noms.setdefault(r, []).append(t["key"])
        t["clientAttendees"] = att
    nominations = [PlanNomination(role=r, neededFor=_uniq(keys), askWho=ask_who if r != "sponsor" else "the client's main contact")
                   for r, keys in noms.items()]
    return order, nominations


def team(order: list[dict], roles: dict, applied_ours: list[str], depth: int = 2) -> list[PlanTeamMember]:
    hours: dict[str, float] = {}
    for t in order:
        for r in t.get("ourRoles") or []:
            hours[r] = hours.get(r, 0) + t["minutes"] / 60 * (1 + PREP_FACTOR)
    total = sum(t["minutes"] for t in order) / 60
    hours["solution-architect"] = hours.get("solution-architect", 0) + total * 0.5   # synthesis and the write-up
    for r in applied_ours if depth > 1 else []:
        hours.setdefault(r, 1.0)  # at least a review of the findings
    out = []
    for r, h in sorted(hours.items(), key=lambda kv: -kv[1]):
        out.append(PlanTeamMember(role=r, why=roles["ours"].get(r, {}).get("does", ""), hours=round(h * 2) / 2))
    return out


# ------------------------------------------------------------------ run

@app.post("/v1/run", response_model=EngineRunResponse, tags=["engine"])
async def run_endpoint(req: EngineRunRequest, c: Caller = Depends(caller_dep)):
    eid, params = req.engagementId, req.params or {}
    run = Run(ENGINE, "plan", PROMPT_VERSION, eid, req.actor, req.correlationId)
    L = ledger()
    ctx = await L.get(f"/v1/engagements/{eid}/context", actor=run.agent)
    planning = await L.get("/v1/planning", actor=run.agent)
    packs = {p["key"]: p for p in await L.get("/v1/packs", actor=run.agent)}
    formats = {f["key"]: f for f in planning["formats"]}
    roles = planning["roles"]
    sources = await L.get(f"/v1/engagements/{eid}/sources", actor=run.agent)
    stakeholders = await L.get(f"/v1/engagements/{eid}/stakeholders", actor=run.agent)
    claims = [x for x in await L.get(f"/v1/engagements/{eid}/claims", actor=run.agent) if x["status"] in ("proposed", "confirmed")]

    docs, used = [], 0
    for s in sources:
        if used >= DOC_BUDGET:
            break
        text = (await L.get(f"/v1/engagements/{eid}/sources/{s['id']}", params={"includeText": "true"}, actor=run.agent)).get("text", "")
        piece = text[: DOC_BUDGET - used]
        used += len(piece)
        docs.append(f"--- {s['title']} ({s.get('kind', 'other')}) ---\n{piece}")
    doc_chars = sum(s.get("chars") or 0 for s in sources)

    brief = ctx["brief"]
    shapes = brief["appliedPacks"][1:]
    shape_note = "from the Brief" + (" (agreed)" if brief["shapeState"] == "agreed" else " (draft)")
    if not shapes:
        shapes = infer_shapes(list(packs.values()), [d for d in docs] + [x["statement"] for x in claims])
        shape_note = "inferred from the documents (the Brief has no shape yet)" if shapes else "unknown: core sessions only"
    applied = ["core"] + [k for k in shapes if k in packs]

    rec_key, rec_reason = recommend_format(sources, doc_chars, shapes)
    fmt_key = params.get("format") or rec_key
    if fmt_key not in formats:
        return await run.finish(accepted=0, rejected=1, input_summary="", output_summary=f"Unknown format {fmt_key}.",
                                error=f"Unknown format {fmt_key}")
    fmt = formats[fmt_key]

    order = build_skeleton(fmt, packs, applied, ctx.get("openItems") or [], brief)
    order, nominations = staff(order, roles, stakeholders)
    skeleton = [{"key": t["key"], "title": t["title"], "day": t["day"], "minutes": t["minutes"], "covers": t.get("covers", []),
                 "clientAttendees": [a.role for a in t["clientAttendees"]], "ourAttendees": t.get("ourRoles", []),
                 "agenda": t.get("agenda", []), "questions": [q.model_dump() for q in t["questions"]]} for t in order]

    tailored, model_error = {}, None
    prompt = render(PROMPT, format=f"{fmt['label']}: {fmt['description']} ({fmt['days']} day(s), up to {fmt['maxMinutes']} minutes of client time)",
                    skeleton=json.dumps(skeleton, indent=1), brief=brief_text(ctx),
                    openitems="\n".join(f"[{o['id']}] {o['question']}" for o in (ctx.get("openItems") or []) if o.get("status") == "open") or "none",
                    claims="\n".join(f"[{x['id']}] ({x['question']}/{x['status']}) {x['statement']}" for x in claims[-200:]) or "none",
                    documents="\n\n".join(docs) or "none")
    try:
        tailored = await run.model(prompt, tier="complex")
    except ServiceError as e:
        model_error = str(e.detail)[:300]

    by_key = {x.get("key"): x for x in (tailored.get("sessions") or []) if isinstance(x, dict)} if isinstance(tailored, dict) else {}
    sessions = []
    for t in order:
        m = by_key.get(t["key"], {})
        qs = []
        for q in (m.get("questions") or [])[:8]:
            try:
                qs.append(PlanQuestion.model_validate({k: q.get(k) for k in ("text", "covers", "openItemId", "mustKnow") if q.get(k) is not None}))
            except Exception:
                continue
        sessions.append(PlanSession(
            key=t["key"], title=t["title"], day=t["day"], minutes=t["minutes"], covers=t.get("covers", []),
            purpose=str(m.get("purpose") or _default_purpose(t))[:400],
            clientAttendees=t["clientAttendees"], ourAttendees=t.get("ourRoles", []),
            agenda=[str(a)[:160] for a in (m.get("agenda") or t.get("agenda") or [])][:8],
            questions=qs or t["questions"][:8],
            preReads=[str(x)[:200] for x in (m.get("preReads") or [])][:4],
            outputs=[str(x)[:200] for x in (m.get("outputs") or [])][:3]))

    ours = _uniq([r for k in applied for r in packs[k].get("ourRoles") or []])
    proposal = PlanProposal(
        format=fmt_key, recommendedFormat=rec_key, recommendationReason=rec_reason, startDate=params.get("startDate") or None,
        summary=str(tailored.get("summary") or "")[:800] if isinstance(tailored, dict) else "",
        sessions=sessions, ourTeam=team(order, roles, ours, fmt["depth"]), nominations=nominations,
        requests=[str(x)[:200] for x in (tailored.get("requests") or [])][:8] if isinstance(tailored, dict) else [],
        risks=([str(x)[:200] for x in (tailored.get("risks") or [])][:5] if isinstance(tailored, dict) else [])
              + ([f"{len(nominations)} client function(s) have no named owner yet."] if nominations else []),
        packs=applied, basis={"sources": len(sources), "claims": len(claims), "stakeholders": len(stakeholders), "shape": shape_note},
        runId=run.id, promptVersion=PROMPT_VERSION, model=run.model_name)
    saved = await L.post(f"/v1/engagements/{eid}/plans", json=proposal.model_dump(mode="json"), actor=run.agent)
    return await run.finish(
        accepted=len(sessions), rejected=0,
        rejected_samples=[{"reason": f"Model unavailable, skeleton kept: {model_error}"}] if model_error else None,
        input_summary=f"{len(sources)} sources, {len(claims)} claims, {len(stakeholders)} stakeholders; shape {shape_note}",
        output_summary=f"Plan v{saved['version']}: {fmt['label']}, {len(sessions)} session(s), {saved['totalMinutes']} min of client time"
                       + (f", {len(nominations)} owner(s) to nominate" if nominations else "")
                       + ("" if not model_error else " (model unavailable: skeleton only)"),
        details={"version": saved["version"], "format": fmt_key, "recommended": rec_key})


def _default_purpose(t: dict) -> str:
    return {"discovery-call": "Understand the requirement, who is involved and what happens next."}.get(
        t["key"], f"Cover {', '.join(x.upper() for x in t.get('covers', [])) or 'the topic'} for {t['title'].lower()}.")
