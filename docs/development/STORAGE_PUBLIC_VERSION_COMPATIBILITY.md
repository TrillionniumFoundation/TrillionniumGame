# Storage public version compatibility

Status: source candidate; no storage behavioral or migration compatibility credit.

## Required semantic separation

The storage implementation must use three distinct concepts:

```text
PublicContentVersion
  Exact public Nakama-compatible 32-character lowercase MD5 hexadecimal value
  derived from the exact stored value bytes.

ExpectedVersion
  The client/server OCC condition: empty, `*`, or one exact public version,
  interpreted according to the pinned operation and caller authority.

InternalIntegrityDigest
  A versioned modern digest used internally for corruption detection, evidence,
  backup comparison or content addressing. It is never substituted on the wire
  for PublicContentVersion.
```

A caller-supplied arbitrary 32-byte digest cannot be treated as the public storage version.

## Current source candidate

```text
crates/trnm-storage-core/src/bin/trnm-storage-version.rs
```

The candidate:

- reads exact raw value bytes;
- enforces a bounded one-MiB source-candidate input;
- computes MD5 according to the public compatibility rule;
- emits exactly 32 lowercase hexadecimal characters;
- includes the RFC 1321 known-answer vectors and boundary lengths;
- explicitly states that MD5 is used only as the compatibility version, not as a security primitive.

## Required integration work

The candidate does not yet change the existing storage core operation model. Before the storage divergence can close:

1. persist the exact public version separately from any internal digest;
2. derive the public version server-side from accepted value bytes;
3. reject a mismatched caller-supplied result rather than trusting it;
4. implement empty/`*`/exact OCC semantics for create, overwrite and delete;
5. preserve default read/write permissions and server-owned objects;
6. prove atomic multi-write/delete behavior;
7. reproduce exact version/error/cursor behavior against immutable Nakama;
8. migrate existing rows without changing visible versions;
9. rebuild search/index state without treating internal digest as public version;
10. independently review the compatibility adapter and migration.

## Security boundary

MD5 collision resistance is not relied on for authentication, authorization, signatures, token security, durable receipt identity or corruption detection. Those uses require a reviewed modern digest or MAC. A public storage version collision must be handled according to the exact pinned compatibility profile and documented residual risk; it must not silently authorize a write outside the approved OCC contract.

## Gap and claim boundary

This candidate advances `DIV-STORAGE-VERSION-001` and the related storage gap only to source-candidate status. `TG-PAR-021`, `TG-PAR-023`, C2, C3, SG5, storage compatibility, production readiness and public-online status remain false until integration and exact differential evidence are accepted.