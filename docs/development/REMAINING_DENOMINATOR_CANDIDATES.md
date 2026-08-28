# Remaining SG1 denominator candidate implementation

Status: **implemented-local candidate; exact-head remote execution required**  
Plan position: W0 `TG-W0-002`, covering `DEN-CONSOLE`, `DEN-PROVIDERS`, `DEN-IAP`, `DEN-METRICS`, and `DEN-OPS`.

## Exact input boundary

The generator consumes only a complete Nakama source tree whose repository, commit and root tree have already passed the recomputed Git-tree source-lock contract from the pinned-source acquisition slice:

```text
heroiclabs/nakama
d4d92f93f78bbbe62c7fc50a3f85c772ec121a09
f3c9cfc2726d5543da1564629170f35b98e3797d
```

Each output leaf binds the exact source path, Git blob, SHA-256 and line range where available. A post-fetch byte or executable-mode mutation causes source-lock verification to fail.

## Console denominator

Publicly licensed inputs are parsed for:

- Console gRPC services and methods;
- Proto messages, fields, enums and enum values;
- Console Swagger operations, schemas and properties;
- Client API Explorer Swagger operations;
- Console UI asset inventory.

The file `console/acl/acl.go` contains an explicit proprietary/confidential notice. The candidate therefore records only its source identity and a `restricted_console_acl_source` manual contract. It deliberately does **not** reproduce ACL route/resource mappings, permission bit positions or implementation logic. Supported ACL behavior must be derived from public contracts, black-box tests and legal review.

The minified Console UI is also inventoried rather than semantically copied. UI workflows, RBAC, accessibility and bundled non-Nakama product surfaces remain black-box/manual contracts.

## Provider and IAP denominators

The provider inventory scans public provider/auth/link/unlink sources for:

- top-level functions, types, constants and variables;
- provider identifiers;
- external endpoint candidates;
- HTTP method candidates.

The IAP inventory separately scans purchase/subscription and IAP adapter sources for:

- functions, types, constants and variables;
- receipt, purchase, subscription, refund, renewal and transaction state candidates;
- external endpoint and HTTP method candidates;
- SQL statement candidates.

Source inventory does not prove Provider token validation, callback authenticity, retry behavior, receipt authenticity, duplicate-value prevention, refund/void/renewal reconciliation or key rotation. Those remain explicit black-box matrix blockers.

## Metrics and operations denominators

The metrics candidate inventories metric-related functions, metric-name/help strings and health/status route candidates. The operations candidate inventories packaging/build files, Dockerfile instructions, Make targets, Compose services and port mappings.

These candidates do not prove runtime emission, label cardinality, readiness semantics, graceful shutdown, upgrade compatibility, backup/PITR or failure behavior.

## Fail-closed state

Every candidate leaf starts as:

```text
classification = unclassified
mandatory = null
status = planned
```

Every manifest retains:

```text
status = candidate-unclassified
unclassified_count = leaf_count
sg1_eligible = false
compatibility_credit = false
production_ready = false
```

`--require-sg1` fails until independent classification, manual-contract resolution, reviewed owner/task/test/evidence assignment and final lock digests exist.

## Remaining denominator gap

`DEN-SDK` remains a separate candidate and review slice because its source matrix spans multiple repositories, release lines, transports and engine/platform support windows. This change does not silently treat SDK coverage as complete.
