# Opportunity intelligence

## Objective
Turn discovered jobs into evidence-backed application decisions: a bounded shortlist, official listing verification, and cited public company briefs. User selected ODD, bounded web discovery (option B), and Brave Search API.

## Authority and baseline
- Branch: `feat/opportunity-intelligence`, based on PR #83 head `a17c6320a964189b42c38c23176069c32c34b6df`.
- PR #83 remains open. This feature must not be added to that PR.
- Source implementation and local delivery commits are authorized. User selected Feature Branch Chain: local child branches depend on their immediate predecessor; tracker stays at baseline until separately authorized integration. Pushes, PR creation, merge, deployment and paid/live API requests remain unauthorized.
- User explicitly accepted two review-size exceptions: public-fetcher slice at most 575 authored lines, evidence-resolver slice at most 925. Other slices must be at most 400, including tests and docs. Forecasts must be replaced with exact tree diff counts before commits.
- Existing untracked `.codegraph/` is not part of this feature.

## Design and constraints
- JobTrail retains scheduling, collection, scoring, SeenCache and journal authority. n8n is optional distribution only.
- Keep original aggregator link; never silently replace it with an uncertain official listing.
- At most three shortlisted opportunities per run. Explicit SKIP, hard exclusions and missing hard requirements cannot become recommendations.
- Preserve legacy score threshold behavior in the first safety task; ranking/threshold changes require explicit policy documentation.
- Brave receives only allowlisted public employer/title/location query fields, no CV, profile, score notes or provider prompts.
- Enrichment is opt-in and disabled by default; absent credentials produce a clear unavailable status, not fabricated success.
- Defaults: three search requests/run, five results/query, ten candidates, twelve fetch attempts/run including redirects, two redirects/request, HTTPS public destinations only, bounded time and response bytes.
- Public fetching must prevent private/metadata access and DNS rebinding; no ambient auth/proxies/cookies/browser scripts. Initial URL and redirects need the same validation.
- ATS hostname/board slug, title similarity or HTTP 200 alone do not prove official ownership, equivalent vacancy or active status.
- Verification requires employer ownership evidence plus a corroborated job identity; ambiguity, unavailable pages, closed listings and budget exhaustion have distinct statuses.
- Company brief: at most three factual claims with source URL, supporting excerpt and retrieval date. Search snippets are leads, not verified facts. No invented hiring probability or culture claims.
- Treat page content as untrusted data, not instructions. No automatic job applications.
- Hermetic tests use synthetic jobs/company pages only; no private files or runtime env/credentials.

## Workflow and checks
- Route: delegated direct, one writer at a time; multi-file implementation trigger applies to all source tasks.
- TDD: strict RED -> GREEN -> REFACTOR, continuing the user-approved project TDD constraint. Record actual RED commands/outcomes; missing evidence is not compliance.
- Runner: `PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider` from the worktree; focused files per task and full suite at closure.
- Builds/test scratch use ordinary pytest temporary paths, never fixture directories scanned as repository tests.
- Delivery strategy: ask-on-risk. Forecast 1,700–2,800 authored lines including tests. Reviewable local slices and local commits are now authorized. Actual per-slice counts and independent checks are required before commits; publication remains unauthorized.

## Tasks
- [x] T1 (verified; committed below): Fix class/exclusion-aware recommendation eligibility shared by production and simulation. Preserve legacy threshold/tie behavior, add at-most-three shortlist without breaking selected compatibility. Checks: focused automation, simulation and shortlist regressions; malicious/partial score-note cases.
- [x] T2 (verified; committed below): Add opt-in Brave public-search adapter with fixed trusted API endpoint, bounded public query/result schema, deterministic error statuses, quotas and secret hygiene. Check official provider docs; mock HTTP and no live key access.
- [x] T3 (verified; committed below): Add bounded public-only fetching with connection-address pinning, TLS hostname verification, redirect validation, streamed decoded byte/time budgets. Test private IPs/DNS rebinding/redirects/compression/body limits. Unsafe fetches fail closed.
- [x] T4 (verified; committed below): Implement official employer/listing resolution and extractive cited company briefs using public ownership evidence and corroborated vacancy identity. Tests: spoofed ATS, ambiguous matches, closed/not-found/failed states, unsupported/conflicting facts, freshness.
- [x] T5 (verified locally; committed below): Wire opt-in enrichment into the canonical run/notification boundary using a bounded public serializer. Preserve original URLs, selected and v1 compatibility; version new contracts deliberately. Tests: default-off and dry-run zero-network, real canonical path, privacy and budgets.
- [x] T6 (verified locally; publication pending): Independent review, full tests, offline packaging and static checks; operator documentation and honest unavailable/live-validation limitations.
- [x] T7 (prepared; committed below): Prepared byte-preserving snapshots and 13 proposed trees, all within approved caps. Independent behavioral checks are T8.
- [x] T8 (independently verified): Every proposed tree passed its public-inventory full suite and diff check. Complete final inventory passed 1346 tests, zero skips; final/root collected node IDs are identical.
- [x] T9 (independently verified locally): Created 13 local child branches and Conventional Commits; every commit tree equals its independently tested proposal. Final source bytes and tracker baseline are unchanged; commit identities are recorded below. No push, PR or merge.

