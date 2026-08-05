# Operations

## Immutable runtime inputs

- Nakama server: `3.40.0`, multi-architecture digest
  `sha256:92fb184e3271be12fd4d239766afb285322a50aaf769a59433445d59624c78cd`.
- Nakama plugin builder: `3.40.0`, multi-architecture digest
  `sha256:0455a119585914341672fc17f3c4195a7a21714ecb85cdf7dacbdc47769aed4c`.
- Go runtime ABI: `github.com/heroiclabs/nakama-common v1.47.0`, Go `1.26.5`.
- PostgreSQL: `17.6-alpine3.22`, multi-architecture digest
  `sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94`.

Never change only one Nakama version or remove an image digest. A plugin built
against a different `nakama-common` ABI may fail at load time.

## Configuration and secrets

`compose.yaml` requires every DB, Nakama, issuer, authority, and legacy operator
secret at model-render time. `deploy/dev.env.example` documents names only. Real values
must live in a non-versioned file or secret manager and must never be logged or
committed. The local Compose profile injects secrets as container environment
variables, so Docker administrators can inspect them; production deployment
must use restricted secret/config mounts or an equivalent secret manager.

The important runtime keys are:

- `TRNM_HEPTA_ISSUER_KEYS`: JSON map from trusted key id to Ed25519 public key;
- `TRNM_HEPTA_CONTROL_ISSUER_KEYS`: independent JSON map of Hepta Ed25519
  public keys authorized to sign short-lived Paper Raid lifecycle commands;
- `TRNM_HEPTA_BASE_URL` and `TRNM_HEPTA_SERVICE_TOKEN`: the HTTPS callback
  endpoint and service credential used for durable Paper Raid consumption and
  completion delivery;
- `TRNM_NAKAMA_AUTHORITY_KEY_ID` and
  `TRNM_NAKAMA_AUTHORITY_PRIVATE_KEY`: completion-signing identity;
- `TRNM_NAKAMA_OPERATOR_TOKEN`: at least 32 random bytes retained only for the
  fixed two-participant v1 create/resume/complete RPCs. Paper Raid v2 lifecycle
  RPCs do not accept it.

Local Nakama service keys also include independent server, session, runtime
HTTP, console-password, and console-signing secrets. The Compose profile sets a
non-default console username and never publishes the console port.

Cryptographic and credential roles are fail-closed: the Nakama operator token
must differ from the Hepta callback service token; every Hepta control issuer
public key must differ from every participant-authorization issuer and from the
Nakama completion authority; and the completion authority must also differ
from every participant-authorization issuer. Issuer and authority key ids
contain only `A-Za-z0-9._:-` and are limited to 128 bytes; whitespace, controls,
and non-ASCII lookalikes are rejected. The control-key decoder additionally
rejects duplicate key ids and duplicate public keys.

The local PostgreSQL and Nakama configuration secrets are restricted to
`A-Za-z0-9._~-`; this prevents YAML/DSN injection in the ephemeral mode-0600
config file. Generate at least 32 random characters from that alphabet.

Hepta issuer keys support an overlap set: add the new public key, switch the
issuer, then remove the old public key only after every affected authorization
and resumable match is retired.

New control issuer keys may be added as an overlap set, but P0 has no command
record archival/garbage-collection protocol. Do not remove a retiring control
key while any stored command signed by it remains: pending and applied command
records are both revalidated on every load. Control claims may live for at most
120 seconds and tolerate at most 30 seconds of clock skew. Exact retries of an
already accepted command remain valid after claim expiry; a different request
body for the same `command_id` is rejected.

P0 does **not** support an overlap set for the Nakama authority private key.
Every authenticated snapshot is bound to one authority key id and public key.
Do not rotate `TRNM_NAKAMA_AUTHORITY_*` while any active or resumable match
exists; first drain and complete those matches, archive their evidence, and
verify that no recovery record depends on the old key. A future key-ring format
must be versioned before online authority-key rotation is allowed.

Publish the authority public-key mapping through a separately versioned and
pinned deployment registry. The `authority_public_key_base64` returned with
evidence is useful for diagnostics, but consumers must never treat a key
bundled beside its own signature as trusted. They must resolve
`authority_key_id` externally, compare the resolved key byte-for-byte, and then
verify the completion signature.

## Local Compose

Render and start with a private environment file:

```bash
docker compose --env-file /secure/path/nakama.env config --quiet
docker compose --env-file /secure/path/nakama.env up -d --build --wait
docker compose --env-file /secure/path/nakama.env port nakama 7350
```

The environment file must set `TRNM_NAKAMA_SOURCE_REVISION` to the exact
40-character lowercase Git commit being built. The Dockerfile rejects missing,
abbreviated, or non-hex revisions and records the value in the OCI revision
label.

Regenerate and compare the deterministic CycloneDX dependency and base-image
inventory before a release:

```bash
scripts/generate-nakama-sbom.sh runtime/sbom.cdx.json
```

The release gate also proves that the runtime image itself is reproducible:

