"""Deterministic offline model. It reads the same prompts the real models read and answers with
contract-shaped JSON, so every engine, the ledger's checks and the UI can be exercised without keys.

It is deliberately imperfect in one respect: each extraction part includes one claim whose quote is
not in the source, so the citation check is visibly exercised in every local run.
"""
from __future__ import annotations

import json
import re

from sdp_common.text import signal_hits

UNITS = r"(tb|gb|reports|report|weeks|months|days|servers|vms|sources|users|people|%|years)"
NUM = re.compile(r"(\d+(?:\.\d+)?)\s*" + UNITS, re.I)
PREFIX = re.compile(r"^\s*(?:\[[^\]]*\]\s*)?([A-Z][\w .'-]{1,40}?):\s+")


def _section(prompt: str, name: str) -> list[str]:
    m = re.search(rf"{name}:\n(.*?)(?:\n[A-Z][A-Z ]+:\n|\Z)", prompt, re.S)
    return [l for l in (m.group(1).splitlines() if m else []) if l.strip()]


def _extract(prompt: str) -> dict:
    m = re.search(r"<<<\n(.*)\n>>>", prompt, re.S)
    text = m.group(1) if m else ""
    claims, speakers = [], []
    for line in text.splitlines():
        pm = PREFIX.match(line)
        speaker = pm.group(1).strip() if pm else ""
        body = line[pm.end():] if pm else line
        if speaker and speaker not in speakers:
            speakers.append(speaker)
        for sent in re.split(r"(?<=[.!?])\s+", body.strip()):
            if len(sent) < 40:
                continue
            low = sent.lower()
            num = NUM.search(sent)
            number = {"value": float(num.group(1)), "unit": num.group(2).lower()} if num else None
            if any(w in low for w in ("must", "policy", "compliance", "soc 2", "residen", "mandate",
                                      "multi-cloud", "multi cloud", "standard", "not allowed", "approval")):
                cat = ("regulatory" if any(w in low for w in ("soc 2", "residen", "compliance"))
                       else "strategic" if "multi" in low else
                       "organisational" if "approv" in low else "architecture")
                q, kind = "rules", "guardrail"
            elif any(w in low for w in ("takes", "manual", "slow", "pain", "problem", "frustrat", "by hand")):
                q, kind, cat = "why", "pain_point", None
            elif any(w in low for w in ("want", "goal", "need to", "aim", "so that", "would like")):
                q, kind, cat = "what", "outcome", None
            elif any(w in low for w in ("we have", "we run", "currently", "today", "server", "database", "warehouse", " tb")):
                q, kind, cat = "from", "estate_fact", None
            else:
                continue
            claims.append({"statement": sent, "quote": sent, "question": q, "kind": kind,
                           "ruleCategory": cat, "number": number, "speaker": speaker, "confidence": "high"})
    claims = claims[:40]
    claims.append({"statement": "Mock model paraphrase that should be caught.",
                   "quote": "This sentence does not appear anywhere in the source text.",
                   "question": "from", "kind": "estate_fact", "ruleCategory": None, "number": None,
                   "speaker": "", "confidence": "low"})
    return {"claims": claims, "people": [{"name": s, "title": "", "organisation": "", "type": ""} for s in speakers]}


def _claim_lines(prompt: str) -> list[dict]:
    out = []
    for line in _section(prompt, "CLAIMS"):
        m = re.match(r"\[([^\]]+)\]\s*(?:\(([^)]*)\)\s*)?(.*)", line.strip())
        if m:
            out.append({"id": m.group(1), "tags": m.group(2) or "", "text": m.group(3)})
    return out


def _diverge(prompt: str) -> dict:
    found, seen = [], {}
    for c in _claim_lines(prompt):
        n = NUM.search(c["text"])
        if not n:
            continue
        unit = n.group(2).lower().rstrip("s")
        val = float(n.group(1))
        if unit in seen and seen[unit][1] != val:
            found.append({"a": seen[unit][0], "b": c["id"], "type": "different_figures", "severity": "high",
                          "explanation": f"Two different figures for {unit}s: {seen[unit][1]:g} and {val:g}."})
        else:
            seen.setdefault(unit, (c["id"], val))
    return {"divergences": found[:20]}


