"""Bounded, authenticated read-only checks of current bridge login state."""
import hashlib
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode

_cache = {}
_lock = threading.Lock()
_slots = threading.BoundedSemaphore(4)
_busy = set()
CACHE_SECONDS = 10
FAILURES = {'BAD_CREDENTIALS','LOGIN_REQUIRED','TRANSIENT_DISCONNECT','UNKNOWN_ERROR',
            'LOGGED_OUT','DISCONNECTED','ERROR','UNAVAILABLE','AUTH_ERROR','BACKOFF'}


def check(app, platform):
    now = time.time()
    with _lock:
        cached = _cache.get(platform)
        if cached and now-cached[0] < CACHE_SECONDS:
            return cached[1]
        if platform in _busy:
            return dict(platform=platform, observed=False, supported=True)
        _busy.add(platform)
    bridge = app.BRIDGES[platform]
    try:
        if not bridge.get('base_url'):
            result = dict(platform=platform, observed=False, supported=False)
        elif not _slots.acquire(blocking=False):
            return dict(platform=platform, observed=False, supported=True)
        else:
            try:
                token = bridge.get('secret') or app.MATRIX_ACCESS_TOKEN
                url = bridge['base_url']+'/_matrix/provision/v3/whoami?'+urlencode({'user_id':app.MATRIX_USER_ID})
                code, raw = app.json_request(url,'GET',{'Authorization':'Bearer '+token},timeout=4)
                if code != 200 or not isinstance(raw,dict) or not isinstance(raw.get('logins'),list):
                    result = dict(platform=platform, observed=False, supported=True)
                else:
                    accounts = []
                    for row in raw['logins']:
                        if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not row['id']:
                            # An incomplete list cannot prove that all accounts recovered.
                            result = dict(platform=platform, observed=False, supported=True)
                            break
                        state = row.get('state')
                        value = (state.get('state_event') if isinstance(state,dict) else state) or row.get('state_event') or 'UNKNOWN'
                        value = value.upper() if isinstance(value,str) else 'UNKNOWN'
                        accounts.append(dict(key=hashlib.sha256(row['id'].encode()).hexdigest(),state=value if value in FAILURES|{'CONNECTED'} else 'UNKNOWN'))
                    else:
                        result = dict(platform=platform,observed=True,supported=True,accounts=accounts)
            except (app.UpstreamError,OSError,TimeoutError):
                result = dict(platform=platform,observed=False,supported=True)
            finally:
                _slots.release()
        with _lock:
            _cache[platform] = (time.time(),result)
        return result
    finally:
        with _lock:
            _busy.discard(platform)


def handle(handler, method, parsed, app):
    if parsed.path != '/api/v1/connection-status':
        return False
    auth = handler.headers.get('Authorization','')
    token = auth[7:] if auth.startswith('Bearer ') else ''
    if not secrets.compare_digest(token,app.API_TOKEN):
        handler.send_json(401,{'error':'unauthorized'});return True
    platform = parse_qs(parsed.query).get('platform',[''])[0]
    if platform not in app.BRIDGES:
        handler.send_json(400,{'error':'invalid_platform'});return True
    if method != 'GET':
        handler.send_json(405,{'error':'method_not_allowed'});return True
    handler.send_json(200,check(app,platform),{'Cache-Control':'no-store'})
    return True
