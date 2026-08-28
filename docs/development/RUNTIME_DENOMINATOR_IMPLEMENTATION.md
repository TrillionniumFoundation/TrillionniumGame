# Runtime framework machine denominator

Status: **candidate implementation; SG1 and runtime compatibility remain open**  
Plan position: W0 `TG-W0-002`, W11 Runtime framework, `DEN-RUNTIME` / D4.

## Exact inputs

The generator consumes only source directories that pass the pinned-source contract from the parent W0 PR:

- `heroiclabs/nakama` commit `d4d92f93f78bbbe62c7fc50a3f85c772ec121a09`, tree `f3c9cfc2726d5543da1564629170f35b98e3797d`;
- `heroiclabs/nakama-common` commit `449b77ecc8789aa466c36b67f6e498033dfcd9c5`, tree `c6a7b9796b9c2a6b5118c74e5f213963a5001f14`.

The complete tree is rehashed at consumer time. A stale marker or any post-fetch byte/mode mutation fails before extraction.

## Extracted candidate leaves

The Go AST helper extracts, without type-checking or executing upstream code:

- exported runtime constants and errors;
- public types, structs and fields;
- function type aliases;
- public interfaces, embedded interfaces and methods;
- exported package functions;
- all top-level declarations in non-test `server/runtime*.go` adapter files.

The TypeScript declaration parser extracts:

- namespaces/modules;
- interfaces/classes and members;
- type aliases;
- enums and enum values;
- declared functions, constants and variables.

Every non-test `server/runtime*.go` file is also a first-class adapter-file leaf. Every leaf carries exact repository, commit, path, Git blob, SHA-256, line range and signature hash.

## Honest compatibility boundary

The Go and TypeScript surfaces are not assumed to be one-to-one. A reconciliation artifact reports shared interface names and member counts, Go-only names and TypeScript-only names, but explicitly keeps semantic equivalence false.

All leaves start as:

```text
classification = unclassified
mandatory = null
status = planned
```

The candidate manifest keeps:

```text
status = candidate-unclassified
unclassified_count = leaf_count
unreviewed_count = leaf_count
sg1_complete = false
runtime_semantic_equivalence = false
go_plugin_abi_compatible = false
compatibility_credit = false
production_ready = false
nakama_retired = false
```

The compiled Go plugin ABI remains outside the supported final-product goal. Existing Go module sources still require explicit Rust/WASM migration and behavioral differential evidence.

## Automated checks

- Go AST and TypeScript parser synthetic fixtures;
- nested interface/member/type/enum coverage;
- exact source-lock verification and post-fetch tamper rejection;
- deterministic clean-directory output;
- stable leaf ID uniqueness;
- minimum nonempty surface counts on the exact upstream source;
- explicit SG1-negative execution;
- immutable-action exact-head artifact upload.

## Remaining work

- inspect every `manual_contract` and close parser blind spots;
- classify every leaf as mandatory, optional or versioned exclusion;
- bind final owner, implementation task, test corpus and evidence;
- define Go/Lua/JavaScript compatibility profiles separately;
- run Goja and GopherLua language corpus spikes;
- select and document candidate JavaScript/Lua engines by ADR;
- build Rust native and WASM capability contracts;
- migrate production Go module sources;
- obtain independent review and lock the final D4 digest.
