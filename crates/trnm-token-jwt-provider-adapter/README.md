# trnm-token-jwt-provider-adapter

Status: **module documentation; source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-token-jwt-provider-adapter`  
Workspace class: `isolated`  
Lifecycle: `security-critical-provider-adapter`  
Owner role: `security`

## Status and authority

This is the current engineering contract for the provider-backed HS256 authentication boundary. The source-candidate is not a KMS, authorization decision, complete JWT claims verifier, production activation or accepted security review. It follows Plan v3.1 and remains dependent on the frozen integration candidate and resolver-hardening candidate.

Source, tests, documentation and CI are separate from independent acceptance. Every promotion binds exact repository, head, tree, base and prospective-merge identities.

## Responsibilities

Authenticate the exact encoded `header.payload` bytes using an opaque provider handle, fence resolver domain/epoch output, and expose authenticated payload bytes for subsequent claims-policy validation. Reject malformed headers and routing before provider use. Decode payload only after signature acceptance.

Non-goals: token issuance, issuer/audience/time validation, authorization, session revocation, socket disconnect, key rotation orchestration, KMS/HSM implementation and logging extracted claims. These remain caller or integration obligations; successful `authenticate` is not permission to perform a protected action.

## Architecture and dependencies

`trnm-token-jwt-adapter` supplies bounded base64url and JSON format handling. `trnm-token-crypto-provider` supplies `KeyDomain`, `KeyReference`, `Hs256Provider` and `verify_exact`. There is no database, transport, global task or cache owned by this crate.

`KeyResolver` output is untrusted routing data. A provider is a trusted injected verification boundary, not an independently validated production implementation. The adapter performs no provider retries. It cannot enforce a hard deadline on a synchronous resolver/provider that never returns; the process integration must supply a bounded implementation and separate cancellation/timeout evidence.

## Public contracts

`authenticate(token, profile, resolver, provider)` returns `Result<AuthenticatedJwt, AuthenticationError>`. It does not return an authenticated result on any failure.

| Profile setting | Default | Validation or effect |
| --- | --- | --- |
| `domain` | `AccessToken` | Returned key domain must match exactly. |
| `max_token_bytes` | 32768 | Nonzero; raw token byte length is checked before parsing. |
| `max_header_bytes` | 1024 | Nonzero and less than token limit; passed to bounded decoding. |
| `max_payload_bytes` | 16384 | Nonzero and less than token limit; decoded only after provider acceptance. |
| `allow_legacy_without_key_id` | `true` | Missing `kid` requires a legacy key with `epoch=None`. |
| `reject_unknown_header_fields` | `true` | Only `alg`, `typ`, `kid` are accepted in this profile. |
| `json_limits` | `JsonLimits::default()` | Applied to header parsing; caller separately supplies claims parsing limits. |

The accepted algorithm is exactly `HS256`; an included `typ` must be `JWT`. Epoch IDs have prefix `trnm-kep-v1:` followed by a canonical positive `u32`, without leading zeroes. Unknown/malformed IDs never fall back. The decoded signature must have exactly 32 bytes.

`AuthenticatedJwt` has private route, key and payload fields, no public constructor and no mutable accessor. `route()` returns the copied route; `key()` returns a borrowed reference; `payload_bytes()` returns a sensitive immutable slice; `parse_claims(limits)` returns sensitive parsed JSON, not validated authorization claims. Clone preserves the same data and redacted diagnostic behavior.

Source migration: replace `.route` with `.route()`, `.key` with `.key()`, and payload reads with `.payload_bytes()`. External struct literals and field writes intentionally stop compiling. Call `authenticate` rather than fabricating proof objects. This is a Rust API hardening change, not a wire-format, SQL, DDL or receipt-identity change. Exact downstream workspace compilation remains required before admission.

## Correctness and failure model

The required sequence is profile validation, token size/segment validation, bounded header decode/JSON validation, header policy, route parsing, resolver, resolved domain/epoch validation, signature decode/length, provider verification over exact encoded input, then bounded payload decode. Payload JSON parsing is an explicit later operation.

Domain and epoch mismatch must produce zero provider calls, including epoch-for-legacy confusion. Provider rejection precedes payload decoding even for malformed payload bytes. Cancellation, duplicate delivery, transaction commit and socket lifecycle are not implemented by this stateless adapter.

`AuthenticationError` retains bounded structured variants so trusted internal policy can classify failures. Its public `Display` surface deliberately collapses `UnknownKey`, domain mismatch and epoch mismatch to the identical text `JWT key route is unavailable`; expected and actual domains, epochs and legacy/epoch routing are never formatted there. Derived `Debug` still contains bounded mismatch metadata and is therefore internal-only: transports, client responses, ordinary application logs and metric labels must use a reviewed public mapping or the generic `Display` string, not `Debug`. Provider outage handling and retry semantics belong to bounded caller policy.

## Security and privacy

Every `AuthenticatedJwt` debug format emits only `AuthenticatedJwt { [REDACTED] }`. This holds for normal, pretty and hex debug, nested `Result`/`Option`/collections, derived diagnostic wrappers and `format_args!`. The output does not vary with payload content or length, route, epoch or provider location. No raw-token logging or new logging dependency is introduced.

The protection ends at explicit extraction: `payload_bytes()`, `parse_claims()` and a borrowed key remain sensitive values. Logging them explicitly is not made safe by this wrapper. Likewise, `AuthenticationError` debug output may contain bounded key-routing metadata even though its public display is non-oracular. This change is not memory zeroization, end-to-end telemetry validation, provider logging validation or proof that every application error chain is redacted. Such claims require separate integration tests and review.

Private fields prevent safe external Rust code from constructing or mutating an authenticated result. They do not remove the requirement to trust the provider implementation or to validate claims and session state afterwards.

## Build and test

```bash
cargo fmt --manifest-path crates/trnm-token-jwt-provider-adapter/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-token-jwt-provider-adapter/Cargo.toml --all-targets --locked
cargo test --manifest-path crates/trnm-token-jwt-provider-adapter/Cargo.toml --doc --locked
cargo clippy --manifest-path crates/trnm-token-jwt-provider-adapter/Cargo.toml --all-targets --locked -- -D warnings
```

`--all-targets` is not the doctest invocation. The workflow runs the four compile-fail examples explicitly and rejects zero-test output. The examples independently prohibit external construction, payload replacement, key replacement and route replacement.

| Invariant | Regression in `src/authenticated_tests.rs` |
| --- | --- |
| Normal/pretty/hex redaction | `debug_redacts_compact_pretty_and_hex_formats` |
| No payload/length/route dependence | `debug_is_independent_of_payload_bytes_length_and_route` |
| Nested diagnostics | `debug_redacts_nested_result_option_and_collection` |
| Derived wrappers and formatting arguments | `debug_redacts_derived_diagnostic_wrapper_and_format_args` |
| Immutable reads and claims parse preserved | `read_only_accessors_preserve_exact_authenticated_data` |
| Key-routing `Display` is generic and non-oracular | `key_routing_display_is_generic_and_non_oracular` |
| Payload parse error remains redacted | `parse_failure_has_no_payload_in_error_formatting` |

The eight existing tests in `src/lib.rs` retain exact signing input, provider-before-payload ordering, algorithm/header rejection, strict epoch routing, resolver-domain mismatch, resolver-epoch mismatch and signature-length assertions. Tests use synthetic payloads and a fake provider; they are not cryptographic provider acceptance or live KMS evidence.

## Operations

This crate creates no queue, worker, background retry or socket. Memory allocation is bounded by the selected token/header/payload and JSON limits, except that injected implementations require their own contracts. Readiness, provider latency budgets, outages and backpressure are process-level obligations.

Metrics must use bounded categories; do not label by token, user, payload, key handle or unbounded epoch. Rollback may revert source in an unactivated candidate, but reintroducing payload-bearing `Debug`, key-routing detail in public `Display`, or public mutation is not an approved production mitigation.

## Compatibility and evidence

Retain separate exact-head and actual prospective-merge native packets, unit/doctest counts, toolchain, workflow/job/attempt identities and artifact digests. Missing, skipped, cancelled, stale or zero-test runs receive no credit. Local inspection is not remote execution; successful native tests are not independent security acceptance.

Server integration, real KMS/HSM, malformed/fuzz corpus, rotation/revoke, official SDK differential, session policy and independent cryptography review remain open. No product gate, acceptance counter or compatibility percentage is increased by this patch.

## Known gaps and exit criteria

Blocking gaps: `GAP-P0-CRYPTO-001`, `GAP-P1-CRYPTO-002`, `GAP-P1-REVIEW-001`, `GAP-P0-CI-001`, `GAP-P1-DOCS-001`.

Exit for this narrow patch requires exact native formatting/tests/doctests/strict lint, downstream compilation, prospective-merge verification, reviewed API migration and conflict-free independent security acceptance. Full gap closure additionally requires every relevant criterion in `docs/status/GAP_REGISTER.json`; the narrow redaction, public-error and immutable-result tests cannot close those broader gaps by themselves.
