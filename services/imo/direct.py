"""Open a private portal from a proven incoming group author, without sending."""
import hashlib,json,re,sqlite3
from aiohttp import web


async def open_direct(bridge,payload,server):
 if bridge.state!='CONNECTED':raise web.HTTPConflict(text='Требуется вход в IMO.')
 if not isinstance(payload,dict):raise web.HTTPBadRequest()
 room=payload.get('room_id');event=payload.get('event_id');sender=payload.get('sender_id')
 if not all(isinstance(v,str) and 0<len(v)<=512 for v in (room,event,sender)):raise web.HTTPBadRequest()
 portal=bridge.db.execute('SELECT * FROM portals WHERE room_id=?',(room,)).fetchone()
 if not portal:raise web.HTTPNotFound()
 record=None
 for table in ('inbox','media_inbox'):
  try:row=bridge.db.execute("SELECT i.payload FROM "+table+" i JOIN portals p ON p.buid=i.buid WHERE p.room_id=? AND i.event_id=? AND i.status='sent'",(room,event)).fetchone()
  except sqlite3.OperationalError:continue
  if row:
   record=json.loads(row['payload']);break
 if not isinstance(record,dict) or record.get('own'):raise web.HTTPNotFound()
 uid=record.get('sender')
 if not isinstance(uid,str) or not re.fullmatch(r'[0-9]{1,30}',uid) or uid==str(bridge.uid):raise web.HTTPNotFound()
 ghost='@imo_'+hashlib.sha256(uid.encode()).hexdigest()[:24]+':'+server
 if sender!=ghost:raise web.HTTPNotFound()
 name=str(record.get('name') or uid).strip()[:160]
 target=await bridge.create_portal(uid,name)
 await bridge.link_portal(target)
 # No history from unrelated contacts and no remote outgoing message is created.
 return {'room_id':target['room_id'],'name':target['name'],'account_id':str(bridge.uid),'peer_id':uid}
