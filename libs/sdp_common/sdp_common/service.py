"""Common FastAPI scaffolding: health, structured logs, error mapping. Every service builds its app here."""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .clients import ServiceError


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "severity": record.levelname, "service": os.getenv("SDP_SERVICE_NAME", "?"),
            "logger": record.name, "message": record.getMessage(),
        })


def create_app(name: str, description: str) -> FastAPI:
    os.environ.setdefault("SDP_SERVICE_NAME", name)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), handlers=[handler], force=True)

    app = FastAPI(title=f"SDP {name}", description=description, version="0.1.0")

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        started = time.perf_counter()
        cid = request.headers.get("x-correlation-id") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = cid
        response.headers["X-Service"] = name
        logging.getLogger("sdp.http").info(
            "%s %s %s %.0fms", request.method, request.url.path, response.status_code,
            (time.perf_counter() - started) * 1000)
        return response

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})

    @app.get("/healthz", tags=["ops"])
    def healthz():
        return {"service": name, "status": "ok"}

    return app
