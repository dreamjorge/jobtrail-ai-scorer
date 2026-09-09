# Re-score Changed Jobs — Implementation Plan

Issue: #34

## Policy

- Build a SHA-256 fingerprint from normalized scoring inputs: title, company, location, description, salary bounds/currency, employment type, and remote status.
- Normalize strings with Unicode normalization, casefolding, trimming, and whitespace collapsing; canonical JSON uses sorted keys.
- Exclude source URL, source ID, metadata, profile, retrieval time, notes, CV, and credentials.
- Store only the digest and version in the score note; never persist raw job input in the fingerprint field.
- Legacy score notes without a fingerprint remain valid and are not automatically rescored.

## Slices

1. Fingerprint helper and note payload compatibility.
2. Scoring eligibility compares current fingerprint to latest fingerprinted note; explicit CLI `--force` remains unconditional.
3. Automation discovers changed existing jobs, reports changed/rescored separately, and preserves score history through append-only notes.

## Verification

- Existing 645 tests remain green after each slice.
- Unit tests cover whitespace/case/key ordering, meaningful changes, legacy notes, and note history.
- Integration tests cover unchanged jobs, changed jobs, and deterministic counters.
