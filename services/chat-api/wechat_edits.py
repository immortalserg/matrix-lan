"""Resolve only same-room, same-author WeChat edits without moving the message."""
from urllib.parse import quote

def _event(api,room,ident):
    if not isinstance(ident,str) or not ident.startswith('$') or len(ident)>256:return None
    status,value=api.matrix_request('/_matrix/client/v3/rooms/'+quote(room,safe='')+'/event/'+quote(ident,safe=''))
    return value if status==200 and isinstance(value,dict) and value.get('event_id')==ident and value.get('room_id')==room else None

def _apply(original,edit,room):
    content=edit.get('content',{})
    if not isinstance(content,dict):return None
    relation=content.get('m.relates_to',{});replacement=content.get('m.new_content')
    if not isinstance(relation,dict) or original.get('type')!='m.room.message' or not original.get('sender'):return None
    if (edit.get('type')!='m.room.message' or edit.get('room_id',room)!=room or edit.get('sender')!=original.get('sender')
        or relation.get('rel_type')!='m.replace' or relation.get('event_id')!=original.get('event_id')
        or not isinstance(replacement,dict) or replacement.get('msgtype') not in ('m.text','m.notice','m.image','m.audio','m.video','m.file')):return None
    return dict(original,content=replacement)

def resolve(api,event,room):
    if not isinstance(event,dict):return None
    if not isinstance(room,str) or not room.startswith('!'):return event
    content=event.get('content') or {}
    if not isinstance(content,dict):return None
    relation=content.get('m.relates_to') or {};edit=None
    if not isinstance(relation,dict):return None
    if relation.get('rel_type')=='m.replace':
        original=_event(api,room,relation.get('event_id'))
        if not original or _apply(original,event,room) is None:return None
        edit=event;event=original
    unsigned=event.get('unsigned') or {};relations=unsigned.get('m.relations',{}) if isinstance(unsigned,dict) else {}
    bundled=relations.get('m.replace') or {} if isinstance(relations,dict) else {}
    if isinstance(bundled,dict) and bundled.get('event_id'):
        latest=_event(api,room,bundled['event_id'])
        if latest and _apply(event,latest,room):edit=latest
    if edit:return _apply(event,edit,room)
    return event

def for_download(api,event,room):
    resolved=resolve(api,event,room)
    if resolved and resolved.get('content',{}).get('msgtype') in ('m.image','m.audio','m.video','m.file'):return resolved
    status,data=api.matrix_request('/_matrix/client/v1/rooms/'+quote(room,safe='')+'/relations/'+quote(event['event_id'],safe='')+'/m.replace/m.room.message?dir=b&limit=10')
    if status==200:
        for edit in data.get('chunk',[]):
            changed=_apply(event,edit,room)
            if changed:return changed
    return resolved
