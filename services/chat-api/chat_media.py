"""Room-scoped media API. No credentials or arbitrary download URLs leave the gateway."""
import base64
import hashlib
import json
import re
import subprocess
import tempfile
import threading
import uuid
import array
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.error import HTTPError, URLError

MAX_BYTES = 25 * 1024 * 1024
SLOTS = threading.BoundedSemaphore(2)
TYPES = {'m.image', 'm.audio', 'm.video', 'm.file'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def matrix_binary(api, path, *, data=None, mimetype=None):
    headers = {'Authorization': 'Bearer ' + api.MATRIX_ACCESS_TOKEN}
    if mimetype:
        headers['Content-Type'] = mimetype
    req = Request(api.MATRIX_BASE_URL + path, data=data, headers=headers,
                  method='POST' if data is not None else 'GET')
    try:
        with build_opener(NoRedirect(), ProxyHandler({})).open(req, timeout=60) as r:
            raw = r.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise api.UpstreamError(413, 'media_too_large')
            return raw
    except HTTPError as e:
        status = e.code
        e.close()
        raise api.UpstreamError(404 if status == 404 else 502, 'media_upstream_rejected') from None
    except (URLError, TimeoutError, OSError):
        raise api.UpstreamError(502, 'media_upstream_unreachable') from None


def clean_name(value):
    name = re.split(r'[/\\]', str(value))[-1]
    name = ''.join(c for c in name if ord(c) >= 32 and ord(c) != 127).strip()[:180]
    return name or 'file'


def header_text(h, name, limit, api):
    value = h.headers.get(name, '')
    try:
        raw = base64.b64decode(value, validate=True)
        text = raw.decode('utf-8')
    except (ValueError, UnicodeError):
        raise api.UpstreamError(400, 'invalid_media_metadata') from None
    if len(text) > limit or '\x00' in text:
        raise api.UpstreamError(400, 'invalid_media_metadata')
    return text


def portal(api, room, platform):
    if platform not in api.BRIDGES or not room.startswith('!') or len(room) > 255 or '/' in room or any(ord(c) < 32 for c in room):
        raise api.UpstreamError(400, 'invalid_room_or_platform')
    api.assert_joined_room(room)
    info = api.bridge_room_info(room, api.room_state(room))
    if not info or info['platform'] != platform:
        raise api.UpstreamError(404, 'bridge_room_not_found')
    return info


def voice_ogg(data, api, *, with_waveform=False):
    if len(data) > 10 * 1024 * 1024:
        raise api.UpstreamError(413, 'voice_too_large')
    demuxer = 'ogg' if data.startswith(b'OggS') else 'matroska' if data.startswith(b'\x1aE\xdf\xa3') else 'mov' if data[4:8]==b'ftyp' else ''
    if not demuxer:
        raise api.UpstreamError(415, 'invalid_voice_recording')
    with tempfile.TemporaryDirectory(prefix='chat-voice-') as td:
        source = Path(td) / 'recording'; target = Path(td) / 'voice.ogg'
        source.write_bytes(data)
        try:
            probe = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                                    '-f', demuxer, '-show_entries', 'format=duration', '-of', 'json', str(source)],
                                   capture_output=True, timeout=10, check=True)
            duration = float(json.loads(probe.stdout).get('format', {}).get('duration', 0))
            # WebM from MediaRecorder may not contain a duration. The decoded output is checked below.
            if duration > 121:
                raise api.UpstreamError(400, 'voice_max_120_seconds')
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                            '-f', demuxer, '-i', str(source), '-map', '0:a:0', '-vn', '-t', '121', '-ac', '1', '-ar', '48000',
                            '-c:a', 'libopus', '-b:a', '32k', '-f', 'ogg', str(target)],
                           capture_output=True, timeout=35, check=True)
            probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(target)],
                                   capture_output=True, timeout=10, check=True)
            duration = float(json.loads(probe.stdout)['format']['duration'])
            if not 0 < duration <= 120.6:
                raise api.UpstreamError(400, 'voice_max_120_seconds')
            if not with_waveform:return target.read_bytes(), round(duration * 1000)
            decoded=subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(target),'-ac','1','-ar','8000','-f','s16le','pipe:1'],capture_output=True,timeout=15,check=True).stdout
            samples=array.array('h');samples.frombytes(decoded)
            if sys.byteorder!='little':samples.byteswap()
            bins=min(64,max(1,len(samples)));step=max(1,len(samples)//bins)
            peaks=[max((abs(v) for v in samples[i*step:min(len(samples),(i+1)*step)]),default=0) for i in range(bins)]
            maximum=max(peaks,default=0)
            waveform=[round(v*1024/maximum) if maximum else 0 for v in peaks]
            return target.read_bytes(), round(duration * 1000),waveform
        except (subprocess.SubprocessError, OSError, ValueError, KeyError):
            raise api.UpstreamError(415, 'invalid_voice_recording') from None


def send_media(api, room, platform, data, name, mimetype, caption, voice, key):
    if platform in ('pinduoduo','taobao'):raise api.UpstreamError(400,'media_unsupported')
    portal(api, room, platform)
    if not data or len(data) > MAX_BYTES:
        raise api.UpstreamError(413, 'media_too_large')
    info = {'mimetype': mimetype, 'size': len(data)}
    if voice:
        data, duration, waveform = voice_ogg(data, api, with_waveform=True)
        name = 'voice.ogg'; mimetype = 'audio/ogg'
        info = {'mimetype': mimetype, 'size': len(data), 'duration': duration}
    raw = matrix_binary(api, '/_matrix/media/v3/upload?' + urlencode({'filename': name}), data=data, mimetype=mimetype)
    try:
        uri = json.loads(raw)['content_uri']
        mxc_parts(uri, api)
    except (ValueError, KeyError, TypeError):
        raise api.UpstreamError(502, 'invalid_upload_response') from None
    kind = 'm.audio' if mimetype.startswith('audio/') else 'm.image' if mimetype in {'image/jpeg','image/png','image/gif','image/webp'} else 'm.video' if mimetype in {'video/mp4','video/webm'} else 'm.file'
    content = {'msgtype': kind, 'body': caption or name, 'filename': name, 'url': uri, 'info': info}
    if voice:
        content['org.matrix.msc3245.voice'] = {}
        content['org.matrix.msc1767.audio'] = {'duration': duration,'waveform':waveform}
        content['org.matrix.msc1767.file'] = {'url':uri,'name':name,'mimetype':mimetype,'size':len(data)}
        content['org.matrix.msc1767.text'] = caption or name
    txn = 'lanmedia_' + hashlib.sha256((api.MATRIX_USER_ID+'\0'+room+'\0'+key).encode()).hexdigest()[:32]
    status, result = api.matrix_request('/_matrix/client/v3/rooms/'+quote(room,safe='')+'/send/m.room.message/'+txn, 'PUT', body=content)
    if status not in (200,201,202) or not isinstance(result.get('event_id'),str):
        raise api.UpstreamError(502, 'media_send_failed')
    message=api.serialize_message({'type':'m.room.message','event_id':result['event_id'],'sender':api.MATRIX_USER_ID,
                                   'origin_server_ts':round(time.time()*1000),'content':content})
    return {'status':'accepted_by_matrix','platform':platform,'room_id':room,'event_id':result['event_id'],'message':message}


def mxc_parts(uri, api):
    match = re.fullmatch(r'mxc://([A-Za-z0-9.:-]{1,255})/([A-Za-z0-9_-]{1,255})', uri if isinstance(uri,str) else '')
    if not match or match[1] != api.MATRIX_USER_ID.split(':',1)[1]:
        raise api.UpstreamError(415, 'unsupported_media_origin')
    return match[1], match[2]


def download(api, room, platform, event):
    portal(api, room, platform)
    if not event.startswith('$') or len(event)>256 or any(ord(c)<33 for c in event):
        raise api.UpstreamError(400,'invalid_event')
    status, data = api.matrix_request('/_matrix/client/v3/rooms/'+quote(room,safe='')+'/event/'+quote(event,safe=''))
    if status != 200 or data.get('room_id') != room or data.get('event_id') != event:
        raise api.UpstreamError(404,'media_event_not_found')
    if platform=='wechat':
        import wechat_edits
        data=wechat_edits.for_download(api,data,room)
        if data is None:raise api.UpstreamError(404,'media_event_not_found')
    content = data.get('content',{})
    if data.get('type')!='m.room.message' or content.get('msgtype') not in TYPES or 'file' in content:
        raise api.UpstreamError(415,'unsupported_media_event')
    origin, media = mxc_parts(content.get('url'),api)
    raw = matrix_binary(api,'/_matrix/client/v1/media/download/'+quote(origin,safe='')+'/'+quote(media,safe='')+'?allow_remote=false')
    return raw, clean_name(content.get('filename') or content.get('body') or 'file')


def handle(h, method, parsed, api):
    match = re.fullmatch(r'/api/v1/chats/([^/]+)/media(?:/([^/]+))?',parsed.path)
    if not match:
        return False
    h.close_connection = True
    token = h.headers.get('Authorization','')
    if not token.startswith('Bearer ') or not api.secrets.compare_digest(token[7:],api.API_TOKEN):
        h.send_json(401,{'error':'unauthorized'});return True
    if not SLOTS.acquire(blocking=False):
        h.send_json(503,{'error':'media_busy'},{'Retry-After':'3'});return True
    try:
        room=unquote(match[1]);query=parse_qs(parsed.query);platform=query.get('platform',[''])[0]
        if method=='GET' and match[2]:
            raw,name=download(api,room,platform,unquote(match[2]))
            h.send_response(200)
            h.send_header('Content-Type','application/octet-stream')
            h.send_header('Content-Length',str(len(raw)))
            h.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(name,safe=''))
            h.send_header('X-Media-Name',base64.b64encode(name.encode()).decode())
            h.send_header('Cache-Control','no-store');h.send_header('X-Content-Type-Options','nosniff')
            h.end_headers();h.wfile.write(raw)
        elif method=='POST' and not match[2]:
            portal(api,room,platform)
            try:length=int(h.headers.get('Content-Length','0'))
            except ValueError:raise api.UpstreamError(400,'invalid_body_size')
            if h.headers.get('Transfer-Encoding') or not 0<length<=MAX_BYTES:
                raise api.UpstreamError(413,'invalid_body_size')
            name=clean_name(header_text(h,'X-Media-Name',180,api))
            caption=header_text(h,'X-Media-Caption',2000,api)
            voice=h.headers.get('X-Media-Voice')=='1'
            mime=h.headers.get('Content-Type','application/octet-stream').split(';')[0].strip().lower()
            if not re.fullmatch(r'[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+',mime):
                raise api.UpstreamError(400,'invalid_media_type')
            key=h.headers.get('Idempotency-Key','')
            if not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',key):
                raise api.UpstreamError(400,'invalid_idempotency_key')
            h.connection.settimeout(60)
            raw=h.rfile.read(length)
            if len(raw)!=length:raise api.UpstreamError(400,'incomplete_upload')
            result=send_media(api,room,platform,raw,name,mime,caption,voice,key)
            h.send_json(202,result)
        else:h.send_json(405,{'error':'method_not_allowed'})
    except api.UpstreamError as e:
        h.send_json(e.status,{'error':e.code})
    except (TimeoutError, OSError):
        h.send_json(502,{'error':'media_unavailable'})
    finally:SLOTS.release()
    return True
