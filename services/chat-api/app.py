#!/usr/bin/env python3
"""Small LAN-only Matrix-to-mautrix outbound messaging adapter."""
from __future__ import annotations

import sys
import chat_media
import chat_feedback
import connection_status
import chat_sync
import chat_sender_names
import chat_direct
import contact_search
import hashlib
import json
import math
import os
import re
import secrets
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit
from urllib.request import Request, urlopen


HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "8080"))
API_TOKEN = os.environ.get("API_BEARER_TOKEN", "")
MATRIX_USER_ID = os.environ.get("MATRIX_USER_ID", "__MATRIX_OWNER__")
MATRIX_ACCESS_TOKEN = os.environ.get("MATRIX_ACCESS_TOKEN", "")
MATRIX_BASE_URL = os.environ.get("MATRIX_BASE_URL", "http://synapse:8008").rstrip("/")
HISTORY_CUTOFF_MS = max(0, int(os.getenv("MATRIX_HISTORY_CUTOFF_MS", "0")))
MAX_BODY_BYTES = 16_384
MAX_TEXT_CHARS = 4_096
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))

BRIDGES = {
    'threads':{'base_url':'http://matrix-threads:29393','secret':'','bot_mxid':'@threadsbot:__SERVER_NAME__','ghost_prefix':'@threads_'},
    'ok':{'base_url':'http://matrix-ok:29393','secret':'','bot_mxid':'@okbot:__SERVER_NAME__','ghost_prefix':'@ok_'},
    'vk':{'base_url':'http://matrix-vk:29393','secret':'','bot_mxid':'@vkbot:__SERVER_NAME__','ghost_prefix':'@vk_'},
    'facebook':{'base_url':'http://mautrix-facebook:29319','secret':'','bot_mxid':'@facebookbot:__SERVER_NAME__','ghost_prefix':'@facebook_'},
    'instagram':{'base_url':'http://mautrix-instagram:29330','secret':'','bot_mxid':'@instagrambot:__SERVER_NAME__','ghost_prefix':'@instagram_'},
    'pinduoduo':{'base_url':'http://matrix-pinduoduo:29392','secret':'','bot_mxid':'@pinduoduobot:__SERVER_NAME__','ghost_prefix':'@pinduoduo_'},
    'taobao':{'base_url':'http://matrix-taobao:29392','secret':'','bot_mxid':'@taobaobot:__SERVER_NAME__','ghost_prefix':'@taobao_'},
    "email": {"base_url":"","secret":"","bot_mxid":"@gmailbot:__SERVER_NAME__","ghost_prefix":"@gmail_"},
    "linkedin": {"base_url":"","secret":"","bot_mxid":"@linkedinbot:__SERVER_NAME__","ghost_prefix":"@linkedin_"},
    "wechat": {"base_url":"http://wechat-native:29391","secret":"","bot_mxid":"@wechatbot:__SERVER_NAME__","ghost_prefix":"@wechat_"},
    "tiktok": {"base_url":"","secret":"","bot_mxid":"@tiktokbot:__SERVER_NAME__","ghost_prefix":"@tiktok_"},
    "twitter": {"base_url":"http://mautrix-twitter:29327","secret":"","bot_mxid":"@twitterbot:__SERVER_NAME__","ghost_prefix":"@twitter_"},
    "imo": {"base_url": "http://matrix-imo:29390", "secret": "", "bot_mxid": "@imobot:__SERVER_NAME__", "ghost_prefix": "@imo_"},
    "whatsapp": {
        "base_url": os.environ.get("WHATSAPP_PROVISIONING_URL", "http://mautrix-whatsapp:29318").rstrip("/"),
        "secret": os.environ.get("WHATSAPP_PROVISIONING_SECRET", ""),
        "bot_mxid": os.environ.get("WHATSAPP_BOT_MXID", "@whatsappbot:__SERVER_NAME__"),
        "ghost_prefix": "@whatsapp_",
    },
    "telegram": {
        "base_url": os.environ.get("TELEGRAM_PROVISIONING_URL", "http://mautrix-telegram:29317").rstrip("/"),
        "secret": os.environ.get("TELEGRAM_PROVISIONING_SECRET", ""),
        "bot_mxid": os.environ.get("TELEGRAM_BOT_MXID", "@telegrambot:__SERVER_NAME__"),
        "ghost_prefix": "@telegram_",
    },
}
BOT_MXIDS = {bridge["bot_mxid"] for bridge in BRIDGES.values()}
WHATSAPP_ACCOUNT_LABELS = [
    label.strip()
    for label in os.environ.get("WHATSAPP_ACCOUNT_LABELS", "Аккаунт 1,Аккаунт 2").split(",")
    if label.strip()
]
_DIRECT_DATA_LOCK = threading.Lock()
_ROOM_NAME_LOCK = threading.Lock()

if not API_TOKEN or not MATRIX_ACCESS_TOKEN:
    raise SystemExit("API_BEARER_TOKEN and MATRIX_ACCESS_TOKEN must be set")
if not MATRIX_USER_ID.startswith("@") or ":" not in MATRIX_USER_ID:
    raise SystemExit("MATRIX_USER_ID must be a full Matrix user ID")

_rate_lock = threading.Lock()
# A busy history mirror must not consume the slots for outgoing messages.
_recent_reads: deque[float] = deque()
_recent_writes: deque[float] = deque()


class UpstreamError(Exception):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code
        super().__init__(code)


def json_request(url: str, method: str, headers: dict[str, str], body: dict | None = None, timeout: int = 45, preserve_list: bool = False) -> tuple[int, dict | list]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request_headers = dict(headers)
    if data is not None:
        request_headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=request_headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read(8_000_001)
            if len(raw)>8_000_000:raise UpstreamError(502,'response_too_large')
            status = response.status
    except HTTPError as exc:
        raw = exc.read(1_000_000)
        status = exc.code
    except (URLError, TimeoutError, OSError):
        raise UpstreamError(502, "upstream_unreachable") from None
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = {}
    if not isinstance(parsed, dict) and not (preserve_list and isinstance(parsed, list)):
        parsed = {}
    return status, parsed


