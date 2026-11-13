"""What every engine does the same way: identify itself as an agent, call models through the gateway,
and leave an audit record. Engines import this; they never import each other."""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .clients import generate, ledger
from .contracts import Actor, ActorType, AgentRunRecord, EngineRunResponse
from .events import make_event, publisher


def load_prompt(engine_dir: str, name: str) -> tuple[str, str]:
    """Prompts live in files named <task>_v<N>.txt; the file name is the prompt version recorded on every output."""
    path = Path(engine_dir) / "prompts" / f"{name}.txt"
    return path.read_text(), name


def render(template: str, **values: Any) -> str:
    out = template
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


class Run:
    def __init__(self, engine: str, task: str, prompt_version: str, engagement_id: str,
                 requested_by: Actor, correlation_id: Optional[str] = None):
        self.id = f"run_{uuid.uuid4().hex[:12]}"
        self.engine, self.task, self.prompt_version = engine, task, prompt_version
        self.engagement_id, self.requested_by, self.correlation_id = engagement_id, requested_by, correlation_id
        self.agent = Actor(type=ActorType.agent, id=engine)
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._t0 = time.perf_counter()
        self.provider = self.model_name = self.tier = ""
        self.tokens_in = self.tokens_out = 0
        self.cost = 0.0

    async def model(self, prompt: str, tier: str = "default", system: Optional[str] = None, max_tokens: int = 8000) -> Any:
        resp = await generate(task=self.task, prompt_version=self.prompt_version, prompt=prompt,
                              engagement_id=self.engagement_id, actor=self.agent, tier=tier, system=system,
                              max_tokens=max_tokens, correlation_id=self.correlation_id)
        self.provider, self.model_name, self.tier = resp["provider"], resp["model"], tier
        self.tokens_in += resp["usage"]["inputTokens"]
        self.tokens_out += resp["usage"]["outputTokens"]
        self.cost += resp.get("costUsd", 0.0)
        return resp["output"]

    async def finish(self, *, accepted: int, rejected: int, input_summary: str, output_summary: str,
                     rejected_samples: Optional[list] = None, error: Optional[str] = None,
                     details: Optional[dict] = None) -> EngineRunResponse:
        record = AgentRunRecord(
            id=self.id, engine=self.engine, task=self.task, promptVersion=self.prompt_version,
            provider=self.provider, model=self.model_name, tier=self.tier, actorId=self.requested_by.id,
            startedAt=self.started_at, durationMs=int((time.perf_counter() - self._t0) * 1000),
            inputSummary=input_summary[:500], outputSummary=output_summary[:500], accepted=accepted,
            rejected=rejected, rejectedSamples=(rejected_samples or [])[:10], tokensIn=self.tokens_in,
            tokensOut=self.tokens_out, costUsd=round(self.cost, 6), error=error)
        await ledger().post(f"/v1/engagements/{self.engagement_id}/runs", json=record.model_dump(mode="json"),
                            actor=self.agent, correlation_id=self.correlation_id)
        await publisher.publish(make_event(
            "sdp.agent.run.completed.v1", self.engine,
            {"runId": self.id, "engine": self.engine, "accepted": accepted, "rejected": rejected},
            engagement_id=self.engagement_id, actor=self.agent, correlation_id=self.correlation_id))
        return EngineRunResponse(runId=self.id, engine=self.engine, status="failed" if error else "completed",
                                 accepted=accepted, rejected=rejected, summary=output_summary, details=details or {})


def engine_dir(file: str) -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(file)))


def brief_text(ctx: dict) -> str:
    """The Brief as engines read it: status, text and gaps per slot."""
    lines = []
    for s in ctx["brief"]["slots"]:
        body = s["text"] or "-"
        if s.get("shapes"):
            body += f" (packs: {', '.join(s['shapes'])})"
        lines.append(f"{s['key']} [{s['status']}]: {body}" + (f" | gaps: {'; '.join(s['gaps'])}" if s.get("gaps") else ""))
    return "\n".join(lines)
