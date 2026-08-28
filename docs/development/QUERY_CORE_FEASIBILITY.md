# Rust query grammar feasibility candidate

Status: **SG3/W4/W8 implementation candidate; no compatibility credit**

## Exact upstream behavior source

Nakama `v3.40.0` calls `ParseQueryString` from `server/match_common.go`. The exact wrapper maps only the query `*` to match-all, otherwise delegates to `github.com/blugelabs/query_string v0.3.0` with a keyword analyzer. The dependency and all grammar, lexer, parser and test blobs are fixed in `contracts/query/upstream-query-lock.json`.

The public upstream grammar is flat rather than a general Boolean expression language. Each whitespace-separated search part has:

- optional `+` must or `-` must-not prefix;
- term, phrase, exact number, fuzzy term, numeric range or RFC3339 date range;
- optional boost suffix;
- optional field prefix where the grammar allows it.

Terms containing `*` or `?` become wildcard expressions. Strings delimited by `/.../` become regexp expressions. An unfielded number represents the upstream combination of a text match and numeric equality.

## Rust implementation

`trnm-query-core` is dependency-free and contains:

- an owned-token lexer with the upstream reserved-character escaping model;
- a typed AST for occurrence, terms, phrases, exact numbers, fuzzy terms and ranges;
- the Nakama empty and exact-star special cases;
- bounded input, token, token-count and clause-count limits;
- RFC3339 date validation without wall-clock access;
- no search index, scoring, database, network or runtime dependency.

## Deliberate hardening profile

The Rust candidate introduces explicit complexity limits:

```text
query bytes  = 4096
token bytes  = 1024
tokens       = 256
clauses      = 64
```

The upstream parser does not expose equivalent limits in its grammar package. These limits are therefore marked `unreviewed-extension`; they require product capacity analysis and immutable Nakama differential evidence before they can become a compatibility profile.

## Evidence

- 15 embedded Rust unit tests;
- 16 accepted and 9 rejected machine-readable vectors;
- independent Python reference parser;
- exact upstream source identity checker;
- no unsafe, network, database, async runtime or search-engine dependency;
- exact-head Rust format/test/Clippy workflow.

The current execution environment has no Rust toolchain. Local evidence is limited to static contracts, JSON/TOML/YAML validation and the independent Python vector suite. Exact-head Cargo evidence is mandatory.

## Remaining work

- run the complete upstream lexer/parser test corpus through a Go observation harness;
- compare Rust acceptance, rejection class and AST category against immutable Nakama;
- resolve float, escaping, Unicode whitespace, regexp and RFC3339 edge cases;
- define one shared query AST for matchmaker, storage search and group search profiles;
- implement index planning and backend execution separately;
- prove complexity limits against real query and abuse workloads;
- fuzz both lexer and parser;
- independently approve an engine/index ADR.

This slice does not close SG3 and does not prove matchmaker query, storage search, search execution or production compatibility.