def matrix_request(path: str, method: str = "GET", body: dict | None = None, timeout: int = 30, preserve_list: bool = False) -> tuple[int, dict | list]:
    return json_request(
        f"{MATRIX_BASE_URL}{path}",
        method,
        {"Authorization": f"Bearer {MATRIX_ACCESS_TOKEN}"},
        body=body,
        timeout=timeout,
        preserve_list=preserve_list,
    )


def list_bridge_accounts(platform: str) -> list[dict]:
    """Return safe account choices from the authenticated bridge whoami response."""
    bridge = BRIDGES[platform]
    if platform not in ("imo","wechat","facebook","instagram","threads","ok","vk","pinduoduo","taobao") and not bridge["secret"]:
        raise UpstreamError(503, f"{platform}_bridge_not_configured")
    # Provisioning shared-secret auth requires the acting Matrix user in the query.
    # Use the same proven auth method as create_dm, not the homeserver access token.
    whoami_url = f"{bridge['base_url']}/_matrix/provision/v3/whoami?{urlencode({'user_id': MATRIX_USER_ID})}"
    status, data = json_request(
        whoami_url,
        "GET",
        {"Authorization": "Bearer " + (MATRIX_ACCESS_TOKEN if platform in ("imo","wechat","facebook","instagram","threads","ok","vk","pinduoduo","taobao") else bridge['secret'])},
        body=None,
    )
    if status < 200 or status >= 300:
        raise UpstreamError(502, data.get("errcode") or f"bridge_http_{status}")
    raw_logins = data.get("logins", [])
    if not isinstance(raw_logins, list):
        raise UpstreamError(502, "invalid_bridge_account_list")
    accounts = []
    for index, login in enumerate(raw_logins, 1):
        if not isinstance(login, dict):
            continue
        login_id = login.get("id")
        if not isinstance(login_id, str) or not login_id:
            continue
        state = login.get("state", {})
        profile = login.get("profile", {})
        name = login.get("name")
        if not isinstance(name, str) or not name.strip():
            name = profile.get("phone") if isinstance(profile, dict) else None
        if not isinstance(name, str) or not name.strip():
            name = f"{platform} account"
        accounts.append({
            "login_id": login_id,
            "name": name,
            "label": (
                WHATSAPP_ACCOUNT_LABELS[index - 1]
                if platform == "whatsapp" and index <= len(WHATSAPP_ACCOUNT_LABELS)
                else f"Аккаунт {index}" if platform == "whatsapp" else name
            ),
            "status": state.get("state_event", "UNKNOWN") if isinstance(state, dict) else "UNKNOWN",
            "space_room": login.get("space_room"),
        })
    return accounts


def add_direct_room(room_id: str, peer_mxid: str) -> None:
    """Add a room to the account's m.direct map without losing other entries."""
    if not room_id.startswith("!") or not peer_mxid.startswith("@"):
        raise UpstreamError(502, "invalid_direct_chat_identity")
    user_path = quote(MATRIX_USER_ID, safe="")
    path = f"/_matrix/client/v3/user/{user_path}/account_data/m.direct"
    with _DIRECT_DATA_LOCK:
        status, data = matrix_request(path)
        if status == 404:
            data = {}
        elif status < 200 or status >= 300:
            raise UpstreamError(502, data.get("errcode") or f"matrix_http_{status}")
        if not isinstance(data, dict):
            data = {}
        room_list = data.get(peer_mxid, [])
        if not isinstance(room_list, list):
            room_list = []
        if room_id in room_list:
            return
        data[peer_mxid] = room_list + [room_id]
        status, result = matrix_request(path, "PUT", body=data)
        if status < 200 or status >= 300:
            raise UpstreamError(502, result.get("errcode") or f"matrix_http_{status}")


def whatsapp_account_label_for_room(room_id: str, state: list, accounts: list[dict]) -> str | None:
    """Match a WhatsApp portal to its per-login Space and friendly label."""
    space_labels = {
        account["space_room"]: account["label"]
        for account in accounts
        if isinstance(account.get("space_room"), str) and account["space_room"].startswith("!")
    }
    if not space_labels:
        return None
    account_label = next(
        (
            space_labels[event.get("state_key")]
            for event in state
            if event.get("type") == "m.space.parent"
            and event.get("state_key") in space_labels
        ),
        None,
    )
    # Some bridges only publish the child relation in the parent Space.
    if account_label is None:
        for space_id, label in space_labels.items():
            space_path = quote(space_id, safe="")
            status, space_state = matrix_request(
                f"/_matrix/client/v3/rooms/{space_path}/state",
                preserve_list=True,
            )
            if status == 200 and isinstance(space_state, list) and any(
                event.get("type") == "m.space.child" and event.get("state_key") == room_id
                for event in space_state
            ):
                account_label = label
                break
    return account_label


def label_whatsapp_room_by_account(room_id: str, state: list) -> bool:
    """Prefix the room name with its WhatsApp login label using per-login Spaces."""
    is_whatsapp = any(
        isinstance(event.get("content"), dict)
        and isinstance(event["content"].get("protocol"), dict)
        and event["content"]["protocol"].get("id") == "whatsapp"
        for event in state
    )
    if not is_whatsapp:
        return False
    try:
        account_label = whatsapp_account_label_for_room(
            room_id, state, list_bridge_accounts("whatsapp")
        )
    except UpstreamError:
        return False
    if account_label is None:
        return False

    room_name = next(
        (
            event.get("content", {}).get("name")
            for event in state
            if event.get("type") == "m.room.name"
            and isinstance(event.get("content"), dict)
            and isinstance(event["content"].get("name"), str)
        ),
        "WhatsApp chat",
    )
    base_name = re.sub(r"^\[Аккаунт \d+\]\s*", "", room_name).strip() or "WhatsApp chat"
    prefixed_name = f"[{account_label}] {base_name}"
    if room_name == prefixed_name:
        return True

    room_path = quote(room_id, safe="")
    with _ROOM_NAME_LOCK:
        status, result = matrix_request(
            f"/_matrix/client/v3/rooms/{room_path}/state/m.room.name",
            "PUT",
            body={"name": prefixed_name},
        )
    if status < 200 or status >= 300:
        code = result.get("errcode") or f"matrix_http_{status}"
        print(f"Could not apply a WhatsApp account label ({code})", flush=True)
        return False
    return True


