"""Event bus (local and test transport). Receives CloudEvents, keeps a replayable log, pushes to subscribers.

On GCP this service is not deployed: services publish the same CloudEvents to Pub/Sub (BUS_BACKEND=pubsub)
and Pub/Sub push subscriptions deliver them to the same /v1/events endpoints.
"""
from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

import httpx
from fastapi import BackgroundTasks, Depends, HTTPException, Request

from sdp_common.auth import Caller, actor_headers, caller_from
from sdp_common.contracts import EVENT_TYPES
from sdp_common.events import CloudEvent
from sdp_common.service import create_app

app = create_app("event-bus", "CloudEvents transport with a replayable log. Swapped for Pub/Sub on GCP.")
DB_PATH = os.getenv("BUS_DB_PATH", "./data/bus.db")
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
_lock = threading.Lock()


def _conn():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.execute("""CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, type TEXT, source TEXT, time TEXT,
                   engagement TEXT, body TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS deliveries (event_id TEXT, subscriber TEXT, status INTEGER,
                   attempts INTEGER, at TEXT)""")
    return con


CON = _conn()


def subscriptions() -> dict[str, list[str]]:
    """type pattern -> subscriber URLs. From SDP_SUBSCRIPTIONS (JSON) so wiring is configuration, not code."""
    return json.loads(os.getenv("SDP_SUBSCRIPTIONS", "{}"))


def caller_dep(request: Request) -> Caller:
    return caller_from(request)


async def deliver(event: dict, url: str) -> None:
    attempts, status = 0, 0
    for attempts in range(1, 4):
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(url, json=event, headers={**actor_headers(None, audience=url.split("/v1/")[0]), "ce-type": event["type"]})
            status = r.status_code
            if status < 500:
                break
        except Exception:
            status = 0
        await asyncio.sleep(0.5 * attempts)
    with _lock:
        CON.execute("INSERT INTO deliveries VALUES (?,?,?,?,?)",
                    (event["id"], url, status, attempts, datetime.now(timezone.utc).isoformat()))
        CON.commit()


@app.post("/v1/publish", status_code=202, tags=["bus"])
async def publish(event: CloudEvent, bg: BackgroundTasks, c: Caller = Depends(caller_dep)):
    model = EVENT_TYPES.get(event.type)
    if model is None:
        raise HTTPException(422, {"code": "unknown_type", "message": f"No contract for {event.type}."})
    model.model_validate(event.data)  # the bus refuses packets that break their contract
    body = event.model_dump(mode="json", exclude_none=True)
    with _lock:
        CON.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?)",
                    (event.id, event.type, event.source, event.time, event.sdpengagement, json.dumps(body)))
        CON.commit()
    targets = [url for pattern, urls in subscriptions().items() if fnmatch.fnmatch(event.type, pattern) for url in urls]
    for url in targets:
        bg.add_task(deliver, body, url)
    return {"accepted": event.id, "deliveredTo": targets}


@app.get("/v1/events", tags=["bus"])
def events(engagement: str | None = None, type: str | None = None, limit: int = 100, c: Caller = Depends(caller_dep)):
    sql, args = "SELECT body FROM events WHERE 1=1", []
    if engagement:
        sql += " AND engagement=?"; args.append(engagement)
    if type:
        sql += " AND type=?"; args.append(type)
    sql += " ORDER BY time DESC LIMIT ?"; args.append(min(limit, 500))
    with _lock:
        rows = CON.execute(sql, args).fetchall()
    return [json.loads(r[0]) for r in rows]


@app.get("/v1/subscriptions", tags=["bus"])
def subs(c: Caller = Depends(caller_dep)):
    return subscriptions()
