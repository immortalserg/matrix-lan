"""Authenticated incremental feed for bridged chats."""
import json,sys,threading,time
import chat_sender_names
from urllib.parse import urlencode,parse_qs

_metadata={};_lock=threading.Lock();_slots=threading.BoundedSemaphore(16);_last_membership={}
_bridge_state = ('m.bridge', 'uk.half-shot.bridge', 'm.room.name', 'm.room.create')
STREAM_PLATFORMS = (
    'whatsapp', 'telegram', 'imo', 'twitter', 'tiktok', 'wechat', 'linkedin',
    'email', 'pinduoduo', 'taobao', 'facebook', 'instagram', 'threads', 'ok', 'vk',
)

def sync(api,since=None,timeout=20000,platform='imo'):
    global _last_membership
    filters={'presence':{'not_types':['*']},'account_data':{'not_types':['*']},'room':{
        'state':{'types':[*_bridge_state, 'm.room.member'], 'lazy_load_members':True},
        'timeline':{'limit':50,'types':['m.room.message','com.matrix.imo.status','com.beeper.message_send_status','m.room.member',*_bridge_state]},
        'include_leave':True,
        'ephemeral':{'not_types':['*']},'account_data':{'not_types':['*']}}}
    query={'timeout':str(timeout if since else 0)}
    if since:query['since']=since
    limits=(50, 5, 1) if since else (5, 1)
    for limit in limits:
        filters['room']['timeline']['limit']=limit
        query['filter']=json.dumps(filters,separators=(',',':'))
        try:
            status,data=api.matrix_request('/_matrix/client/v3/sync?'+urlencode(query),timeout=30)
            break
        except api.UpstreamError as exc:
            if exc.code!='response_too_large' or limit==limits[-1]:
                raise
    if status==400 and since and data.get('errcode') in ['M_UNKNOWN_POS','M_INVALID_PARAM']:
        return sync(api,None,0,platform)
    if status!=200:raise api.UpstreamError(502,'matrix_sync_unavailable')
    token=data.get('next_batch')
    if not isinstance(token,str) or not token:raise api.UpstreamError(502,'invalid_sync_response')
    changed=[]
    for room,payload in data.get('rooms',{}).get('join',{}).items():
        events=payload.get('state',{}).get('events',[])
        timeline=payload.get('timeline',{});messages=timeline.get('events',[])
        state_events=events+[e for e in messages if e.get('type') in _bridge_state]
        with _lock:known=room in _metadata;cached=_metadata.get(room)
        has_bridge=any(e.get('type') in ['m.bridge','uk.half-shot.bridge'] for e in state_events)
        if not known or has_bridge:
            state=state_events
            if not has_bridge:
                try:
                    state=api.room_state(room)
                except api.UpstreamError as exc:
                    if exc.status == 404:
                        # The room may have been left after Synapse produced this batch.
                        continue
                    raise
            info=api.bridge_room_info(room,state)
            with _lock:_metadata[room]=info
        else:info=dict(cached) if cached else None
        if not info or info['platform']!=platform:continue
        if platform=='tiktok' and (str(info.get('name','')).strip().casefold() in ('chat','чат','tiktok','') or any(e.get('type')=='m.room.member' for e in events+messages)):
            candidate=api.bridge_room_info(room,state_events+events+messages) if has_bridge else None
            if candidate and str(candidate.get('name','')).strip().casefold() not in ('chat','чат','tiktok',''):
                info=candidate
            else:
                try:
                    full_state=api.room_state(room)
                    candidate=api.bridge_room_info(room,full_state)
                    if candidate and candidate['platform']==platform:info=candidate
                    events=events+full_state
                except api.UpstreamError:pass
        for event in state_events:
            if event.get('type')=='m.room.name' and isinstance(event.get('content',{}).get('name'),str):
                name=event['content']['name']
                if platform!='tiktok' or name.strip().casefold() not in ('chat','чат','tiktok',''):info['name']=name
        with _lock:_metadata[room]=dict(info)
        serialized=[item for event in messages if (item:=api.serialize_message(event,info)) is not None]
        sender_names=chat_sender_names.enrich(api,room,serialized,events+messages)
        if serialized:info['last_message']=serialized[-1]
        unread=payload.get('unread_notifications',{}).get('notification_count',0)
        info['unread_count']=unread if type(unread)is int and unread>=0 else 0
        changed.append({'chat':info,'messages':serialized,'sender_names':sender_names,'status_updates':api.chat_feedback.status_updates(api,messages,platform),
                        'limited':timeline.get('limited') is True,'prev_batch':timeline.get('prev_batch')})
    left=list(data.get('rooms',{}).get('leave',{}))
    for room in left:
        with _lock:_metadata.pop(room,None)
    result={'platform':platform,'next_batch':token,'initial':not bool(since),'rooms':changed,'left':left}
    if not since or time.monotonic()-_last_membership.get(platform,0)>30:
        status,membership=api.matrix_request('/_matrix/client/v3/joined_rooms')
        if status==200 and isinstance(membership.get('joined_rooms'),list):
            result['joined_rooms']=membership['joined_rooms'];_last_membership[platform]=time.monotonic()
    return result

def handle(handler,parsed,api):
    if parsed.path!='/api/v1/sync':return False
    header=handler.headers.get('Authorization','');token=header[7:] if header.startswith('Bearer ') else ''
    if not api.secrets.compare_digest(token,api.API_TOKEN):handler.send_json(401,{'error':'unauthorized'});return True
    query=parse_qs(parsed.query);since=query.get('since',[None])[0]
    platform=query.get('platform',[''])[0]
    if platform not in STREAM_PLATFORMS or since is not None and (len(since)>2048 or any(ord(c)<33 for c in since)):
        handler.send_json(400,{'error':'invalid_sync_request'});return True
    if not _slots.acquire(blocking=False):handler.send_json(429,{'error':'sync_busy'},{'Retry-After':'2'});return True
    try:handler.send_json(200,sync(api,since,platform=platform))
    except api.UpstreamError as exc:handler.send_json(exc.status,{'error':exc.code})
    finally:_slots.release()
    return True
