"""Matrix-LAN onboarding HTTP service.

Requires psycopg2 and MATRIX_LAN_DSN, SYNAPSE_REGISTRATION_SECRET,
MATRIX_SERVER_NAME. Synapse public registration remains disabled.
"""
import hashlib
import hmac
import json
import os
import stat
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlencode
from bridge_accounts import claim, require_access, release, OwnershipConflict

import psycopg2
from registration import (
    RegistrationError, bootstrap_first_user, create_invite,
    is_bootstrapped, redeem_invite, revoke_invite, change_role,
)

DSN = os.environ["MATRIX_LAN_DSN"]
SECRET = os.environ["SYNAPSE_REGISTRATION_SECRET"]
SERVER = os.environ["MATRIX_SERVER_NAME"]
SYNAPSE = os.environ.get("SYNAPSE_URL", "http://synapse:8008").rstrip("/")
USER_RE = re.compile(r"^[a-z0-9._=/-]{1,80}$")
RATE = {}
RATE_LOCK = threading.Lock()


def request(path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None if body is None else json.dumps(body).encode()
    req = Request(SYNAPSE + path, data=data, headers=headers)
    try:
        with urlopen(req, timeout=12) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        try:
            result = json.load(exc)
        except (ValueError, UnicodeDecodeError):
            result = {}
        return exc.code, result


def verify(token):
    status, data = request("/_matrix/client/v3/account/whoami", token=token)
    if status != 200:
        raise PermissionError("Invalid Matrix session")
    return data["user_id"]


def register_synapse(username, password):
    # Synapse shared-secret registration; never expose SECRET to clients.
    status, nonce_data = request("/_synapse/admin/v1/register")
    if status != 200:
        raise RegistrationError("Synapse registration unavailable")
    nonce = nonce_data["nonce"]
    mac = hmac.new(SECRET.encode(), "\0".join(
        (nonce, username, password, "notadmin")
    ).encode(), hashlib.sha1).hexdigest()
    status, data = request("/_synapse/admin/v1/register", {
        "nonce": nonce, "username": username, "password": password,
        "admin": False, "mac": mac,
    })
    if status == 400 and data.get("errcode") == "M_USER_IN_USE":
        login_status, login = request("/_matrix/client/v3/login", {
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": username},
            "password": password,
            "initial_device_display_name": "Matrix-LAN registration recovery",
        })
        if login_status != 200:
            raise RegistrationError("Matrix username already exists")
        if login.get("user_id") != f"@{username}:{SERVER}":
            raise RegistrationError("Unexpected Matrix login identity")
        return login
    if status != 200:
        raise RegistrationError("Matrix account registration failed")
    return data


def allowed(ip):
    now = time.monotonic()
    with RATE_LOCK:
        entries = [t for t in RATE.get(ip, []) if now - t < 60]
        if len(entries) >= 5:
            RATE[ip] = entries
            return False
        entries.append(now)
        RATE[ip] = entries
        return True


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        length = int(self.headers.get("Content-Length", "-1"))
        if not 0 <= length <= 4096:
            raise RegistrationError("Invalid request size")
        result = json.loads(self.rfile.read(length))
        if not isinstance(result, dict):
            raise RegistrationError("Expected JSON object")
        return result

    def superuser(self, conn):
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            raise PermissionError("Bearer token required")
        mxid = verify(authorization[7:])
        with conn.cursor() as cur:
            cur.execute("SELECT role FROM matrix_lan_users WHERE mxid=%s", (mxid,))
            row = cur.fetchone()
        if not row or row[0] != "superuser":
            raise PermissionError("Superuser required")
        return mxid

    def list_bridge_accounts(self):
        try:
            from contextlib import closing
            authorization = self.headers.get("Authorization", "")
            if not authorization.startswith("Bearer "):
                raise PermissionError("Bearer token required")
            actor = verify(authorization[7:])
            with closing(psycopg2.connect(DSN)) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT role FROM matrix_lan_users WHERE mxid=%s", (actor,))
                    role = cur.fetchone()
                    if not role:
                        raise PermissionError("Unknown Matrix-LAN user")
                    query = "SELECT bridge,login_id,owner_mxid,created_at FROM matrix_lan_bridge_accounts"
                    if role[0] == "superuser":
                        cur.execute(query + " ORDER BY owner_mxid,bridge,login_id")
                    else:
                        cur.execute(query + " WHERE owner_mxid=%s ORDER BY bridge,login_id", (actor,))
                    accounts = [{"bridge": bridge, "login_id": login_id,
                                 "owner_mxid": owner, "created_at": created.isoformat()}
                                for bridge, login_id, owner, created in cur.fetchall()]
            return self.send_json(200, {"accounts": accounts})
        except PermissionError:
            return self.send_json(403, {"error": "forbidden"})
        except (psycopg2.Error, OSError):
            return self.send_json(503, {"error": "backend_unavailable"})

    def do_GET(self):
        if urlsplit(self.path).path == "/api/admin/bridge-accounts":
            return self.list_bridge_accounts()
        path = urlsplit(self.path).path
        logs = re.fullmatch(r"/api/admin/bridges/([a-z0-9]+)/logs", path)
        if logs:
            return self.bridge_logs(logs.group(1))
        if path not in ("/api/setup/status", "/api/admin/users", "/api/admin/invites", "/api/admin/bridges"):
            return self.send_json(404, {"error": "not_found"})
        try:
            with psycopg2.connect(DSN) as conn:
                if path == "/api/setup/status":
                    return self.send_json(200, {"initialized": is_bootstrapped(conn)})
                self.superuser(conn)
                if path == "/api/admin/bridges":
                    with open("/app/bridge-catalog.json", encoding="utf-8") as catalog:
                        inventory = json.load(catalog)
                    with conn.cursor() as cur:
                        cur.execute("SELECT bridge, desired_enabled FROM matrix_lan_bridge_settings")
                        preferences = dict(cur.fetchall())
                        cur.execute("SELECT api_id, applied_at FROM matrix_lan_telegram_credentials WHERE singleton")
                        telegram = cur.fetchone()
                        cur.execute("SELECT bridge,state,health,checked_at FROM matrix_lan_bridge_runtime")
                        observed = {key: {'state': state, 'health': health, 'checked_at': checked.isoformat()} for key,state,health,checked in cur.fetchall()}
                        cur.execute("SELECT bridge,generation,applied_generation,requested_at FROM matrix_lan_bridge_restart")
                        restarts = {key: {"generation": str(gen), "applied_generation": str(applied),
                                          "pending": gen > applied, "requested_at": requested.isoformat()}
                                    for key,gen,applied,requested in cur.fetchall()}
                    for bridge in inventory["bridges"]:
                        bridge["desired_enabled"] = preferences.get(bridge["id"], bridge["prepared"])
                        bridge["runtime"] = observed.get(bridge["id"])
                        bridge["restart"] = restarts.get(bridge["id"])
                        if bridge["id"] == "telegram":
                            bridge["credentials"] = {"configured": bool(telegram), "api_id": telegram[0] if telegram else None, "applied": bool(telegram and telegram[1]), "applied_at": telegram[1].isoformat() if telegram and telegram[1] else None}
                    return self.send_json(200, inventory)
                with conn.cursor() as cur:
                    if path == "/api/admin/users":
                        cur.execute("SELECT mxid,role,created_at FROM matrix_lan_users ORDER BY created_at")
                        users = [{"mxid": mxid, "role": role, "created_at": created.isoformat()}
                                 for mxid, role, created in cur.fetchall()]
                        return self.send_json(200, {"users": users})
                    cur.execute("SELECT id,expires_at,consumed_at,revoked_at "
                                "FROM matrix_lan_registration_invites ORDER BY id DESC LIMIT 200")
                    invites = [{"id": invite_id, "expires_at": expires.isoformat(),
                                "consumed": consumed is not None, "revoked": revoked is not None}
                               for invite_id, expires, consumed, revoked in cur.fetchall()]
                    return self.send_json(200, {"invites": invites})
        except PermissionError:
            self.send_json(403, {"error": "forbidden"})
        except (psycopg2.Error, OSError):
            self.send_json(503, {"error": "backend_unavailable"})

    def bridge_logs(self, bridge_id):
        try:
            with psycopg2.connect(DSN) as conn:
                actor = self.superuser(conn)
                with open("/app/bridge-catalog.json", encoding="utf-8") as source:
                    if not any(b["id"] == bridge_id for b in json.load(source)["bridges"]):
                        return self.send_json(404, {"error": "bridge_unknown"})
                # Audit access without copying log content into the audit table.
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO matrix_lan_audit (actor_mxid,target_mxid,action) VALUES (%s,%s,%s)",
                                (actor, bridge_id, "bridge_logs_read"))
            root = "/var/log/matrix-bridges"
            fd = os.open(root + "/" + bridge_id + ".log",
                         os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    return self.send_json(404, {"error": "log_unavailable"})
                stream.seek(max(0, info.st_size - 32768))
                content = stream.read(32768).decode("utf-8", errors="replace")
            return self.send_json(200, {"bridge": bridge_id, "log": content,
                                        "truncated": info.st_size > 32768})
        except PermissionError:
            return self.send_json(403, {"error": "forbidden"})
        except FileNotFoundError:
            return self.send_json(404, {"error": "log_unavailable"})
        except (psycopg2.Error, OSError):
            return self.send_json(503, {"error": "backend_unavailable"})

    def do_DELETE(self):
        match = re.fullmatch(r"/api/admin/invites/([1-9][0-9]*)", urlsplit(self.path).path)
        if not match:
            return self.send_json(404, {"error": "not_found"})
        try:
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                raise PermissionError("Missing bearer token")
            with psycopg2.connect(DSN) as conn:
                actor = self.superuser(conn)
                changed = revoke_invite(conn, actor, int(match.group(1)))
            return self.send_json(200 if changed else 404, {"revoked": changed})
        except PermissionError:
            return self.send_json(403, {"error": "forbidden"})
        except (psycopg2.Error, OSError):
            return self.send_json(503, {"error": "backend_unavailable"})

    def bridge_preference(self, bridge_id):
        try:
            with psycopg2.connect(DSN) as conn:
                actor = self.superuser(conn)
                data = self.body()
                enabled = data.get("enabled")
                if type(enabled) is not bool:
                    raise RegistrationError("enabled must be boolean")
                with open("/app/bridge-catalog.json", encoding="utf-8") as source:
                    bridges = json.load(source)["bridges"]
                bridge = next((b for b in bridges if b["id"] == bridge_id), None)
                if not bridge or not bridge["available"]:
                    return self.send_json(404, {"error": "bridge_unavailable"})
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO matrix_lan_bridge_settings (bridge, desired_enabled, updated_by) VALUES (%s,%s,%s) ON CONFLICT (bridge) DO UPDATE SET desired_enabled=EXCLUDED.desired_enabled, updated_by=EXCLUDED.updated_by, updated_at=now()", (bridge_id, enabled, actor))
                conn.commit()
                return self.send_json(200, {"desired_enabled": enabled, "applied": False})
        except PermissionError:
            return self.send_json(403, {"error": "forbidden"})
        except (RegistrationError, ValueError):
            return self.send_json(400, {"error": "invalid_request"})
        except (psycopg2.Error, OSError):
            return self.send_json(503, {"error": "backend_unavailable"})

    def telegram_credentials(self):
        try:
            with psycopg2.connect(DSN) as conn:
                actor = self.superuser(conn)
                data = self.body()
                api_id, api_hash = data.get("api_id"), data.get("api_hash")
                if type(api_id) is not int or not 0 < api_id <= 2147483647 or not isinstance(api_hash, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash):
                    return self.send_json(400, {"error": "invalid_telegram_credentials"})
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO matrix_lan_telegram_credentials (singleton,api_id,api_hash,updated_by) VALUES (true,%s,%s,%s) ON CONFLICT (singleton) DO UPDATE SET api_id=EXCLUDED.api_id,api_hash=EXCLUDED.api_hash,updated_by=EXCLUDED.updated_by,updated_at=now(),applied_at=NULL", (api_id,api_hash,actor))
                conn.commit()
                return self.send_json(200, {"configured": True, "api_id": api_id, "applied": False})
        except PermissionError:
            return self.send_json(403, {"error": "forbidden"})
        except (RegistrationError, ValueError):
            return self.send_json(400, {"error": "invalid_request"})
        except (psycopg2.Error, OSError):
            return self.send_json(503, {"error": "backend_unavailable"})

    def do_PATCH(self):
        if urlsplit(self.path).path == "/api/admin/bridges/telegram/credentials":
            return self.telegram_credentials()
        bridge = re.fullmatch(r"/api/admin/bridges/([a-z0-9_-]+)", urlsplit(self.path).path)
        if bridge:
            return self.bridge_preference(bridge.group(1))
        match = re.fullmatch(r"/api/admin/users/(@[^/]+)/role", urlsplit(self.path).path)
        if not match:
            return self.send_json(404, {"error": "not_found"})
        if not allowed(self.client_address[0]):
            return self.send_json(429, {"error": "rate_limited"})
        try:
            data = self.body()
            role = data.get("role")
            with psycopg2.connect(DSN) as conn:
                actor = self.superuser(conn)
                changed = change_role(conn, actor, match.group(1), role)
                conn.commit()
            return self.send_json(200, {"changed": changed, "role": role})
        except RegistrationError as exc:
            return self.send_json(400, {"error": str(exc)})
        except (ValueError, json.JSONDecodeError):
            return self.send_json(400, {"error": "invalid_request"})
        except PermissionError:
            return self.send_json(403, {"error": "forbidden"})
        except (psycopg2.Error, OSError):
            return self.send_json(503, {"error": "backend_unavailable"})

    def restart_bridge(self, bridge_id):
        try:
            with psycopg2.connect(DSN) as conn:
                actor = self.superuser(conn)
                with open("/app/bridge-catalog.json", encoding="utf-8") as source:
                    bridge = next((b for b in json.load(source)["bridges"] if b["id"] == bridge_id), None)
                if not bridge or not bridge["available"] or not bridge["prepared"]:
                    return self.send_json(404, {"error": "bridge_unavailable"})
                with conn.cursor() as cur:
                    cur.execute("SELECT desired_enabled FROM matrix_lan_bridge_settings WHERE bridge=%s", (bridge_id,))
                    row = cur.fetchone()
                    if row and not row[0]:
                        return self.send_json(409, {"error": "bridge_disabled"})
                    cur.execute("""INSERT INTO matrix_lan_bridge_restart (bridge,requested_by)
                        VALUES (%s,%s) ON CONFLICT (bridge) DO UPDATE SET
                        generation=matrix_lan_bridge_restart.generation+1,
                        requested_by=EXCLUDED.requested_by,requested_at=now()
                        RETURNING generation""", (bridge_id, actor))
                    generation = cur.fetchone()[0]
                    cur.execute("INSERT INTO matrix_lan_audit (actor_mxid,target_mxid,action) VALUES (%s,%s,%s)",
                                (actor, bridge_id, "bridge_restart_requested"))
                conn.commit()
            return self.send_json(202, {"generation": str(generation), "status": "queued"})
        except PermissionError:
            return self.send_json(403, {"error": "forbidden"})
        except (psycopg2.Error, OSError):
            return self.send_json(503, {"error": "backend_unavailable"})

    def sync_bridge_accounts(self, bridge):
        """Discover logins from the bridge itself, never from request JSON."""
        from contextlib import closing
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return self.send_json(403, {"error": "forbidden"})
        try:
            actor = verify(authorization[7:])
            with closing(psycopg2.connect(DSN)) as conn:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1 FROM matrix_lan_users WHERE mxid=%s", (actor,))
                        if not cur.fetchone():
                            raise PermissionError("Unknown user")
                    defaults = {"whatsapp": "http://mautrix-whatsapp:29318",
                                "telegram": "http://mautrix-telegram:29317"}
                    base = os.getenv(bridge.upper() + "_PROVISIONING_URL", defaults[bridge]).rstrip("/")
                    req = Request(base + "/_matrix/provision/v3/whoami?" + urlencode({"user_id": actor}),
                                  headers={"Authorization": authorization})
                    with urlopen(req, timeout=12) as response:
                        data = json.load(response)
                    logins = data.get("logins")
                    if not isinstance(logins, list):
                        return self.send_json(502, {"error": "invalid_bridge_response"})
                    ids = set()
                    for row in logins:
                        if not isinstance(row, dict):
                            continue
                        login_id = row.get("id")
                        if isinstance(login_id, str) and 0 < len(login_id) <= 1024:
                            ids.add(login_id)
                    for login_id in sorted(ids):
                        claim(conn, bridge, login_id, actor)
            return self.send_json(200, {"registered": len(ids)})
        except OwnershipConflict:
            return self.send_json(409, {"error": "account_ownership_conflict"})
        except PermissionError:
            return self.send_json(403, {"error": "forbidden"})
        except (psycopg2.Error, OSError, ValueError):
            return self.send_json(503, {"error": "backend_unavailable"})

    def confirm_bridge_disconnect(self, bridge):
        """Release a stale ownership record only after a fresh native inventory."""
        from contextlib import closing
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return self.send_json(403, {"error": "forbidden"})
        try:
            actor = verify(authorization[7:])
            login_id = self.body().get("login_id")
            if not isinstance(login_id, str) or not 0 < len(login_id) <= 1024:
                return self.send_json(400, {"error": "invalid_login_id"})
            with closing(psycopg2.connect(DSN)) as conn:
                with conn:
                    owner = require_access(conn, actor, bridge, login_id)
                    # A superuser's own native inventory cannot establish that
                    # another user's login has disappeared.
                    if owner != actor:
                        return self.send_json(409, {"error": "owner_session_required"})
                    defaults = {"whatsapp": "http://mautrix-whatsapp:29318",
                                "telegram": "http://mautrix-telegram:29317"}
                    base = os.getenv(bridge.upper() + "_PROVISIONING_URL", defaults[bridge]).rstrip("/")
                    req = Request(base + "/_matrix/provision/v3/whoami?" + urlencode({"user_id": owner}),
                                  headers={"Authorization": authorization})
                    with urlopen(req, timeout=12) as response:
                        data = json.load(response)
                    logins = data.get("logins") if isinstance(data, dict) else None
                    if not isinstance(logins, list) or any(
                        not isinstance(row, dict) or not isinstance(row.get("id"), str)
                        or not row["id"] for row in logins
                    ):
                        return self.send_json(502, {"error": "invalid_bridge_response"})
                    if any(row["id"] == login_id for row in logins):
                        return self.send_json(409, {"error": "account_still_connected"})
                    release(conn, actor, bridge, login_id)
            return self.send_json(200, {"released": True})
        except PermissionError:
            return self.send_json(403, {"error": "forbidden"})
        except (RegistrationError, ValueError):
            return self.send_json(400, {"error": "invalid_request"})
        except (psycopg2.Error, OSError):
            return self.send_json(503, {"error": "backend_unavailable"})

    def do_POST(self):
        disconnect = re.fullmatch(r"/api/admin/bridge-accounts/(whatsapp|telegram)/confirm-disconnect", urlsplit(self.path).path)
        if disconnect:
            return self.confirm_bridge_disconnect(disconnect.group(1))
        sync = re.fullmatch(r"/api/admin/bridge-accounts/(whatsapp|telegram)/sync", urlsplit(self.path).path)
        if sync:
            return self.sync_bridge_accounts(sync.group(1))
        path = urlsplit(self.path).path
        restart = re.fullmatch(r"/api/admin/bridges/([a-z0-9]+)/restart", path)
        if restart:
            return self.restart_bridge(restart.group(1))
        if path not in ("/api/setup/register", "/api/auth/register", "/api/admin/invites"):
            return self.send_json(404, {"error": "not_found"})
        if not allowed(self.client_address[0]):
            return self.send_json(429, {"error": "rate_limited"})
        try:
            data = self.body()
            with psycopg2.connect(DSN) as conn:
                if path == "/api/admin/invites":
                    actor = self.superuser(conn)
                    token = create_invite(conn, actor, hours=data.get("hours", 24))
                    conn.commit()
                    return self.send_json(201, {"token": token})
                username, password = data.get("username"), data.get("password")
                if not isinstance(username, str) or not USER_RE.fullmatch(username):
                    raise RegistrationError("Invalid username")
                if not isinstance(password, str) or not 12 <= len(password) <= 256:
                    raise RegistrationError("Password must contain 12..256 characters")
                # Serialize account creation and DB claim to prevent bootstrap
                # races. The external Synapse operation is not transactional;
                # clean-install tests must cover failed/abandoned registration.
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_xact_lock(%s)", (0x4D4C414E,))
                initialized = is_bootstrapped(conn)
                if path == "/api/setup/register" and initialized:
                    raise RegistrationError("Initial registration is closed")
                if path == "/api/auth/register" and not initialized:
                    raise RegistrationError("Initial registration required")
                if path == "/api/auth/register":
                    token = data.get("invite")
                    if not isinstance(token, str):
                        raise RegistrationError("Invitation required")
                    digest = hashlib.sha256(token.encode()).digest()
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT 1 FROM matrix_lan_registration_invites "
                            "WHERE token_hash=%s AND consumed_at IS NULL "
                            "AND revoked_at IS NULL AND expires_at>now() FOR UPDATE",
                            (digest,),
                        )
                        if not cur.fetchone():
                            raise RegistrationError("Invalid or expired invitation")
                account = register_synapse(username, password)
                mxid = account["user_id"]
                if mxid != f"@{username}:{SERVER}":
                    raise RegistrationError("Unexpected Matrix user identity")
                if path == "/api/setup/register":
                    bootstrap_first_user(conn, mxid)
                else:
                    redeem_invite(conn, token, mxid)
                conn.commit()
                return self.send_json(201, {
                    "user_id": mxid, "access_token": account["access_token"],
                    "device_id": account.get("device_id"),
                })
        except (RegistrationError, ValueError, KeyError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})
        except PermissionError:
            self.send_json(403, {"error": "forbidden"})
        except (psycopg2.Error, OSError):
            self.send_json(503, {"error": "backend_unavailable"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "8081"))), Handler).serve_forever()
