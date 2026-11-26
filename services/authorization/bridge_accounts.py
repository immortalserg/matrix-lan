"""Server-side ownership operations.

Call claim only from a trusted adapter after verified native login, never
from a client-supplied login identifier. Caller owns the transaction.
"""
class OwnershipConflict(PermissionError):
    pass


def claim(conn, bridge, login_id, owner_mxid):
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO matrix_lan_bridge_accounts
            (bridge,login_id,owner_mxid) VALUES (%s,%s,%s)
            ON CONFLICT (bridge,login_id) DO NOTHING""",
            (bridge, login_id, owner_mxid))
        cur.execute("""SELECT owner_mxid FROM matrix_lan_bridge_accounts
            WHERE bridge=%s AND login_id=%s FOR UPDATE""", (bridge, login_id))
        row = cur.fetchone()
        if not row or row[0] != owner_mxid:
            raise OwnershipConflict("Account belongs to another user")
        return row[0]


def require_access(conn, actor_mxid, bridge, login_id):
    with conn.cursor() as cur:
        cur.execute("""SELECT a.owner_mxid,u.role
            FROM matrix_lan_bridge_accounts a
            JOIN matrix_lan_users u ON u.mxid=%s
            WHERE a.bridge=%s AND a.login_id=%s
            FOR UPDATE OF a""", (actor_mxid, bridge, login_id))
        row = cur.fetchone()
        if not row or (row[0] != actor_mxid and row[1] != "superuser"):
            raise PermissionError("Account unavailable")
        return row[0]


def release(conn, actor_mxid, bridge, login_id):
    """Call only after the trusted adapter confirms native disconnection."""
    owner = require_access(conn, actor_mxid, bridge, login_id)
    with conn.cursor() as cur:
        cur.execute("""DELETE FROM matrix_lan_bridge_accounts
            WHERE bridge=%s AND login_id=%s AND owner_mxid=%s""",
            (bridge, login_id, owner))
        return cur.rowcount == 1
