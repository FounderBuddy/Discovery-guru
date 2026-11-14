"""Artefacts engine: builds outputs from the ledger. Read-only; no model calls, so outputs are reproducible.

Kinds: discovery-summary, open-items, decision-log, divergence-log, recommendations, claims-register (CSV).
"""
from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from sdp_common.auth import Caller, caller_from
from sdp_common.clients import ledger
from sdp_common.contracts import Actor, ActorType
from sdp_common.service import create_app

ENGINE = "engine-artefacts"
app = create_app(ENGINE, "Builds the proposal inputs: registers, logs and summaries, all traceable.")
ME = Actor(type=ActorType.agent, id=ENGINE)
Q = {"why": "WHY: business drivers", "what": "WHAT: outcomes and use cases", "rules": "RULES: guardrails",
     "from": "FROM: current estate", "how": "HOW: approaches and design"}
OUTCOME = {"unclassified": "Unclassified", "insist": "Insist", "estimate": "Estimate", "risk": "Risk",
           "pending": "Pending decision", "design_around": "Design around"}


def caller_dep(request: Request) -> Caller:
    return caller_from(request)


def cite(c: dict, sources: dict) -> str:
    if c.get("evidence") == "none":
        return "_assumption: entered by a person, no client evidence_"
    src = sources.get(c.get("sourceId"), "source")
    who = f"{c['speaker']}, " if c.get("speaker") else ""
    return f"{who}{src}: “{c.get('quote', '')[:220]}”"


async def gather(eid: str) -> dict:
    L = ledger()
    get = lambda p: L.get(f"/v1/engagements/{eid}{p}", actor=ME)
    return {"engagement": await get(""), "claims": await get("/claims"), "sources": await get("/sources"),
            "openItems": await get("/open-items"), "decisions": await get("/decisions"),
            "divergences": await get("/divergences"), "recs": await get("/recommendations"),
            "rules": await get("/rules"), "stakeholders": await get("/stakeholders"), "brief": await get("/brief"),
            "plan": await get("/plan"), "planning": await L.get("/v1/planning", actor=ME)}


def header(d: dict, title: str) -> list[str]:
    e = d["engagement"]
    return [f"# {title}", "", f"**{e['name']}** · {e['client']} · generated {date.today().isoformat()}", ""]


STATE = {"empty": "not yet known", "draft": "draft, not agreed", "agreed": "agreed", "needs_review": "needs review: evidence changed"}


def brief_section(d: dict) -> list[str]:
    b = d["brief"]
    out = ["## Brief", "", f"_{b['agreed']} of {b['total']} slots agreed by the solution owner._", ""]
    for s in b["slots"]:
        text = s["text"] or "—"
        if s["key"] == "shape" and s.get("shapes"):
            labels = {p["key"]: p["label"] for p in b["packs"]}
            text = ", ".join(labels.get(k, k) for k in s["shapes"]) + (f". {s['text']}" if s["text"] else "")
        out += [f"**{s['label']}** ({STATE.get(s['status'], s['status'])}): {text}"]
        if s.get("gaps") and s["status"] != "agreed":
            out += [f"  - still to learn: {'; '.join(s['gaps'])}"]
        out += [""]
    return out


def discovery_summary(d: dict) -> str:
    srcs = {s["id"]: s["title"] for s in d["sources"]}
    live = [c for c in d["claims"] if c["status"] == "confirmed"]
    out = header(d, "Discovery summary")
    out += brief_section(d)
    people = [s for s in d["stakeholders"] if s["status"] == "confirmed"]
    if people:
        out += ["## People", ""] + [f"- **{p['name']}**{', ' + p['title'] if p['title'] else ''}{' · ' + p['type'] if p['type'] else ''}" for p in people] + [""]
    for q, label in Q.items():
        items = [c for c in live if c["question"] == q]
        out += [f"## {label}", ""]
        if not items:
            out += ["_Nothing confirmed yet._", ""]
            continue
        for c in items:
            num = f" ({c['number']['value']:g} {c['number'].get('unit', '')})" if c.get("number") else ""
            strength = f" [{c['strength']}]" if c.get("strength") else ""
            out += [f"- {c['statement']}{num}{strength}", f"  - {cite(c, srcs)}"]
        out.append("")
    out += ["## Guardrail checklist", "", "| Category | Status | Gap decision |", "|---|---|---|"]
    out += [f"| {r['label']} | {r['status']} | {r.get('gapDecision') or ('needed' if r['needsGapDecision'] else '')} |" for r in d["rules"]]
    return "\n".join(out) + "\n"


def open_items(d: dict) -> str:
    out = header(d, "Open items register") + ["| # | Question | Demands | Resolution | Owner | Due | Escalation | Status |", "|---|---|---|---|---|---|---|---|"]
    for i, o in enumerate(d["openItems"], 1):
        due = (o.get("dueDate") or "") + (" (overdue)" if o.get("overdue") else "")
        out.append(f"| {i} | {o['question']} | {o['kind']} | {OUTCOME.get(o['outcome'], o['outcome'])} | {o.get('ownerId') or 'unassigned'} | {due} | {o['escalationLevel']} | {o['status']} |")
    return "\n".join(out) + "\n"


def decision_log(d: dict) -> str:
    out = header(d, "Decision log")
    for x in d["decisions"]:
        out += [f"## ADR-{x['number']:03d} {x['title']}", "",
                f"Status: **{x['status']}** · Source: {'client decision' if x['source'] == 'client' else 'our decision'}", ""]
        if x.get("context"): out += ["**Context.** " + x["context"], ""]
        if x.get("options"): out += ["**Options.**"] + [f"- {o}" for o in x["options"]] + [""]
        if x.get("decision"): out += ["**Decision.** " + x["decision"], ""]
        if x.get("rationale"): out += ["**Rationale.** " + x["rationale"], ""]
    return "\n".join(out) + "\n"


