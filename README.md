# Discovery Guru

A pre-sales discovery platform. It turns what a client tells you (transcripts, RFPs, emails, notes)
into a governed, cited picture of their situation, then into recommendations a solution owner signs off.

Discovery is organised around five questions:

| | Question | Holds |
|---|---|---|
| **WHY** | Why are they doing this? | business drivers, pain points, use cases |
| **WHAT** | What does good look like? | outcomes and success measures |
| **RULES** | What must we respect? | guardrails, policies, standards (each with a status, not just "do they have one") |
| **FROM** | Where are they today? | current estate, systems, volumes, skills |
| **HOW** | How do we get there? | recommendations, decisions, plan |

Above the five questions sits **the Brief**: what this engagement is for. It starts blank and fills as
evidence is confirmed (purpose, business value, success measures, scope, out of scope, and the engagement's
**shape**). The solution owner agrees each part. The shape (data platform, cloud migration, integration hub,
AI agents, application build, or a mix) is never picked up front; it emerges from the evidence, and once it
is set it decides which question packs, current-state checklists, Datatonic practices and recommendation
paths apply. Packs live in [`config/packs.yaml`](config/packs.yaml); adding one needs no code.

Every fact is a **claim** with a verbatim quote from a source. Agents propose; people confirm.
Nothing an agent writes counts until a person with the right role accepts it.

## Architecture in one picture

```
 browser ──IAP──▶ api-gateway ──▶ ledger (the only store, Postgres)
                     │   ▲              ▲
                     │   └── events ── event-bus ──▶ subscribers
                     ▼                  ▲
   ingestion · engine-extraction · engine-divergence · engine-questions
   engine-recommendation · engine-resolution · engine-artefacts · engine-brief
                     │
                     ▼
               model-gateway ──▶ Claude on Vertex AI │ Claude API │ Kimi │ Gemini │ mock
```

Twelve FastAPI services, each its own container. They talk in JSON over HTTP; every state change is
also published as a CloudEvents 1.0 packet. Contracts live in `libs/sdp_common/contracts.py`
and are exported as JSON Schema to [`contracts/`](contracts/). See [docs/architecture.md](docs/architecture.md).

| Service | Port (local) | Job |
|---|---|---|
| api-gateway | 8000 | Sign-in, role checks, the only public door; serves the web app |
| ledger | 8001 | The single source of truth: claims, rules, open items, decisions, recommendations, history |
| event-bus | 8002 | Validates and fans out CloudEvents (swap for Pub/Sub with `BUS_BACKEND=pubsub`) |
| model-gateway | 8003 | One API for every model; routing in `config/models.yaml`; spend caps |
| ingestion | 8004 | Files and pasted text → sources and chunks |
| engine-extraction | 8010 | Reads a source, proposes cited claims, stakeholders, open items |
| engine-divergence | 8011 | Finds where sources or people disagree |
| engine-questions | 8012 | Writes the next questions to ask, by gap and by stakeholder |
| engine-recommendation | 8013 | Proposes HOW recommendations at three levels; escalates practice-vs-client conflicts |
| engine-resolution | 8014 | Ages unanswered questions and moves them to insist / estimate / risk / decision / design-around |
| engine-artefacts | 8015 | Discovery summary, open items, decision log, divergence log, claims register |
| engine-brief | 8016 | Drafts the Brief and the engagement's shape from confirmed claims; never agrees anything |

## Run it

**1. In one process tree (fastest, SQLite, mock model)**

```bash
pip install -e libs/sdp_common
for s in services/*/; do pip install -r "$s/requirements.txt"; done
(cd web && npm ci && npm run build)
python3 scripts/dev.py --fresh          # starts all 12 services, seeds a demo engagement
open http://localhost:8000              # pick a dev user to sign in
pytest -q tests/e2e_test.py             # 11 end-to-end tests against the running stack
```

`python3 scripts/dev.py --model claude-api` uses a real model (needs `ANTHROPIC_API_KEY`).

**2. On your laptop with Docker (Postgres, one container per service)**
*(Dockerfiles and compose are written but not yet built: the authoring sandbox could not pull base images.
Expect the first `docker compose up` to need a small fix or two.)*

```bash
docker compose up --build
open http://localhost:8000
```

**3. On Google Cloud (sandbox project)**

```bash
PROJECT_ID=my-sandbox REGION=europe-west2 IAP_MEMBERS=group:presales@yourco.com \
  ./deploy/cloudrun/deploy.sh
```

Cloud Run for every service, Cloud SQL Postgres for the ledger, Secret Manager for the DB password,
Vertex AI for Claude, IAP for sign-in, Cloud Scheduler for daily ageing. Only `api-gateway` takes
traffic from outside; everything else is internal-ingress and IAM-invoker restricted.
Read the `VERIFY` notes in the script before the first run.

## Switching models

Edit `config/models.yaml` (or set `SDP_MODEL_DEFAULT`). Each task (extraction, divergence, questions, recommendation, brief)
can be routed to a different provider. Adapters exist for `vertex_anthropic`, `anthropic`,
`openai_compatible` (Kimi, DeepSeek, vLLM, Ollama…), `vertex_gemini`, and `mock`.
The model ids in the file are placeholders: confirm them for your account.

## Governance

See [docs/governance.md](docs/governance.md). The short version: four roles (owner, contributor, reviewer,
observer); agents can never confirm, approve or decide; the ledger re-checks every quote against its
source; every change is in an append-only history; superseding a claim flags everything built on it.

## Repo layout

```
libs/sdp_common/     shared contracts, events, auth, clients, engine kit
services/<name>/     one FastAPI service each (app/, prompts/, requirements.txt)
config/              models.yaml, packs.yaml, practices.seed.json
contracts/           generated JSON Schemas (python3 scripts/gen_schemas.py)
web/                 React 18 + Vite + TypeScript
deploy/              Dockerfiles, Cloud Run deploy script, Cloud Build config
docs/                architecture, governance, ADRs
tests/               end-to-end tests
```