def add_bridge_conversation_to_home(room_id: str) -> bool:
    """Add a bridged conversation (DM or group) to Element Home, but skip Matrix Spaces."""
    room_path = quote(room_id, safe="")
    status, state = matrix_request(
        f"/_matrix/client/v3/rooms/{room_path}/state",
        preserve_list=True,
    )
    if status != 200 or not isinstance(state, list):
        return False
    if not any("bridge" in str(event.get("type", "")).lower() for event in state):
        return False
    # Do not add the bridge's per-account organizational Spaces to the chat list.
    if any(
        event.get("type") == "m.room.create"
        and isinstance(event.get("content"), dict)
        and event["content"].get("type") == "m.space"
        for event in state
    ):
        return False
    status, members = matrix_request(f"/_matrix/client/v3/rooms/{room_path}/joined_members")
    joined = members.get("joined", {}) if status == 200 else {}
    if not isinstance(joined, dict):
        return False
    peers = sorted(
        mxid for mxid in joined
        if mxid != MATRIX_USER_ID and mxid not in BOT_MXIDS
    )
    # The user opted to include groups too. Use a stable remote member as m.direct's key.
    if peers:
        peer_mxid = peers[0]
    else:
        joined_bots = sorted(mxid for mxid in joined if mxid in BOT_MXIDS)
        if not joined_bots:
            return False
        peer_mxid = joined_bots[0]
    add_direct_room(room_id, peer_mxid)
    label_whatsapp_room_by_account(room_id, state)
    return True


def backfill_direct_rooms() -> None:
    """On startup, add joined bridge conversation rooms (including groups) to Element Home."""
    status, data = matrix_request("/_matrix/client/v3/joined_rooms")
    if status != 200:
        print("Matrix direct-room scan failed", flush=True)
        return
    rooms = data.get("joined_rooms", [])
    marked = 0
    for room_id in rooms if isinstance(rooms, list) else []:
        try:
            marked += int(add_bridge_conversation_to_home(room_id))
        except UpstreamError:
            continue
    if marked:
        print(f"Added {marked} existing bridge conversation(s) to the unified chat list", flush=True)


def process_bridge_invite(room_id: str, invite: dict) -> None:
    """Auto-accept only invitations sent by the configured mautrix bridge bots."""
    invite_state = invite.get("invite_state", {})
    events = invite_state.get("events", []) if isinstance(invite_state, dict) else []
    from_bridge = any(
        isinstance(event, dict)
        and event.get("type") == "m.room.member"
        and event.get("state_key") == MATRIX_USER_ID
        and event.get("sender") in BOT_MXIDS
        and isinstance(event.get("content"), dict)
        and event["content"].get("membership") == "invite"
        for event in events
    )
    if not from_bridge:
        return
    room_path = quote(room_id, safe="")
    status, result = matrix_request(
        f"/_matrix/client/v3/rooms/{room_path}/join", "POST", body=None
    )
    if status < 200 or status >= 300:
        code = result.get("errcode") or f"matrix_http_{status}"
        print(f"Could not auto-join a bridge invite ({code})", flush=True)
        return
    # The bridge may add the remote ghost shortly after the join event.
    for _ in range(10):
        try:
            if add_bridge_conversation_to_home(room_id):
                print("Auto-joined a bridge conversation and added it to the unified chat list", flush=True)
                return
        except UpstreamError:
            pass
        time.sleep(1)
    print("Auto-joined a bridge room; it was not added to the unified chat list", flush=True)


def invite_sync_worker() -> None:
    """Watch Matrix sync for new bridge invitations and accept only WhatsApp/Telegram bot invites."""
    backfill_direct_rooms()
    since = None
    while True:
        query = {"timeout": "30000"}
        if since:
            query["since"] = since
        status, data = matrix_request(
            "/_matrix/client/v3/sync?" + urlencode(query),
            timeout=40,
        )
        if status != 200:
            code = data.get("errcode") or f"matrix_http_{status}"
            print(f"Matrix invite watcher sync failed ({code}); retrying", flush=True)
            time.sleep(5)
            continue
        since = data.get("next_batch", since)
        rooms = data.get("rooms", {})
        invite_rooms = rooms.get("invite", {})
        if isinstance(invite_rooms, dict):
            for room_id, invite in invite_rooms.items():
                if isinstance(invite, dict):
                    try:
                        process_bridge_invite(room_id, invite)
                    except UpstreamError as exc:
                        print(f"Bridge-invite processing failed ({exc.code})", flush=True)

        # Re-apply Home membership and account labels when bridge metadata/room names change.
        joined_updates = rooms.get("join", {})
        if isinstance(joined_updates, dict):
            for room_id, room in joined_updates.items():
                if not isinstance(room, dict):
                    continue
                state_part = room.get("state", {})
                timeline_part = room.get("timeline", {})
                state_events = state_part.get("events", []) if isinstance(state_part, dict) else []
                timeline_events = timeline_part.get("events", []) if isinstance(timeline_part, dict) else []
                events = (state_events if isinstance(state_events, list) else []) + (
                    timeline_events if isinstance(timeline_events, list) else []
                )
                relevant = any(
                    isinstance(event, dict)
                    and (
                        "bridge" in str(event.get("type", "")).lower()
                        or event.get("type") in ("m.room.name", "m.space.parent")
                    )
                    for event in events
                )
                if relevant:
                    try:
                        add_bridge_conversation_to_home(room_id)
                    except UpstreamError as exc:
                        print(f"Bridge room metadata processing failed ({exc.code})", flush=True)


def room_state(room_id: str) -> list:
    room_path = quote(room_id, safe="")
    status, state = matrix_request(
        f"/_matrix/client/v3/rooms/{room_path}/state",
        preserve_list=True,
    )
    if status != 200 or not isinstance(state, list):
        raise UpstreamError(404 if status == 404 else 502, "room_state_unavailable")
    return state


def assert_joined_room(room_id: str) -> None:
    status, data = matrix_request("/_matrix/client/v3/joined_rooms")
    joined_rooms = data.get("joined_rooms", [])
    if status != 200 or not isinstance(joined_rooms, list):
        raise UpstreamError(502, data.get("errcode") or f"matrix_http_{status}")
    if room_id not in joined_rooms:
        raise UpstreamError(404, "room_not_joined")


