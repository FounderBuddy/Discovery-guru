"""Ledger persistence. Postgres in the cloud (LEDGER_DB_URL=postgresql+psycopg://...), SQLite locally."""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DB_URL = os.getenv("LEDGER_DB_URL", "sqlite:///./data/ledger.db")
if "{password}" in DB_URL:  # on Cloud Run the password arrives from Secret Manager, never in the URL itself
    from urllib.parse import quote
    DB_URL = DB_URL.replace("{password}", quote(os.environ["LEDGER_DB_PASSWORD"], safe=""))
if DB_URL.startswith("sqlite:///"):
    os.makedirs(os.path.dirname(DB_URL.replace("sqlite:///", "")) or ".", exist_ok=True)

engine = create_engine(DB_URL, future=True,
                       connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def camel(s: str) -> str:
    head, *rest = s.split("_")
    return head + "".join(w[:1].upper() + w[1:] for w in rest)


def snake(s: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower()


class Base(DeclarativeBase):
    def to_api(self) -> dict[str, Any]:
        out = {}
        for col in self.__table__.columns:
            val = getattr(self, col.name)
            if isinstance(val, datetime):
                val = val.isoformat()
            out[camel(col.name)] = val
        return out


def ts():  # timezone-aware timestamp column
    return Column(DateTime(timezone=True), default=now)


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True)
    name = Column(String, default="")
    email = Column(String, default="")
    is_practice_admin = Column(Boolean, default=False)
    created_at = ts()


class Engagement(Base):
    __tablename__ = "engagements"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    client = Column(String, default="")
    sector = Column(String, default="")
    description = Column(Text, default="")
    owner_id = Column(String, nullable=True)
    profile = Column(JSON, default=dict)          # desired-state profile: market, audiences, notes
    ai_budget_usd = Column(Float, default=25.0)   # spend cap for model calls on this engagement
    created_at = ts()
    created_by = Column(String, default="")


class Member(Base):
    __tablename__ = "members"
    engagement_id = Column(String, ForeignKey("engagements.id"), primary_key=True)
    user_id = Column(String, primary_key=True)
    role = Column(String, nullable=False)
    added_by = Column(String, default="")
    added_at = ts()


class Stakeholder(Base):
    __tablename__ = "stakeholders"
    id = Column(String, primary_key=True)
    engagement_id = Column(String, index=True)
    name = Column(String)
    title = Column(String, default="")
    organisation = Column(String, default="")
    type = Column(String, default="")             # sponsor, owner, champion, approver, blocker, influencer
    decides = Column(Text, default="")
    status = Column(String, default="proposed")   # proposed | confirmed
    origin = Column(String, default="human")
    source_id = Column(String, nullable=True)
    created_at = ts()


class Source(Base):
    __tablename__ = "sources"
    id = Column(String, primary_key=True)
    engagement_id = Column(String, index=True)
    title = Column(String)
    kind = Column(String, default="other")        # transcript, architecture, policy, inventory, other
    rule_category = Column(String, nullable=True)
    file_name = Column(String, default="")
    chars = Column(Integer, default=0)
    status = Column(String, default="new")        # new | extracting | extracted | failed
    proposed_claims = Column(Integer, default=0)
    rejected_claims = Column(Integer, default=0)
    created_by = Column(String, default="")
    created_at = ts()


class SourceChunk(Base):
    __tablename__ = "source_chunks"
    source_id = Column(String, ForeignKey("sources.id"), primary_key=True)
    idx = Column(Integer, primary_key=True)
    text = Column(Text)


class Claim(Base):
    __tablename__ = "claims"
    id = Column(String, primary_key=True)
    engagement_id = Column(String, index=True)
    statement = Column(Text)
    quote = Column(Text, default="")
    question = Column(String)
    kind = Column(String)
    rule_category = Column(String, nullable=True)
    number = Column(JSON, nullable=True)
    speaker = Column(String, default="")
    confidence = Column(String, default="medium")
    strength = Column(String, nullable=True)      # hard | soft, for guardrails
    status = Column(String, default="proposed")   # proposed | confirmed | rejected | superseded
    origin = Column(String, default="agent")      # agent | human
    evidence = Column(String, default="cited")    # cited | none (a human statement with no source = assumption)
    source_id = Column(String, nullable=True)
    part = Column(Integer, nullable=True)
    run_id = Column(String, nullable=True)
    prompt_version = Column(String, nullable=True)
    model = Column(String, nullable=True)
    provider = Column(String, nullable=True)
    created_at = ts()
    created_by = Column(String, default="")
    confirmed_by = Column(String, nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    rejected_reason = Column(Text, nullable=True)
    superseded_by = Column(String, nullable=True)
    superseded_reason = Column(Text, nullable=True)
    needs_review = Column(Boolean, default=False)


class RuleStatus(Base):
    __tablename__ = "rules"
    engagement_id = Column(String, primary_key=True)
    category = Column(String, primary_key=True)
    status = Column(String, default="unknown")    # unknown | yes | partial | no
    gap_decision = Column(String, nullable=True)  # establish (scope) | accept (risk)
    notes = Column(Text, default="")
    source_ids = Column(JSON, default=list)
    updated_by = Column(String, default="")
    updated_at = ts()


class OpenItem(Base):
    __tablename__ = "open_items"
    id = Column(String, primary_key=True)
    engagement_id = Column(String, index=True)
    question = Column(Text)
    kind = Column(String)
    outcome = Column(String, default="unclassified")
    owner_id = Column(String, nullable=True)
    due_date = Column(String, nullable=True)      # ISO date
    impact = Column(Text, default="")
    default_if_silent = Column(Text, default="")
    ask_role = Column(String, default="")
    status = Column(String, default="open")       # open | answered | closed
    answer = Column(Text, default="")
    escalation_level = Column(Integer, default=0)  # 0 owner, 1 solution owner, 2 client sponsor
    related_claim_ids = Column(JSON, default=list)
    origin = Column(String, default="human")
    run_id = Column(String, nullable=True)
    needs_review = Column(Boolean, default=False)
    created_at = ts()
    created_by = Column(String, default="")


class Divergence(Base):
    __tablename__ = "divergences"
    id = Column(String, primary_key=True)
    engagement_id = Column(String, index=True)
    claim_a = Column(String)
    claim_b = Column(String)
    type = Column(String)
    severity = Column(String)
    explanation = Column(Text)
    status = Column(String, default="open")       # open | resolved | with_client
    resolution = Column(String, nullable=True)
    resolution_note = Column(Text, default="")
    resolved_by = Column(String, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    origin = Column(String, default="agent")
    run_id = Column(String, nullable=True)
    created_at = ts()


class Decision(Base):
    __tablename__ = "decisions"
    id = Column(String, primary_key=True)
    engagement_id = Column(String, index=True)
    number = Column(Integer)
    title = Column(String)
    context = Column(Text, default="")
    options = Column(JSON, default=list)
    decision = Column(Text, default="")
    rationale = Column(Text, default="")
    status = Column(String, default="proposed")   # proposed | escalated | accepted | superseded
    source = Column(String, default="ours")       # ours | client
    related_claim_ids = Column(JSON, default=list)
    practice_id = Column(String, nullable=True)
    recommendation_id = Column(String, nullable=True)
    decided_by = Column(String, nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    needs_review = Column(Boolean, default=False)
    origin = Column(String, default="human")
    created_at = ts()
    created_by = Column(String, default="")


class Recommendation(Base):
    __tablename__ = "recommendations"
    id = Column(String, primary_key=True)
    engagement_id = Column(String, index=True)
    path = Column(String)
    recommendation = Column(Text)
    rationale = Column(Text, default="")
    level = Column(String)
    practice_ids = Column(JSON, default=list)
    claim_ids = Column(JSON, default=list)
    alternatives = Column(JSON, default=list)
    assumptions = Column(JSON, default=list)
    conflicts = Column(JSON, default=list)
    status = Column(String, default="draft")      # draft | blocked | needs_review | approved
    review_reason = Column(Text, default="")
    approved_by = Column(String, nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    run_id = Column(String, nullable=True)
    prompt_version = Column(String, nullable=True)
    model = Column(String, nullable=True)
    created_at = ts()
    requested_by = Column(String, default="")


class Practice(Base):
    __tablename__ = "practices"
    id = Column(String, primary_key=True)
    domain = Column(String)
    title = Column(String)
    statement = Column(Text)
    rationale = Column(Text, default="")
    status = Column(String, default="active")     # draft | active | retired
    applies_to = Column(JSON, default=list)       # pack keys; empty = every engagement
    version = Column(Integer, default=1)
    updated_by = Column(String, default="")
    updated_at = ts()


class BriefSlot(Base):
    """One slot of the Brief. empty -> draft (engine or person) -> agreed (owner) -> needs_review (evidence moved)."""
    __tablename__ = "brief_slots"
    id = Column(String, primary_key=True)
    engagement_id = Column(String, index=True)
    key = Column(String)                          # purpose | value | measures | scope | nonGoals | shape
    status = Column(String, default="empty")
    text = Column(Text, default="")
    shapes = Column(JSON, default=list)           # shape slot only: pack keys
    claim_ids = Column(JSON, default=list)
    gaps = Column(JSON, default=list)
    suggestion = Column(JSON, nullable=True)      # a newer engine draft offered for an agreed slot; never applied silently
    version = Column(Integer, default=0)
    drafted_by = Column(String, default="")       # actor id of the last draft (engine or person)
    run_id = Column(String, nullable=True)
    agreed_by = Column(String, nullable=True)
    agreed_at = Column(DateTime(timezone=True), nullable=True)
    review_reason = Column(Text, default="")
    updated_at = ts()


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id = Column(String, primary_key=True)
    engagement_id = Column(String, index=True)
    engine = Column(String)
    task = Column(String)
    prompt_version = Column(String)
    provider = Column(String, default="")
    model = Column(String, default="")
    tier = Column(String, default="")
    actor_id = Column(String, default="")         # who asked for it
    started_at = Column(String)
    duration_ms = Column(Integer, default=0)
    input_summary = Column(Text, default="")
    output_summary = Column(Text, default="")
    accepted = Column(Integer, default=0)
    rejected = Column(Integer, default=0)
    rejected_samples = Column(JSON, default=list)
    tokens_in = Column(Integer, default=0)
    tokens_out = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)
    error = Column(Text, nullable=True)


class History(Base):
    """Append-only. Every change to anything is a row here; nothing in this table is ever updated or deleted."""
    __tablename__ = "history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    engagement_id = Column(String, index=True, nullable=True)
    entity = Column(String)
    entity_id = Column(String, index=True)
    action = Column(String)
    actor_type = Column(String)
    actor_id = Column(String)
    via_service = Column(String)
    at = ts()
    payload = Column(JSON, default=dict)


MIGRATIONS = os.path.join(os.path.dirname(__file__), "..", "migrations")


def init_db() -> None:
    """Bring the schema to the latest migration. Safe to run from many instances at once: on Postgres
    an advisory lock makes the others wait while one migrates."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect, text

    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS)
    with engine.connect() as conn:
        pg = conn.dialect.name == "postgresql"
        if pg:
            conn.execute(text("SELECT pg_advisory_lock(727001)"))
        try:
            tables = set(inspect(conn).get_table_names())
            if tables and "alembic_version" not in tables:
                raise RuntimeError("This ledger database was created before migrations existed. "
                                   "Start fresh (python3 scripts/dev.py --fresh, or drop the database).")
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
            conn.commit()
        finally:
            if pg:
                conn.execute(text("SELECT pg_advisory_unlock(727001)"))
                conn.commit()
