BEGIN;

CREATE TABLE trnm_schema_metadata (
    singleton INT2 PRIMARY KEY CHECK (singleton = 1),
    schema_version INT8 NOT NULL CHECK (schema_version >= 1),
    profile STRING NOT NULL CHECK (profile = 'cockroachdb'),
    source_commit STRING NOT NULL CHECK (length(source_commit) = 40),
    applied_at_ms INT8 NOT NULL CHECK (applied_at_ms >= 0)
);

CREATE TABLE trnm_entity_heads (
    entity_id BYTES PRIMARY KEY CHECK (octet_length(entity_id) = 16),
    revision INT8 NOT NULL CHECK (revision >= 0),
    last_event_sequence INT8 NOT NULL CHECK (last_event_sequence >= 0),
    authority_generation INT8 NOT NULL CHECK (authority_generation > 0),
    state_digest BYTES NOT NULL CHECK (octet_length(state_digest) = 32),
    updated_at_ms INT8 NOT NULL CHECK (updated_at_ms >= 0)
);

CREATE TABLE trnm_command_receipts (
    entity_id BYTES NOT NULL CHECK (octet_length(entity_id) = 16),
    command_id BYTES NOT NULL CHECK (octet_length(command_id) = 16),
    fingerprint BYTES NOT NULL CHECK (octet_length(fingerprint) = 32),
    revision INT8 NOT NULL CHECK (revision > 0),
    state_digest BYTES NOT NULL CHECK (octet_length(state_digest) = 32),
    first_event_sequence INT8,
    last_event_sequence INT8 NOT NULL CHECK (last_event_sequence >= 0),
    event_count INT4 NOT NULL CHECK (event_count >= 0 AND event_count <= 64),
    committed_at_ms INT8 NOT NULL CHECK (committed_at_ms >= 0),
    PRIMARY KEY (entity_id, command_id),
    UNIQUE (entity_id, revision),
    FOREIGN KEY (entity_id) REFERENCES trnm_entity_heads(entity_id) ON DELETE RESTRICT,
    CHECK (
        (event_count = 0 AND first_event_sequence IS NULL)
        OR
        (event_count > 0 AND first_event_sequence IS NOT NULL
         AND first_event_sequence > 0
         AND last_event_sequence = first_event_sequence + event_count - 1)
    )
);

CREATE TABLE trnm_events (
    entity_id BYTES NOT NULL CHECK (octet_length(entity_id) = 16),
    sequence INT8 NOT NULL CHECK (sequence > 0),
    event_id BYTES NOT NULL UNIQUE CHECK (octet_length(event_id) = 16),
    command_id BYTES NOT NULL CHECK (octet_length(command_id) = 16),
    payload_digest BYTES NOT NULL CHECK (octet_length(payload_digest) = 32),
    created_at_ms INT8 NOT NULL CHECK (created_at_ms >= 0),
    PRIMARY KEY (entity_id, sequence),
    FOREIGN KEY (entity_id, command_id)
        REFERENCES trnm_command_receipts(entity_id, command_id)
        ON DELETE RESTRICT
);

CREATE TABLE trnm_outbox (
    intent_id BYTES PRIMARY KEY CHECK (octet_length(intent_id) = 16),
    entity_id BYTES NOT NULL CHECK (octet_length(entity_id) = 16),
    command_id BYTES NOT NULL CHECK (octet_length(command_id) = 16),
    kind INT2 NOT NULL CHECK (kind BETWEEN 0 AND 4),
    payload_digest BYTES NOT NULL CHECK (octet_length(payload_digest) = 32),
    attempt INT8 NOT NULL DEFAULT 0 CHECK (attempt >= 0 AND attempt <= 32),
    lease_generation INT8 NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
    state INT2 NOT NULL DEFAULT 0 CHECK (state BETWEEN 0 AND 3),
    owner_node BYTES CHECK (owner_node IS NULL OR octet_length(owner_node) = 16),
    receipt_digest BYTES CHECK (receipt_digest IS NULL OR octet_length(receipt_digest) = 32),
    dead_reason_digest BYTES CHECK (dead_reason_digest IS NULL OR octet_length(dead_reason_digest) = 32),
    available_at_ms INT8 NOT NULL CHECK (available_at_ms >= 0),
    updated_at_ms INT8 NOT NULL CHECK (updated_at_ms >= 0),
    FOREIGN KEY (entity_id, command_id)
        REFERENCES trnm_command_receipts(entity_id, command_id)
        ON DELETE RESTRICT,
    CHECK (
        (state = 0 AND owner_node IS NULL AND receipt_digest IS NULL AND dead_reason_digest IS NULL)
        OR
        (state = 1 AND owner_node IS NOT NULL AND receipt_digest IS NULL AND dead_reason_digest IS NULL)
        OR
        (state = 2 AND owner_node IS NULL AND receipt_digest IS NOT NULL AND dead_reason_digest IS NULL)
        OR
        (state = 3 AND owner_node IS NULL AND receipt_digest IS NULL AND dead_reason_digest IS NOT NULL)
    )
);

