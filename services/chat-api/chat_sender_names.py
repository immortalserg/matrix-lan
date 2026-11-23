"""Bounded room membership cache; display names never determine message direction."""
import threading,time
from collections import OrderedDict
from urllib.parse import quote

_rooms=OrderedDict();_lock=threading.RLock();MAX_ROOMS=256;MAX_MEMBERS=4096


def clean(value):
    if not isinstance(value,str):return ''
    return ''.join(c for c in value if ord(c)>=32 and ord(c)!=127 and c not in '\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069').strip()[:120]


def observe(room,events,complete=False):
    with _lock:
        state=_rooms.setdefault(room,{'names':{},'checked':0})
        _rooms.move_to_end(room)
        for event in events:
            if not isinstance(event,dict) or event.get('type')!='m.room.member':continue
            sender=event.get('state_key');content=event.get('content')
            if not isinstance(sender,str) or not sender.startswith('@') or len(sender)>255 or not isinstance(content,dict):continue
            name=clean(content.get('displayname'))
            if name and len(state['names'])<MAX_MEMBERS:state['names'][sender]=name
            elif not name:state['names'].pop(sender,None)
        if complete:state['checked']=time.monotonic()
        while len(_rooms)>MAX_ROOMS:_rooms.popitem(last=False)
        return dict(state['names'])


def enrich(api,room,messages,events=(),complete=False):
    names=observe(room,events,complete)
    missing=any(m.get('sender') and m['sender'] not in names for m in messages)
    with _lock:
        state=_rooms[room];check=missing and time.monotonic()-state['checked']>60
        if check:state['checked']=time.monotonic()
    if check:
        try:
            status,events=api.matrix_request('/_matrix/client/v3/rooms/'+quote(room,safe='')+'/state',timeout=5,preserve_list=True)
            if status==200 and isinstance(events,list):names=observe(room,events,True)
        except api.UpstreamError:pass
    for message in messages:
        name=names.get(message.get('sender'))
        if name:message['sender_name']=name
    for sender,name in list(names.items()):
        suffix=' (LinkedIn)' if sender.startswith('@linkedin_') else ' (WeChat)' if sender.startswith('@wechat_') else ''
        if suffix and name.endswith(suffix):
            names[sender]=name[:-len(suffix)].strip()
            for message in messages:
                if message.get('sender')==sender:message['sender_name']=names[sender]
    return names
