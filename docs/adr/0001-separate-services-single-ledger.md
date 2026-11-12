# ADR 0001 — Separate services, one ledger

**Status:** accepted

**Context.** Discovery logic falls into distinct jobs (extract, diverge, ask, recommend, resolve, publish)
that change at different speeds and may use different models. A single app would couple them.

**Decision.** Each job is its own stateless service. All state lives in one service, the ledger, which
also enforces governance. Engines read context from it and write proposals to it.

**Consequences.** Engines can be rewritten, scaled or swapped independently. The ledger is a
bottleneck and a single point of failure, which is acceptable at pre-sales volumes; Cloud SQL HA can be
switched on later.
