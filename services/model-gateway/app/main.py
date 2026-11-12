"""Model gateway: the only place that talks to model providers.

Engines send a task, a prompt version and a tier. The gateway routes to a provider from config/models.yaml,
enforces the engagement's spend cap, parses JSON, prices the call and logs it. Switching Claude to Kimi or
Gemini is a config change here, never an engine change.
"""
from __future__ import annotations

import json
import os
import time
from collections import deque

import yaml
from fastapi import Depends, HTTPException, Request

from sdp_common.auth import Caller, caller_from
from sdp_common.clients import ServiceError, ledger
from sdp_common.contracts import Actor, ActorType, ModelGenerateRequest, ModelGenerateResponse, Usage
from sdp_common.service import create_app
from sdp_common.text import first_json_block

from .providers import ProviderError, build

app = create_app("model-gateway", "Routes model calls to any provider, enforces spend caps, logs every call.")
CONFIG_PATH = os.getenv("MODELS_CONFIG", "config/models.yaml")
CALLS: deque = deque(maxlen=500)


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    if os.getenv("SDP_MODEL_DEFAULT"):  # e.g. "mock" for tests, "claude-api" on a laptop
        cfg["default"] = os.environ["SDP_MODEL_DEFAULT"]
        cfg["routes"] = {k: os.environ["SDP_MODEL_DEFAULT"] for k in cfg.get("routes", {})}
    return cfg


CONFIG = load_config()
PROVIDERS = {name: build(name, pc) for name, pc in CONFIG["providers"].items()}


def caller_dep(request: Request) -> Caller:
    return caller_from(request)


def price(provider: str, model: str, tin: int, tout: int) -> float:
    p = CONFIG["providers"][provider].get("prices", {}).get(model)
    return round((tin * p[0] + tout * p[1]) / 1_000_000, 6) if p else 0.0


async def check_budget(engagement_id: str) -> None:
    me = Actor(type=ActorType.system, id="model-gateway")
    try:
        b = await ledger().get(f"/v1/engagements/{engagement_id}/budget", actor=me)
    except ServiceError as e:
        raise HTTPException(e.status, e.detail)
    if b["remainingUsd"] <= 0:
        raise HTTPException(402, {"code": "budget_exceeded",
                                  "message": f"This engagement's model budget (${b['budgetUsd']}) is spent. The solution owner can raise it."})


@app.post("/v1/generate", response_model=ModelGenerateResponse, tags=["models"])
async def generate(req: ModelGenerateRequest, c: Caller = Depends(caller_dep)):
    if req.engagementId:
        await check_budget(req.engagementId)
    name = req.provider or CONFIG.get("routes", {}).get(req.task) or CONFIG["default"]
    provider = PROVIDERS.get(name)
    if provider is None:
        raise HTTPException(400, {"code": "unknown_provider", "message": f"No provider named {name}."})
    model = provider.model_for(req.tier.value)
    messages = [m.model_dump() for m in req.messages]
    started = time.perf_counter()
    tin = tout = 0
    try:
        text, a, b = await provider.complete(model=model, system=req.system, messages=messages,
                                             max_tokens=req.maxTokens, temperature=req.temperature, task=req.task)
        tin, tout = a, b
        output = text
        if req.responseFormat == "json":
            try:
                output = first_json_block(text)
            except (ValueError, json.JSONDecodeError):
                retry = messages + [{"role": "assistant", "content": text},
                                    {"role": "user", "content": "That was not valid JSON. Reply with only the JSON object."}]
                text, a, b = await provider.complete(model=model, system=req.system, messages=retry,
                                                     max_tokens=req.maxTokens, temperature=0, task=req.task)
                tin, tout = tin + a, tout + b
                output = first_json_block(text)
    except ProviderError as e:
        CALLS.appendleft({"task": req.task, "provider": name, "model": model, "error": str(e)[:200], "caller": c.service})
        raise HTTPException(502, {"code": "provider_error", "message": str(e)[:300]})
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(502, {"code": "bad_json", "message": "The model did not return valid JSON after one retry."})
    latency = int((time.perf_counter() - started) * 1000)
    cost = price(name, model, tin, tout)
    CALLS.appendleft({"task": req.task, "promptVersion": req.promptVersion, "provider": name, "model": model,
                      "engagementId": req.engagementId, "tokensIn": tin, "tokensOut": tout, "latencyMs": latency,
                      "costUsd": cost, "caller": c.service})
    return ModelGenerateResponse(output=output, rawText=text, provider=name, model=model,
                                 usage=Usage(inputTokens=tin, outputTokens=tout), latencyMs=latency, costUsd=cost)


@app.get("/v1/providers", tags=["models"])
def providers(c: Caller = Depends(caller_dep)):
    return {
        "default": CONFIG["default"],
        "routes": CONFIG.get("routes", {}),
        "providers": [{"name": n, "type": CONFIG["providers"][n]["type"], "models": CONFIG["providers"][n].get("models", {}),
                       "credentialsPresent": p.available()} for n, p in PROVIDERS.items()],
    }


@app.get("/v1/calls", tags=["models"])
def calls(limit: int = 50, c: Caller = Depends(caller_dep)):
    return list(CALLS)[:limit]
