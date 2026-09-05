# Security policy

## Reporting a vulnerability

Do not open a public issue for an undisclosed vulnerability, credential exposure or exploit chain. Use the repository's GitHub private vulnerability reporting/security advisory channel when enabled. When that channel is unavailable, contact the Trillionnium Foundation security owners through the organization's established private security contact before publishing details.

Include, where safe:

- affected commit, component and configuration/profile;
- impact and prerequisites;
- reproduction steps or a minimal proof;
- whether tokens, keys, personal data, durable writes, authority or value effects are involved;
- suggested containment;
- whether the issue is already public or actively exploited.

Do not include live secrets, user data or production tokens. Use deterministic fixtures and redacted evidence.

## Response priorities

Security reports are triaged with the project risk model:

- **P0 / critical**: signing or authentication bypass, remote code execution, sandbox escape, unauthorized durable/value effect, cross-project/ACL bypass, secret exposure, stale authority acceptance, acknowledged-write loss or reproducible supply-chain compromise;
- **P1 / high**: meaningful denial of service, token replay/revocation failure, provider callback weakness, privilege escalation requiring additional conditions, sensitive-data exposure or serious cryptographic misuse;
- **P2**: defense-in-depth and lower-impact issues without the above consequences.

A security issue is not considered fixed merely because source changed. Required closure includes exact-head tests, artifacts, impact analysis, key/session/data disposition where applicable and independent review.

## Supported versions

The project has not released a production-supported version. `main`, branches, pull requests, crates, workflows and the current Go migration input are development candidates. No C1–C5 compatibility, production-readiness, public-online or replacement support commitment exists unless a future signed release explicitly states one.

## Current engineering controls

The active security and evidence contracts are:

- [`docs/SECURITY_AND_PRIVACY.md`](docs/SECURITY_AND_PRIVACY.md)
- [`docs/TESTING_AND_EVIDENCE.md`](docs/TESTING_AND_EVIDENCE.md)
- [`docs/GOVERNANCE.md`](docs/GOVERNANCE.md)
- [`docs/evidence/index.json`](docs/evidence/index.json)
- [`docs/status/GAP_REGISTER.json`](docs/status/GAP_REGISTER.json)
- [`docs/status/PRODUCT_GATES.json`](docs/status/PRODUCT_GATES.json)

`docs/DOCUMENTATION_AUTHORITY.json` defines the current documentation set. Historical/versioned security notes are not active policy.

Security-critical paths require independent review and aggregate-gate coverage. Empty, skipped, cancelled, zero-job, startup-failure or older-head checks are not security evidence.

## Disclosure and remediation

The response owner coordinates containment, affected-key/session/data identification, fixes, regression tests, release/advisory preparation and notification. Public disclosure timing should allow users to mitigate while avoiding unnecessary delay. If a report reveals a credential, production access or personal data, the credential/data incident process takes precedence over ordinary code review.

## Safe-harbor intent

Good-faith research that avoids privacy violations, service disruption, persistence, social engineering, physical attacks and access beyond what is necessary to demonstrate the issue will be treated as intended security research. This statement does not authorize testing against third-party services, provider sandboxes or infrastructure that the Foundation does not own or explicitly permit.
