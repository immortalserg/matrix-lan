-- Matrix-LAN authorization schema (apply transactionally to the application database).
-- A fixed bootstrap owner is inserted by the installer; registration must never
-- execute this migration with a different owner identifier.
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
COMMIT;
