# Exact candidate manifest contract

Status: binding source contract for plan v3 evidence generation.

## Purpose

Every build, database run, oracle differential, security review and relay job needs one unambiguous target identity. `scripts/generate-candidate-manifest.py` produces that identity from the checked-out Git object rather than from manually copied PR text.

## Required identity

The generated manifest contains:

- canonical repository;
- exact commit and tree;
- checked-out branch/ref;
- GitHub run identity when available;
- generator environment;
- every ordered PostgreSQL and CockroachDB migration file and aggregate chain digest;
- plan, boundary, status, gap, gate, schema-authority, evidence-index and merge-gate digests;
- source digests for the server, storage-version, JWT, persistence core and PG adapter candidates.

When `GITHUB_SHA` or `TRNM_EXPECTED_HEAD` is present, a mismatch is fatal.

## Evidence use

The candidate manifest is an identity envelope, not proof that tests passed. Downstream evidence must reference its artifact digest and then add:

- exact commands and assertions;
- terminal result;
- environment and fixtures;
- divergences and limitations;
- independent review;
- expiry where required.

A relay result is rejected when its target commit/tree or migration-chain digest differs from this manifest.

## Claim boundary

Generating a manifest grants no SG, C-level, compatibility, production, public-online or replacement credit. Its initial claim fields deliberately remain:

```text
source_validation_executed = false
independently_reviewed = false
compatibility_credit = false
production_ready = false
public_online = false
```
