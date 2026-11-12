"""Provider adapters. Each turns one generic request into one provider call and returns (text, tokens in, tokens out).

Adding a provider = one class here + an entry in config/models.yaml. Engines never see the difference.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

from .mock import respond as mock_respond


class ProviderError(Exception):
    pass


class Provider:
    def __init__(self, name: str, cfg: dict):
        self.name, self.cfg = name, cfg

    def model_for(self, tier: str) -> str:
        models = self.cfg.get("models", {})
        return models.get(tier) or models.get("default") or ""

    def available(self) -> bool:
        return True

    async def complete(self, *, model: str, system: Optional[str], messages: list[dict], max_tokens: int,
                       temperature: float, task: str) -> tuple[str, int, int]:
        raise NotImplementedError


class AnthropicProvider(Provider):
    def available(self) -> bool:
        return bool(os.getenv(self.cfg.get("api_key_env", "ANTHROPIC_API_KEY")))

    async def complete(self, *, model, system, messages, max_tokens, temperature, task):
        body = {"model": model, "max_tokens": max_tokens, "messages": messages, "temperature": temperature}
        if system:
            body["system"] = system
        headers = {"x-api-key": os.getenv(self.cfg.get("api_key_env", "ANTHROPIC_API_KEY"), ""),
                   "anthropic-version": "2023-06-01", "content-type": "application/json"}
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post("https://api.anthropic.com/v1/messages", json=body, headers=headers)
        if r.status_code >= 400:
            raise ProviderError(f"anthropic {r.status_code}: {r.text[:300]}")
        j = r.json()
        text = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
        u = j.get("usage", {})
        return text, u.get("input_tokens", 0), u.get("output_tokens", 0)


def _google_token() -> str:
    import google.auth  # optional dependency, present in the GCP image
    import google.auth.transport.requests
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


class VertexAnthropicProvider(Provider):
    """Claude on Vertex AI. Data stays in the GCP project; billing goes through Google."""

    def available(self) -> bool:
        return bool(os.getenv(self.cfg.get("project_env", "GCP_PROJECT")))

    async def complete(self, *, model, system, messages, max_tokens, temperature, task):
        project = os.getenv(self.cfg.get("project_env", "GCP_PROJECT"), "")
        region = self.cfg.get("region", "us-east5")
        url = (f"https://{region}-aiplatform.googleapis.com/v1/projects/{project}/locations/{region}"
               f"/publishers/anthropic/models/{model}:rawPredict")
        body = {"anthropic_version": "vertex-2023-10-16", "messages": messages, "max_tokens": max_tokens,
                "temperature": temperature}
        if system:
            body["system"] = system
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(url, json=body, headers={"Authorization": f"Bearer {_google_token()}"})
        if r.status_code >= 400:
            raise ProviderError(f"vertex-anthropic {r.status_code}: {r.text[:300]}")
        j = r.json()
        text = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
        u = j.get("usage", {})
        return text, u.get("input_tokens", 0), u.get("output_tokens", 0)


class OpenAICompatibleProvider(Provider):
    """Kimi, DeepSeek, vLLM, Ollama, OpenRouter: anything speaking the chat-completions protocol."""

    def available(self) -> bool:
        return bool(os.getenv(self.cfg.get("api_key_env", ""))) or "localhost" in self.cfg.get("base_url", "")

    async def complete(self, *, model, system, messages, max_tokens, temperature, task):
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        body = {"model": model, "messages": msgs, "max_tokens": max_tokens, "temperature": temperature}
        headers = {"Authorization": f"Bearer {os.getenv(self.cfg.get('api_key_env', ''), '')}"}
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(f"{self.cfg['base_url'].rstrip('/')}/chat/completions", json=body, headers=headers)
        if r.status_code >= 400:
            raise ProviderError(f"{self.name} {r.status_code}: {r.text[:300]}")
        j = r.json()
        u = j.get("usage", {})
        return j["choices"][0]["message"]["content"], u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


class VertexGeminiProvider(Provider):
    def available(self) -> bool:
        return bool(os.getenv(self.cfg.get("project_env", "GCP_PROJECT")))

    async def complete(self, *, model, system, messages, max_tokens, temperature, task):
        project = os.getenv(self.cfg.get("project_env", "GCP_PROJECT"), "")
        region = self.cfg.get("region", "us-central1")
        url = (f"https://{region}-aiplatform.googleapis.com/v1/projects/{project}/locations/{region}"
               f"/publishers/google/models/{model}:generateContent")
        body = {"contents": [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]} for m in messages],
                "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(url, json=body, headers={"Authorization": f"Bearer {_google_token()}"})
        if r.status_code >= 400:
            raise ProviderError(f"vertex-gemini {r.status_code}: {r.text[:300]}")
        j = r.json()
        parts = j.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        u = j.get("usageMetadata", {})
        return "".join(p.get("text", "") for p in parts), u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0)


class MockProvider(Provider):
    async def complete(self, *, model, system, messages, max_tokens, temperature, task):
        prompt = messages[-1]["content"]
        text = mock_respond(task, prompt)
        return text, len(prompt) // 4, len(text) // 4


TYPES = {
    "anthropic": AnthropicProvider,
    "vertex_anthropic": VertexAnthropicProvider,
    "openai_compatible": OpenAICompatibleProvider,
    "vertex_gemini": VertexGeminiProvider,
    "mock": MockProvider,
}


def build(name: str, cfg: dict) -> Provider:
    cls = TYPES.get(cfg.get("type", ""))
    if cls is None:
        raise ValueError(f"Unknown provider type {cfg.get('type')} for {name}")
    return cls(name, cfg)