def linkedin_own_mxid(account):
    # LinkedIn profile IDs use ASCII letters, digits, underscore and hyphen.
    # mautrix escapes uppercase with an underscore and doubles literal underscores.
    if not isinstance(account,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}',account):return ''
    local=''.join('__' if c=='_' else '_'+c.lower() if 'A'<=c<='Z' else c for c in account)
    return '@linkedin_'+local+':'+MATRIX_USER_ID.split(':',1)[1]


def email_own_mxid(account):
    if not isinstance(account,str) or not account.startswith('email:') or len(account)>260 or any(ord(c)<33 for c in account):return ''
    local=''.join('__' if c=='_' else '_'+c.lower() if 'A'<=c<='Z' else c if c in 'abcdefghijklmnopqrstuvwxyz0123456789.-' else ''.join('=%02x'%b for b in c.encode()) for c in account)
    return '@gmail_'+local+':'+MATRIX_USER_ID.split(':',1)[1]


def bridge_room_info(room_id: str, state: list, whatsapp_accounts: list[dict] | None = None) -> dict | None:
    """Parse bridge metadata and omit organizational Matrix Spaces."""
    if any(
        event.get("type") == "m.room.create"
        and isinstance(event.get("content"), dict)
        and event["content"].get("type") == "m.space"
        for event in state
    ):
        return None
    for event in state:
        content = event.get("content", {})
        if not isinstance(content, dict) or "bridge" not in str(event.get("type", "")).lower():
            continue
        protocol = content.get("protocol", {})
        channel = content.get("channel", {})
        platform = protocol.get("id") if isinstance(protocol, dict) else None
        if platform=='github.com/httpjamesm/matrix-tiktok':platform='tiktok'
        if platform=='facebookgo':platform='facebook'
        if platform=='instagramgo':platform='instagram'
        if platform not in BRIDGES or not isinstance(channel, dict):
            continue
        room_name = next(
            (
                e.get("content", {}).get("name")
                for e in state
                if e.get("type") == "m.room.name"
                and isinstance(e.get("content"), dict)
                and isinstance(e["content"].get("name"), str)
            ),
            None,
        ) or channel.get("displayname") or channel.get("name") or "Chat"
        room_type = channel.get("type") if isinstance(channel.get("type"), str) else "unknown"
        channel_id = channel.get("id")
        if room_type == "unknown" and isinstance(channel_id, str):
            if channel_id.endswith("@g.us"):
                room_type = "group"
            elif channel_id.endswith(("@s.whatsapp.net", "@lid")):
                room_type = "direct"
        if platform in ('whatsapp','telegram'):
            kind=content.get('com.beeper.room_type.v2') or content.get('com.beeper.room_type') or room_type
            if kind in ('space','personal_filtering_space'):return None
            if kind in ('dm','direct'):room_type=room_type if room_type in ('dm','direct') else 'direct'
            elif kind in ('group','group_dm'):room_type='group'
            elif kind=='channel':room_type='channel'
            elif platform=='telegram' and isinstance(channel_id,str):
                if channel_id.startswith('user:'):room_type='direct'
                elif channel_id.startswith('chat:'):room_type='group'
                elif channel_id.startswith('channel:'):room_type='channel'
        info = {
            "room_id": room_id,
            "platform": platform,
            "name": room_name,
            "room_type": room_type,
        }
        if platform=='tiktok':
            receiver=channel.get('fi.mau.receiver')
            if isinstance(receiver,(str,int)) and not isinstance(receiver,bool):
                account=str(receiver)[:120];info.update(account_id=account,account_label='TikTok · '+account[-6:],account_code='T'+account[-4:])
                own='@tiktok_'+account+':'+MATRIX_USER_ID.split(':',1)[1]
                peers=[]
                for member in state:
                    sender=member.get('state_key','');profile=member.get('content',{})
                    if member.get('type')!='m.room.member' or not isinstance(sender,str) or not sender.startswith('@tiktok_') or sender==own or not isinstance(profile,dict) or profile.get('membership') not in ('join','invite'):continue
                    name=chat_sender_names.clean(profile.get('displayname'))
                    if name and name not in peers:peers.append(name)
                if str(room_name).strip().casefold() in ('chat','чат','tiktok','') and peers:
                    info['name']=', '.join(peers[:3])+('…' if len(peers)>3 else '')
            kind=content.get('com.beeper.room_type.v2') or content.get('com.beeper.room_type') or room_type
            info['room_type']='group' if kind in ('group','group_dm') else 'direct'
        if platform=='twitter':
            receiver=channel.get('fi.mau.receiver')
            if isinstance(receiver,(str,int)) and not isinstance(receiver,bool):
                account=str(receiver)[:120];info.update(account_id=account,account_label='X · '+account[-6:],account_code='X'+account[-4:])
                own='@twitter_'+account+':'+MATRIX_USER_ID.split(':',1)[1]
                peers={e.get('state_key') for e in state if e.get('type')=='m.room.member' and e.get('content',{}).get('membership')=='join' and isinstance(e.get('state_key'),str) and e['state_key'].startswith('@twitter_') and e['state_key']!=own}
                info['room_type']='group' if len(peers)>1 or channel.get('type')=='group' or '-' not in str(channel_id or '') else 'direct'
        if platform=='imo':
            receiver=channel.get('fi.mau.receiver') or channel.get('com.matrix.imo.account_id')
            if isinstance(receiver,(str,int)) and not isinstance(receiver,bool):
                account=str(receiver)[:120]
                info.update(account_id=account,account_label=str(channel.get('com.matrix.imo.account_label') or 'IMO · '+account[-6:])[:120],account_code='I'+account[-4:])
            info['room_type']='group' if channel.get('com.matrix.imo.kind')=='group' else 'direct'
        if platform in ('threads','ok','vk'):
            receiver=channel.get('fi.mau.receiver')
            if isinstance(receiver,str) and receiver:info.update(account_id=receiver[:120],account_label={'threads':'Threads','ok':'Одноклассники','vk':'ВКонтакте'}[platform]+' · '+receiver[-6:],account_code={'threads':'TH','ok':'OK','vk':'VK'}[platform]+receiver[-4:])
            info['room_type']='group' if channel.get('type')=='group' else 'direct'
        if platform in ('pinduoduo','taobao'):
            receiver=channel.get('fi.mau.receiver')
            if isinstance(receiver,str) and receiver:info.update(account_id=receiver[:120],account_label=('Pinduoduo' if platform=='pinduoduo' else 'Taobao')+' · '+receiver[-6:],account_code=('P' if platform=='pinduoduo' else 'T')+receiver[-3:])
            info['room_type']='direct'
        if platform=='email':
            receiver=channel.get('fi.mau.receiver')
            if isinstance(receiver,str) and receiver:
                info.update(account_id=receiver[:260],account_label=receiver.removeprefix('email:')[:254],account_code='G')
            info['room_type']='direct'
        if platform in ('facebook','instagram'):
            receiver=channel.get('fi.mau.receiver')
            if isinstance(receiver,(str,int)) and str(receiver).isdigit():
                account=str(receiver)[:120];info.update(account_id=account,account_label=('Facebook' if platform=='facebook' else 'Instagram')+' · '+account[-6:],account_code=('F' if platform=='facebook' else 'IG')+account[-4:])
            kind=content.get('com.beeper.room_type.v2') or content.get('com.beeper.room_type') or room_type
            if kind in ('space','personal_filtering_space'):return None
            info['room_type']='group' if kind in ('group','group_dm') else 'direct'
        if platform=='linkedin':
            receiver=channel.get('fi.mau.receiver')
            if isinstance(receiver,str) and receiver:
                account=receiver[:120];info.update(account_id=account,account_label='LinkedIn · '+account[-6:],account_code='L'+account[-4:])
            kind=content.get('com.beeper.room_type.v2') or content.get('com.beeper.room_type') or room_type
            if kind in ('space','personal_filtering_space'):return None
            info['room_type']='group' if kind in ('group','group_dm') else 'direct'
            if str(info['name']).endswith(' (LinkedIn)'):info['name']=str(info['name'])[:-len(' (LinkedIn)')]
        if platform=='wechat':
            receiver=channel.get('fi.mau.receiver')
            if isinstance(receiver,(str,int)) and not isinstance(receiver,bool):
                account=str(receiver)[:120]
                info.update(account_id=account,account_label='WeChat · '+account[-6:],account_code='W'+account[-4:])
            info['room_type']='group' if str(channel_id or '').endswith('@chatroom') else 'direct'
        if platform == "whatsapp" and whatsapp_accounts is not None:
            label = whatsapp_account_label_for_room(room_id, state, whatsapp_accounts)
            if label:
                info["account_label"] = label
                base_name = room_name
                for account in whatsapp_accounts:
                    account_label = account.get("label")
                    if isinstance(account_label, str):
                        base_name = re.sub(rf"^\[{re.escape(account_label)}\]\s*", "", base_name)
                info["name"] = f"[{label}] {base_name}"
        return info
    return None