def _questions(prompt: str) -> dict:
    items = []
    for line in _section(prompt, "RULES CHECKLIST"):
        m = re.match(r"-\s*(\w+)\s*\(([^)]*)\):\s*unknown", line.strip())
        if m:
            items.append({"question": f"Is there a documented {m.group(2).lower()} position this solution must follow? Please share the document.",
                          "kind": "artefact", "outcome": "risk", "impact": f"{m.group(2)} guardrails are unknown, so design choices may need rework.",
                          "askRole": "Security or architecture lead", "relatedClaimIds": []})
    shaped = [l for l in _section(prompt, "MUST KNOW") if "(Every engagement)" not in l]
    for line in shaped[:4]:
        m = re.match(r"-\s*(.*)\s+\(([^)]*)\)$", line.strip())
        if m:
            items.append({"question": f"Can you share this, please: {m.group(1)}?", "kind": "artefact", "outcome": "insist",
                          "impact": f"Needed to scope the {m.group(2).lower()} work.", "askRole": "Technical lead", "relatedClaimIds": []})
    for c in _claim_lines(prompt):
        if c["tags"].startswith("why") and "number:" not in c["text"]:
            items.append({"question": f"What is today's baseline for this, as a number? {c['text'][:140]}",
                          "kind": "number", "outcome": "estimate", "impact": "Without a baseline the outcome can't be measured.",
                          "askRole": "Business sponsor", "relatedClaimIds": [c["id"]]})
    return {"items": items[:12]}


def _recommend(prompt: str) -> dict:
    path = (re.search(r"PATH:\s*(.+)", prompt) or [None, "the path"])[1].strip()
    words = {w for w in re.findall(r"[a-z]+", path.lower()) if len(w) > 3}
    practices = []
    for line in _section(prompt, "DATATONIC PRACTICES"):
        m = re.match(r"\[([^\]]+)\]\s*(.*)", line.strip())
        if m and (words & set(re.findall(r"[a-z]+", m.group(2).lower()))):
            practices.append((m.group(1), m.group(2)))
    rules = []
    for line in _section(prompt, "CLIENT RULES"):
        m = re.match(r"\[([^\]]+)\]\s*\((hard|soft)\)\s*(.*)", line.strip())
        if m:
            rules.append((m.group(1), m.group(2), m.group(3)))
    claims = [c["id"] for c in _claim_lines(prompt)][:5]
    conflicts = []
    for rid, strength, text in rules:
        if strength == "hard" and ("multi-cloud" in text.lower() or "multi cloud" in text.lower()):
            for pid, ptext in practices:
                if any(w in ptext.lower() for w in ("native", "single cloud", "only")):
                    conflicts.append({"practiceId": pid, "claimId": rid,
                                      "description": f"Client rule requires multi-cloud; practice says: {ptext[:120]}"})
    return {
        "recommendation": f"For {path}, follow the matching Datatonic practices and build on the confirmed estate facts.",
        "rationale": "Mock rationale: practices first, then best practice, with client rules respected.",
        "level": "practice" if practices else "best_practice",
        "practiceIds": [p for p, _ in practices], "claimIds": claims,
        "alternatives": [{"option": "Lift and shift as-is", "whyNot": "Carries today's cost and complexity forward."}],
        "assumptions": ["Mock assumption: volumes stay within the stated figures."],
        "conflicts": conflicts,
    }


