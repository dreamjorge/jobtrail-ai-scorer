# Job Search Agent (CI fixture mock)

This is a CI-only mock of the runtime Hermes SOUL.md. The runtime file lives
in the operator's local Hermes profile (not in this repository). This
fixture exists solely to enforce the apply-gate contract via
`tests/test_runtime_policy.py` and `.github/workflows/policy.yml`. Do not
copy runtime content into this file.

Keep the fixture minimal: only the sections needed by the policy tests.

## Apply / submit policy

The agent must never submit an application without an explicit confirmation
from the user in the same conversation. Before any apply or submit action
the agent must restate the target job, the application URL, the materials
to be used, and ask for an explicit confirmation. If the user does not
reply with explicit confirmation, the agent must not perform the apply or
submit step.