def serialize_message(event: dict, room_info=None) -> dict | None:
    if isinstance(room_info,dict) and room_info.get('platform')=='wechat':
        import wechat_edits
        event=wechat_edits.resolve(sys.modules[__name__],event,room_info.get('room_id') or event.get('room_id'))
        if event is None:return None
    content = event.get("content", {})
    if event.get("type") != "m.room.message" or not isinstance(content, dict):
        return None
    if isinstance(room_info, dict) and room_info.get('platform') in ('whatsapp','telegram') and HISTORY_CUTOFF_MS:
        timestamp=event.get('origin_server_ts')
        if not isinstance(timestamp,int) or timestamp < HISTORY_CUTOFF_MS:
            return None
    body = content.get("body", "")
    if not isinstance(body, str):
        body = ""
    message = {
        "event_id": event.get("event_id"),
        "sender": event.get("sender"),
        "timestamp": event.get("origin_server_ts"),
        "msgtype": content.get("msgtype", "m.text"),
        "body": body[:4096],
    }
    if isinstance(room_info,dict) and room_info.get('platform')=='email':
        import email_content
        message['body']=email_content.plain_body(content)
    sender = event.get('sender', '')
    if isinstance(sender, str) and sender.startswith('@imo_'):
        marker = content.get('com.matrix.imo', {})
        message['outgoing'] = isinstance(marker, dict) and marker.get('own') is True
    elif isinstance(room_info,dict) and room_info.get('platform') in ('pinduoduo','taobao') and isinstance(sender,str) and sender.startswith(BRIDGES[room_info['platform']]['ghost_prefix']):
        marker=content.get('com.matrix.shopping',{})
        message['outgoing']=isinstance(marker,dict) and marker.get('platform')==room_info['platform'] and marker.get('own') is True
    elif isinstance(room_info,dict) and room_info.get('platform') in ('threads','ok','vk') and isinstance(sender,str) and sender.startswith(BRIDGES[room_info['platform']]['ghost_prefix']):
        marker=content.get('com.matrix.social',{})
        message['outgoing']=isinstance(marker,dict) and marker.get('platform')==room_info['platform'] and marker.get('own') is True
    elif sender == MATRIX_USER_ID:
        message['outgoing'] = True
    elif isinstance(room_info,dict) and room_info.get('platform')=='wechat' and sender==BRIDGES['wechat']['bot_mxid'] and content.get('msgtype') in ('m.text','m.image','m.video','m.audio','m.file'):
        # Native outgoing messages use the bridge intent; incoming contacts use
        # account-scoped @wechat_ ghosts. Service notices remain separate.
        message['outgoing']=True
    message.setdefault('outgoing',False)
    if isinstance(room_info,dict) and room_info.get('platform') in ('twitter','tiktok','facebook','instagram') and room_info.get('account_id'):
        own='@'+room_info['platform']+'_'+str(room_info['account_id'])+':'+MATRIX_USER_ID.split(':',1)[1]
        if sender==own:message['outgoing']=True
    if isinstance(room_info,dict) and room_info.get('platform')=='linkedin':
        own=linkedin_own_mxid(room_info.get('account_id'))
        if own and sender==own:message['outgoing']=True
    if isinstance(room_info,dict) and room_info.get('platform')=='email':
        own=email_own_mxid(room_info.get('account_id'))
        if own and sender==own:message['outgoing']=True
    if message['outgoing']:
        message['delivery_status']='accepted'
    if isinstance(content.get('filename'), str):
        message['filename'] = content['filename']
    if 'org.matrix.msc3245.voice' in content:
        message['voice'] = True
    if isinstance(content.get("url"), str):
        message["url"] = content["url"]
    if isinstance(content.get("info"), dict):
        message["info"] = content["info"]
    return message


