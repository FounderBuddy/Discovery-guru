"""Who may do what. Enforced here, in the store, so no client or engine can route around it.

Users act through their engagement role. Services and agents act through a fixed permission set per
service. Engines hold proposal rights only: they can never confirm, reject, resolve, decide, approve or delete.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from sdp_common.auth import Caller
from sdp_common.contracts import ActorType

from .db import Member, User

ALL_USER_PERMS = {
    "read", "engagement.update", "members.manage", "sources.write", "sources.delete",
    "claims.propose", "claims.review", "claims.supersede", "stakeholders.write", "rules.write",
    "openitems.write", "openitems.escalate", "divergences.propose", "divergences.resolve",
    "decisions.write", "decisions.decide", "recs.propose", "recs.approve", "engines.run", "delete",
}

ROLE_PERMS: dict[str, set[str]] = {
    "owner": ALL_USER_PERMS,
    "contributor": {"read", "sources.write", "claims.propose", "claims.review", "stakeholders.write",
                    "rules.write", "openitems.write", "divergences.propose", "decisions.write",
                    "recs.propose", "engines.run"},
    "reviewer": {"read", "decisions.write", "recs.approve", "openitems.write"},
    "observer": {"read"},
}

# Least privilege per service. "act_as_user" lets a service carry a signed-in user's identity.
SERVICE_PERMS: dict[str, set[str]] = {
    "api-gateway": {"act_as_user", "users.write", "read"},
    "ingestion": {"act_as_user", "read"},
    "engine-extraction": {"read", "claims.propose", "stakeholders.propose", "sources.status", "runs.write"},
    "engine-divergence": {"read", "divergences.propose", "runs.write"},
    "engine-questions": {"read", "openitems.propose", "runs.write"},
    "engine-recommendation": {"read", "recs.propose", "decisions.escalate", "runs.write"},
    "engine-resolution": {"read", "openitems.escalate", "runs.write"},
    "engine-artefacts": {"read"},
    "model-gateway": {"read"},
    "event-bus": set(),
}

# Actions an agent may never take, whatever service it runs in. Checked separately as a backstop.
HUMAN_ONLY = {"claims.review", "claims.supersede", "divergences.resolve", "decisions.decide",
              "recs.approve", "members.manage", "delete", "engagement.update", "practices.write"}


def deny(message: str, code: str = "forbidden"):
    raise HTTPException(403, {"code": code, "message": message})


def check_actor_allowed(caller: Caller) -> None:
    if caller.actor.type == ActorType.user and "act_as_user" not in SERVICE_PERMS.get(caller.service, set()):
        deny(f"{caller.service} may not act on behalf of a user.")


def role_of(db: Session, engagement_id: str, user_id: str) -> str | None:
    m = db.get(Member, {"engagement_id": engagement_id, "user_id": user_id})
    return m.role if m else None


def require(db: Session, caller: Caller, engagement_id: str | None, perm: str) -> str | None:
    """Raise 403 unless the caller may do `perm` on the engagement. Returns the user's role, if a user."""
    check_actor_allowed(caller)
    if caller.actor.type == ActorType.user:
        if engagement_id is None:
            return None
        role = role_of(db, engagement_id, caller.actor.id)
        if role is None:
            deny("You are not a participant in this engagement. Ask its solution owner to nominate you.", "not_member")
        if perm not in ROLE_PERMS[role]:
            deny(f"Your role ({role}) cannot do this.", "role")
        return role
    if perm in HUMAN_ONLY:
        deny(f"Only a person can do this. Agents propose; people confirm.", "human_only")
    if perm not in SERVICE_PERMS.get(caller.service, set()):
        deny(f"{caller.service} is not permitted to {perm}.")
    return None


def require_practice_admin(db: Session, caller: Caller) -> None:
    check_actor_allowed(caller)
    if caller.actor.type != ActorType.user:
        deny("Only a person can change Datatonic practices.", "human_only")
    u = db.get(User, caller.actor.id)
    if not u or not u.is_practice_admin:
        deny("Only practice maintainers can change Datatonic practices.", "role")
