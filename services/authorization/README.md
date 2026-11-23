# Matrix-LAN authorization service

This service is **not production-ready** until cross-database registration failure
recovery and browser onboarding are completed.

It runs on the private Compose network and receives Synapse's registration shared
secret through the private generated runtime environment file. The database
schema is initialized from schema.sql when PostgreSQL is first created.

API paths: GET /api/setup/status; POST /api/setup/register;
POST /api/auth/register; POST /api/admin/invites;
DELETE /api/admin/invites/{id}.

Do not expose the authorization container's port directly to the host.