def latest_room_message(room_id: str, state=None, room_info=None) -> dict | None:
    room_path = quote(room_id, safe="")
    query = urlencode({"dir": "b", "limit": "30", "filter": json.dumps({"types":["m.room.message"]})})
    status, data = matrix_request(
        f"/_matrix/client/v3/rooms/{room_path}/messages?{query}",
        timeout=30,
    )
    if status != 200:
        return None
    chunk = data.get("chunk", [])
    if not isinstance(chunk, list):
        return None
    for event in chunk:
        if isinstance(event, dict):
            message = serialize_message(event,room_info)
            if message is not None:
                chat_sender_names.enrich(sys.modules[__name__],room_id,[message],state or (),complete=state is not None)
                return message
    return None


def list_bridge_chats(platform: str) -> list[dict]:
    status, data = matrix_request("/_matrix/client/v3/joined_rooms")
    if status != 200:
        raise UpstreamError(502, data.get("errcode") or f"matrix_http_{status}")
    accounts = []
    if platform == "whatsapp":
        try:
            accounts = list_bridge_accounts("whatsapp")
        except UpstreamError:
            accounts = []
    chats = []
    for room_id in data.get("joined_rooms", []):
        try:
            state = room_state(room_id)
            info = bridge_room_info(room_id, state, accounts)
            if info is None or info["platform"] != platform:
                continue
            info["last_message"] = latest_room_message(room_id,state,info)
            chats.append(info)
        except UpstreamError:
            continue
    chats.sort(
        key=lambda chat: (chat.get("last_message") or {}).get("timestamp") or 0,
        reverse=True,
    )
    return chats


def get_chat_messages(room_id: str, platform: str, limit: int, cursor: str | None) -> dict:
    assert_joined_room(room_id)
    state = room_state(room_id)
    accounts = []
    if platform == "whatsapp":
        try:
            accounts = list_bridge_accounts("whatsapp")
        except UpstreamError:
            accounts = []
    info = bridge_room_info(room_id, state, accounts)
    if info is None or info["platform"] != platform:
        raise UpstreamError(404, "bridge_room_not_found")
    room_path = quote(room_id, safe="")
    params = {"dir": "b", "limit": str(limit), "filter": json.dumps({"types":["m.room.message","com.matrix.imo.status","com.beeper.message_send_status"]})}
    if cursor:
        params["from"] = cursor
    status, data = matrix_request(
        f"/_matrix/client/v3/rooms/{room_path}/messages?{urlencode(params)}",
        timeout=30,
    )
    if status != 200:
        raise UpstreamError(502, data.get("errcode") or f"matrix_http_{status}")
    chunk = data.get("chunk", [])
    messages = [
        message
        for event in chunk if isinstance(event, dict)
        for message in [serialize_message(event,info)]
        if message is not None
    ] if isinstance(chunk, list) else []
    messages.reverse()
    sender_names=chat_sender_names.enrich(sys.modules[__name__],room_id,messages,state,complete=True)
    return {
        "platform": platform,
        "room_id": room_id,
        "name": info["name"],
        "messages": messages,
        **({"history_since_ms":HISTORY_CUTOFF_MS} if platform in ("whatsapp","telegram") and HISTORY_CUTOFF_MS else {}),
        "sender_names":sender_names,
        "next_token": data.get("end"),
        "status_updates": chat_feedback.status_updates(sys.modules[__name__],chunk,platform) if isinstance(chunk,list) else [],
    }


def send_matrix_text(room_id: str, text: str, idempotency_key: str) -> str:
    txn_seed = idempotency_key or uuid.uuid4().hex
    txn_id = "lanapi_" + hashlib.sha256(
        (MATRIX_USER_ID + "\0" + room_id + "\0" + txn_seed).encode()
    ).hexdigest()[:32]
    room_path = quote(room_id, safe="")
    txn_path = quote(txn_id, safe="")
    send_path = f"/_matrix/client/v3/rooms/{room_path}/send/m.room.message/{txn_path}"
    status, result = matrix_request(
        send_path,
        "PUT",
        body={"msgtype": "m.text", "body": text},
    )
    event_id = result.get("event_id")
    if status < 200 or status >= 300 or not isinstance(event_id, str):
        raise UpstreamError(502, result.get("errcode") or f"matrix_http_{status}")
    return event_id


def rate_retry_after(*, read: bool = False) -> int:
    """Reserve a slot and return zero, or return seconds until a slot opens."""
    requests = _recent_reads if read else _recent_writes
    with _rate_lock:
        now = time.monotonic()
        while requests and now - requests[0] >= 60:
            requests.popleft()
        if len(requests) >= RATE_LIMIT:
            return max(1, math.ceil(60 - (now - requests[0]))) if requests else 60
        requests.append(now)
        return 0


def allow_rate() -> bool:
    return rate_retry_after() == 0


