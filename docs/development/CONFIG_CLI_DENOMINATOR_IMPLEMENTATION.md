# Configuration and CLI machine denominators

Status: **candidate implementation; SG1 remains open**  
Plan position: W0 `TG-W0-002`, W1 foundation, `DEN-CONFIG` and `DEN-CLI` / D5.

## Exact source contract

The extractor consumes a complete `heroiclabs/nakama` source tree at commit `d4d92f93f78bbbe62c7fc50a3f85c772ec121a09`, root tree `f3c9cfc2726d5543da1564629170f35b98e3797d`. The parent source-fetch contract recomputes the Git tree at download and again at consumer time.

Source inputs include:

- `server/config.go`;
- `flags/flags.go`;
- `flags/vars.go`;
- `main.go`;
- `migrate/migrate.go`.

## Candidate extraction

The standard-library Go AST helper emits:

- config structs and interfaces;
- exported fields and embedded fields;
- exact YAML/JSON/usage/env/mapstructure tags;
- composite-literal and assignment default candidates;
- validation predicates tied to fatal paths;
- configuration precedence call order candidates;
- generated flag candidates derived from tagged config fields;
- CLI flag-set creation, parse events, command case strings and exit/fatal paths.

Line and column are incorporated where identical calls or assignments may occur more than once. This prevents accidental stable-ID collapse while keeping the exact pinned-source location visible.

## Reconciliation limits

A separate candidate report lists fields without an observed default candidate and records counts for defaults, validations and generated flags. It does not infer that every field must have a default, that every string switch case is a public command, or that source order alone proves complete runtime precedence and exit-code behavior.

Every leaf starts `unclassified`, `mandatory=null`, `planned`, and `unreviewed`. Both manifests retain false claims for SG1, behavior compatibility, migration compatibility, operational replacement and production readiness.

## Validation

- Go AST synthetic fixture;
- tag/default/validation/precedence/CLI extraction;
- duplicate same-line call handling;
- stable deterministic manifest bytes;
- exact source-lock post-fetch tamper rejection;
- SG1-negative promotion test;
- exact-head workflow artifact generation.

## Remaining work

- independently classify every candidate leaf;
- distinguish public commands from internal switch cases;
- resolve embedded config paths and flattened flag names;
- prove defaults after all config files, environment mapping and CLI overrides;
- capture exact validation error class and process exit behavior;
- build black-box config precedence and malformed-config corpus;
- bind official runbook, migration and rolling-upgrade behavior;
- review and lock final D5 digests.
