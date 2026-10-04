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
- [ ] P2 (in progress): Add meaningful tracker documentation and reconstruct 13 child trees/commits on the new common base; preserve original refs.
- [ ] P3: Independently verify branch bases, source parity, authored budgets, local tests and public PR descriptions.
- [ ] P4: Push only the new tracker and children; open draft tracker and correctly based child PRs with issue linkage and labels.
- [ ] P5: Observe each PR's remote diff and CI, record links and pending checks, and stop without merge or activation.

## Recovery
The implementation history is recorded in `odd/tasks/opportunity-intelligence.md`. Publication evidence is recorded here as each task closes. CI must be observed, not inferred from local tests.
