"""Verified message-author navigation. Never sends a messenger message."""
import hashlib,json,re,secrets,threading
from urllib.parse import quote,unquote,urlencode

_lock=threading.Lock()


def identity(state,platform):
 for e in state:
  c=e.get('content',{})
  if not isinstance(c,dict) or 'bridge' not in str(e.get('type','')).lower():continue
  proto=c.get('protocol',{}).get('id');channel=c.get('channel',{})
  if proto=='github.com/httpjamesm/matrix-tiktok':proto='tiktok'
  if proto=='facebookgo':proto='facebook'
  if proto=='instagramgo':proto='instagram'
  if proto==platform and isinstance(channel,dict):
   receiver=channel.get('fi.mau.receiver') or channel.get('com.matrix.imo.account_id')
   return str(receiver or ''),str(channel.get('id') or '')
 return '', ''


def open_direct(api,room,payload):
 platform=payload.get('platform');sender=payload.get('sender_id');event_id=payload.get('event_id')
 if platform not in api.BRIDGES or not all(isinstance(v,str) and 0<len(v)<=512 for v in (room,sender,event_id)):
  raise api.UpstreamError(400,'invalid_direct_request')
 if not room.startswith('!') or not event_id.startswith('$') or not sender.startswith(api.BRIDGES[platform]['ghost_prefix']):
  raise api.UpstreamError(400,'invalid_direct_request')
 api.assert_joined_room(room);state=api.room_state(room);info=api.bridge_room_info(room,state)
 if not info or info['platform']!=platform:raise api.UpstreamError(404,'bridge_room_not_found')
 status,event=api.matrix_request('/_matrix/client/v3/rooms/'+quote(room,safe='')+'/event/'+quote(event_id,safe=''))
 if status!=200 or event.get('sender')!=sender or event.get('type')!='m.room.message':raise api.UpstreamError(404,'author_not_found')
 message=api.serialize_message(event,info)
 if not message or message.get('outgoing'):raise api.UpstreamError(400,'own_author')
 if info.get('room_type') in ('direct','dm'):return {'ok':True,'chat':info}
 account,channel=identity(state,platform)
 # A receiver is required for multi-account platforms; never guess an account.
 if platform in ('imo','twitter','tiktok','wechat','linkedin','whatsapp') and not account:return {'ok':False,'code':'direct_unavailable'}
 if platform=='imo':
  url=api.BRIDGES[platform]['base_url']+'/_matrix/provision/v3/chats/direct?'+urlencode({'user_id':api.MATRIX_USER_ID,'login_id':account})
  with _lock:
   status,result=api.json_request(url,'POST',{'Authorization':'Bearer '+api.MATRIX_ACCESS_TOKEN},{'room_id':room,'event_id':event_id,'sender_id':sender},timeout=50)
  if status!=200:return {'ok':False,'code':'direct_unavailable'}
  target=result.get('room_id')
  if not isinstance(target,str) or not target.startswith('!'):raise api.UpstreamError(502,'invalid_direct_response')
  status,_=api.matrix_request('/_matrix/client/v3/rooms/'+quote(target,safe='')+'/join','POST',{})
  if status!=200:raise api.UpstreamError(502,'direct_join_failed')
  target_state=api.room_state(target);target_info=api.bridge_room_info(target,target_state)
  peer=result.get('peer_id')
  if not isinstance(peer,str) or sender!='@imo_'+hashlib.sha256(peer.encode()).hexdigest()[:24]+':'+api.MATRIX_USER_ID.split(':',1)[1]:raise api.UpstreamError(502,'invalid_direct_response')
  if not target_info or target_info['platform']!=platform or identity(target_state,platform)!=(account,peer) or target_info.get('room_type')!='direct':
   raise api.UpstreamError(502,'invalid_direct_response')
  return {'ok':True,'chat':target_info}
 # Other bridges: open only an existing private portal on the same account.
 status,joined=api.matrix_request('/_matrix/client/v3/joined_rooms')
 if status!=200:raise api.UpstreamError(502,'matrix_rooms_unavailable')
 import chat_sync
 with chat_sync._lock:cached=dict(chat_sync._metadata)
 for candidate in joined.get('joined_rooms',[]):
  known=cached.get(candidate)
  if candidate==room or candidate in cached and (not known or known.get('platform')!=platform):continue
  s=api.room_state(candidate);i=api.bridge_room_info(candidate,s)
  if not i or i['platform']!=platform or i.get('room_type') not in ('direct','dm'):continue
  receiver,peer=identity(s,platform)
  if receiver!=account:continue
  if platform=='wechat' and sender=='@wechat_'+hashlib.sha256((account+'|'+peer).encode()).hexdigest()[:30]+':'+api.MATRIX_USER_ID.split(':',1)[1]:return {'ok':True,'chat':i}
  if any(e.get('type')=='m.room.member' and e.get('state_key')==sender and e.get('content',{}).get('membership')=='join' for e in s):
   return {'ok':True,'chat':i}
 return {'ok':False,'code':'direct_unavailable'}


def handle(handler,parsed,api):
 match=re.fullmatch(r'/api/v1/chats/([^/]+)/direct',parsed.path)
 if not match:return False
 token=handler.headers.get('Authorization','').removeprefix('Bearer ')
 if not secrets.compare_digest(token,api.API_TOKEN):handler.send_json(401,{'error':'unauthorized'});return True
 try:
  length=int(handler.headers.get('Content-Length','0'))
  if not 0<length<=4096:raise ValueError()
  payload=json.loads(handler.rfile.read(length))
  if not isinstance(payload,dict):raise ValueError()
 except (ValueError,UnicodeError):handler.send_json(400,{'error':'invalid_direct_request'});return True
 if not api.allow_rate():handler.send_json(429,{'error':'rate_limited'},{'Retry-After':'60'});return True
 try:handler.send_json(200,open_direct(api,unquote(match[1]),payload))
 except api.UpstreamError as exc:handler.send_json(exc.status,{'error':exc.code})
 return True