```bash
make release-check
```

Its image phase downloads a version- and SHA-256-pinned Buildx binary into a
disposable Docker configuration, builds the clean commit twice without cache,
and requires identical image IDs and plugin hashes. The source commit time is
used as `SOURCE_DATE_EPOCH`, including the compiled plugin file mtime. BuildKit
attestations are disabled because this release path carries its own checked
SBOM and provenance manifest; per-invocation attestations would otherwise make
identical runtime bytes appear different. No Docker plugin is installed
globally.

`up --wait` covers container liveness. A deployment is usable only after
`trnm_ready_v1` also reports `configuration`, `database`, and writable
server-owned `storage` as `ok`.

The default host port is dynamically allocated and bound to `127.0.0.1`. Only
the HTTP/realtime port is published. PostgreSQL, gRPC, and console ports are not
published. Set a unique `TRNM_NAKAMA_COMPOSE_PROJECT` for each concurrent run.

Nakama is configured with an eight-day (`691200` second) empty-runtime grace
period so a seven-day Paper Raid and a temporary Hepta callback outage are not
mistaken for abandonment. Both adapters still limit each ordinary runtime
generation to six hours (measured by authoritative ticks); reaching it stops
that in-memory instance without inventing completion evidence. A completed
Paper Raid with an undelivered signed-ACK outbox stays alive beyond that
generation boundary. Durable logical sessions remain operator-resumable, and a
completion evidence read can start a delivery-only recovery runtime after
SIGKILL. These are resource-lifecycle limits, not research deadlines or
authority results.

Both services use a read-only root filesystem, `no-new-privileges`, and dropped
capabilities. PostgreSQL is attached only to the internal backend and receives
the minimal capabilities needed by its official entrypoint. Nakama also joins
a separate edge network because Docker Engine 29 does not activate a published
host port for an internal-only container; that edge is used solely for the
explicit `127.0.0.1` client-port binding, while PostgreSQL remains backend-only.

Stop a local stack, including its disposable database volume, with:

```bash
docker compose --env-file /secure/path/nakama.env down -v --remove-orphans
```

Do not add `-v` for an environment whose database must be retained.

## Health and readiness

These signals are deliberately different:

- container health invokes `/nakama/nakama healthcheck`; it answers whether the
  Nakama process and listener are alive;
- RPC `trnm_health_v1` proves the Go plugin loaded;
- RPC `trnm_ready_v1` additionally checks required cryptographic configuration,
  database ping, and server-owned storage access.

A live process may be unready. Missing keys prevent the Compose model from
starting; invalid keys make the runtime readiness check fail. Database or
storage loss must produce `ready:false` and must never be treated as a healthy
release gate.

## Restart and recovery

External Nakama match ids are ephemeral. Recovery addresses the stable logical
`match_id`, authenticates its authority-signed snapshot, and creates a new match instance.
Consumed authorization ids, command fingerprints, participant/global sequence,
events, roots, completion bytes, and signature are durable and must be identical
after retry or restart.

Paper Raid uses the same rule with stable `session_id`, plus a complete
authorization-consumption outbox for every session epoch and one completion
outbox. Local state advances before network delivery. Retries retain the exact
JSON bytes, SHA-256, and idempotency key; only an exact, pinned-issuer Ed25519
ACK with `Content-Type: application/json` marks delivery. Redirects are never
followed while carrying the Hepta token. Persisted receipt bytes and checksums
are signature-verified again at every restore.

Paper Raid lifecycle mutations use the four signed-control v2 RPCs. Nakama
persists the canonical request bytes, SHA-256 fingerprint, accepted time,
operation, and exact response. Create stores the initial session and pending
command atomically; resume reserves its command before creating a runtime;
replace-roster and complete reserve their commands before signaling the match,
then commit the session mutation/outbox and applied response in one storage
batch. Exact replay checks the stored original acceptance time, so the original
command need not be re-signed merely because its claim has since expired.
Reusing a `command_id` with any byte difference fails closed.

Create/resume recovery can recreate its own runtime. A pending roster
replacement or completion cannot signal a runtime that died: issue a distinct,
currently valid signed resume first, and reconnect the current roster before a
completion retry. The already accepted replace/complete request itself remains
byte-identical and may be replayed after its claim expires.

P0 permits one active instance per logical match. Optimistic storage conflicts
mean a second writer exists; alert and fail closed rather than retrying both.

The runtime caps each logical match at 512 distinct commands and 2 MiB of
cumulative decoded command payload. Before accepting a command it encodes the
candidate snapshot and requires an additional 1 MiB of headroom for completion.
Capacity rejection is atomic; operators should alert on it rather than raising
the limits ad hoc, because consumers depend on the published P0 bounds.

The snapshot signature detects unauthorized mutation, while Nakama storage OCC
guards normal concurrent writers. P0 does not claim rollback protection against
an administrator who can replace the entire database with an older, correctly
signed backup; production backup/restore procedures must preserve and audit
monotonic storage history.
