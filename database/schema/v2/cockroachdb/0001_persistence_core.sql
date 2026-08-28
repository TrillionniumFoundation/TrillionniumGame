-- TrillionniumGame CockroachDB persistence profile v2.
--
-- Correctness boundary:
--   * the complete command transaction runs at SERIALIZABLE isolation;
--   * SQLSTATE 40001 restarts the complete transaction with the same command
--     identity and fingerprint;
--   * all UUIDs are supplied by the authority layer.

CREATE TABLE IF NOT EXISTS trnm_schema_migrations (
    profile STRING NOT NULL,
    version INT8 NOT NULL CHECK (version > 0),
    contract_digest BYTES NOT NULL CHECK (octet_length(contract_digest) = 32),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp(),
    PRIMARY KEY (profile, version)
);

CREATE TABLE IF NOT EXISTS trnm_entity_heads (
    tenant_id UUID NOT NULL,
    entity_id UUID NOT NULL,
    revision INT8 NOT NULL CHECK (revision >= 0),
    last_sequence INT8 NOT NULL CHECK (last_sequence >= 0),
    authority_generation INT8 NOT NULL CHECK (authority_generation > 0),
    state_digest BYTES NOT NULL CHECK (octet_length(state_digest) = 32),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp(),
    PRIMARY KEY (tenant_id, entity_id)
);

CREATE TABLE IF NOT EXISTS trnm_command_receipts (
    tenant_id UUID NOT NULL,
    entity_id UUID NOT NULL,
    command_id UUID NOT NULL,
    fingerprint BYTES NOT NULL CHECK (octet_length(fingerprint) = 32),
    committed_revision INT8 NOT NULL CHECK (committed_revision > 0),
    committed_state_digest BYTES NOT NULL
        CHECK (octet_length(committed_state_digest) = 32),
    first_sequence INT8,
    last_sequence INT8 NOT NULL CHECK (last_sequence >= 0),
    event_count INT4 NOT NULL CHECK (event_count BETWEEN 0 AND 64),
    outbox_count INT4 NOT NULL CHECK (outbox_count BETWEEN 0 AND 64),
    receipt_bytes BYTES NOT NULL,
    receipt_digest BYTES NOT NULL CHECK (octet_length(receipt_digest) = 32),
    committed_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp(),
    PRIMARY KEY (tenant_id, entity_id, command_id),
    CONSTRAINT trnm_receipt_entity_head_fk
        FOREIGN KEY (tenant_id, entity_id)
        REFERENCES trnm_entity_heads (tenant_id, entity_id)
        ON DELETE RESTRICT,
    CONSTRAINT trnm_receipt_sequence_shape_ck CHECK (
        (
            event_count = 0
            AND first_sequence IS NULL
            AND last_sequence >= 0
        )
        OR
        (
            event_count > 0
            AND first_sequence IS NOT NULL
            AND first_sequence > 0
            AND last_sequence = first_sequence + event_count - 1
        )
    )
);

CREATE TABLE IF NOT EXISTS trnm_events (
    tenant_id UUID NOT NULL,
    entity_id UUID NOT NULL,
    sequence INT8 NOT NULL CHECK (sequence > 0),
    event_id UUID NOT NULL,
    command_id UUID NOT NULL,
    payload_digest BYTES NOT NULL CHECK (octet_length(payload_digest) = 32),
    payload_bytes BYTES NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp(),
    PRIMARY KEY (tenant_id, entity_id, sequence),
    CONSTRAINT trnm_event_identity_uq
        UNIQUE (tenant_id, entity_id, event_id),
    CONSTRAINT trnm_event_command_receipt_fk
        FOREIGN KEY (tenant_id, entity_id, command_id)
        REFERENCES trnm_command_receipts (tenant_id, entity_id, command_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS trnm_outbox (
    tenant_id UUID NOT NULL,
    intent_id UUID NOT NULL,
    entity_id UUID NOT NULL,
    command_id UUID NOT NULL,
    kind STRING NOT NULL CHECK (kind <> '' AND octet_length(kind) <= 128),
    payload_digest BYTES NOT NULL CHECK (octet_length(payload_digest) = 32),
    payload_bytes BYTES NOT NULL,
    attempt INT4 NOT NULL DEFAULT 0 CHECK (attempt BETWEEN 0 AND 32),
    lease_generation INT8 NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
    state STRING NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'leased', 'applied', 'dead_letter')),
    lease_owner UUID,
    lease_expires_at TIMESTAMPTZ,
    applied_receipt_digest BYTES,
    dead_letter_reason_digest BYTES,
    available_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp(),
    PRIMARY KEY (tenant_id, intent_id),
    CONSTRAINT trnm_outbox_command_receipt_fk
        FOREIGN KEY (tenant_id, entity_id, command_id)
        REFERENCES trnm_command_receipts (tenant_id, entity_id, command_id)
        ON DELETE RESTRICT,
    CONSTRAINT trnm_outbox_applied_digest_ck CHECK (
        applied_receipt_digest IS NULL
        OR octet_length(applied_receipt_digest) = 32
    ),
    CONSTRAINT trnm_outbox_dead_letter_digest_ck CHECK (
        dead_letter_reason_digest IS NULL
        OR octet_length(dead_letter_reason_digest) = 32
    ),
    CONSTRAINT trnm_outbox_state_shape_ck CHECK (
        (
            state = 'pending'
            AND lease_owner IS NULL
            AND lease_expires_at IS NULL
            AND applied_receipt_digest IS NULL
            AND dead_letter_reason_digest IS NULL
        )
        OR
        (
            state = 'leased'
            AND lease_owner IS NOT NULL
            AND lease_expires_at IS NOT NULL
            AND lease_generation > 0
            AND applied_receipt_digest IS NULL
            AND dead_letter_reason_digest IS NULL
        )
        OR
        (
            state = 'applied'
            AND lease_owner IS NULL
            AND lease_expires_at IS NULL
            AND applied_receipt_digest IS NOT NULL
            AND dead_letter_reason_digest IS NULL
        )
        OR
        (
            state = 'dead_letter'
            AND lease_owner IS NULL
            AND lease_expires_at IS NULL
            AND applied_receipt_digest IS NULL
            AND dead_letter_reason_digest IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS trnm_events_by_command_idx
    ON trnm_events (tenant_id, entity_id, command_id, sequence);

CREATE INDEX IF NOT EXISTS trnm_outbox_by_command_idx
    ON trnm_outbox (tenant_id, entity_id, command_id, intent_id);

CREATE INDEX IF NOT EXISTS trnm_outbox_pending_idx
    ON trnm_outbox (tenant_id, available_at, intent_id)
    WHERE state = 'pending';

CREATE INDEX IF NOT EXISTS trnm_outbox_expired_lease_idx
    ON trnm_outbox (tenant_id, lease_expires_at, intent_id)
    WHERE state = 'leased';