## Evidence and progress
Exploration found no general-web provider in current code; user selected Brave Search API. Existing ATS adapters require configured boards and do not prove employer ownership. Current selection ignores classification/exclusions; T1 addresses this first. T1 source implemented with observed RED/GREEN. Independent review approved; focused suite 199 passed and full suite 837 passed; diff check passed. No commit/push/deploy. Native assessment was unassessable and required independent verification; no native approval claimed.

Brave official documentation retrieved: https://api-dashboard.search.brave.com/documentation/services/web-search — fixed endpoint https://api.search.brave.com/res/v1/web/search; X-Subscription-Token auth; web.results contains title/url/description. Search snippets remain unverified leads, not company citations. No live authenticated requests performed.

T2 implemented as an isolated, default-off adapter; no launcher activation yet. Independent review approved after fixing credential-bearing result URL rejection. T2 tests: 136 passed; full suite: 973 passed; diff check passed. Mocked HTTP only, no real Brave credentials or authenticated requests. Search snippets are unverified leads; destination DNS/fetch safety is still T3. At T2 closure, source changes were uncommitted and unpublished; current commit evidence is recorded below.

T3 implemented and independently approved after a real-parser buffering/accounting defect was found and fixed with observed RED (five failures) -> GREEN. Focused 88 tests and full suite 1061 tests passed, zero skips. One verifier command initially ran from /root and failed to collect tests; corrected worktree reruns passed. Connections pin validated public IPs while TLS checks original host. Budgets cap decrypted HTTP wire reads (including headers/framing/discarded bytes), not TLS handshake/TCP overhead. Compressed responses fail closed; no live fetches performed. Libc DNS cannot be cancelled; at most two outstanding daemon lookups remain bounded until completion.

T4 mapping confirms no trusted employer-domain field in current source adapters. Conservative resolver must keep candidates unverified without an explicit public employer-domain trust anchor; search rank, ATS slug and self-claimed Organization are insufficient. Careers-page links must prove exact tenant/listing delegation. Company claims are extractive and attributed, not inferred. Fetcher currently cannot distinguish 404/410 from generic HTTP failure; do not invent closed/not-found evidence from that result.

T4 implemented with independent approval: 99 focused and 1160 total tests passed; diff check passed. Original worker timed out, so initial TDD provenance is unknown; follow-up repairs have observed RED (12 failures) -> GREEN. Conflicting same-vacancy records are rejected before matching and citations preserve actual raw page reference fields. No live provider/page calls; unknown employer ownership remains unverified; fetch errors do not prove closure.

T5A independently approved: canonical launcher lazily creates one per-run search/fetch/resolver after scoring; disabled/simulation do not access credentials or network; public-only projection and notification cards. Observed RED/GREEN for timer placement and inline credential-URL rejection; unsafe citation claims are omitted rather than rewritten. T5B source review found no code blocker: schema v2 completion cards are opt-in, default/no-card v1 preserved, closed adapter validation and versioned retry identity covered. Focused T5B 232 tests and full suite 1346 passed. Stale standalone-only documentation was corrected; final checks were completed in T6. No live consumer migration.

T6 independently approved for local readiness: final full suite 1346 passed, zero skips; shellcheck and diff check passed. Compileall passed using external temporary pycache. Offline wheel test ran with --no-index and isolated CWD; Node helper harness ran, not full n8n runtime. Obsolete standalone-only documentation was corrected. No full-source strict-TDD claim: initial T4 provenance remains unknown. Native risk assessment unavailable; independent checks performed, no native approval claimed.

## Delivery plan and limits
Actual authored change: approximately 3,992 lines (3,921 additions plus 71 deletions), including new source/tests/docs, excluding .codegraph, artifacts and this task file. This exceeds the original upper forecast by approximately 1,192 lines. Local commits are authorized, but must use the more granular chain below. Original major feature boundaries, with related tests and docs in each:
1. Recommendation eligibility and shortlist compatibility.
2. Bounded Brave public search.
3. Public HTTPS transport and safety tests.
4. Employer/listing evidence resolver and cited briefs.
5. Public cards and canonical notification/launcher integration.
6. n8n completion schema v2 with v1 compatibility.
These six initial boundaries required more granular slicing; the completed local chain below records the actual review units and approved caps.

Pending live prerequisites: separately authorized Brave key setup/quota, explicit public employer trust anchors, live public-provider/DNS/page validation, n8n v2 consumer migration and actual delivery/persistence verification. Tests do not establish live-provider or production readiness. Feature remains disabled by default. Its verified source is now committed locally on the child chain; it is unpublished and the tracker remains at baseline.

## Authorized local chain
The existing `feat/opportunity-intelligence` tracker remains at baseline `a17c632`. Each local child starts from the prior child. No tracker merge or remote publication is authorized.

