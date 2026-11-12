"""CloudEvents 1.0 envelope and the bus publisher.

Every asynchronous packet travels in this envelope. Locally it goes to the
event-bus service over HTTP; on GCP the same JSON goes to Pub/Sub, so services
never change when the transport does (BUS_BACKEND=http|pubsub|none).
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .contracts import EVENT_TYPES, Actor

log = logging.getLogger("sdp.events")


class CloudEvent(BaseModel):
    """CloudEvents 1.0 structured JSON. Extension attributes are lowercase alphanumerics per the spec."""
    model_config = ConfigDict(extra="forbid")

    specversion: str = "1.0"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str
    source: str
    time: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    datacontenttype: str = "application/json"
    subject: Optional[str] = None
    sdpengagement: Optional[str] = None
    sdpactortype: Optional[str] = None
    sdpactorid: Optional[str] = None
    sdpcorrelation: Optional[str] = None
    data: dict[str, Any]


def make_event(event_type: str, source_service: str, data: BaseModel | dict, *,
               engagement_id: str | None = None, actor: Actor | None = None,
               correlation_id: str | None = None, subject: str | None = None) -> CloudEvent:
    payload_model = EVENT_TYPES.get(event_type)
    payload = data.model_dump(mode="json") if isinstance(data, BaseModel) else dict(data)
    if payload_model is not None:
        payload = payload_model.model_validate(payload).model_dump(mode="json")  # contract check at the edge
    return CloudEvent(
        type=event_type,
        source=f"/services/{source_service}",
        subject=subject,
        sdpengagement=engagement_id,
        sdpactortype=actor.type.value if actor else None,
        sdpactorid=actor.id if actor else None,
        sdpcorrelation=correlation_id,
        data=payload,
    )


class Publisher:
    def __init__(self) -> None:
        self.backend = os.getenv("BUS_BACKEND", "http")
        self.bus_url = os.getenv("EVENT_BUS_URL", "http://localhost:8002")
        self.token = os.getenv("SDP_SERVICE_TOKEN", "")
        self._pubsub = None

    async def publish(self, event: CloudEvent) -> None:
        body = event.model_dump(mode="json", exclude_none=True)
        try:
            if self.backend == "none":
                return
            if self.backend == "pubsub":
                await self._publish_pubsub(body)
                return
            async with httpx.AsyncClient(timeout=5.0) as client:
                from .auth import service_auth_header
                r = await client.post(f"{self.bus_url}/v1/publish", json=body,
                                      headers=service_auth_header(self.bus_url))
                r.raise_for_status()
        except Exception as exc:  # events are best-effort; the ledger is the source of truth
            log.warning("event publish failed type=%s err=%s", event.type, exc)

    async def _publish_pubsub(self, body: dict) -> None:
        from google.cloud import pubsub_v1  # optional dependency: sdp-common[pubsub]
        if self._pubsub is None:
            self._pubsub = pubsub_v1.PublisherClient()
        topic = self._pubsub.topic_path(os.environ["GCP_PROJECT"], os.getenv("PUBSUB_TOPIC", "sdp-events"))
        self._pubsub.publish(topic, json.dumps(body).encode(), type=body["type"]).result(timeout=10)


publisher = Publisher()
