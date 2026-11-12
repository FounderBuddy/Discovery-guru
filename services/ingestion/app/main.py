"""Ingestion: turns an uploaded file or pasted text into a stored source, then announces it.

Reads .txt .md .vtt .srt .csv .json .pdf .docx. Stores the text in the ledger on behalf of the uploader,
then publishes sdp.source.added.v1 so the extraction engine can pick it up.
"""
from __future__ import annotations

import io
import re
from typing import Optional

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from sdp_common.auth import Caller, caller_from
from sdp_common.clients import ledger
from sdp_common.events import make_event, publisher
from sdp_common.service import create_app

app = create_app("ingestion", "Turns uploads into stored sources and announces them on the bus.")
MAX_BYTES = 25 * 1024 * 1024


def caller_dep(request: Request) -> Caller:
    return caller_from(request)


def extract_text(name: str, data: bytes) -> str:
    lower = name.lower()
    if lower.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((p.extract_text() or "") for p in reader.pages)
    if lower.endswith(".docx"):
        import docx
        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        return "\n".join(parts)
    if lower.endswith((".txt", ".md", ".csv", ".json", ".vtt", ".srt", ".html", ".htm")):
        text = data.decode("utf-8", errors="replace")
        if lower.endswith((".vtt", ".srt")):  # drop cue numbers and timing lines, keep the words
            text = "\n".join(l for l in text.splitlines()
                             if l.strip() and l.strip() != "WEBVTT" and "-->" not in l and not l.strip().isdigit())
        return text
    raise HTTPException(415, {"code": "unsupported", "message": "Use .txt, .md, .vtt, .srt, .csv, .json, .pdf or .docx, or paste the text."})


class TextSourceIn(BaseModel):
    title: str
    kind: str = "other"
    ruleCategory: Optional[str] = None
    text: str


async def _store(eid: str, c: Caller, title: str, kind: str, rule_category: Optional[str], file_name: str, text: str):
    text = re.sub(r"\r\n?", "\n", text).strip()
    if not text:
        raise HTTPException(422, {"code": "empty", "message": "No text could be read from this source."})
    body = {"title": title, "kind": kind, "fileName": file_name, "text": text}
    if rule_category:
        body["ruleCategory"] = rule_category
    src = await ledger().post(f"/v1/engagements/{eid}/sources", json=body, actor=c.actor, correlation_id=c.correlation_id)
    await publisher.publish(make_event("sdp.source.added.v1", "ingestion",
                                       {"sourceId": src["id"], "title": src["title"], "kind": src["kind"], "chars": src["chars"]},
                                       engagement_id=eid, actor=c.actor, correlation_id=c.correlation_id, subject=src["id"]))
    return src


@app.post("/v1/engagements/{eid}/sources/upload", status_code=201, tags=["ingestion"])
async def upload(eid: str, file: UploadFile = File(...), title: str = Form(""), kind: str = Form("other"),
                 ruleCategory: str = Form(""), c: Caller = Depends(caller_dep)):
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, {"code": "too_large", "message": "Files are limited to 25 MB."})
    text = extract_text(file.filename or "upload.txt", data)
    return await _store(eid, c, title or file.filename or "Untitled source", kind, ruleCategory or None, file.filename or "", text)


@app.post("/v1/engagements/{eid}/sources/text", status_code=201, tags=["ingestion"])
async def paste(eid: str, body: TextSourceIn, c: Caller = Depends(caller_dep)):
    return await _store(eid, c, body.title, body.kind, body.ruleCategory, "", body.text)
