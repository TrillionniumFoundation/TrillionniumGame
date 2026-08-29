# Oracle instrumentation hook inventory

Status: **candidate inventory only; no Nakama patch or instrumented image is authorized**

This SG2 slice scans the exact pinned Nakama Git tree and emits stable candidates for six allowed instrumentation capabilities:

- clock capture;
- random/UUID capture;
- provider HTTP intent capture;
- database logical-effect capture;
- runtime hook/event capture;
- trace correlation.

Every site binds path, line, column, call, import path where known, Git blob and source SHA-256. Stable IDs include line and column, preventing same-line duplicate calls from collapsing. Go import inference handles semantic-version path suffixes such as `/v5` by selecting the actual package segment before the version.

Restricted sources are represented only as source-identity manual contracts and are never semantically scanned. The output is `candidate-unreviewed`, keeps every patch/image/equivalence claim false, and cannot update the instrumentation allowlist by itself.

The next step is independent review against PR #20's build-provenance policy: each selected site must justify why it cannot affect authority, ACL, error, ordering, money, durable effects or signing semantics. Only a separately reviewed patch may consume approved sites.
