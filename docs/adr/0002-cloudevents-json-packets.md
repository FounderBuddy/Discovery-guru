# ADR 0002 — CloudEvents JSON packets, HTTP bus first

**Status:** accepted

**Context.** Services need to react to each other without tight coupling, and the transport should be
replaceable.

**Decision.** Every state change is published as a CloudEvents 1.0 structured-JSON event with
`sdp*` extension attributes. A small HTTP event-bus validates and fans out; `BUS_BACKEND=pubsub` swaps
in Google Pub/Sub without changing producers.

**Consequences.** Events are best-effort; correctness never depends on them because the ledger is the
source of truth. Schemas are versioned by type name.
