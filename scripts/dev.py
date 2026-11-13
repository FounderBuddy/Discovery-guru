#!/usr/bin/env python3
"""Run the whole platform locally as separate processes, one per service, wired by URL and token.

    python scripts/dev.py            # mock model, keeps data between runs
    python scripts/dev.py --fresh    # wipe local data first
    python scripts/dev.py --model claude-api   # use a real provider from config/models.yaml

Then open http://localhost:8000 (after `npm run build` in web/) or run the web dev server.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

SERVICES = [
    ("ledger", 8001), ("event-bus", 8002), ("model-gateway", 8003), ("ingestion", 8004),
    ("engine-extraction", 8010), ("engine-divergence", 8011), ("engine-questions", 8012),
    ("engine-recommendation", 8013), ("engine-resolution", 8014), ("engine-artefacts", 8015),
    ("engine-brief", 8016),
    ("api-gateway", 8000),
]
PORT = dict(SERVICES)

DEV_USERS = [
    {"id": "u_yesser", "name": "Yesser Abdin", "email": "yesser@example.com", "isPracticeAdmin": True},
    {"id": "u_alex", "name": "Alex Morgan", "email": "alex@example.com"},
    {"id": "u_priya", "name": "Priya Shah", "email": "priya@example.com"},
    {"id": "u_sam", "name": "Sam Lee", "email": "sam@example.com"},
]


def base_env(model: str, tokens: dict[str, str]) -> dict[str, str]:
    url = lambda name: f"http://127.0.0.1:{PORT[name]}"
    return {
        "SDP_SERVICE_TOKENS": json.dumps(tokens),
        "INTERNAL_AUTH": "token",
        "LEDGER_URL": url("ledger"), "EVENT_BUS_URL": url("event-bus"), "MODEL_GATEWAY_URL": url("model-gateway"),
        "INGESTION_URL": url("ingestion"),
        "ENGINE_EXTRACTION_URL": url("engine-extraction"), "ENGINE_DIVERGENCE_URL": url("engine-divergence"),
        "ENGINE_QUESTIONS_URL": url("engine-questions"), "ENGINE_RECOMMENDATION_URL": url("engine-recommendation"),
        "ENGINE_RESOLUTION_URL": url("engine-resolution"), "ENGINE_ARTEFACTS_URL": url("engine-artefacts"),
        "ENGINE_BRIEF_URL": url("engine-brief"),
        "BUS_BACKEND": "http",
        "SDP_SUBSCRIPTIONS": json.dumps({"sdp.source.added.v1": [url("engine-extraction") + "/v1/events"]}),
        "AUTO_EXTRACT": os.getenv("AUTO_EXTRACT", "false"),
        "SDP_MODEL_DEFAULT": model,
        "MODELS_CONFIG": str(ROOT / "config" / "models.yaml"),
        "LEDGER_DB_URL": f"sqlite:///{DATA / 'ledger.db'}",
        "BUS_DB_PATH": str(DATA / "bus.db"),
        "SDP_DEV_USERS": json.dumps(DEV_USERS),
        "SDP_SEED_PRACTICES": str(ROOT / "config" / "practices.seed.json"),
        "SDP_PACKS": str(ROOT / "config" / "packs.yaml"),
        "WEB_DIST": str(ROOT / "web" / "dist"),
        "AUTH_MODE": "dev",
        "PYTHONPATH": str(ROOT / "libs" / "sdp_common"),
    }


def wait_healthy(port: int, timeout: float = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--model", default=os.getenv("SDP_MODEL_DEFAULT", "mock"))
    args = ap.parse_args()
    if args.fresh and DATA.exists():
        shutil.rmtree(DATA)
    DATA.mkdir(exist_ok=True)
    (DATA / "logs").mkdir(exist_ok=True)
    tokens = {name: secrets.token_hex(16) for name, _ in SERVICES}
    env = {**os.environ, **base_env(args.model, tokens)}
    procs = []
    for name, port in SERVICES:
        senv = {**env, "SDP_SERVICE_NAME": name, "SDP_SERVICE_TOKEN": tokens[name]}
        log = open(DATA / "logs" / f"{name}.log", "a")
        p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                             cwd=ROOT / "services" / name, env=senv, stdout=log, stderr=subprocess.STDOUT)
        procs.append((name, p))
        if not wait_healthy(port):
            print(f"  {name} did not start: see data/logs/{name}.log")
            for _, q in procs:
                q.terminate()
            sys.exit(1)
        print(f"  {name:<24} http://127.0.0.1:{port}  (docs: /docs)")
    print(f"\nAll services up. Model provider: {args.model}. Open http://localhost:8000\nPress Ctrl-C to stop.")
    (DATA / "tokens.json").write_text(json.dumps(tokens))  # for tests that call services directly

    def stop(*_):
        for _, p in procs:
            p.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while True:
        for name, p in procs:
            if p.poll() is not None:
                print(f"{name} exited with {p.returncode}: see data/logs/{name}.log")
                stop()
        time.sleep(1)


if __name__ == "__main__":
    main()
