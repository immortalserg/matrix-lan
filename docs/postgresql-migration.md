# PostgreSQL consolidation audit and migration plan

Status: **planning / no production migration performed**.

## Current verified storage configuration

| Component | Current database | Evidence | Proposed treatment |
| --- | --- | --- | --- |
| Synapse | PostgreSQL `synapse` | `templates/config/synapse.json` | Retain PostgreSQL; do not merge tables without verified schema isolation |
| WhatsApp | PostgreSQL `whatsapp` | `templates/config/whatsapp.json` | Retain; test dedicated PostgreSQL schema |
| Telegram | PostgreSQL `telegram` | `templates/config/telegram.json` | Retain; test dedicated PostgreSQL schema |
| LinkedIn | PostgreSQL `linkedin` | `templates/config/linkedin.json` | Retain; test dedicated PostgreSQL schema |
| Facebook Messenger | SQLite `/data/facebook.db` | `templates/config/facebook.json` | Test PostgreSQL backend and supported data migration |
| Instagram | SQLite `/data/instagram.db` | `templates/config/instagram.json` | Test PostgreSQL backend and supported data migration |
| Gmail/Matrimail | SQLite `/data/email.db` | `templates/config/email.json` | Verify exact vendored version's PostgreSQL support and migrations |
| X, TikTok | Not yet verified in this audit | `templates/config/` and vendored sources | Inspect configuration and backend support |
| IMO, WeChat, Threads, OK, VK, Taobao, Pinduoduo, accounts, chat-api | Not yet verified in this audit | `services/` | Inventory SQLite files, state, and session stores |

The repository's `install.py` declares `POSTGRES_ROLES = ('synapse', 'whatsapp', 'telegram', 'linkedin')`, creates four databases and roles in `state/postgres-init.sql`, and uses the Docker volume `postgres` for database persistence. Other service data lives in `runtime/state/`.

## Target design

- **One PostgreSQL server** is already in place. Prefer **one PostgreSQL cluster with separate databases and per-service roles** as the safe initial target.
- An optional **single database** `matrix_lan` with **separate schemas** (e.g. `synapse`, `whatsapp`, `telegram`, `linkedin`, `facebook`, `instagram`, `email`) requires compatibility testing for every application. Some applications assume the default `public` schema or use unqualified table names.
- A **single shared database role** is technically possible for compatible services, but is not recommended for production: one compromised bridge could read or modify every service's data. Prefer one role per bridge and schema-scoped grants.
- Media files, encryption/signing keys, browser profiles, and external service sessions are **not automatically moved into PostgreSQL**. Retain and back up their filesystem storage unless application code is explicitly changed.

## Implementation phases

1. **Inventory**: inspect every service's actual database driver, connection URI, migration framework, local files, and image version. Mark unsupported PostgreSQL backends explicitly.
2. **Backup and restore rehearsal**: snapshot `runtime/state` and PostgreSQL with `pg_dump`/`pg_dumpall --globals-only`; verify a restore into an isolated Compose project.
3. **Switch fresh-install defaults only** for bridges confirmed to support PostgreSQL, initially using independent databases to avoid table collisions.
4. **SQLite migration**: use a supported bridge-native migration mechanism where available. Validate row counts, bridge user mapping, credentials/sessions, and bidirectional message sync. Never replace an existing SQLite file merely by changing its URI.
5. **Optional schema consolidation**: test each bridge with an isolated PostgreSQL schema and `search_path` (or explicit schema configuration), including its automatic migrations and upgrades. Do not consolidate Synapse without documented compatibility.
6. **Installer changes**: add opt-in storage mode and idempotent database provisioning, preserving existing `runtime/bootstrap.private.json` and all existing installs. Add preflight checks and a rollback path.
7. **Acceptance tests**: new install, restart, upgrade, existing-data migration, attachments, login persistence, send/receive, and restore.

## Non-negotiable safety requirements

- Do not apply a new `postgres-init.sql` to an existing PostgreSQL Docker volume and expect new databases to appear; entrypoint initialization scripts run only for a fresh data directory.
- Never remove SQLite files until migration is independently verified and a restorable backup exists.
- Never expose PostgreSQL outside the internal Docker network by default.
- Do not commit `runtime/`, passwords, tokens, actual sessions, media, or dumps.
- Do not claim an SQLite bridge supports PostgreSQL without testing its exact image/source version.

## Files to update after compatibility is confirmed

- `install.py` — provisioning, secrets, configuration rendering, migration orchestration.
- `templates/config/*.json` — supported bridge database drivers and URIs.
- `templates/services.json` — service startup dependencies and health checks.
- `README.md` — supported database modes, backup, restore, migration and rollback.
- New isolated tests — generated Compose validity and database isolation.