| Slice | Branch suffix | Boundary | Authored cap |
|---|---|---|---:|
| 01 | shortlist-policy | Shared eligibility/shortlist policy, tests and docs | 400 |
| 02 | shortlist-orchestration | Production/simulation compatibility, tests and docs | 400 |
| 03 | public-search-contract | Public query/config/result contracts and tests | 400 |
| 04 | brave-adapter | Bounded provider adapter, HTTP tests and docs | 400 |
| 05 | public-http-guards | Fetch config/URL/IP/DNS guards and direct tests | 400 |
| 06 | pinned-http-transport | Pinned TLS/byte/deadline transport and direct tests | 400 |
| 07 | public-fetcher | Fetch orchestration and inseparable safety regressions | 575 (approved exception) |
| 08 | evidence-models | Closed public evidence/trust models and tests | 400 |
| 09 | evidence-resolver | Employer/listing evidence and behavioral regressions | 925 (approved exception) |
| 10 | public-cards | Bounded validated public cards, privacy tests and docs | 400 |
| 11 | automation-enrichment | Default-off lazy enrichment and runner tests | 400 |
| 12 | n8n-v2 | Completion v2 mapping/adapter and v1 compatibility | 400 |
| 13 | canonical-launcher | Actual launcher wiring and canonical timing/delivery tests | 400 |

Tests and docs belong with their behavior, not separate cosmetic slices. Intermediate direct-helper tests may be needed where existing tests require later APIs; count subsequent deletions too. Preserve the completed final source/test/doc bytes exactly. Do not introduce unsafe partial resolver behavior. If an exact candidate exceeds its approved cap, stop rather than create a silent exception.

## Local chain preparation evidence
Scratch: `/tmp/jobtrail-opportunity-chain.un33v1vc`, including `chain-manifest.json`, `backup-manifest.json` and public-only materialization manifest. Original files, normal Git index, HEAD, branch and status remained unchanged. Final proposed tree: `a0ac2d2d40850bd614a70b12bb32bd4123c3396e`; source/test/script/runtime-documentation bytes match the completed implementation. ODD task documentation evolves separately.
Exact final prepared authored counts for slices 01–13: 241, 229, 228, 362, 212, 324, 575, 248, 875, 351, 176, 235, 290. First fetcher proposal exceeded its cap (665); it was rejected and guard/transport contracts and tests redistributed into their coherent earlier units. No size cap was silently increased. Intermediate-only EOF whitespace was corrected without changing final source bytes. Independent verification approved all 13 prepared snapshots. Recorded public-inventory full-suite counts: 824, 836, 868, 972, 1010, 1014, 1060, 1069, 1159, 1283, 1288, 1339, 1345, all zero skips. One unchanged baseline public template (`prompts/job-fit.example.md`) was initially omitted from materialization; restoring it gave 1346 passed at the final snapshot, matching the root full suite and exact collected node-ID set. That constant baseline case was not rerun on every earlier snapshot. Final shellcheck/compileall and all tree diff checks passed. Original 17 source/test/script/runtime-doc bytes match final tree; normal index/refs remained unchanged through verification. Local source commits now exist, matching every prepared tree exactly. The normal Git index was synchronized after each ordinary commit; no hooks were bypassed. Final source bytes and the tracker baseline were preserved.

## Local commit evidence
T7 plan was included in slice 01; T8 verification is tied to the exact trees recorded below. T9 independently confirmed all 13 actual commit trees, parents, branch refs, approved budgets, unchanged tracker/remote refs and final byte parity. Post-commit root suite passed 1346 tests, zero skips; shellcheck and diff checks passed. This evidence update belongs to child 13 and must keep its complete branch diff at most 400 authored lines.

| Slice | Source commit | Authored lines |
|---|---|---:|
| 01 | `21d25d159b5bc9408ec4df921f9da9ea91af4d87` | 241 |
| 02 | `4a4f201a2d0b2fedf5ac9abcffda701f56f9a6cc` | 229 |
| 03 | `5ab51d120f9035ec4e0ca4dbd2faf0d2d41574b5` | 228 |
| 04 | `d81448f21820904607f97dacdc8c159f0cfdeb86` | 362 |
| 05 | `0f16e61203548ceaa91a33df0121ba8065116701` | 212 |
| 06 | `2d62acd87ba9d9e5845e5f4f68ce76e78208dfce` | 324 |
| 07 | `8122fd5690e35a8b7fc539d8ddc2ff91352688c8` | 575 |
| 08 | `773832d81061221360ea01fe527482152f5d5994` | 248 |
| 09 | `ae87c9bd51b4f9eee6ff2112b37f4a03449407c8` | 875 |
| 10 | `49e1345d80b737826f5998106eb25ecf02644623` | 351 |
| 11 | `0d64ab74659cdf1d0d9755d7485fcb48c4cff5d7` | 176 |
| 12 | `3fd8893078ac46b6802f282e15b1fc4345015878` | 235 |
| 13 | `883ce030d5319387215b3cd77c4d4d4c8688141d` | 290 (before evidence update) |

## Next step
The verified chain is local only. The tracker remains at baseline and the worktree stays on child 13, which contains the full feature. Publishing branches/PRs, tracker integration, merge, API activation and deployment require separate authorization. Remaining untracked `.codegraph/` is unrelated and untouched. No push, PR, merge, real API activation or deployment is authorized.
