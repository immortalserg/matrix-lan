"""Database operations for Matrix-LAN onboarding.

The caller must verify a newly created Matrix account with Synapse before
committing its role. Never accept an mxid asserted by an unauthenticated client.
All functions operate on a psycopg2 connection with autocommit disabled.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

_LOCK_ID = 0x4D4C414E  # Matrix-LAN onboarding advisory lock


class RegistrationError(ValueError):
    pass


def _lock(cur):
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_ID,))


def is_bootstrapped(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT EXISTS(SELECT 1 FROM matrix_lan_bootstrap)")
        return cur.fetchone()[0]


def bootstrap_first_user(conn, mxid):
    """Claim first-user superuser status; commit only after Synapse verifies mxid.

    Locking serializes all bootstrap attempts. The singleton primary key
    permanently prevents a second bootstrap even if the owner is deleted.
    """
    if not isinstance(mxid, str) or not mxid.startswith("@") or ":" not in mxid or len(mxid) > 255:
        raise RegistrationError("Invalid verified Matrix user ID")
    with conn.cursor() as cur:
        _lock(cur)
        cur.execute("SELECT EXISTS(SELECT 1 FROM matrix_lan_bootstrap)")
        if cur.fetchone()[0]:
            raise RegistrationError("Initial registration is closed")
        cur.execute(
            "INSERT INTO matrix_lan_users(mxid, role) VALUES (%s, 'superuser')",
            (mxid,),
        )
        cur.execute(
            "INSERT INTO matrix_lan_bootstrap(singleton, owner_mxid) VALUES (TRUE, %s)",
            (mxid,),
        )
        cur.execute(
            "INSERT INTO matrix_lan_audit(actor_mxid,target_mxid,action,new_role) "
            "VALUES (%s,%s,'bootstrap','superuser')",
            (mxid, mxid),
        )


def create_invite(conn, actor_mxid, *, hours=24):
    """Create a single-use invitation; display the raw token exactly once."""
    if not isinstance(hours, int) or not 1 <= hours <= 168:
        raise RegistrationError("Invitation lifetime must be 1..168 hours")
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode("ascii")).digest()
    expiry = datetime.now(timezone.utc) + timedelta(hours=hours)
    with conn.cursor() as cur:
        cur.execute("SELECT role FROM matrix_lan_users WHERE mxid=%s FOR UPDATE", (actor_mxid,))
        row = cur.fetchone()
        if not row or row[0] != "superuser":
            raise PermissionError("Only the superuser can issue invitations")
        cur.execute(
            "INSERT INTO matrix_lan_registration_invites"
            "(token_hash,created_by,expires_at) VALUES (%s,%s,%s)",
            (digest, actor_mxid, expiry),
        )
        cur.execute(
            "INSERT INTO matrix_lan_audit(actor_mxid,target_mxid,action) "
            "VALUES (%s,%s,'invite.create')",
            (actor_mxid, actor_mxid),
        )
    return token


def redeem_invite(conn, token, verified_mxid):
    """Consume an invite once and create an ordinary user.

    Call only after Synapse has created/verified the Matrix identity.
    The caller must hold the transaction open and roll back on any failure.
    """
    if not isinstance(verified_mxid, str) or not verified_mxid.startswith("@") or ":" not in verified_mxid or len(verified_mxid) > 255:
        raise RegistrationError("Invalid verified Matrix user ID")
    if not isinstance(token, str) or len(token) > 256:
        raise RegistrationError("Invalid invitation")
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    with conn.cursor() as cur:
        _lock(cur)
        cur.execute("SELECT EXISTS(SELECT 1 FROM matrix_lan_bootstrap)")
        if not cur.fetchone()[0]:
            raise RegistrationError("First user must complete bootstrap registration")
        cur.execute(
            "SELECT id,created_by FROM matrix_lan_registration_invites "
            "WHERE token_hash=%s AND consumed_at IS NULL AND revoked_at IS NULL "
            "AND expires_at > now() FOR UPDATE",
            (digest,),
        )
        row = cur.fetchone()
        if not row:
            raise RegistrationError("Invalid or expired invitation")
        invite_id, issuer = row
        cur.execute(
            "INSERT INTO matrix_lan_users(mxid,role) VALUES (%s,'user')",
            (verified_mxid,),
        )
        cur.execute(
            "UPDATE matrix_lan_registration_invites SET consumed_at=now(),consumed_by=%s "
            "WHERE id=%s",
            (verified_mxid, invite_id),
        )
        cur.execute(
            "INSERT INTO matrix_lan_audit(actor_mxid,target_mxid,action,new_role) "
            "VALUES (%s,%s,'invite.redeem','user')",
            (issuer, verified_mxid),
        )


def revoke_invite(conn, actor_mxid, invite_id):
    with conn.cursor() as cur:
        cur.execute("SELECT role FROM matrix_lan_users WHERE mxid=%s", (actor_mxid,))
        row = cur.fetchone()
        if not row or row[0] != "superuser":
            raise PermissionError("Only the superuser can revoke invitations")
        cur.execute(
            "UPDATE matrix_lan_registration_invites SET revoked_at=now() "
            "WHERE id=%s AND consumed_at IS NULL AND revoked_at IS NULL",
            (invite_id,),
        )
        return cur.rowcount == 1


def change_role(conn, actor_mxid, target_mxid, new_role):
    """Change an existing user's role, preserving at least one superuser.

    The advisory lock serializes all role transitions, preventing concurrent
    demotions from independently observing another superuser.
    """
    if new_role not in ("user", "admin", "superuser"):
        raise RegistrationError("Invalid role")
    if not isinstance(target_mxid, str) or not target_mxid.startswith("@"):
        raise RegistrationError("Invalid Matrix user ID")
    with conn.cursor() as cur:
        _lock(cur)
        cur.execute("SELECT role FROM matrix_lan_users WHERE mxid=%s FOR UPDATE", (actor_mxid,))
        actor = cur.fetchone()
        if not actor or actor[0] != "superuser":
            raise PermissionError("Only a superuser can assign roles")
        cur.execute("SELECT role FROM matrix_lan_users WHERE mxid=%s FOR UPDATE", (target_mxid,))
        target = cur.fetchone()
        if not target:
            raise RegistrationError("User not found")
        old_role = target[0]
        if old_role == new_role:
            return False
        if old_role == "superuser" and new_role != "superuser":
            cur.execute("SELECT COUNT(*) FROM matrix_lan_users WHERE role='superuser'")
            if cur.fetchone()[0] <= 1:
                raise RegistrationError("Cannot demote the last superuser")
        cur.execute(
            "UPDATE matrix_lan_users SET role=%s, updated_at=now() WHERE mxid=%s",
            (new_role, target_mxid),
        )
        cur.execute(
            "INSERT INTO matrix_lan_audit(actor_mxid,target_mxid,action,old_role,new_role) "
            "VALUES (%s,%s,'role.change',%s,%s)",
            (actor_mxid, target_mxid, old_role, new_role),
        )
        return True
