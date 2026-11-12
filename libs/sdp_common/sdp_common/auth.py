"""Service identity and actor propagation.

Every internal call carries:
  Authorization: Bearer <service token>   who is calling (a service)
  X-Actor-Type / X-Actor-Id               on whose behalf (a user, an agent, or the system)
  X-Correlation-Id                        the request chain

INTERNAL_AUTH=token  (local, tests): SDP_SERVICE_TOKENS maps service name -> token.
INTERNAL_AUTH=gcp    (Cloud Run): Cloud Run has already verified the Google ID token
                     (ingress internal + IAM invoker); SDP_SERVICE_ACCOUNTS maps the
                     service account email in that token to a service name.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from fastapi import HTTPException, Request

from .contracts import Actor, ActorType


@dataclass(frozen=True)
class Caller:
    service: str
    actor: Actor
    correlation_id: Optional[str]


@lru_cache(maxsize=1)
def _token_map() -> dict[str, str]:
    raw = os.getenv("SDP_SERVICE_TOKENS", "{}")
    mapping = json.loads(raw)
    return {token: name for name, token in mapping.items()}


@lru_cache(maxsize=1)
def _sa_map() -> dict[str, str]:
    return json.loads(os.getenv("SDP_SERVICE_ACCOUNTS", "{}"))


def _service_from_bearer(auth_header: str) -> str:
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, {"code": "unauthenticated", "message": "Missing service credentials."})
    token = auth_header[7:]
    mode = os.getenv("INTERNAL_AUTH", "token")
    if mode == "token":
        name = _token_map().get(token)
        if not name:
            raise HTTPException(401, {"code": "unauthenticated", "message": "Unknown service token."})
        return name
    if mode == "gcp":
        try:  # signature already verified by Cloud Run's IAM invoker check
            payload = token.split(".")[1] + "=="
            claims = json.loads(base64.urlsafe_b64decode(payload))
        except Exception:
            raise HTTPException(401, {"code": "unauthenticated", "message": "Unreadable ID token."})
        name = _sa_map().get(claims.get("email", ""))
        if not name:
            raise HTTPException(401, {"code": "unauthenticated", "message": "Service account not registered."})
        return name
    raise HTTPException(500, {"code": "misconfigured", "message": f"Unknown INTERNAL_AUTH {mode}"})


def caller_from(request: Request) -> Caller:
    service = _service_from_bearer(request.headers.get("authorization", ""))
    a_type = request.headers.get("x-actor-type", "system")
    a_id = request.headers.get("x-actor-id", service)
    try:
        actor = Actor(type=ActorType(a_type), id=a_id)
    except ValueError:
        raise HTTPException(400, {"code": "invalid_actor", "message": f"Unknown actor type {a_type}"})
    return Caller(service=service, actor=actor, correlation_id=request.headers.get("x-correlation-id"))


_ID_TOKENS: dict[str, tuple[str, float]] = {}


def service_auth_header(audience: str | None = None) -> dict[str, str]:
    """Token mode: the service's shared token. GCP mode: a Google ID token minted for the target service,
    which Cloud Run verifies before the request ever reaches our code."""
    if os.getenv("INTERNAL_AUTH", "token") == "gcp" and audience:
        import time
        cached = _ID_TOKENS.get(audience)
        if not cached or cached[1] < time.time():
            from google.auth.transport.requests import Request as GRequest
            from google.oauth2 import id_token
            _ID_TOKENS[audience] = (id_token.fetch_id_token(GRequest(), audience), time.time() + 3000)
        return {"Authorization": f"Bearer {_ID_TOKENS[audience][0]}"}
    return {"Authorization": f"Bearer {os.getenv('SDP_SERVICE_TOKEN', '')}"}


def actor_headers(actor: Actor | None, correlation_id: str | None = None, audience: str | None = None) -> dict[str, str]:
    headers = service_auth_header(audience)
    if actor:
        headers["X-Actor-Type"] = actor.type.value
        headers["X-Actor-Id"] = actor.id
    if correlation_id:
        headers["X-Correlation-Id"] = correlation_id
    return headers