class Handler(BaseHTTPRequestHandler):
    server_version = "LANChatAPI/1.0"
    sys_version = ""

    # Do not log request paths or bodies: paths contain recipient identifiers.
    def log_message(self, fmt: str, *args) -> None:
        return

    def send_json(self, status: int, payload: dict, extra_headers: dict[str, str] | None = None) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if chat_sync.handle(self,parsed,sys.modules[__name__]):
            return
        if chat_media.handle(self, "GET", parsed, sys.modules[__name__]):
            return
        if connection_status.handle(self, "GET", parsed, sys.modules[__name__]):
            return
        if contact_search.handle(self, "GET", parsed, sys.modules[__name__]):
            return
        if parsed.path == "/api/healthz":
            self.send_json(200, {"status": "ok"})
            return
        account_route = parsed.path == "/api/v1/accounts"
        chats_route = parsed.path == "/api/v1/chats"
        messages_match = re.fullmatch(r"/api/v1/chats/([^/]+)/messages", parsed.path)
        if not account_route and not chats_route and not messages_match:
            self.send_json(404, {"error": "not_found"})
            return

        auth = self.headers.get("Authorization", "")
        supplied_token = auth[7:] if auth.startswith("Bearer ") else ""
        if not secrets.compare_digest(supplied_token, API_TOKEN):
            self.send_json(401, {"error": "unauthorized"})
            return
        retry_after = rate_retry_after(read=True)
        if retry_after:
            self.send_json(429, {"error": "rate_limited"}, {"Retry-After": str(retry_after)})
            return

        query = parse_qs(parsed.query)
        if account_route:
            platform = query.get("platform", [""])[0]
            if platform not in BRIDGES:
                self.send_json(400, {"error": "platform_must_be_whatsapp_or_telegram"})
                return
            try:
                accounts = list_bridge_accounts(platform)
            except UpstreamError as exc:
                self.send_json(502 if exc.status < 500 else exc.status, {
                    "error": "account_list_failed",
                    "upstream_code": exc.code,
                })
                return
            public_accounts = [
                {key: value for key, value in account.items() if key != "space_room"}
                for account in accounts
            ]
            self.send_json(200, {"platform": platform, "accounts": public_accounts})
            return

        platform = query.get("platform", [""])[0]
        if platform not in BRIDGES:
            self.send_json(400, {"error": "platform_must_be_whatsapp_or_telegram"})
            return
        try:
            if chats_route:
                chats = list_bridge_chats(platform)
                self.send_json(200, {"platform": platform, "chats": chats, **({"history_since_ms":HISTORY_CUTOFF_MS} if platform in ("whatsapp","telegram") and HISTORY_CUTOFF_MS else {})})
                return

            room_id = unquote(messages_match.group(1))
            if not room_id.startswith("!") or len(room_id) > 255 or "/" in room_id or any(ord(ch) < 32 for ch in room_id):
                self.send_json(400, {"error": "invalid_room_id"})
                return
            limit_value = query.get("limit", ["50"])[0]
            try:
                limit = int(limit_value)
            except ValueError:
                self.send_json(400, {"error": "invalid_limit"})
                return
            if not 1 <= limit <= 100:
                self.send_json(400, {"error": "limit_must_be_1_to_100"})
                return
            cursor = query.get("from", [None])[0]
            if cursor is not None and (len(cursor) > 2048 or any(ord(ch) < 32 for ch in cursor)):
                self.send_json(400, {"error": "invalid_pagination_token"})
                return
            result = get_chat_messages(room_id, platform, limit, cursor)
            self.send_json(200, result)
        except UpstreamError as exc:
            self.send_json(exc.status, {"error": "chat_read_failed", "upstream_code": exc.code})

    def handle_chat_reply(self, room_id: str) -> None:
        if not room_id.startswith("!") or len(room_id) > 255 or "/" in room_id or any(ord(ch) < 32 for ch in room_id):
            self.send_json(400, {"error": "invalid_room_id"})
            return
        auth = self.headers.get("Authorization", "")
        supplied_token = auth[7:] if auth.startswith("Bearer ") else ""
        if not secrets.compare_digest(supplied_token, API_TOKEN):
            self.send_json(401, {"error": "unauthorized"})
            return
        retry_after = rate_retry_after()
        if retry_after:
            self.send_json(429, {"error": "rate_limited"}, {"Retry-After": str(retry_after)})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "invalid_content_length"})
            return
        if length <= 0 or length > (128*1024):
            self.send_json(413 if length > (128*1024) else 400, {"error": "invalid_body_size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"error": "invalid_json"})
            return
        if not isinstance(payload, dict):
            self.send_json(400, {"error": "json_object_required"})
            return
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 20000:
            self.send_json(400, {"error": "text_must_be_1_to_4096_characters"})
            return
        idem = self.headers.get("Idempotency-Key", "").strip()
        if len(idem) > 200:
            self.send_json(400, {"error": "idempotency_key_too_long"})
            return
        try:
            assert_joined_room(room_id)
            state = room_state(room_id)
            whatsapp_accounts = []
            is_whatsapp = any(
                isinstance(event.get("content"), dict)
                and isinstance(event["content"].get("protocol"), dict)
                and event["content"]["protocol"].get("id") == "whatsapp"
                for event in state
            )
            if is_whatsapp:
                try:
                    whatsapp_accounts = list_bridge_accounts("whatsapp")
                except UpstreamError:
                    whatsapp_accounts = []
            info = bridge_room_info(room_id, state, whatsapp_accounts)
            if info is None:
                self.send_json(404, {"error": "bridge_room_not_found"})
                return
            if info['platform']!='email' and len(text)>MAX_TEXT_CHARS:
                self.send_json(400, {'error':'text_must_be_1_to_4096_characters'});return
            if info['platform'] in ('pinduoduo','taobao') and len(text)>500:
                self.send_json(400,{'error':'text_must_be_1_to_500_characters'});return
            event_id = send_matrix_text(room_id, text, idem)
        except UpstreamError as exc:
            self.send_json(exc.status, {"error": "chat_send_failed", "upstream_code": exc.code})
            return
        self.send_json(202, {
            "status": "accepted_by_matrix",
            "platform": info["platform"],
            "room_id": room_id,
            "event_id": event_id,
        })

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        if contact_search.handle(self, "POST", parsed, sys.modules[__name__]):
            return
        if chat_direct.handle(self, parsed, sys.modules[__name__]):
            return
        if chat_media.handle(self, "POST", parsed, sys.modules[__name__]):
            return
        room_match = re.fullmatch(r"/api/v1/chats/([^/]+)/messages", parsed.path)
        if room_match:
            self.handle_chat_reply(unquote(room_match.group(1)))
            return
        if parsed.path != "/api/v1/messages":
            self.send_json(404, {"error": "not_found"})
            return

        auth = self.headers.get("Authorization", "")
        supplied_token = auth[7:] if auth.startswith("Bearer ") else ""
        if not secrets.compare_digest(supplied_token, API_TOKEN):
            self.send_json(401, {"error": "unauthorized"})
            return

        retry_after = rate_retry_after()
        if retry_after:
            self.send_json(429, {"error": "rate_limited"}, {"Retry-After": str(retry_after)})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "invalid_content_length"})
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self.send_json(413 if length > MAX_BODY_BYTES else 400, {"error": "invalid_body_size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"error": "invalid_json"})
            return
        if not isinstance(payload, dict):
            self.send_json(400, {"error": "json_object_required"})
            return

        platform = payload.get("platform")
        recipient = payload.get("to")
        text = payload.get("text")
        login_id = payload.get("login_id")
        if platform not in BRIDGES:
            self.send_json(400, {"error": "platform_must_be_whatsapp_or_telegram"})
            return
        if login_id is not None:
            if platform != "whatsapp":
                self.send_json(400, {"error": "login_id_currently_supported_for_whatsapp_only"})
                return
            if not isinstance(login_id, str) or not login_id or len(login_id) > 256 or any(ord(ch) < 32 for ch in login_id):
                self.send_json(400, {"error": "invalid_login_id"})
                return
        if not isinstance(recipient, str) or not recipient or len(recipient) > 128:
            self.send_json(400, {"error": "invalid_recipient"})
            return
        if any(ord(ch) < 32 for ch in recipient) or "/" in recipient:
            self.send_json(400, {"error": "invalid_recipient"})
            return
        if platform == "whatsapp" and not re.fullmatch(r"\+[1-9][0-9]{6,14}", recipient):
            self.send_json(400, {"error": "whatsapp_recipient_must_be_e164"})
            return
        if platform == "telegram" and not re.fullmatch(r"(?:\+[1-9][0-9]{6,14}|@?[A-Za-z0-9_]{5,64})", recipient):
            self.send_json(400, {"error": "telegram_recipient_must_be_e164_or_username"})
            return
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            self.send_json(400, {"error": "text_must_be_1_to_4096_characters"})
            return

        bridge = BRIDGES[platform]
        if not bridge["secret"]:
            self.send_json(503, {"error": f"{platform}_bridge_not_configured"})
            return
        if login_id is not None:
            try:
                accounts = list_bridge_accounts(platform)
            except UpstreamError as exc:
                self.send_json(502, {"error": "account_list_failed", "upstream_code": exc.code})
                return
            if not any(account["login_id"] == login_id for account in accounts):
                self.send_json(400, {"error": "unknown_login_id"})
                return

        # Create or retrieve the network DM portal for this Matrix user.
        # login_id is optional: without it the bridge selects its default WhatsApp login.
        query_params = {"user_id": MATRIX_USER_ID}
        if login_id is not None:
            query_params["login_id"] = login_id
        query = urlencode(query_params)
        identifier = quote(recipient, safe="")
        create_url = f"{bridge['base_url']}/_matrix/provision/v3/create_dm/{identifier}?{query}"
        create_status, created = json_request(
            create_url,
            "POST",
            {"Authorization": f"Bearer {bridge['secret']}"},
            body=None,
        )
        room_id = created.get("dm_room_mxid")
        if create_status < 200 or create_status >= 300 or not isinstance(room_id, str) or not room_id.startswith("!"):
            code = created.get("errcode") or f"bridge_http_{create_status}"
            self.send_json(502, {"error": "chat_lookup_or_create_failed", "upstream_code": code})
            return

        # mautrix may invite the Matrix user to a newly-created portal room instead of joining them.
        # Accept that invite through the Matrix Client API so callers never need to click in Element.
        join_room_path = quote(room_id, safe="")
        join_url = f"{MATRIX_BASE_URL}/_matrix/client/v3/rooms/{join_room_path}/join"
        join_status, joined = json_request(
            join_url,
            "POST",
            {"Authorization": f"Bearer {MATRIX_ACCESS_TOKEN}"},
            body=None,
        )
        if join_status < 200 or join_status >= 300:
            code = joined.get("errcode") or f"matrix_http_{join_status}"
            self.send_json(502, {"error": "matrix_join_failed", "upstream_code": code, "room_id": room_id})
            return

        # Add the portal to Element's direct-chat list as well as joining it.
        peer_mxid = created.get("mxid")
        if not isinstance(peer_mxid, str) or not peer_mxid.startswith("@"):
            self.send_json(502, {"error": "bridge_did_not_return_peer_mxid"})
            return
        try:
            add_direct_room(room_id, peer_mxid)
        except UpstreamError as exc:
            self.send_json(502, {"error": "direct_chat_list_update_failed", "upstream_code": exc.code})
            return
        if platform == "whatsapp":
            try:
                label_status, room_state = matrix_request(
                    f"/_matrix/client/v3/rooms/{quote(room_id, safe='')}/state",
                    preserve_list=True,
                )
                if label_status == 200 and isinstance(room_state, list):
                    label_whatsapp_room_by_account(room_id, room_state)
            except UpstreamError:
                pass

        idem = self.headers.get("Idempotency-Key", "").strip()
        if len(idem) > 200:
            self.send_json(400, {"error": "idempotency_key_too_long"})
            return
        txn_seed = idem or uuid.uuid4().hex
        txn_id = "lanapi_" + hashlib.sha256((MATRIX_USER_ID + "\0" + txn_seed).encode()).hexdigest()[:32]
        room_path = quote(room_id, safe="")
        txn_path = quote(txn_id, safe="")
        send_url = f"{MATRIX_BASE_URL}/_matrix/client/v3/rooms/{room_path}/send/m.room.message/{txn_path}"
        send_status, sent = json_request(
            send_url,
            "PUT",
            {"Authorization": f"Bearer {MATRIX_ACCESS_TOKEN}"},
            body={"msgtype": "m.text", "body": text},
        )
        event_id = sent.get("event_id")
        if send_status < 200 or send_status >= 300 or not isinstance(event_id, str):
            code = sent.get("errcode") or f"matrix_http_{send_status}"
            self.send_json(502, {"error": "matrix_send_failed", "upstream_code": code, "room_id": room_id})
            return

        # This confirms Synapse accepted the event. The bridge's delivery to the remote network is asynchronous.
        self.send_json(202, {
            "status": "accepted_by_matrix",
            "platform": platform,
            "room_id": room_id,
            "event_id": event_id,
        })

    def do_PUT(self) -> None:
        self.send_json(405, {"error": "method_not_allowed"}, {"Allow": "GET, POST"})

    def do_DELETE(self) -> None:
        self.send_json(405, {"error": "method_not_allowed"}, {"Allow": "GET, POST"})

    def do_OPTIONS(self) -> None:
        self.send_json(405, {"error": "method_not_allowed"}, {"Allow": "GET, POST"})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    print(f"LAN Chat API listening on {HOST}:{PORT}", flush=True)
    threading.Thread(target=invite_sync_worker, name="matrix-invite-watcher", daemon=True).start()
    Server((HOST, PORT), Handler).serve_forever()
