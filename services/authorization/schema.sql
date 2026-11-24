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
COMMIT;
