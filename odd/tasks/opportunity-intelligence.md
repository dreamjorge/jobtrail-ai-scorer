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
- [x] T1 (verified, uncommitted): Fix class/exclusion-aware recommendation eligibility shared by production and simulation. Preserve legacy threshold/tie behavior, add at-most-three shortlist without breaking selected compatibility. Checks: focused automation, simulation and shortlist regressions; malicious/partial score-note cases.
- [x] T2 (verified, uncommitted): Add opt-in Brave public-search adapter with fixed trusted API endpoint, bounded public query/result schema, deterministic error statuses, quotas and secret hygiene. Check official provider docs; mock HTTP and no live key access.
- [x] T3 (verified, uncommitted): Add bounded public-only fetching with connection-address pinning, TLS hostname verification, redirect validation, streamed decoded byte/time budgets. Test private IPs/DNS rebinding/redirects/compression/body limits. Unsafe fetches fail closed.
- [x] T4 (verified, uncommitted): Implement official employer/listing resolution and extractive cited company briefs using public ownership evidence and corroborated vacancy identity. Tests: spoofed ATS, ambiguous matches, closed/not-found/failed states, unsupported/conflicting facts, freshness.
- [x] T5 (verified locally, uncommitted): Wire opt-in enrichment into the canonical run/notification boundary using a bounded public serializer. Preserve original URLs, selected and v1 compatibility; version new contracts deliberately. Tests: default-off and dry-run zero-network, real canonical path, privacy and budgets.
- [x] T6 (verified locally; publication pending): Independent review, full tests, offline packaging and static checks; operator documentation and honest unavailable/live-validation limitations.
- [ ] T7 (in progress): Prepare explicit byte-preserving snapshots and bounded local chain trees without changing completed source behavior. Record exact diff counts and review boundaries.
- [ ] T8: Independently verify each proposed tree and the final tree, including hermetic tests and local privacy/compatibility boundaries. Do not commit failed or oversized candidates.
- [ ] T9: Create verified local child branches and Conventional Commits, prove final source bytes unchanged, record commit identities and leave tracker unchanged. No push, PR or merge.

## Evidence and progress
Exploration found no general-web provider in current code; user selected Brave Search API. Existing ATS adapters require configured boards and do not prove employer ownership. Current selection ignores classification/exclusions; T1 addresses this first. T1 source implemented with observed RED/GREEN. Independent review approved; focused suite 199 passed and full suite 837 passed; diff check passed. No commit/push/deploy. Native assessment was unassessable and required independent verification; no native approval claimed.

Brave official documentation retrieved: https://api-dashboard.search.brave.com/documentation/services/web-search — fixed endpoint https://api.search.brave.com/res/v1/web/search; X-Subscription-Token auth; web.results contains title/url/description. Search snippets remain unverified leads, not company citations. No live authenticated requests performed.

T2 implemented as an isolated, default-off adapter; no launcher activation yet. Independent review approved after fixing credential-bearing result URL rejection. T2 tests: 136 passed; full suite: 973 passed; diff check passed. Mocked HTTP only, no real Brave credentials or authenticated requests. Search snippets are unverified leads; destination DNS/fetch safety is still T3. All source changes remain uncommitted and unpublished.

T3 implemented and independently approved after a real-parser buffering/accounting defect was found and fixed with observed RED (five failures) -> GREEN. Focused 88 tests and full suite 1061 tests passed, zero skips. One verifier command initially ran from /root and failed to collect tests; corrected worktree reruns passed. Connections pin validated public IPs while TLS checks original host. Budgets cap decrypted HTTP wire reads (including headers/framing/discarded bytes), not TLS handshake/TCP overhead. Compressed responses fail closed; no live fetches performed. Libc DNS cannot be cancelled; at most two outstanding daemon lookups remain bounded until completion.

T4 mapping confirms no trusted employer-domain field in current source adapters. Conservative resolver must keep candidates unverified without an explicit public employer-domain trust anchor; search rank, ATS slug and self-claimed Organization are insufficient. Careers-page links must prove exact tenant/listing delegation. Company claims are extractive and attributed, not inferred. Fetcher currently cannot distinguish 404/410 from generic HTTP failure; do not invent closed/not-found evidence from that result.

T4 implemented with independent approval: 99 focused and 1160 total tests passed; diff check passed. Original worker timed out, so initial TDD provenance is unknown; follow-up repairs have observed RED (12 failures) -> GREEN. Conflicting same-vacancy records are rejected before matching and citations preserve actual raw page reference fields. No live provider/page calls; unknown employer ownership remains unverified; fetch errors do not prove closure.

T5A independently approved: canonical launcher lazily creates one per-run search/fetch/resolver after scoring; disabled/simulation do not access credentials or network; public-only projection and notification cards. Observed RED/GREEN for timer placement and inline credential-URL rejection; unsafe citation claims are omitted rather than rewritten. T5B source review found no code blocker: schema v2 completion cards are opt-in, default/no-card v1 preserved, closed adapter validation and versioned retry identity covered. Focused T5B 232 tests and full suite 1346 passed. Stale standalone-only documentation was corrected; final checks still pending. No live consumer migration.

T6 independently approved for local readiness: final full suite 1346 passed, zero skips; shellcheck and diff check passed. Compileall passed using external temporary pycache. Offline wheel test ran with --no-index and isolated CWD; Node helper harness ran, not full n8n runtime. Obsolete standalone-only documentation was corrected. No full-source strict-TDD claim: initial T4 provenance remains unknown. Native risk assessment unavailable; independent checks performed, no native approval claimed.

## Delivery plan and limits
Actual authored change: approximately 3,992 lines (3,921 additions plus 71 deletions), including new source/tests/docs, excluding .codegraph, artifacts and this task file. This exceeds the original upper forecast by approximately 1,192 lines. Local commits are authorized, but must use the more granular chain below. Original major feature boundaries, with related tests and docs in each:
1. Recommendation eligibility and shortlist compatibility.
2. Bounded Brave public search.
3. Public HTTPS transport and safety tests.
4. Employer/listing evidence resolver and cited briefs.
5. Public cards and canonical notification/launcher integration.
6. n8n completion schema v2 with v1 compatibility.
Shared automation/test/doc changes require hunk-aware separation; no splitting or git mutation has been performed. These are coherent review units, not a promise each is below 400 lines.

Pending live prerequisites: separately authorized Brave key setup/quota, explicit public employer trust anchors, live public-provider/DNS/page validation, n8n v2 consumer migration and actual delivery/persistence verification. Tests do not establish live-provider or production readiness. Feature remains disabled by default and currently uncommitted/unpublished.

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

## Next step
Prepare proposed chain trees, verify independently, then create local commits only for approved candidates. No push, PR, merge, real API activation or deployment is authorized.
