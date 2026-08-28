# Third-party source and clean-room policy

Status: binding planning policy  
Effective date: 2026-08-28

## Purpose

TrillionniumGame targets observable behavioral and protocol compatibility with
the pinned Nakama OSS baseline while maintaining explicit source provenance and
license compliance.

## Rules

1. Every upstream repository, tag, commit, tree and copied blob is recorded in a
   machine-readable manifest before derived implementation is accepted.
2. Apache-2.0 source may be studied, copied or modified only with required
   notices, retained attribution and modification markers.
3. Generated protocol artifacts must preserve the license headers and exact
   source identities of their input schemas.
4. No Heroic Labs or Nakama trademark may be used to imply endorsement,
   affiliation or an official distribution.
5. Public behavior may be captured through black-box differential fixtures.
   Secrets, production user data and provider credentials are forbidden in the
   fixture corpus.
6. Enterprise or hosted-service behavior not present in public OSS source or
   public protocol documentation is outside the 1.0 denominator.
7. A contributor must identify copied, translated, mechanically transformed and
   independently authored material in the pull request description.
8. License scanning, NOTICE validation and source-manifest validation are
   release-blocking checks.

## Implementation classification

Each implementation unit must use one of these provenance labels:

- `independent`: designed from public behavior and protocol contracts;
- `translated`: structurally derived from an Apache-2.0 upstream unit;
- `generated`: produced from a pinned schema or code generator;
- `adapter`: integration code against a third-party provider;
- `original`: Trillionnium-specific functionality not intended as Nakama parity.

The label is metadata for review and does not replace applicable copyright or
license obligations.
