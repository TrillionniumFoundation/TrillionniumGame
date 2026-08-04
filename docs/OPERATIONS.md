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

`compose.yaml` requires every DB, Nakama, issuer, authority, and operator secret
at model-render time. `deploy/dev.env.example` documents names only. Real values
must live in a non-versioned file or secret manager and must never be logged or
committed. The local Compose profile injects secrets as container environment
variables, so Docker administrators can inspect them; production deployment
must use restricted secret/config mounts or an equivalent secret manager.

The important runtime keys are:

- `TRNM_HEPTA_ISSUER_KEYS`: JSON map from trusted key id to Ed25519 public key;
- `TRNM_NAKAMA_AUTHORITY_KEY_ID` and
  `TRNM_NAKAMA_AUTHORITY_PRIVATE_KEY`: completion-signing identity;
- `TRNM_NAKAMA_OPERATOR_TOKEN`: at least 32 random bytes for privileged create,
  resume, and complete operations.

Local Nakama service keys also include independent server, session, runtime
HTTP, console-password, and console-signing secrets. The Compose profile sets a
non-default console username and never publishes the console port.

The local PostgreSQL and Nakama configuration secrets are restricted to
`A-Za-z0-9._~-`; this prevents YAML/DSN injection in the ephemeral mode-0600
config file. Generate at least 32 random characters from that alphabet.

Hepta issuer keys support an overlap set: add the new public key, switch the
issuer, then remove the old public key only after every affected authorization
and resumable match is retired.

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

`up --wait` covers container liveness. A deployment is usable only after
`trnm_ready_v1` also reports `configuration`, `database`, and writable
server-owned `storage` as `ok`.

The default host port is dynamically allocated and bound to `127.0.0.1`. Only
the HTTP/realtime port is published. PostgreSQL, gRPC, and console ports are not
published. Set a unique `TRNM_NAKAMA_COMPOSE_PROJECT` for each concurrent run.

Nakama is configured to stop an authoritative runtime after 300 consecutive
seconds with no presences. The P0 adapter also limits each runtime generation
to six hours (measured by authoritative ticks); reaching it stops that in-memory
instance without inventing completion evidence. The durable logical match
remains operator-only resumable under a new fenced generation. These are
resource-lifecycle limits, not gameplay timeouts or authority results.

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