def _brief(prompt: str) -> dict:
    claims = _claim_lines(prompt)
    by = lambda q, *kinds: [c for c in claims if c["tags"].startswith(q) and (not kinds or c["tags"].split("/")[-1] in kinds)]
    why, what, frm = by("why"), by("what"), by("from")
    numbered = lambda cs: [c for c in cs if NUM.search(c["text"])]
    slot = lambda key, cs, text, gaps: {"key": key, "text": text if cs else "", "claimIds": [c["id"] for c in cs], "shapes": [], "gaps": [] if cs else gaps}
    slots = [
        slot("purpose", (what[:1] + why[:1]), " ".join(c["text"] for c in (what[:1] + why[:1])),
             ["What the client is trying to achieve, in their words", "Why now: the date or event driving it"]),
        slot("value", numbered(why)[:2], " ".join(c["text"] for c in numbered(why)[:2]),
             ["A cost, time or risk figure for today's pain"]),
        slot("measures", numbered(what)[:3], "; ".join(c["text"] for c in numbered(what)[:3]),
             ["A measurable target for each outcome", "Today's baseline for each target"]),
        slot("scope", frm[:3], "Covers: " + "; ".join(c["text"] for c in frm[:3]),
             ["Which systems, teams and processes are in"]),
        slot("nonGoals", [c for c in claims if re.search(r"out of scope|not in scope|not part of|excluded", c["text"], re.I)][:2],
             "; ".join(c["text"] for c in claims if re.search(r"out of scope|not in scope|not part of|excluded", c["text"], re.I)),
             ["What the client has said is out of scope"]),
    ]
    scores, hits = {}, {}
    for line in _section(prompt, "PACKS"):
        m = re.match(r"([\w-]+):.*\|\s*signals:\s*(.*)$", line.strip())
        if not m:
            continue
        sigs = [x.strip().lower() for x in m.group(2).split(",") if x.strip()]
        matched = [c for c in claims if signal_hits(sigs, c["text"])]
        if matched:
            scores[m.group(1)], hits[m.group(1)] = len(matched), matched
    shapes = [k for k, n in sorted(scores.items(), key=lambda kv: -kv[1]) if n >= 2][:3]
    cited = list({c["id"]: c for k in shapes for c in hits[k][:3]}.values())
    slots.append({"key": "shape", "text": f"Signals in the confirmed claims point to: {', '.join(shapes)}." if shapes else "",
                  "claimIds": [c["id"] for c in cited], "shapes": shapes,
                  "gaps": [] if shapes else ["What kind of work this is: platform, migration, integration, agents or an application"]})
    return {"slots": slots}


def _plan(prompt: str) -> dict:
    m = re.search(r"SKELETON:\n(.*?)\n\nBRIEF:", prompt, re.S)
    skeleton = json.loads(m.group(1)) if m else []
    claims = _claim_lines(prompt)
    numbered = [c for c in claims if NUM.search(c["text"])]
    out = []
    for i, t in enumerate(skeleton):
        qs = list(t.get("questions") or [])
        if i < len(numbered):  # one evidence-specific question per session, from a claim with a figure in it
            qs.insert(0, {"text": f"You said: \"{numbered[i]['text'][:90]}\". What is today's baseline, and who measures it?", "covers": "what"})
        out.append({"key": t["key"], "purpose": f"Leave with a clear, shared view of {t['title'].lower()}.",
                    "agenda": t.get("agenda") or [], "questions": qs[:8],
                    "preReads": [f"Anything you already have on {t['title'].lower()}"], "outputs": [f"Notes on {t['title'].lower()} for the Brief"]})
    return {"summary": f"{len(skeleton)} session(s) to build the Brief and the current-state picture.",
            "sessions": out, "requests": ["Organisation chart for the teams involved", "Any existing architecture diagrams"],
            "risks": ["Sessions may slip if the right people cannot attend."]}


def respond(task: str, prompt: str) -> str:
    fn = {"extraction": _extract, "divergence": _diverge, "questions": _questions, "recommendation": _recommend,
          "brief": _brief, "plan": _plan}.get(task)
    return json.dumps(fn(prompt) if fn else {"text": "mock"})
