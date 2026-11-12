# Governance

## Roles

| Role | Can |
|---|---|
| **owner** (solution owner) | everything: invite and remove people, set the profile and budget, resolve divergences, run the resolution engine, delete sources, plus all below |
| **contributor** | add sources, run engines, propose/edit/confirm/reject claims, stakeholders, rules, open items, decisions, recommendations |
| **reviewer** | read everything, write decisions and open items, approve recommendations |
| **observer** | read only |

The exact matrix is `ROLE_PERMS` in `services/ledger/app/authz.py`.

Practice library edits need `isPracticeAdmin` on the user, separate from engagement roles.

## Rules enforced by the ledger (not the UI)

- Agents cannot confirm, approve, decide or resolve anything (`HUMAN_ONLY`).
- Only api-gateway and ingestion may act as a user; engines always act as themselves.
- A claim's quote must appear in its source (whitespace and quote-mark normalised). Otherwise: 422.
- Bulk confirmation (`confirm-high`) only touches high-confidence proposals; everything else is
  confirmed one by one.
- A source cannot be deleted while a confirmed claim cites it.
- Superseding a claim marks every open item, recommendation and decision built on it `needs_review`.
- Every write appends to `History` with actor, correlation id, before/after and reason.
- Each agent run records prompt version, model, tokens and cost (`AgentRun`); per-engagement spend
  is capped by the model gateway.

## Conflicts between client rules and Datatonic practice

Every conflict is escalated. The engine never picks a side. The solution owner decides, with a reason,
and the decision is linked to both the rule claim and the practice.

## Data handling

- Client material stays in the GCP project: Cloud SQL, and Claude on Vertex AI by default.
- Switching a task to an external provider (Claude API, Kimi…) sends that task's prompt outside the
  project. Do it per engagement only with the client's agreement.
- No user emails or client names belong in this repo. Dev users use `@example.com`.
