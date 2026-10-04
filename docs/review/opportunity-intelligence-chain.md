# Opportunity intelligence review chain

## Publication scope and dependency

Approved enhancement: [issue #84](https://github.com/dreamjorge/jobtrail-ai-scorer/issues/84).
Publication is authorized; merge, deployment, live API use and activation are not.
PR bodies will carry `Closes #84`; this document links the issue without claiming closure.

The new tracker, `feat/opportunity-intelligence-review`, starts from the **open
[PR #83](https://github.com/dreamjorge/jobtrail-ai-scorer/pull/83) head**
`feat/job-feedback-balanced-matching` at
`a17c6320a964189b42c38c23176069c32c34b6df`, not from main.
Its PR targets that head branch and must remain **DRAFT / no-merge** until PR #83,
all child reviews and integration are ready. This bootstrap adds only this public
review guide and `odd/tasks/opportunity-intelligence-publication.md` as the common
base. It introduces no runtime behavior and does not establish feature readiness.

Reconstruct the implementation on that common base without rewriting or deleting
any of the old 13 child refs or commits, or the old tracker. Preserve the historical
source tip `650da21a8c3fbda31cf381602e3e2b61a468d03f` and prior source, test and
runtime-documentation bytes. The old `odd/tasks/opportunity-intelligence.md` is
historical evidence: its local-only/no-publication authorizations are superseded
for this publication by this guide and the publication task file, not retroactively
rewritten into new approvals.

## Review units

Each branch below has prefix `feat/opportunity-intelligence-review-`.
Child 01 bases on the new tracker; every later child bases on the immediately
preceding child, never main or the old chain. Tests and docs stay with their behavior.
Caps count authored additions **plus deletions**, including tests, docs and replacements.

| Child branch suffix | Unit / review focus | Dependency | Approved authored cap |
|---|---|---|---:|
| 01-shortlist-policy | Shared eligibility, exclusions and bounded shortlist policy | New tracker | 400 |
| 02-shortlist-orchestration | Production/simulation compatibility and legacy threshold/tie behavior | Child 01 | 400 |
| 03-public-search-contract | Public query/config/result contracts and field allowlists | Child 02 | 400 |
| 04-brave-adapter | Bounded Brave adapter, deterministic failures and secret hygiene | Child 03 | 400 |
| 05-public-http-guards | URL/IP/DNS guards and fail-closed public destination validation | Child 04 | 400 |
| 06-pinned-http-transport | Pinned TLS, byte/deadline accounting and direct transport tests | Child 05 | 400 |
| 07-public-fetcher | Fetch orchestration, redirects and inseparable safety regressions | Child 06 | 575 (exception) |
| 08-evidence-models | Closed public evidence and explicit employer trust models | Child 07 | 400 |
| 09-evidence-resolver | Ownership, vacancy identity, ambiguity and cited-brief regressions | Child 08 | 925 (exception) |
| 10-public-cards | Bounded public serializer/cards, privacy and compatibility | Child 09 | 400 |
| 11-automation-enrichment | Default-off lazy per-run enrichment and runner tests | Child 10 | 400 |
| 12-n8n-v2 | Completion v2 mapping/adapter with v1 compatibility | Child 11 | 400 |
| 13-canonical-launcher | Canonical launcher timing/delivery and historical ODD evidence | Child 12 | 400 |

Only 07 and 09 have size exceptions. Every PR links issue #84 and carries exactly
one `type:feature` label; only those two children use `size:exception`.
The final old-source slice 13 budget is **339 authored lines**, including historical
ODD evidence; the earlier 290 figure predates those evidence updates. This is a
historical source budget, not an observed new PR count. Exact reconstructed tree
counts must be checked before publication, and remote diffs and future CI must be
observed afterward. Neither new counts nor new CI results are asserted here.
Stop rather than silently increase a cap or change preserved implementation bytes.

## Behavior and evidence limits

Enrichment remains opt-in and **disabled by default**. JobTrail retains collection,
scoring, shortlist and journal authority; n8n is optional distribution only.
The bounded policy is at most three opportunities, three searches/run, five
results/query, ten candidates, twelve fetch attempts including redirects and two
redirects/request. Queries use only allowlisted public employer/title/location
fields. Keep original listing URLs; search snippets are leads, not verified facts.
Official listing verification requires employer ownership and corroborated vacancy
identity. Company briefs contain at most three cited, extractive factual claims;
page content is untrusted data, not instructions. No automatic applications.

Historical local verification recorded **1346 passing tests, zero skips**, plus
shellcheck, diff/syntax checks and offline packaging. These are prior observations,
not fresh bootstrap or publication results. The Node fixture used a helper stub,
not a real n8n runtime. Live Brave, DNS/page behavior, n8n v2 consumer migration and
actual delivery/persistence remain unvalidated; local tests do not prove production
readiness. Initial T4 writer RED provenance is unknown; subsequent repairs have
observed RED failures and GREEN passes. Do not claim universal strict-TDD compliance
or native approval; historical independent verification is not either claim.

## Planned verification, not executed by this bootstrap

Run fresh hermetic validation against the reconstructed public inventory at each
child, then the integrated inventory. Use synthetic fixtures and no live calls,
private inputs or credential access. The full-suite command is:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider
```

Additional planned checks (base/head identify each reconstructed child):

```sh
shellcheck scripts/hermes-docker-wrapper.example.sh scripts/legacy/hermes-score-jobs.sh scripts/legacy/run-scorer.example.sh scripts/notify-whatsapp-via-hermes.example.sh
git diff --check <base>..<head>
PYTHONPYCACHEPREFIX="$VALIDATION_OUTPUT/pycache" python -m compileall -q src scripts tests
python -m pip wheel --no-index --no-deps --no-build-isolation --wheel-dir "$VALIDATION_OUTPUT/wheels" .
```

Syntax/packaging outputs require a separately approved isolated validation directory;
packaging uses an isolated public checkout and already available build dependencies,
with no downloads. Compare final source/test/runtime-doc bytes with the preserved
source tip, verify all parent/base relationships and authored caps, and inspect
remote diff/CI results after publication. Fresh checks and PR-body generation belong
to the later publication steps; this documentation-only bootstrap runs no tests,
creates no PRs, and authorizes no merge, deployment or live activation.
