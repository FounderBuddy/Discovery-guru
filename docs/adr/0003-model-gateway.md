# ADR 0003 — All model calls through a gateway

**Status:** accepted

**Context.** We start with Claude on Vertex AI but want to switch any task to Kimi, Gemini or a
self-hosted model later.

**Decision.** Engines call only `model-gateway /v1/generate` with a task name. `config/models.yaml`
routes each task to a provider adapter. The gateway enforces spend caps, parses JSON with one retry,
and records cost.

**Consequences.** Switching models is a config change. Prompt quality may differ by model, so prompts are
versioned files and each run records the prompt version and model used.
