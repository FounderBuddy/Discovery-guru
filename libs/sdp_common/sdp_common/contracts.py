"""Packet contracts. These pydantic models are the single source of truth;
`scripts/gen_schemas.py` writes them to contracts/*.schema.json for any language to use.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Packet(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------- vocabularies

class Question(str, Enum):
    why = "why"        # business drivers, pain points, baselines
    what = "what"      # outcomes and use cases: what we build or transform
    rules = "rules"    # guardrails the solution must adhere to
    from_ = "from"     # current-estate facts
    how = "how"        # approaches, designs, effort


class ClaimKind(str, Enum):
    driver = "driver"
    pain_point = "pain_point"
    baseline = "baseline"
    outcome = "outcome"
    use_case = "use_case"
    guardrail = "guardrail"
    estate_fact = "estate_fact"
    number = "number"
    constraint = "constraint"
    decision = "decision"
    owner = "owner"
    risk = "risk"
    open_question = "open_question"
    preference = "preference"


class RuleCategory(str, Enum):
    regulatory = "regulatory"
    security = "security"
    strategic = "strategic"
    architecture = "architecture"
    organisational = "organisational"
    commercial = "commercial"
    operational = "operational"


class Outcome(str, Enum):
    """How an unanswered question is resolved."""
    unclassified = "unclassified"
    insist = "insist"              # changes architecture or price: escalate, cannot price without it
    estimate = "estimate"          # ambiguous countable fact: range, capped, fixed at the design gate
    risk = "risk"                  # not blocking: likelihood, impact, fallback, contingency
    pending = "pending"            # client's decision that gates work: default if silent, deadline
    design_around = "design_around"  # remove the dependency


class QuestionKind(str, Enum):
    number = "number"
    constraint = "constraint"
    owner = "owner"
    decision = "decision"
    artefact = "artefact"


class Role(str, Enum):
    owner = "owner"
    contributor = "contributor"
    reviewer = "reviewer"
    observer = "observer"


class ActorType(str, Enum):
    user = "user"
    agent = "agent"
    system = "system"


class Tier(str, Enum):
    quick = "quick"
    default = "default"
    complex = "complex"


class RecLevel(str, Enum):
    practice = "practice"              # a Datatonic practice applies
    best_practice = "best_practice"    # no practice applies: researched or model best practice
    client_enforced = "client_enforced"  # a hard client rule dictates it


# ---------------------------------------------------------------- actor

class Actor(_Packet):
    type: ActorType
    id: str = Field(description="User id, or the engine name for agents, or the service name for system actions.")


# ---------------------------------------------------------------- model gateway

class ChatMessage(_Packet):
    role: Literal["user", "assistant"]
    content: str


class ModelGenerateRequest(_Packet):
    task: str = Field(description="Logical task, used for routing: extraction, divergence, questions, recommendation...")
    promptVersion: str
    engagementId: Optional[str] = None
    tier: Tier = Tier.default
    system: Optional[str] = None
    messages: list[ChatMessage]
    responseFormat: Literal["json", "text"] = "json"
    maxTokens: int = 4096
    temperature: float = 0.0
    provider: Optional[str] = Field(default=None, description="Override the routed provider for this call.")


class Usage(_Packet):
    inputTokens: int = 0
    outputTokens: int = 0


class ModelGenerateResponse(_Packet):
    output: Any
    rawText: str
    provider: str
    model: str
    usage: Usage
    latencyMs: int
    costUsd: float = 0.0


# ---------------------------------------------------------------- engine commands

class EngineRunRequest(_Packet):
    engagementId: str
    params: dict[str, Any] = Field(default_factory=dict)
    actor: Actor
    correlationId: Optional[str] = None


class EngineRunResponse(_Packet):
    runId: str
    engine: str
    status: Literal["completed", "failed", "skipped"]
    accepted: int = 0
    rejected: int = 0
    summary: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- proposals (agent -> ledger)

class NumberValue(_Packet):
    value: float
    unit: str = ""


class ClaimProposal(_Packet):
    statement: str
    quote: str = ""
    question: Question
    kind: ClaimKind
    ruleCategory: Optional[RuleCategory] = None
    number: Optional[NumberValue] = None
    speaker: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"
    sourceId: Optional[str] = None
    part: Optional[int] = None
    runId: Optional[str] = None
    promptVersion: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None


class StakeholderProposal(_Packet):
    name: str
    title: str = ""
    organisation: str = ""
    type: str = ""
    sourceId: Optional[str] = None


class DivergenceProposal(_Packet):
    claimA: str
    claimB: str
    type: Literal["contradiction", "different_figures", "withdrawn", "scope_conflict"]
    severity: Literal["high", "medium", "low"]
    explanation: str
    runId: Optional[str] = None


class OpenItemProposal(_Packet):
    question: str
    kind: QuestionKind
    outcome: Outcome = Outcome.unclassified
    impact: str = ""
    askRole: str = ""
    relatedClaimIds: list[str] = Field(default_factory=list)
    runId: Optional[str] = None


class Alternative(_Packet):
    option: str
    whyNot: str


class Conflict(_Packet):
    practiceId: str
    claimId: str
    description: str


class RecommendationProposal(_Packet):
    path: str
    recommendation: str
    rationale: str
    level: RecLevel
    practiceIds: list[str] = Field(default_factory=list)
    claimIds: list[str] = Field(default_factory=list)
    alternatives: list[Alternative] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    runId: Optional[str] = None
    promptVersion: Optional[str] = None
    model: Optional[str] = None


class BriefSlotKey(str, Enum):
    """The Brief: the short, agreed statement of what this engagement is for. Starts blank, fills from evidence."""
    purpose = "purpose"            # one or two sentences: what the client is trying to achieve and why now
    value = "value"                # the business value, in their terms, with numbers where known
    measures = "measures"          # how everyone will know it worked: measurable outcomes with baselines
    scope = "scope"                # what is in
    non_goals = "nonGoals"         # what is explicitly out
    shape = "shape"                # what kind of engagement this is: one or more packs (migration, ai-agents, ...)


class BriefSlotProposal(_Packet):
    key: BriefSlotKey
    text: str = ""
    claimIds: list[str] = Field(default_factory=list)     # confirmed claims the text rests on: required for a draft
    shapes: list[str] = Field(default_factory=list)       # pack keys; only for the shape slot
    gaps: list[str] = Field(default_factory=list)         # what is still missing to complete or strengthen the slot


class BriefProposal(_Packet):
    slots: list[BriefSlotProposal]
    runId: Optional[str] = None
    promptVersion: Optional[str] = None
    model: Optional[str] = None


class PlanAttendee(_Packet):
    role: str                                   # key from roles.yaml (client or ours)
    stakeholderId: Optional[str] = None         # client side: the named person, when we know them
    name: str = ""                              # display name; empty when still to be nominated


class PlanQuestion(_Packet):
    text: str
    covers: str = ""                            # why | what | rules | from | how
    openItemId: Optional[str] = None            # set when the question is tracked as an open item
    mustKnow: str = ""                          # the pack checklist item it answers, if any


class PlanSession(_Packet):
    key: str                                    # template key, or a new key for a session the model added
    title: str
    purpose: str = ""
    day: int = 1
    minutes: int = 60
    covers: list[str] = Field(default_factory=list)
    clientAttendees: list[PlanAttendee] = Field(default_factory=list)
    ourAttendees: list[str] = Field(default_factory=list)       # role keys
    agenda: list[str] = Field(default_factory=list)
    questions: list[PlanQuestion] = Field(default_factory=list)
    preReads: list[str] = Field(default_factory=list)           # what to ask the client to send before
    outputs: list[str] = Field(default_factory=list)


class PlanTeamMember(_Packet):
    role: str
    why: str = ""
    hours: float = 0                            # in sessions, plus preparation and write-up


class PlanNomination(_Packet):
    role: str                                   # client function with no named owner yet
    neededFor: list[str] = Field(default_factory=list)          # session keys
    askWho: str = ""                            # who to ask for the nomination


class PlanProposal(_Packet):
    format: str
    recommendedFormat: str = ""
    recommendationReason: str = ""
    startDate: Optional[str] = None
    summary: str = ""
    sessions: list[PlanSession]
    ourTeam: list[PlanTeamMember] = Field(default_factory=list)
    nominations: list[PlanNomination] = Field(default_factory=list)
    requests: list[str] = Field(default_factory=list)           # artefacts to request up front
    risks: list[str] = Field(default_factory=list)
    packs: list[str] = Field(default_factory=list)
    basis: dict[str, Any] = Field(default_factory=dict)         # what the plan was built from
    runId: Optional[str] = None
    promptVersion: Optional[str] = None
    model: Optional[str] = None


class AgentRunRecord(_Packet):
    id: str
    engine: str
    task: str
    promptVersion: str
    provider: str = ""
    model: str = ""
    tier: str = ""
    actorId: str = ""
    startedAt: str
    durationMs: int = 0
    inputSummary: str = ""
    outputSummary: str = ""
    accepted: int = 0
    rejected: int = 0
    rejectedSamples: list[dict[str, Any]] = Field(default_factory=list)
    tokensIn: int = 0
    tokensOut: int = 0
    costUsd: float = 0.0
    error: Optional[str] = None


# ---------------------------------------------------------------- event data payloads

class SourceAddedData(_Packet):
    sourceId: str
    title: str
    kind: str
    chars: int


class SourceExtractedData(_Packet):
    sourceId: str
    runId: str
    proposed: int
    rejected: int


class ClaimStateData(_Packet):
    claimId: str
    status: str
    supersededBy: Optional[str] = None


class RunCompletedData(_Packet):
    runId: str
    engine: str
    accepted: int
    rejected: int


class DecisionEscalatedData(_Packet):
    decisionId: str
    title: str
    recommendationId: Optional[str] = None


class BriefChangedData(_Packet):
    slot: str
    status: str            # empty | draft | agreed | needs_review
    version: int


class PlanChangedData(_Packet):
    version: int
    status: str            # draft | approved | superseded | needs_review
    format: str


class OpenItemEscalatedData(_Packet):
    openItemId: str
    level: int


EVENT_TYPES: dict[str, type[_Packet]] = {
    "sdp.source.added.v1": SourceAddedData,
    "sdp.source.extracted.v1": SourceExtractedData,
    "sdp.claim.confirmed.v1": ClaimStateData,
    "sdp.claim.rejected.v1": ClaimStateData,
    "sdp.claim.superseded.v1": ClaimStateData,
    "sdp.agent.run.completed.v1": RunCompletedData,
    "sdp.decision.escalated.v1": DecisionEscalatedData,
    "sdp.openitem.escalated.v1": OpenItemEscalatedData,
    "sdp.brief.changed.v1": BriefChangedData,
    "sdp.plan.changed.v1": PlanChangedData,
}

PACKETS: dict[str, type[BaseModel]] = {
    "actor": Actor,
    "model-generate-request": ModelGenerateRequest,
    "model-generate-response": ModelGenerateResponse,
    "engine-run-request": EngineRunRequest,
    "engine-run-response": EngineRunResponse,
    "claim-proposal": ClaimProposal,
    "stakeholder-proposal": StakeholderProposal,
    "divergence-proposal": DivergenceProposal,
    "open-item-proposal": OpenItemProposal,
    "recommendation-proposal": RecommendationProposal,
    "brief-proposal": BriefProposal,
    "plan-proposal": PlanProposal,
    "agent-run-record": AgentRunRecord,
}
