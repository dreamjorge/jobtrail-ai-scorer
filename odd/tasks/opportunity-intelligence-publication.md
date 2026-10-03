# Opportunity intelligence publication

## Authority
The user authorized a feature-specific approved issue, a documentation-only new tracker, reconstruction on that common base without changing existing branches, and publishing branches/chained PRs. Merge, deployment and live APIs remain unauthorized.

## Baseline and boundaries
- Repository: `dreamjorge/jobtrail-ai-scorer`; dependency: open PR #83 at `a17c6320a964189b42c38c23176069c32c34b6df`.
- Preserve all existing `feat/opportunity-intelligence*` refs, including source tip `650da21a8c3fbda31cf381602e3e2b61a468d03f`.
- New tracker: `feat/opportunity-intelligence-review`; children: `feat/opportunity-intelligence-review-NN-<unit>`.
- Tracker targets the PR #83 head branch and remains draft/no-merge. Child 01 targets tracker; later children target the immediate predecessor.
- Every PR links the new approved issue and has exactly one `type:feature` label. Only slices 07 and 09 use the approved `size:exception` label.
- Authored caps: 400 per child, except 07 at most 575 and 09 at most 925. Include tests, docs and replacements.
- New common-base documents: this task file and `docs/review/opportunity-intelligence-chain.md`. No runtime code changes.
- Preserve prior source/test/runtime-doc bytes; use hermetic checks only. Existing `.codegraph/` is unrelated and must remain untouched.

## Tasks
- [x] P1: Created issue [#84](https://github.com/dreamjorge/jobtrail-ai-scorer/issues/84), with `enhancement` and `status:approved`; duplicate/policy/privacy checks passed.
- [x] P2: Created tracker bootstrap `82d45522ec5b8f2ccfc09b77c05168a7a229417a` and 13 new child commits, ending at `6497c16789e2428f914596127babd5322a2d8c9f`; original refs are intact.
- [x] P3: Actual commit trees, immediate parents, 17 preserved source files, approved budgets and public PR bodies independently verified; fresh final suite: 1346 passed, zero skips.
- [ ] P4 (in progress): Push only the new tracker and children; open draft tracker and correctly based child PRs with issue linkage and labels.
- [ ] P5: Observe each PR's remote diff and CI, record links and pending checks, and stop without merge or activation.

## Evidence and recovery
Issue #84 is approved. The documentation-only tracker has 128 authored lines. New adjacent child diffs are 241, 229, 228, 362, 212, 324, 575, 248, 875, 351, 176, 235 and 339 lines before this publication journal update. All 14 proposed public trees passed independent full suites; the final tree passed 1346 tests without skips. Source trees differ from their preserved predecessors only by the two common-base documents. Fresh shellcheck and syntax checks passed. Remote CI has not yet been observed.

The implementation history is recorded in `odd/tasks/opportunity-intelligence.md`; its earlier local-only authorization is historical and superseded by this publication task. Existing branches and their commits remain preserved.
