"""Matrix-LAN onboarding HTTP service.

Requires psycopg2 and MATRIX_LAN_DSN, SYNAPSE_REGISTRATION_SECRET,
MATRIX_SERVER_NAME. Synapse public registration remains disabled.
"""
import hashlib
import hmac
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlsplit

import psycopg2
from registration import (
    RegistrationError, bootstrap_first_user, create_invite,
    is_bootstrapped, redeem_invite, revoke_invite,
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

    def do_GET(self):
        if urlsplit(self.path).path != "/api/setup/status":
            return self.send_json(404, {"error": "not_found"})
        try:
            with psycopg2.connect(DSN) as conn:
                self.send_json(200, {"initialized": is_bootstrapped(conn)})
        except psycopg2.Error:
            self.send_json(503, {"error": "database_unavailable"})

    def do_DELETE(self):
        match = re.fullmatch(r"/api/admin/invites/([1-9][0-9]*)", urlsplit(self.path).path)
        if not match:
            return self.send_json(404, {"error": "not_found"})
        try:
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                raise PermissionError("Missing bearer token")
            actor = verify(auth[7:])
            with psycopg2.connect(DSN) as conn:
                changed = revoke_invite(conn, actor, int(match.group(1)))
            return self.send_json(200 if changed else 404, {"revoked": changed})
        except PermissionError:
            return self.send_json(403, {"error": "forbidden"})
        except (psycopg2.Error, OSError):
            return self.send_json(503, {"error": "backend_unavailable"})

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in ("/api/setup/register", "/api/auth/register", "/api/admin/invites"):
            return self.send_json(404, {"error": "not_found"})
        if not allowed(self.client_address[0]):
            return self.send_json(429, {"error": "rate_limited"})
        try:
            data = self.body()
            with psycopg2.connect(DSN) as conn:
                if path == "/api/admin/invites":
                    actor = verify(self.headers.get("Authorization", "").removeprefix("Bearer "))
                    token = create_invite(conn, actor, hours=data.get("hours", 24))
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
