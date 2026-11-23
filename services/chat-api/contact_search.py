"""Contact search (resolve_identifier) and DM portal creation (create_dm) for Telegram, WhatsApp, and IMO."""
import json
import re
import secrets
import time
from urllib.parse import parse_qs, quote, urlencode

SUPPORTED = ('telegram', 'whatsapp', 'imo')
_acc_cache = {}


def _bridge_auth(api, platform):
    bridge = api.BRIDGES[platform]
    token = api.MATRIX_ACCESS_TOKEN if platform == 'imo' else bridge.get('secret', '')
    if not bridge.get('base_url') or not token:
        raise api.UpstreamError(503, f'{platform}_bridge_not_configured')
    return bridge['base_url'], {'Authorization': 'Bearer ' + token}


def resolve_contact(api, platform, login_id, identifier):
    if platform not in SUPPORTED:
        raise api.UpstreamError(400, 'unsupported_platform')
    login_id = str(login_id or '').strip()
    identifier = str(identifier or '').strip()
    if not login_id or len(login_id) > 120 or not identifier or len(identifier) > 160:
        raise api.UpstreamError(400, 'invalid_input')
    base_url, headers = _bridge_auth(api, platform)
    qs = urlencode({'user_id': api.MATRIX_USER_ID, 'login_id': login_id})
    url = f"{base_url}/_matrix/provision/v3/resolve_identifier/{quote(identifier, safe='')}?{qs}"
    status, data = api.json_request(url, 'GET', headers, body=None, timeout=45)
    return status, data if isinstance(data, dict) else {}


def open_contact_dm(api, payload):
    platform = str(payload.get('platform') or '').strip()
    login_id = str(payload.get('login_id') or '').strip()
    contact_id = str(payload.get('contact_id') or '').strip()
    room_id = str(payload.get('dm_room_mxid') or '').strip()
    if platform not in SUPPORTED or not login_id or not contact_id:
        raise api.UpstreamError(400, 'invalid_input')
    base_url, headers = _bridge_auth(api, platform)
    peer_mxid = ''
    if not room_id:
        qs = urlencode({'user_id': api.MATRIX_USER_ID, 'login_id': login_id})
        url = f"{base_url}/_matrix/provision/v3/create_dm/{quote(contact_id, safe='')}?{qs}"
        status, created = api.json_request(url, 'POST', headers, body={}, timeout=70)
        if status < 200 or status >= 300 or not isinstance(created, dict):
            return status, created if isinstance(created, dict) else {'error': 'create_dm_failed'}
        room_id = str(created.get('dm_room_mxid') or '')
        peer_mxid = str(created.get('mxid') or '')
    if not room_id.startswith('!'):
        raise api.UpstreamError(502, 'invalid_dm_room')
    join_status, joined = api.matrix_request(f"/_matrix/client/v3/rooms/{quote(room_id, safe='')}/join", 'POST', body={})
    if join_status < 200 or join_status >= 300:
        raise api.UpstreamError(502, (joined.get('errcode') if isinstance(joined, dict) else None) or f'matrix_join_{join_status}')
    if peer_mxid.startswith('@'):
        try:
            api.add_direct_room(room_id, peer_mxid)
        except Exception:
            pass
    else:
        try:
            api.add_bridge_conversation_to_home(room_id)
        except Exception:
            pass
    # Wait up to a few seconds for bridge state event to be present in room_state
    chat = None
    for _ in range(8):
        try:
            state = api.room_state(room_id)
            if platform == 'whatsapp':
                try:
                    api.label_whatsapp_room_by_account(room_id, state)
                    state = api.room_state(room_id)
                except Exception:
                    pass
            chat = api.bridge_room_info(room_id, state)
            if chat and chat.get('platform') == platform:
                break
        except Exception:
            pass
        time.sleep(0.6)
    if not chat:
        chat = {
            'room_id': room_id,
            'platform': platform,
            'name': str(payload.get('name') or contact_id),
            'room_type': 'direct',
            'kind': 'direct',
            'account_id': login_id,
        }
    try:
        import chat_sync
        with chat_sync._lock:
            chat_sync._metadata[room_id] = chat
    except Exception:
        pass
    return 200, {'ok': True, 'room_id': room_id, 'chat': chat}


def handle(handler, method, parsed, api):
    if parsed.path not in ('/api/v1/contacts/accounts', '/api/v1/contacts/resolve', '/api/v1/contacts/open'):
        return False
    auth = handler.headers.get('Authorization', '')
    token = auth[7:] if auth.startswith('Bearer ') else ''
    if not secrets.compare_digest(token, api.API_TOKEN):
        handler.send_json(401, {'error': 'unauthorized'})
        return True
    if parsed.path == '/api/v1/contacts/accounts':
        if method != 'GET':
            handler.send_json(405, {'error': 'method_not_allowed'})
            return True
        platform = parse_qs(parsed.query).get('platform', [''])[0]
        if platform not in SUPPORTED:
            handler.send_json(400, {'error': 'unsupported_platform'})
            return True
        now = time.time()
        cached = _acc_cache.get(platform)
        if cached and now - cached[0] < 10:
            handler.send_json(200, {'platform': platform, 'accounts': cached[1]})
            return True
        try:
            accounts = [{k: v for k, v in a.items() if k != 'space_room'} for a in api.list_bridge_accounts(platform)]
            _acc_cache[platform] = (now, accounts)
            handler.send_json(200, {'platform': platform, 'accounts': accounts})
        except api.UpstreamError as exc:
            handler.send_json(exc.status, {'error': exc.code})
        return True
    if parsed.path == '/api/v1/contacts/resolve':
        if method != 'GET':
            handler.send_json(405, {'error': 'method_not_allowed'})
            return True
        q = parse_qs(parsed.query)
        try:
            status, data = resolve_contact(
                api,
                q.get('platform', [''])[0],
                q.get('login_id', [''])[0],
                q.get('identifier', [''])[0],
            )
            handler.send_json(status, data)
        except api.UpstreamError as exc:
            handler.send_json(exc.status, {'error': exc.code})
        return True
    if parsed.path == '/api/v1/contacts/open':
        if method != 'POST':
            handler.send_json(405, {'error': 'method_not_allowed'})
            return True
        try:
            length = int(handler.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096:
                raise ValueError()
            payload = json.loads(handler.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError()
        except (ValueError, UnicodeError):
            handler.send_json(400, {'error': 'invalid_json'})
            return True
        try:
            status, data = open_contact_dm(api, payload)
            handler.send_json(status, data)
        except api.UpstreamError as exc:
            handler.send_json(exc.status, {'error': exc.code})
        return True
    return False
