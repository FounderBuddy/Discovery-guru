"""Thin HTTP clients. A service talks to another only through these, by URL from the environment."""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from .auth import actor_headers
from .contracts import Actor


class ServiceError(Exception):
    def __init__(self, status: int, detail: Any):
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


class ServiceClient:
    def __init__(self, url_env: str, default_url: str, timeout: float = 30.0):
        self.base = os.getenv(url_env, default_url).rstrip("/")
        self.timeout = timeout

    async def call(self, method: str, path: str, *, actor: Actor | None = None,
                   correlation_id: str | None = None, json: Any = None,
                   params: dict | None = None, timeout: float | None = None) -> Any:
        async with httpx.AsyncClient(timeout=timeout or self.timeout) as client:
            r = await client.request(method, f"{self.base}{path}", json=json, params=params,
                                     headers=actor_headers(actor, correlation_id, audience=self.base))
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            raise ServiceError(r.status_code, detail)
        if r.status_code == 204 or not r.content:
            return None
        ctype = r.headers.get("content-type", "")
        return r.json() if "json" in ctype else r.text

    async def get(self, path: str, **kw): return await self.call("GET", path, **kw)
    async def post(self, path: str, **kw): return await self.call("POST", path, **kw)
    async def patch(self, path: str, **kw): return await self.call("PATCH", path, **kw)
    async def put(self, path: str, **kw): return await self.call("PUT", path, **kw)
    async def delete(self, path: str, **kw): return await self.call("DELETE", path, **kw)


def ledger() -> ServiceClient:
    return ServiceClient("LEDGER_URL", "http://localhost:8001")


def model_gateway() -> ServiceClient:
    return ServiceClient("MODEL_GATEWAY_URL", "http://localhost:8003", timeout=180.0)


def service(url_env: str, default_url: str, timeout: float = 300.0) -> ServiceClient:
    return ServiceClient(url_env, default_url, timeout=timeout)


async def generate(*, task: str, prompt_version: str, prompt: str, engagement_id: Optional[str],
                   actor: Actor, tier: str = "default", system: Optional[str] = None,
                   max_tokens: int = 4096, correlation_id: Optional[str] = None) -> dict:
    """One model call through the gateway. Returns the ModelGenerateResponse packet as a dict."""
    body = {
        "task": task, "promptVersion": prompt_version, "engagementId": engagement_id, "tier": tier,
        "system": system, "messages": [{"role": "user", "content": prompt}],
        "responseFormat": "json", "maxTokens": max_tokens,
    }
    return await model_gateway().post("/v1/generate", json=body, actor=actor, correlation_id=correlation_id)
