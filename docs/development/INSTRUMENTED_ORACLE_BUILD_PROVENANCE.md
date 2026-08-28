# Instrumented Oracle build provenance gate

Status: **candidate provenance contract; no instrumented image or equivalence claim**

This slice makes every future instrumented Nakama image prove its exact upstream commit/tree, patch digest, changed files, old/new blobs, per-file diff digests, instrumentation capabilities, toolchain digests, hermetic build command, OCI image digest, SBOM and provenance attestation.

The initial policy permits only newly added files under `internal/trnm_oracle/` or `server/trnm_oracle_`. Modifying an existing Nakama file requires an explicit reviewed policy update; deletion is forbidden. Migration, data, Console ACL, IAP, social and selected authority-facing API files are forbidden by default.

Allowed capabilities are limited to clock/random capture, Provider intent capture, database-effect capture, Runtime hook capture and trace correlation. The manifest may never claim semantic behavior changes, instrumented equivalence, SG2 closure, compatibility, production readiness or public online.

Candidate manifests can be structurally valid but cannot become approved without at least two independent reviewers and per-file approval. Even an approved build proves provenance only; real immutable/instrumented 10× differential evidence remains mandatory.
