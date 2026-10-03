# Opportunity chain review fixes

## Intent and authority

Fix verified bot findings at their origin PRs, propagate corrections forward with normal Git merges, and verify the corrected chain before any PR merge. User explicitly accepted this approach on 2026-10-03. This authorizes source remediation and dependency propagation, not merging PRs, integrating the draft tracker into main, deploying, activating live APIs, creating issues, posting replies, or resolving review threads. Existing local work-unit commit authority is retained. Publishing changes to existing remote heads will be reported separately before delivery.

Scope: PR83 dependency and opportunity intelligence PR85 (draft tracker), PR86–98. Preserve the original opportunity chain, all original commits and unrelated refs. No reset, stash, rebase, force push, hook bypass, private CV/profile/environment/credential access, or inspection of `.codegraph` contents.

## Accepted design

Use a single pure score-eligibility policy in existing `scoring.py`, available at PR83, and delegate shortlist eligibility to it at PR86. Reject explicit SKIP/exclusions/missing hard requirements and malformed or incomplete additive safety metadata, while preserving genuine legacy score-only behavior, threshold, ranking, ties and positional compatibility. Do not import future feature modules into PR83.

Fix public URL credentials centrally and consume the policy at HTTP/card boundaries. Reject site-local IPv6 before resolving/connecting. Generic ATS hosts cannot establish employer ownership. Malformed page attributes are untrusted data and must not invalidate otherwise usable evidence. Cards must bind to their source job and retain validated original links, including fragments. Preserve lazy opt-in enrichment, strict feature-flag validation, notification choices, normalized employer lookup, bounded budgets, and versioned closed n8n envelopes.

Keep injected transports caller-owned through a minimal non-closing adapter; preserve per-query cookie isolation. Normalize a stripped empty v2 selected mapping to null. No new scheduler, providers, parallel state/config representations, automatic applications, or expansion of claim/search/fetch budgets.

## Route, checks and delivery

- Workflow: ODD, delegated direct. No SDD artifacts.
- Source work is delegated: multi-file edits and preparation for writes trigger one bounded writer per origin unit. Verification is delegated; writes remain single-threaded.
- TDD: ON, inherited explicit project/user RED → GREEN → REFACTOR contract. Baseline synthetic probes are discoveries, not task RED evidence. Each new regression test must fail for the intended product behavior before source edits.
- Exact runner: `PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider`.
- Hermetic public synthetic fixtures only; no live DNS, HTTP, Brave, n8n or actual runtime journals.
- Delivery: retain Feature Branch Chain, tracker85 draft/no-merge. Normal forward dependency merges only; both predecessor and child must include predecessor fixes before comparing/publishing child diffs.
- Estimated correction work: about 500–800 authored diff lines spread across existing origin units, including regression tests. This is a forecast, not measured evidence. Each unit closes with code/tests together and a Conventional Commit; no publishing or PR merges inferred from tests.
- Existing PR caps: 400 additions+deletions including tests/docs, except PR92 ≤575 and PR94 ≤925. PR83 already has 2938 changed lines with no observed exception; correction authority does not authorize its eventual size exception or merge. Measure actual updated diffs; stop for a named exception if a bounded correction exceeds an approved cap. Do not minify or drop tests to fit.
- Native review: read effective user-owned mode; never enable it. Previously unavailable native review is not approval. Record exact current outcome per candidate. If assessment is unavailable, use independent verification as high-risk fallback.

## Tasks

- [x] R1 — PR83: shared eligibility policy and canonical/simulation SKIP exclusion regression tests.
- [x] R2 — PR86: delegate shortlist eligibility; reject missing additive safety arrays. Verify PR87 propagation.
- [x] R3 — PR88: reject nested credential-bearing URLs and unsafe fragments.
- [x] R4 — PR89: preserve borrowed injected transport lifecycle and fresh-client cookie isolation.
- [ ] R5 — PR90: shared credential URL boundary and site-local IPv6 rejection. Verify PR91/92 propagation.
- [ ] R6 — PR93: deny generic ATS host families as employer trust anchors.
- [ ] R7 — PR94: tolerate valueless/malformed HTML attributes without discarding valid evidence.
- [ ] R8 — PR95: enforce evidence/job association and preserve validated original fragments.
- [ ] R9 — PR96: failure-only notification gating, employer normalization and shared strict intelligence flag parser.
- [ ] R10 — PR97: null stripped empty v2 selection and align rollout documentation.
- [ ] R11 — PR98: reuse strict flag parser; preserve disabled/dry-run zero-secret-read lazy behavior.
- [ ] R12 — Independently verify every corrected head, full final suite, focused diffs/budgets/ref preservation; report delivery readiness and remaining authorization.

