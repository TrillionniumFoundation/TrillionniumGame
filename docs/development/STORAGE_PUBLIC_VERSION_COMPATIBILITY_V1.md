# Storage public-version compatibility v1

Status: **source candidate; not yet integrated into the storage engine**.

## Problem closed at the design/source boundary

The original Rust storage core accepted a caller-supplied 32-byte `version` value. That conflated three different concepts:

1. the public Nakama storage object version returned over the API;
2. the client optimistic-concurrency precondition;
3. an internal integrity digest suitable for stronger native invariants.

The pinned Nakama behavior instead derives the public version from the exact stored value bytes and exposes it as lowercase MD5 hexadecimal. MD5 is retained only as a compatibility identifier. It is not an authentication, signature, secret-derivation or security-integrity primitive.

## Types

`crates/trnm-storage-nakama-version` introduces:

```text
PublicStorageVersion   16 bytes, exposed as 32 lowercase hex characters
ContentIntegrityDigest 32 bytes, internal and never wire-substituted
WriteCondition          Blind | CreateOnly | Exact(PublicStorageVersion)
```

## Client condition mapping

```text
version == ""    -> Blind
version == "*"   -> CreateOnly
version == 32 lowercase hex -> Exact
anything else    -> reject
```

No normalizer may rewrite uppercase, malformed or mismatched versions into a passing exact condition.

## Required integration

The source adapter does not close storage compatibility. The next change must:

- remove caller control over the committed public version;
- compute `PublicStorageVersion::from_value(exact_value_bytes)` in the authoritative write path;
- carry `WriteCondition` separately from the resulting version;
- retain a separate internal integrity digest where required;
- implement blind/create-only/exact checks atomically in both database profiles;
- compare API response, row effects, errors and batch rollback against the immutable Nakama oracle;
- verify server-owned objects, ACL defaults, delete conditions, cursor and search interactions;
- obtain independent storage/database review.

## Source gate

```bash
cargo fmt --manifest-path crates/trnm-storage-nakama-version/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-storage-nakama-version/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-storage-nakama-version/Cargo.toml --all-targets --locked -- -D warnings
```

The valid gap advancement is limited to:

```text
GAP-P1-STORAGE-001: open -> source-candidate
```

C2, C3, storage compatibility, database durability and production readiness remain false.
