# Architecture

## Principles

1. **One store.** The ledger owns all state. Engines are stateless: they read context from the ledger,
   call a model, and write proposals back. Losing an engine loses nothing.
2. **Agents propose, people decide.** Engines act as `agent` actors. The ledger refuses any
   human-only action (confirm, approve, decide, resolve) from an agent, whatever the engine asks.
3. **Everything is cited.** A claim carries a verbatim quote and the source it came from. The ledger
   re-verifies the quote against the stored source text; invented quotes are rejected.
4. **Contracts first.** Every request, response and event is a Pydantic model in `sdp_common.contracts`,
   exported to JSON Schema. Services can be rewritten in any language against those schemas.
5. **Model-agnostic.** No engine calls a model directly; all calls go through the model gateway.

## Request flow: uploading a transcript

```
user ─POST /api/engagements/E/sources/upload─▶ api-gateway   (checks role: contributor+)
api-gateway ─▶ ingestion /v1/.../sources/upload               (acts as the user)
ingestion   ─▶ ledger POST /v1/.../sources                    (stores text + 60k-char chunks)
ingestion   ─▶ event-bus  sdp.source.added.v1
event-bus   ─▶ engine-extraction /v1/events                   (only if AUTO_EXTRACT=true)
user ─POST /api/engagements/E/run/extraction─▶ api-gateway ─▶ engine-extraction /v1/run
engine-extraction ─▶ ledger GET context ─▶ model-gateway /v1/generate ─▶ provider
engine-extraction ─▶ ledger POST claims/batch (as agent; status=proposed)
ledger      ─▶ event-bus sdp.claim.proposed.v1 … ; engine ─▶ sdp.agent.run.completed.v1
user confirms claims in the UI ─▶ ledger (human-only action)
```

## Packets

The envelope is CloudEvents 1.0 (structured JSON) with four extension attributes:

| Attribute | Meaning |
|---|---|
| `sdpengagement` | engagement id the event belongs to |
| `sdpactortype` | `user`, `agent` or `system` |
| `sdpactorid` | who did it |
| `sdpcorrelation` | ties every call and event of one user action together |

Event types are versioned in the name (`sdp.claim.superseded.v1`). A breaking change ships as `.v2`
alongside `.v1` until every subscriber has moved. Schemas: `contracts/events/*.json`.

## Service-to-service auth

| Mode | Where | How |
|---|---|---|
| `INTERNAL_AUTH=token` | laptop, compose | each service has a random bearer token; `SDP_SERVICE_TOKENS` maps token → service |
| `INTERNAL_AUTH=gcp` | Cloud Run | callers mint a Google ID token for the callee's URL; Cloud Run verifies it (IAM invoker); `SDP_SERVICE_ACCOUNTS` maps the SA email → service name |

Either way the ledger then applies `SERVICE_PERMS`: least privilege per service. Only
`api-gateway` and `ingestion` may carry a user's identity (`X-Actor-Type: user`); engines always act as
themselves. So a compromised engine can propose, never confirm.

## Data model (ledger)

`Engagement` → `Member` (role) · `Stakeholder` · `Source` → `SourceChunk` · `Claim` (question, kind,
quote, status, supersededBy) · `RuleStatus` · `OpenItem` (owner, ageing, resolution path) ·
`Divergence` · `Decision` (incl. escalated conflicts) · `BriefSlot` (status, text, shapes, cited claims, gaps, suggestion) · `Recommendation` (level, conflicts, status) ·
`Practice` (Datatonic practice library, with `appliesTo` packs) · `AgentRun` (prompt version, model, tokens, cost) · `History`
(append-only, every change, who and why).

## The Brief and engagement shape

The Brief is six slots: purpose, value, measures, scope, nonGoals, shape. Each moves through
`empty → draft → agreed`, and back to `needs_review` when a claim it cites is superseded.

- **Drafts** come from engine-brief (from confirmed claims only, citing their ids) or from a person.
  A slot with no confirmed claims behind it stays empty, with "still to find out" gaps that feed the
  question engine.
- **Agreement** is the solution owner's alone, on the exact words, and only while every cited claim is
  still confirmed. Editing an agreed slot puts it back to draft.
- **An engine never overwrites an agreed slot.** A different draft is stored beside it as a
  `suggestion` the owner can use or dismiss.
- **Shape** is a list of pack keys from `config/packs.yaml`. A drafted shape applies its packs
  *tentatively*; an agreed shape applies them firmly. The ledger's `/context` packet carries the Brief and
  the applied packs, so every engine sees them:
  - the question engine asks for each pack's `mustKnow` items the claims don't cover, and its `askAbout` topics;
  - the recommendation engine gets only practices whose `appliesTo` matches the applied packs
    (a practice with no `appliesTo` applies everywhere), and serves the Brief's purpose, scope and non-goals;
  - the How page offers each pack's `paths`.

"core" is always applied. Adding a kind of engagement means adding a pack block: no code changes.

## Recommendation levels and conflicts

1. **Practice** — a maintained Datatonic practice from the library applies.
2. **Best practice** — no practice exists; the engine researches and cites an opinionated default.
3. **Client rule** — a confirmed client RULES claim forces a choice.

When a client rule contradicts a practice, the recommendation is created `blocked` and each conflict
becomes an `escalated` decision for the solution owner. Only when all are decided does it move to
`needs_review`, then to `approved`.

## Unanswered questions

Open items age daily (Cloud Scheduler → engine-resolution `/v1/tick`). Past their threshold the
resolution engine proposes one of five paths: **insist** (chase), **estimate** (assume and flag),
**risk** (carry as a named risk), **pending decision** (escalate), **design around** (make the
design independent of the answer). Policy is versioned (`policy-v1`).

## Schema changes

The ledger schema is managed by Alembic (`services/ledger/migrations/`, baseline `0001`). The ledger runs
`upgrade head` on every start, under a Postgres advisory lock so concurrent Cloud Run instances don't race.
A database created before migrations existed is refused with a clear message: start it fresh.
Every model change ships with a migration (`make db-revision`), and `make db-check` fails if they drift.

## Moving to Pub/Sub

`BUS_BACKEND=pubsub` publishes to topic `sdp-events` with the event type as an attribute. Push
subscriptions would then need a small adapter on each subscriber to unwrap the Pub/Sub push envelope
(`message.data` is the base64 CloudEvent). The deploy script keeps the HTTP bus for now: one less moving
part for a sandbox, and the ledger, not the bus, is the source of truth.