## State and evidence

R1–R4 COMPLETE; R5 IN PROGRESS; R6–R12 pending. Active worktree: `/root/.config/superpowers/worktrees/jobtrail-ai-scorer/feat-opportunity-intelligence-review`, branch `feat/opportunity-intelligence-review-04-brave-adapter`, source/merge commit `abc3d6d58dab3dd75c6d60f113bcdd658669e441`.

Prior read-only triage: Engram280/281, `/tmp/jobtrail-bot-validation-20261003T052646Z`, fresh GitHub metadata 2026-10-03 14:44 UTC. PR83 had 799 passing tests and five earlier bot findings fixed, but a separate canonical probe selected/notified an explicit SKIP job; final98 rejected it. Twelve feature root causes reproduced; transport and unusual selected-input production impact not demonstrated. PR97 stale documentation is specifically superseded by PR98.

R1 evidence: bootstrap `40b4675`; source `3ef307c4cf727fa80c3cfc095efe9486364856f4`, 248 additions + 5 deletions. Writer observed RED 67 failures before source edits, then GREEN 67 regressions / 253 focused / 866 full, zero skips. Independent full suite: 866 passed; actual legacy/additive writer serialization both eligible. Standalone probe initially imported a stale editable sibling package; process-local `PYTHONPATH=src` and source-file assertion corrected verification only, without environment repair. Native assessment high; inspect stopped at `managed_assets_outdated`, no START/lineage/approval and no harness sync. No publishing or PR merges.

R2 evidence: source `286633cd31bcb08e8d468f3aadd71eff173f3cb0`, 34 additions + 39 deletions. RED 11 unsafe-shortlist assertions; GREEN 42 focused / 908 full, zero skips, independently repeated. PR86 focused diff 236/400. Normal propagation into PR87 conflicted only in automation.py; separately diagnosed and reconciled to shared shortlist orchestration plus pre-filter score counters, with one positive fixture upgraded to complete safe additive metadata. Existing negative fixtures unchanged. Writer and independent checkpoint: 231 focused / 920 full, zero skips; merge `3b460b7383f7c3f77dd9dd2a69604deee228226c`, PR87 focused diff 236/400 before this evidence update. Assessment unassessable, independent high-risk fallback; inspect `managed_assets_outdated`, no native approval/sync. Next: propagate checkpoint to PR88 and perform R3 public URL credential correction. Publishing, all remaining origin fixes and final all-head verification are pending.

R3 evidence: source `141bedc8d7f73073b80ffbb805e243378bc64841`, 167 additions + 3 deletions. Initial RED 28 credential cases and five later fragment cases; independent review found three encoded-fragment bypasses despite 83 focused / 1003 full passing. Fresh RED three bypass assertions, then final GREEN 91 focused / 1011 full, zero skips, independently repeated with all three rejected and ten safe controls accepted. Bounded generic URI inspection: three decode passes and depth four; no query rewriting. PR88 focused diff 392/400 before this receipt. Assessment unassessable/high-risk fallback; fresh inspect `managed_assets_outdated`, no START/native approval/sync. Future HTTP reuse and original card fragment preservation remain R5/R8. Next: propagate normally to PR89, preserve borrowed transport lifecycle for R4. No publishing or PR merges.

R4 evidence: source/normal merge `abc3d6d58dab3dd75c6d60f113bcdd658669e441`. Append-only test conflicts separately diagnosed; preserved both imports/blocks and inherited URL regressions. Baseline 196 focused; new RED one premature-close assertion; GREEN 197 focused / 1117 full, zero skips, independently repeated. Two queries succeed with no cookies, caller-owned transport stays open, explicit caller close occurs exactly once. Minimal non-closing delegate wraps injected transports only; default HTTPX ownership unchanged. PR89 focused diff 394/400 before this six-line receipt. Native assessment high, inspect `managed_assets_outdated`, no START/approval/sync. Next: R5 shared HTTP URL policy and site-local IPv6 at PR90, then verify propagation to PR91/92 without adding authored lines to capped PR92. R5–R12 and final all-head verification remain unfinished; no publishing, PR merges or live services.