def divergence_log(d: dict) -> str:
    srcs = {s["id"]: s["title"] for s in d["sources"]}
    claims = {c["id"]: c for c in d["claims"]}
    out = header(d, "Divergence log")
    for x in d["divergences"]:
        a, b = claims.get(x["claimA"], {}), claims.get(x["claimB"], {})
        out += [f"## {x['type'].replace('_', ' ').capitalize()} · {x['severity']} · {x['status']}", "", x["explanation"], "",
                f"- A: {a.get('statement', '?')} — {cite(a, srcs) if a else ''}",
                f"- B: {b.get('statement', '?')} — {cite(b, srcs) if b else ''}"]
        if x.get("resolution"): out += [f"- Resolution: {x['resolution'].replace('_', ' ')}. {x.get('resolutionNote', '')}"]
        out.append("")
    return "\n".join(out) + "\n"


def recommendations(d: dict) -> str:
    out = header(d, "Recommendations")
    for r in d["recs"]:
        out += [f"## {r['path']}", "", f"Level: **{r['level'].replace('_', ' ')}** · Status: **{r['status'].replace('_', ' ')}**", "",
                r["recommendation"], "", "**Rationale.** " + r.get("rationale", ""), ""]
        if r.get("alternatives"): out += ["**Alternatives considered.**"] + [f"- {a['option']}: {a['whyNot']}" for a in r["alternatives"]] + [""]
        if r.get("assumptions"): out += ["**Assumptions.**"] + [f"- {a}" for a in r["assumptions"]] + [""]
    return "\n".join(out) + "\n"


def claims_register(d: dict) -> str:
    srcs = {s["id"]: s["title"] for s in d["sources"]}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "status", "question", "kind", "statement", "quote", "speaker", "source", "evidence", "confirmedBy", "supersededBy", "runId", "promptVersion", "model"])
    for c in d["claims"]:
        w.writerow([c["id"], c["status"], c["question"], c["kind"], c["statement"], c.get("quote", ""), c.get("speaker", ""),
                    srcs.get(c.get("sourceId"), ""), c.get("evidence", ""), c.get("confirmedBy") or "", c.get("supersededBy") or "",
                    c.get("runId") or "", c.get("promptVersion") or "", c.get("model") or ""])
    return buf.getvalue()


def discovery_plan(d: dict) -> str:
    """Client-facing: the sessions, who we'd like in each, the agenda, and what to send beforehand.
    Our internal effort, risks and nominations stay in the app."""
    p = (d.get("plan") or {}).get("plan")
    out = header(d, "Discovery plan")
    if not p:
        return "\n".join(out + ["_No plan yet: run the Discovery Plan engine._"])
    croles = d["planning"]["roles"]["client"]
    oroles = d["planning"]["roles"]["ours"]
    status = "approved" if p["status"] == "approved" else f"draft (version {p['version']}, not yet approved)"
    out += [f"**Format:** {p['formatLabel']} · {len(p['sessions'])} session(s) · {p['totalMinutes']} minutes of your time · {status}", ""]
    if p.get("summary"):
        out += [p["summary"], ""]
    if p.get("requests"):
        out += ["## Before we start, please send", ""] + [f"- {r}" for r in p["requests"]] + [""]
    from datetime import date, timedelta
    def when(day: int) -> str:
        if not p.get("startDate"):
            return f"Day {day}"
        dd, left = date.fromisoformat(p["startDate"]), day - 1
        while dd.weekday() > 4:
            dd += timedelta(days=1)
        while left:
            dd += timedelta(days=1)
            left -= dd.weekday() < 5
        return dd.strftime("%a %d %b %Y")
    for x in sorted(p["sessions"], key=lambda x: x["day"]):
        who = [a["name"] + f" ({croles.get(a['role'], {}).get('label', a['role'])})" if a.get("name")
               else f"{croles.get(a['role'], {}).get('label', a['role'])} (to be confirmed)" for a in x["clientAttendees"]]
        out += [f"## {x['title']}", "", f"**{when(x['day'])} · {x['minutes']} minutes**", ""]
        if x.get("purpose"):
            out += [x["purpose"], ""]
        out += [f"**Your side:** {', '.join(who) or 'to be confirmed'}",
                f"**Our side:** {', '.join(oroles.get(r, {}).get('label', r) for r in x['ourAttendees'])}", ""]
        if x.get("agenda"):
            out += ["**Agenda**", ""] + [f"{i}. {a}" for i, a in enumerate(x["agenda"], 1)] + [""]
        if x.get("questions"):
            out += ["**Questions we'd like to cover**", ""] + [f"- {q['text']}" for q in x["questions"]] + [""]
        if x.get("preReads"):
            out += ["**Helpful to have beforehand**", ""] + [f"- {r}" for r in x["preReads"]] + [""]
    return "\n".join(out)


BUILDERS = {"discovery-plan": discovery_plan, "discovery-summary": discovery_summary, "open-items": open_items, "decision-log": decision_log,
            "divergence-log": divergence_log, "recommendations": recommendations, "claims-register": claims_register}


@app.get("/v1/engagements/{eid}/outputs/{kind}", response_class=PlainTextResponse, tags=["engine"])
async def output(eid: str, kind: str, c: Caller = Depends(caller_dep)):
    if kind not in BUILDERS:
        raise HTTPException(404, {"code": "not_found", "message": f"No output called {kind}."})
    return BUILDERS[kind](await gather(eid))