CREATE INDEX trnm_outbox_ready_idx
    ON trnm_outbox (state, available_at_ms, intent_id);

CREATE TABLE trnm_command_outbox (
    entity_id BYTES NOT NULL CHECK (octet_length(entity_id) = 16),
    command_id BYTES NOT NULL CHECK (octet_length(command_id) = 16),
    position INT4 NOT NULL CHECK (position >= 0 AND position < 64),
    intent_id BYTES NOT NULL UNIQUE CHECK (octet_length(intent_id) = 16),
    PRIMARY KEY (entity_id, command_id, position),
    FOREIGN KEY (entity_id, command_id)
        REFERENCES trnm_command_receipts(entity_id, command_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (intent_id) REFERENCES trnm_outbox(intent_id) ON DELETE RESTRICT
);

CREATE TABLE trnm_authority_leases (
    entity_id BYTES PRIMARY KEY CHECK (octet_length(entity_id) = 16),
    owner_node BYTES NOT NULL CHECK (octet_length(owner_node) = 16),
    lease_generation INT8 NOT NULL CHECK (lease_generation > 0),
    authority_generation INT8 NOT NULL CHECK (authority_generation > 0),
    expires_at_ms INT8 NOT NULL CHECK (expires_at_ms >= 0),
    updated_at_ms INT8 NOT NULL CHECK (updated_at_ms >= 0),
    FOREIGN KEY (entity_id) REFERENCES trnm_entity_heads(entity_id) ON DELETE RESTRICT
);

CREATE TABLE trnm_session_families (
    family_id BYTES PRIMARY KEY CHECK (octet_length(family_id) = 16),
    user_id BYTES NOT NULL CHECK (octet_length(user_id) = 16),
    generation INT8 NOT NULL CHECK (generation >= 0),
    active_token_id BYTES CHECK (active_token_id IS NULL OR octet_length(active_token_id) = 16),
    revoked_reason INT2 CHECK (revoked_reason IS NULL OR revoked_reason BETWEEN 0 AND 3),
    created_at_ms INT8 NOT NULL CHECK (created_at_ms >= 0),
    updated_at_ms INT8 NOT NULL CHECK (updated_at_ms >= created_at_ms),
    CHECK (
        (revoked_reason IS NULL AND active_token_id IS NOT NULL)
        OR
        (revoked_reason IS NOT NULL AND active_token_id IS NULL)
    )
);

CREATE TABLE trnm_refresh_tokens (
    family_id BYTES NOT NULL CHECK (octet_length(family_id) = 16),
    token_id BYTES NOT NULL CHECK (octet_length(token_id) = 16),
    token_digest BYTES NOT NULL UNIQUE CHECK (octet_length(token_digest) = 32),
    generation INT8 NOT NULL CHECK (generation >= 0),
    state INT2 NOT NULL CHECK (state BETWEEN 0 AND 1),
    issued_at_ms INT8 NOT NULL CHECK (issued_at_ms >= 0),
    consumed_at_ms INT8,
    PRIMARY KEY (family_id, token_id),
    FOREIGN KEY (family_id) REFERENCES trnm_session_families(family_id) ON DELETE RESTRICT,
    CHECK (
        (state = 0 AND consumed_at_ms IS NULL)
        OR
        (state = 1 AND consumed_at_ms IS NOT NULL AND consumed_at_ms >= issued_at_ms)
    )
);

CREATE TABLE trnm_storage_objects (
    collection STRING NOT NULL CHECK (length(collection) BETWEEN 1 AND 128),
    object_key STRING NOT NULL CHECK (length(object_key) BETWEEN 1 AND 128),
    user_id BYTES NOT NULL CHECK (octet_length(user_id) = 16),
    value_bytes BYTES NOT NULL CHECK (octet_length(value_bytes) <= 1048576),
    version_digest BYTES NOT NULL CHECK (octet_length(version_digest) = 32),
    read_permission INT2 NOT NULL CHECK (read_permission BETWEEN 0 AND 2),
    write_permission INT2 NOT NULL CHECK (write_permission BETWEEN 0 AND 1),
    updated_at_ms INT8 NOT NULL CHECK (updated_at_ms >= 0),
    PRIMARY KEY (collection, object_key, user_id)
);

COMMIT;
