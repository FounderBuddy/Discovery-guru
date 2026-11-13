"""API gateway: the only service the browser talks to.

It signs people in (AUTH_MODE=dev for local work, AUTH_MODE=iap behind Google Identity-Aware Proxy),
checks engagement roles before invoking engines, and forwards everything else to the owning service
with the person's identity attached. It holds no data and no business rules of its own.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from sdp_common.auth import actor_headers
from sdp_common.clients import ServiceError, ledger, service
from sdp_common.contracts import Actor, ActorType, EngineRunRequest
from sdp_common.service import create_app

app = create_app("api-gateway", "Sign-in, role checks and routing for the web app.")
AUTH_MODE = os.getenv("AUTH_MODE", "dev")
ENGINES = {
    "extraction": ("ENGINE_EXTRACTION_URL", "http://localhost:8010", {"owner", "contributor"}),
    "divergence": ("ENGINE_DIVERGENCE_URL", "http://localhost:8011", {"owner", "contributor"}),
    "questions": ("ENGINE_QUESTIONS_URL", "http://localhost:8012", {"owner", "contributor"}),
    "recommendation": ("ENGINE_RECOMMENDATION_URL", "http://localhost:8013", {"owner", "contributor"}),
    "resolution": ("ENGINE_RESOLUTION_URL", "http://localhost:8014", {"owner"}),
    "brief": ("ENGINE_BRIEF_URL", "http://localhost:8016", {"owner", "contributor"}),
}


# ------------------------------------------------------------------ sign-in

def _iap_user(request: Request) -> tuple[str, str]:
    from google.auth.transport import requests as greq  # optional dependency: sdp-common[iap]
    from google.oauth2 import id_token
    assertion = request.headers.get("x-goog-iap-jwt-assertion", "")
    claims = id_token.verify_token(assertion, greq.Request(), audience=os.environ["IAP_AUDIENCE"],
                                   certs_url="https://www.gstatic.com/iap/verify/public_key")
    return claims["sub"].replace("accounts.google.com:", "u_"), claims.get("email", "")


async def current_user(request: Request) -> Actor:
    if AUTH_MODE == "iap":
        try:
            uid, email = _iap_user(request)
        except Exception:
            raise HTTPException(401, {"code": "unauthenticated", "message": "Sign in through the company login."})
        await ledger().post("/v1/users", json={"id": uid, "email": email, "name": email.split("@")[0]},
                            actor=Actor(type=ActorType.system, id="api-gateway"))
        return Actor(type=ActorType.user, id=uid)
    uid = request.headers.get("x-dev-user") or request.cookies.get("sdp_dev_user")
    if not uid:
        raise HTTPException(401, {"code": "unauthenticated", "message": "Choose who you are on the sign-in screen."})
    return Actor(type=ActorType.user, id=uid)


async def role_in(eid: str, user: Actor) -> Optional[str]:
    try:
        e = await ledger().get(f"/v1/engagements/{eid}", actor=user)
    except ServiceError as err:
        if err.status in (403, 404):
            return None
        raise
    return e.get("myRole")


# ------------------------------------------------------------------ session

@app.get("/api/session/dev-users", tags=["session"])
async def dev_users(q: str = ""):
    if AUTH_MODE != "dev":
        raise HTTPException(404, {"code": "not_found", "message": "Not available."})
    return await ledger().get("/v1/users", params={"q": q}, actor=Actor(type=ActorType.system, id="api-gateway"))


@app.get("/api/me", tags=["session"])
async def me(request: Request):
    user = await current_user(request)
    profile = await ledger().get(f"/v1/users/{user.id}", actor=user)
    return {**profile, "authMode": AUTH_MODE}


@app.get("/api/directory", tags=["session"])
async def directory(request: Request, q: str = ""):
    user = await current_user(request)
    return await ledger().get("/v1/users", params={"q": q}, actor=user)


# ------------------------------------------------------------------ engines and outputs

@app.post("/api/engagements/{eid}/run/{engine}", tags=["engines"])
async def run_engine(eid: str, engine: str, request: Request):
    user = await current_user(request)
    if engine not in ENGINES:
        raise HTTPException(404, {"code": "not_found", "message": f"No engine called {engine}."})
    url_env, default, roles = ENGINES[engine]
    role = await role_in(eid, user)
    if role not in roles:
        raise HTTPException(403, {"code": "role", "message": f"Running the {engine} engine needs one of: {', '.join(sorted(roles))}."})
    params = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    body = EngineRunRequest(engagementId=eid, params=params, actor=user,
                            correlationId=request.headers.get("x-correlation-id")).model_dump(mode="json")
    return await service(url_env, default).post("/v1/run", json=body, actor=user)


@app.get("/api/engagements/{eid}/outputs/{kind}", tags=["engines"])
async def outputs(eid: str, kind: str, request: Request):
    user = await current_user(request)
    if await role_in(eid, user) is None:
        raise HTTPException(403, {"code": "not_member", "message": "You are not a participant in this engagement."})
    text = await service("ENGINE_ARTEFACTS_URL", "http://localhost:8015").get(f"/v1/engagements/{eid}/outputs/{kind}", actor=user)
    return PlainTextResponse(text, media_type="text/csv" if kind == "claims-register" else "text/markdown")


@app.post("/api/engagements/{eid}/sources/upload", tags=["evidence"])
async def upload(eid: str, request: Request):
    user = await current_user(request)
    body = await request.body()
    async with httpx.AsyncClient(timeout=120) as c:
        base = os.getenv('INGESTION_URL', 'http://localhost:8004')
        r = await c.post(f"{base}/v1/engagements/{eid}/sources/upload",
                         content=body, headers={**actor_headers(user, audience=base), "content-type": request.headers.get("content-type", "")})
    return JSONResponse(status_code=r.status_code, content=r.json())


@app.post("/api/engagements/{eid}/sources/text", tags=["evidence"], status_code=201)
async def paste(eid: str, request: Request):
    user = await current_user(request)
    return await service("INGESTION_URL", "http://localhost:8004").post(f"/v1/engagements/{eid}/sources/text",
                                                                       json=await request.json(), actor=user)


@app.get("/api/system/providers", tags=["system"])
async def providers(request: Request):
    user = await current_user(request)
    return await service("MODEL_GATEWAY_URL", "http://localhost:8003").get("/v1/providers", actor=user)


@app.get("/api/engagements/{eid}/events", tags=["system"])
async def bus_events(eid: str, request: Request):
    user = await current_user(request)
    if await role_in(eid, user) is None:
        raise HTTPException(403, {"code": "not_member", "message": "You are not a participant in this engagement."})
    if os.getenv("BUS_BACKEND", "http") != "http":
        return []
    return await service("EVENT_BUS_URL", "http://localhost:8002").get("/v1/events", params={"engagement": eid, "limit": 50}, actor=user)


# ------------------------------------------------------------------ everything else goes to the ledger

@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], tags=["ledger"])
async def to_ledger(path: str, request: Request):
    user = await current_user(request)
    body = await request.body()
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.request(request.method, f"{ledger().base}/v1/{path}", params=dict(request.query_params),
                            content=body or None, headers={**actor_headers(user, request.headers.get("x-correlation-id"), audience=ledger().base),
                                                           "content-type": "application/json"})
    return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))


# ------------------------------------------------------------------ the web app (optional: same origin for local runs)

WEB_DIST = Path(os.getenv("WEB_DIST", "web/dist"))
if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        target = WEB_DIST / full_path
        return FileResponse(target if full_path and target.is_file() else WEB_DIST / "index.html")
