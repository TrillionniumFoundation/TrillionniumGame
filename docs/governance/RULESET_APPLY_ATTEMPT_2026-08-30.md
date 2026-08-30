# Main ruleset application attempt — 2026-08-30

This record is intentionally claim-free. The desired policy remains defined by `MAIN_RULESET_DESIRED.json` and is not considered active until GitHub API readback confirms an active ruleset or branch-protection configuration for `main`.

Required properties:

- direct and force pushes to `main` blocked;
- branch deletion blocked;
- linear history and conversation resolution required;
- exact aggregate check `trillionnium-game-merge-gate` required on the latest head;
- stale approvals dismissed;
- CODEOWNERS review and approval of the most recent push required;
- no implicit bypass actors;
- API response identity and digest recorded outside the candidate commit.

A desired-state document, a failed API request, or repository-local tests do not satisfy `GAP-P0-GOV-001`.
