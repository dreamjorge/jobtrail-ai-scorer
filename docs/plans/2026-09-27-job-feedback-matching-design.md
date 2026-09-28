# Job feedback and balanced matching design

## Goal

Increase useful job opportunities without sacrificing precision by separating strict matches from adjacent opportunities, while collecting explicit user feedback through bounded n8n action links.

## Constraints

- JobTrail remains authoritative for discovery, SeenCache, scoring, classification, and journal state.
- n8n remains disabled by default, local-only, and does not become a second scheduler.
- CV and private profile files remain local to JobTrail and are never sent to n8n.
- Existing sanitized JobTrail-to-n8n handoff remains backwards compatible.
- No automatic application submission.
- No automatic score recalibration until sufficient real feedback exists.

## Architecture

JobTrail owns the candidate context, search, scoring, evidence classification, and durable run identity. n8n receives only bounded public job fields and orchestrates notification plus feedback callbacks.

The matching strategy is a versioned repository Markdown file containing public rules, not CV content. It defines target roles, required signals, equivalent skills, adjacent roles, exclusion rules, location/seniority tolerance, and evidence policy.

## Matching model

Each scored opportunity receives two dimensions:

- `fit_score`: precision against required role and candidate evidence.
- `coverage_score`: opportunity potential when the match is adjacent or uses configured equivalences.

The classification is explicit:

- `APPLY`: high fit and critical requirements satisfied.
- `REVIEW`: promising but evidence or requirements need confirmation.
- `EXPLORE`: adjacent opportunity with useful coverage but lower precision.
- `SKIP`: exclusion rule or clear contradiction.

Evidence must be labelled `direct`, `equivalent`, `inferred`, or `missing`. Inference cannot be represented as direct experience.

The initial public strategy prioritizes Python/backend/API, automation, CI/CD, Docker, databases, service integration, agents/LLM tooling, and C++/MATLAB where relevant. Adjacent coverage includes backend engineering, automation/platform/integration engineering, developer tooling, QA automation, data/AI tooling, and technical solutions engineering.

## Feedback flow

The existing JobTrail-to-n8n envelope remains schema version 1 and gains only optional bounded fields. For selected opportunities it may include classification, fit/coverage scores, and one-time action descriptors. It never includes CV, private profile, full description, prompts, notes, credentials, or reusable secrets.

n8n renders local action links for `applied`, `dismissed`, and `interesting` (with optional `not_my_profile` if retained after implementation review). Each action is bound to the event/run/job identity, expires, and is single-use.

The callback emits a separate `job.feedback.recorded` event. JobTrail validates the action, expiry, replay status, identity correlation, and input bounds before journaling it. Callback errors are generic and never disclose private data.

## Observability

Record bounded metrics for classification counts, feedback actions, feedback latency, score distributions, and exclusion reasons. Do not tune weights automatically in this phase.

## Delivery phases

1. Address the two additional PR #81 findings: validate the legacy `/health` payload and sanitize supplied run IDs through the notification builder path.
2. Add the strategy Markdown, evidence labels, dual scores, and deterministic classification while preserving current score compatibility.
3. Add one-time feedback action descriptors and a local n8n callback workflow using synthetic fixtures.
4. Measure real feedback before proposing any calibration or automatic strategy changes.

## Out of scope

- Reading or modifying the private CV through n8n.
- Automatic job applications.
- Public exposure of n8n.
- A second scheduler in n8n.
- Training or automatic weight updates without labelled feedback.
