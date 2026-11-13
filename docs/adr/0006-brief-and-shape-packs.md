# ADR 0006 — A Brief that starts blank, and a shape that picks the packs

**Status:** accepted

**Context.** Engagements vary: a data platform, an integration hub, a couple of AI agents, an AWS-to-GCP
migration, or a mix. The five questions are generic, but what we must learn, which practices apply and
which recommendation paths make sense depend on the kind of work. Early builds leaned towards data
platforms (a `dataOrTransactional` profile field, data-heavy practices).

**Decision.** Add the Brief: six slots (purpose, value, measures, scope, non-goals, shape) that start blank,
are drafted from confirmed claims with citations, and are agreed by the solution owner. The shape slot names
one or more packs from `config/packs.yaml`. It is not chosen up front: it emerges from evidence and is
agreed like any other slot. Applied packs drive the question engine's checklist, filter practices by
`appliesTo`, and supply recommendation paths. Remove `dataOrTransactional`.

**Consequences.** The platform no longer assumes a kind of engagement; new kinds are configuration. Mixed
engagements apply several packs at once. Packs apply tentatively from a drafted shape, so early questions
are already shape-aware; the owner's agreement firms them up. Pack signals only help propose a shape; they
never decide it.
