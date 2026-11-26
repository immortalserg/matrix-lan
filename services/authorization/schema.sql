-- Matrix-LAN authorization schema for web-first registration.
-- Apply transactionally. Registration API must serialize bootstrap using
-- pg_advisory_xact_lock and create the Synapse account safely.
BEGIN;
CREATE TABLE IF NOT EXISTS matrix_lan_users (
    mxid TEXT PRIMARY KEY,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin','superuser')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS matrix_lan_bootstrap (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    owner_mxid TEXT NOT NULL UNIQUE REFERENCES matrix_lan_users(mxid),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS matrix_lan_registration_invites (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    token_hash BYTEA NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),
    created_by TEXT NOT NULL REFERENCES matrix_lan_users(mxid),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    consumed_by TEXT UNIQUE REFERENCES matrix_lan_users(mxid),
    revoked_at TIMESTAMPTZ,
    CHECK (expires_at > created_at),
    CHECK (NOT (consumed_at IS NOT NULL AND revoked_at IS NOT NULL)),
    CHECK ((consumed_at IS NULL) = (consumed_by IS NULL))
);
CREATE INDEX IF NOT EXISTS matrix_lan_invites_active_idx
    ON matrix_lan_registration_invites(expires_at)
    WHERE consumed_at IS NULL AND revoked_at IS NULL;
CREATE TABLE IF NOT EXISTS matrix_lan_audit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_mxid TEXT NOT NULL,
    target_mxid TEXT NOT NULL,
    action TEXT NOT NULL,
    old_role TEXT,
    new_role TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS matrix_lan_audit_created_idx ON matrix_lan_audit(created_at DESC);
CREATE TABLE IF NOT EXISTS matrix_lan_bridge_settings (
    bridge TEXT PRIMARY KEY,
    desired_enabled BOOLEAN NOT NULL,
    updated_by TEXT NOT NULL REFERENCES matrix_lan_users(mxid),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS matrix_lan_bridge_runtime (
    bridge TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    health TEXT NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS matrix_lan_telegram_credentials (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    api_id BIGINT NOT NULL CHECK (api_id > 0 AND api_id <= 2147483647),
    api_hash TEXT NOT NULL CHECK (api_hash ~ '^[a-fA-F0-9]{32}$'),
    updated_by TEXT NOT NULL REFERENCES matrix_lan_users(mxid),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS matrix_lan_bridge_restart (
    bridge TEXT PRIMARY KEY,
    generation BIGINT NOT NULL DEFAULT 1,
    applied_generation BIGINT NOT NULL DEFAULT 0,
    requested_by TEXT NOT NULL REFERENCES matrix_lan_users(mxid),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE matrix_lan_telegram_credentials
    ADD COLUMN IF NOT EXISTS applied_at TIMESTAMPTZ;
-- Native login identifiers are scoped to a bridge. Ownership is enforced
-- by application APIs, not by per-user PostgreSQL roles.
CREATE TABLE IF NOT EXISTS matrix_lan_bridge_accounts (
    bridge TEXT NOT NULL,
    login_id TEXT NOT NULL,
    owner_mxid TEXT NOT NULL REFERENCES matrix_lan_users(mxid),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (bridge, login_id),
    CHECK (length(bridge) BETWEEN 1 AND 64),
    CHECK (length(login_id) BETWEEN 1 AND 1024)
);
CREATE INDEX IF NOT EXISTS matrix_lan_bridge_accounts_owner_idx
    ON matrix_lan_bridge_accounts(owner_mxid, bridge);
COMMIT;
