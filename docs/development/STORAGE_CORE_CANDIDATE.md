# Rust storage ACL/OCC core candidate

This W4 slice adds a pure, dependency-free storage state machine on top of the Rust foundation PR.

Implemented invariants:

- collection/key length and control-character bounds;
- owner/public/private read ACL;
- owner/server write ACL;
- server-owned objects using the zero user identity;
- unconditional, must-not-exist and exact-version writes;
- exact-version delete;
- duplicate-key rejection within a batch;
- clone/apply/swap atomic batch semantics;
- no partial mutation on validation or OCC failure;
- same version cannot identify different value bytes;
- bounded value and batch sizes.

The value bytes and version digest are supplied by the caller. Canonical JSON validation and digest calculation belong to the protocol/adapter layer and are deliberately not duplicated here.

This is not a PostgreSQL/CockroachDB implementation, search index, cursor, migration, wire compatibility or production-readiness claim. Database transactions, catalog schema, retry loops, query grammar, index lag/rebuild and Oracle differentials remain separate work.
