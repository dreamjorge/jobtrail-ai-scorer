# JobTrail Automation (CI fixture mock)

This is a CI-only mock of the runtime Hermes SKILL.md for the
`jobtrail-automation` skill. The runtime file lives in the operator's local
Hermes profile (not in this repository). This fixture exists solely to
enforce the apply-gate contract via `tests/test_runtime_policy.py` and
`.github/workflows/policy.yml`. Do not copy runtime content into this file.

Keep the fixture minimal: only the sections needed by the policy tests.

## Apply / submit intent

When the user asks to apply, submit, or otherwise send an external
application, the agent must never submit automatically. The agent first
shows the exact target job, the application URL, the materials it plans to
use, and asks for explicit confirmation in the same conversation. Only
after the user provides explicit confirmation may the agent proceed; the
agent otherwise never submits without an explicit confirmation.
