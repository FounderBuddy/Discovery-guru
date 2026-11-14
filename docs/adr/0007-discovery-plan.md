# ADR 0007 — Plan the discovery from what we have and the time we have

**Status:** accepted

**Context.** The platform assumed we already knew how to run discovery: which sessions, how many, who attends.
In practice the first artefact is often an RFP or one call transcript, and the time available ranges from a
single call with a prospect to a two-week paid discovery. Session design, attendees on both sides and the
questions to ask should follow from the evidence and the time, and nobody should be guessed at.

**Decision.** Add a Discovery Plan built by a new engine-plan service. The person chooses the format (or takes
the recommendation): it is a commercial fact, not something to infer silently. Code builds the skeleton from
the format and the engagement's packs, so the budget always holds; the model only tailors content. Client
functions with no named owner become nominations, never invented names. Plans are versioned and approved
by the solution owner; a shape change sends them back for review. Formats, roles and session templates are
configuration.

**Consequences.** One button turns an RFP into a staffed, time-boxed plan, and the same evidence gives a
deeper plan when there is more time. The planner reads unconfirmed material, which is acceptable for a
working plan but not for the Brief. Session templates will need tuning from real deals; that is a YAML
change. The model cannot restructure the plan, which costs some creativity in exchange for predictability.
