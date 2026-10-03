-- Copyright 2018 The Nakama Authors. Apache-2.0; upstream-derived table contract.
-- Copyright 2026 Trillionnium contributors. Independent typed migration adapter.
-- Pinned Nakama d4d92f93: initial schema + Facebook Instant Games + Apple + Console device fields.
-- Source-only schema5 AccountsV5 candidate. Native catalog captures are pending.
-- Storage writer epoch remains4. This migration grants no account transfer or serving authority.
BEGIN;
-- trnm:action metadata_v4_apply_source_commit
ALTER TABLE trnm_schema_metadata ADD COLUMN v4_apply_source_commit TEXT;

-- trnm:action nakama_users
CREATE TABLE public.users (
id UUID NOT NULL,
username VARCHAR(128) NOT NULL,
display_name VARCHAR(255),
avatar_url VARCHAR(512),
lang_tag VARCHAR(18) NOT NULL DEFAULT 'en',
location VARCHAR(255),
timezone VARCHAR(255),
metadata JSONB NOT NULL DEFAULT '{}',
wallet JSONB NOT NULL DEFAULT '{}',
email VARCHAR(255),
password BYTEA,
facebook_id VARCHAR(128),
google_id VARCHAR(128),
gamecenter_id VARCHAR(128),
steam_id VARCHAR(128),
custom_id VARCHAR(128),
edge_count INT NOT NULL DEFAULT 0,
create_time TIMESTAMPTZ NOT NULL DEFAULT now(),
update_time TIMESTAMPTZ NOT NULL DEFAULT now(),
verify_time TIMESTAMPTZ NOT NULL DEFAULT '1970-01-01 00:00:00 UTC',
disable_time TIMESTAMPTZ NOT NULL DEFAULT '1970-01-01 00:00:00 UTC',
facebook_instant_game_id VARCHAR(128),
apple_id VARCHAR(128),
CONSTRAINT users_pkey PRIMARY KEY (id),
CONSTRAINT users_username_key UNIQUE (username),
CONSTRAINT users_email_key UNIQUE (email),
CONSTRAINT users_facebook_id_key UNIQUE (facebook_id),
CONSTRAINT users_google_id_key UNIQUE (google_id),
CONSTRAINT users_gamecenter_id_key UNIQUE (gamecenter_id),
CONSTRAINT users_steam_id_key UNIQUE (steam_id),
CONSTRAINT users_custom_id_key UNIQUE (custom_id),
CONSTRAINT users_facebook_instant_game_id_key UNIQUE (facebook_instant_game_id),
CONSTRAINT users_apple_id_key UNIQUE (apple_id),
CONSTRAINT users_password_check CHECK (length(password) < 32000),
CONSTRAINT users_edge_count_check CHECK (edge_count >= 0)
);

-- trnm:action nakama_system_user
INSERT INTO public.users (id, username) VALUES ('00000000-0000-0000-0000-000000000000', '') ON CONFLICT (id) DO NOTHING;

-- trnm:action nakama_user_device
CREATE TABLE public.user_device (
id VARCHAR(128) NOT NULL,
user_id UUID NOT NULL,
preferences JSONB NOT NULL DEFAULT '{}',
push_token_amazon VARCHAR(512) NOT NULL DEFAULT '',
push_token_android VARCHAR(512) NOT NULL DEFAULT '',
push_token_huawei VARCHAR(512) NOT NULL DEFAULT '',
push_token_ios VARCHAR(512) NOT NULL DEFAULT '',
push_token_web VARCHAR(512) NOT NULL DEFAULT '',
CONSTRAINT user_device_pkey PRIMARY KEY (id),
CONSTRAINT user_device_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users (id) ON UPDATE NO ACTION ON DELETE CASCADE,
CONSTRAINT user_device_user_id_id_key UNIQUE (user_id, id)
);

-- trnm:action metadata_v4_history
ALTER TABLE trnm_schema_metadata ADD CONSTRAINT metadata_v4_history CHECK (schema_version < 5 OR (v4_apply_source_commit IS NOT NULL AND length(v4_apply_source_commit) = 40));
COMMIT;
