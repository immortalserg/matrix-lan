import asyncio,json,hashlib,hmac,time,contextlib
from pathlib import Path
from urllib.parse import quote,urlencode
from aiohttp import web
from message_store import MessageStore

def q(s):return quote(str(s),safe='')
def digest(*values):return hashlib.sha256(json.dumps(values,ensure_ascii=False).encode()).hexdigest()[:32]

class MatrixBridge:
 def __init__(self,gateway,platform):
  self.g=gateway;self.platform=platform;self.config=json.loads((gateway.data/'matrix.json').read_text());self.store=MessageStore(gateway.data/'messages.sqlite')
  self.bot='@'+platform+'bot:'+gateway.owner['server_name'];self.homeserver=gateway.owner['homeserver'];self.server=gateway.owner['server_name'];self.last_chat_poll={};self.chats={};self.last_native={};self.space_lock=asyncio.Lock();self.profiled=set();self.sync_ok=set();self.detail_pages={};self.identities={};self.recovery={};self.failures={}
  from media_pipeline import MediaPipeline
  self.media=MediaPipeline(self)
 async def matrix(self,method,path,body=None,user=None,timestamp=None):
  query={} if path.endswith('/register') else {'user_id':user or self.bot}
  if timestamp is not None:query['ts']=int(timestamp)
  url=self.homeserver+path+('?' + urlencode(query) if query else '')
  async with self.g.http.request(method,url,json=body,headers={'Authorization':'Bearer '+self.config['as_token']}) as r:
   data=await r.json()
   if r.status>=300:raise RuntimeError(data.get('errcode','MATRIX_ERROR'))
   return data
 async def ensure_user(self,user,name):
  if user in self.profiled:return
  try:await self.matrix('POST','/_matrix/client/v3/register',{'type':'m.login.application_service','username':user.split(':')[0][1:]})
  except RuntimeError as e:
   if 'M_USER_IN_USE' not in str(e):raise
  await self.matrix('PUT','/_matrix/client/v3/profile/'+q(user)+'/displayname',{'displayname':name},user=user);self.profiled.add(user)
 async def create(self,alias,body):
  try:return (await self.matrix('POST','/_matrix/client/v3/createRoom',dict(body,room_alias_name=alias)))['room_id']
  except RuntimeError as e:
   if 'M_ROOM_IN_USE' not in str(e):raise
   return (await self.matrix('GET','/_matrix/client/v3/directory/room/'+q('#'+alias+':'+self.server)))['room_id']
 async def space(self,slot):
  async with self.space_lock:
   row=self.g.registry.get(slot)
   if not row:raise RuntimeError('ACCOUNT_REMOVED')
   if row.get('space_room'):return row['space_room']
   await self.ensure_user(self.bot,self.platform.title()+' bridge')
   sid=await self.create(self.platform+'_account_'+digest(slot),{'creation_content':{'type':'m.space'},'name':self.platform.title()+' ('+row['name']+')','preset':'private_chat','visibility':'private','invite':[self.g.owner['owner']],'initial_state':[{'type':'com.matrix.account','state_key':'','content':{'platform':self.platform,'login_id':slot}}]})
   row['space_room']=sid;self.g.persist();return sid
 async def portal(self,slot,chat):
  existing=self.store.db.execute('SELECT room_id FROM chats WHERE slot=? AND cid=?',(slot,chat['id'])).fetchone()
  if existing:return existing['room_id']
  sid=await self.space(slot);info={'bridgebot':self.bot,'creator':self.bot,'protocol':{'id':self.platform,'displayname':self.platform.title()},'channel':{'id':chat['id'],'displayname':chat['name'],'fi.mau.receiver':slot}}
  rid=await self.create(self.platform+'_chat_'+digest(slot,chat['id']),{'name':chat['name']+' · '+self.platform.title(),'topic':'Аккаунт: '+self.g.registry[slot]['name']+'. Текст синхронизируется в обе стороны; вложения пока открывайте в приложении '+self.platform.title()+'.','preset':'private_chat','visibility':'private','invite':[self.g.owner['owner']],'initial_state':[{'type':'m.bridge','state_key':self.platform+'://'+chat['id'],'content':info}]})
  await self.matrix('PUT','/_matrix/client/v3/rooms/'+q(sid)+'/state/m.space.child/'+q(rid),{'via':[self.server]})
  await self.matrix('PUT','/_matrix/client/v3/rooms/'+q(rid)+'/state/m.space.parent/'+q(sid),{'via':[self.server],'canonical':True})
  self.store.link_chat(slot,chat['id'],chat['name'],rid);return rid
 async def native(self,slot,op,args=None):
  page=await self.g.browser(slot)
  if self.platform=='pinduoduo':
   args=dict(args or {})
   if op in ['history','send','media']:
    detail=self.detail_pages.get(slot)
    if not detail or detail.is_closed():detail=await self.g.contexts[slot].new_page();self.detail_pages[slot]=detail
    target='https://mobile.yangkeduo.com/chat_detail.html?mall_id='+q(args['cid'])
    if '/chat_detail.html' not in detail.url or (op=='send' and detail.url.split('mall_id=')[-1].split('&')[0]!=str(args['cid'])):
     await detail.goto(target,wait_until='domcontentloaded',timeout=45000)
     # Wait for the same React store used by the official page, without probing
     # authentication cookies, bypassing checks or synthesizing signatures.
     for _ in range(40):
      try:
       await detail.evaluate(Path('/shopping/pdd_adapter.js').read_text(),{'op':'ready','args':{}});break
      except Exception as exc:
       if 'IM_STORE_NOT_READY' not in str(exc):break
       await asyncio.sleep(.5)
    args['own_id']=self.identities.get(slot,{}).get('id');page=detail
   return await page.evaluate(Path('/shopping/pdd_adapter.js').read_text(),{'op':op,'args':args})
  frame=next((f for f in page.frames if '/app/im/chat-core/' in f.url),None)
  if not frame:raise RuntimeError('IM_FRAME_NOT_READY')
  return await frame.evaluate(Path('/shopping/taobao_adapter.js').read_text(),{'op':op,'args':args or {}})
 async def refresh_chat(self,slot,chat):
  await self.portal(slot,chat);cursor=self.store.cursor(slot,chat['id'])
  history=await self.native(slot,'history',{'cid':chat['id'],'since':int(cursor or 0)})
  self.store.ingest(slot,chat['id'],history['messages'],str(history['cursor']))
  self.last_native[(slot,chat['id'])]=(chat['last_id'],chat['modified'])
 async def poll(self,slot):
  async with self.g.locks.setdefault(slot,asyncio.Lock()):
   if slot not in self.g.registry:return
   page=await self.g.ensure_loaded(slot)
   if not await self.g.logged_in(slot):
    if self.g.is_error_page(page):
     self.g.states[slot]={'state_event':'TRANSIENT_DISCONNECT','error':'Страница сервиса временно не загрузилась, повторяем попытку','timestamp':int(time.time())};return
    self.g.states[slot]={'state_event':'BAD_CREDENTIALS','error':'Нужен повторный вход по QR-коду','timestamp':int(time.time())};return
   identity=await self.native(slot,'identity');self.identities[slot]=identity;await self.space(slot)
   bound=self.g.registry[slot].get('native_id')
   if bound and bound!=identity['id']:
    self.g.states[slot]={'state_event':'WRONG_ACCOUNT','error':'В браузере другой аккаунт; выполните повторный вход','timestamp':int(time.time())};return
   self.g.registry[slot]['native_id']=identity['id']
   if self.platform=='pinduoduo' and self.g.registry[slot]['name']=='Pinduoduo · покупатель':self.g.registry[slot]['name']='Покупатель · …'+identity['id'][-6:];self.g.persist()
   chats=await self.native(slot,'chats');self.chats[slot]=chats
   if not chats:raise RuntimeError('EMPTY_CHAT_LIST_UNVERIFIED')
   first=slot not in self.sync_ok
   mapped={r['cid'] for r in self.store.db.execute('SELECT cid FROM chats WHERE slot=?',(slot,))}
   initial={c['id'] for c in chats[:20]}
   previous=self.g.registry[slot].get('seen_chats',{})
   selected=[c for c in chats if c['id'] in mapped or c['id'] in initial or (previous and (c['id'] not in previous or previous[c['id']]!=[c['last_id'],c['modified']]))]
   failed=[]
   for chat in selected:
    fingerprint=(chat['last_id'],chat['modified'])
    if self.last_native.get((slot,chat['id']))!=fingerprint:
     try:await self.refresh_chat(slot,chat)
     except asyncio.CancelledError:raise
     except Exception:failed.append(chat['id'])
   self.g.registry[slot]['seen_chats']={c['id']:[c['last_id'],c['modified']] for c in chats};self.g.persist();self.sync_ok.add(slot)
   self.g.states[slot]={'state_event':'TRANSIENT_DISCONNECT' if failed else 'CONNECTED','timestamp':int(time.time())}
   if failed:self.g.states[slot]['error']='Не удалось обновить переписки: '+str(len(failed))+'. Остальные продолжают обновляться.'
   self.g.registry[slot]['transport_verified']=True;self.g.persist();self.failures[slot]=0
 async def recover_native(self,slot):
  """Reload a broken web channel with saved cookies, with bounded backoff.
  A genuine login wall remains BAD_CREDENTIALS for the owner to resolve.
  """
  now=time.monotonic();state=self.recovery.setdefault(slot,{'next':0,'attempts':0})
  if now<state['next']:return False
  state['attempts']+=1;state['next']=now+min(300,60*2**min(state['attempts']-1,3))
  async with self.g.locks.setdefault(slot,asyncio.Lock()):
   if slot not in self.g.registry:return False
   self.g.states[slot]={'state_event':'CONNECTING','error':'Восстанавливается соединение с веб-чатом','timestamp':int(time.time())}
   page=await self.g.browser(slot)
   from app import SITE
   await page.goto(SITE['url'],wait_until='domcontentloaded',timeout=45000)
   old=self.detail_pages.pop(slot,None)
   if old:
    with contextlib.suppress(Exception):await old.close()
   for _ in range(24):
    if not await self.g.logged_in(slot):
     self.g.states[slot]={'state_event':'BAD_CREDENTIALS','error':'Сервис запросил повторный вход по QR-коду','timestamp':int(time.time())};return False
    try:
     await self.native(slot,'identity');return True
    except Exception:await asyncio.sleep(.5)
  return False
 async def incoming(self):
  for row in self.store.pending_incoming():
   if row['slot'] not in self.g.registry:continue
   if row['kind']=='media' and not row['body']:
    self.store.mark_incoming(row['slot'],row['cid'],row['msgid'],'media-queued');continue
   if row['own'] and self.store.is_outbound_echo(row['slot'],row['cid'],row['msgid']):self.store.mark_incoming(row['slot'],row['cid'],row['msgid'],'outbound-echo');continue
   user='@'+self.platform+'_'+digest(row['slot'],row['sender'])+':'+self.server
   name=('Вы · '+self.g.registry[row['slot']]['name']) if row['own'] else row['sender'] if '@' not in row['sender'] else row['name']
   await self.ensure_user(user,name+' ('+self.platform.title()+')')
   try:await self.matrix('POST','/_matrix/client/v3/rooms/'+q(row['room_id'])+'/invite',{'user_id':user})
   except RuntimeError as e:
    if 'M_FORBIDDEN' not in str(e):raise
   await self.matrix('POST','/_matrix/client/v3/rooms/'+q(row['room_id'])+'/join',{},user=user)
   content={'msgtype':'m.text' if row['kind']=='text' else 'm.notice','body':row['body'],'com.matrix.shopping':{'platform':self.platform,'native_id':row['msgid'],'own':bool(row['own']),'timestamp':row['ts']}}
   result=await self.matrix('PUT','/_matrix/client/v3/rooms/'+q(row['room_id'])+'/send/m.room.message/'+self.store.txn(row['slot'],row['cid'],row['msgid']),content,user=user,timestamp=row['ts'])
   self.store.mark_incoming(row['slot'],row['cid'],row['msgid'],result['event_id'])
 async def outgoing(self):
  row=self.store.pending_outgoing()
  if not row or row['slot'] not in self.g.registry or self.g.states.get(row['slot'],{}).get('state_event')!='CONNECTED':return
  async with self.g.locks.setdefault(row['slot'],asyncio.Lock()):
   if row['slot'] not in self.g.registry:return
   if not self.store.start_send(row['event_id']):return
   try:
    result=await self.native(row['slot'],'send',{'cid':row['cid'],'body':row['body']});self.store.finish_send(row['event_id'],result['id'])
   except Exception:self.store.uncertain_send(row['event_id'])
 async def report_send_errors(self):
  rows=self.store.db.execute("SELECT o.*,c.room_id FROM outbox o JOIN chats c ON o.slot=c.slot AND o.cid=c.cid WHERE o.state='uncertain' AND o.notice_event IS NULL LIMIT 10").fetchall()
  for row in rows:
   result=await self.matrix('PUT','/_matrix/client/v3/rooms/'+q(row['room_id'])+'/send/m.room.message/'+q('error_'+digest(row['event_id'])),{'msgtype':'m.notice','body':self.platform.title()+' не подтвердил отправку этого сообщения. Проверьте чат в приложении перед повтором. Автоматический повтор отключён, чтобы не отправить сообщение дважды.','m.relates_to':{'m.in_reply_to':{'event_id':row['event_id']}}})
   with self.store.db:self.store.db.execute('UPDATE outbox SET notice_event=? WHERE event_id=?',(result['event_id'],row['event_id']))
 async def as_transaction(self,request):
  supplied=request.headers.get('Authorization','').removeprefix('Bearer ') or request.query.get('access_token','')
  if not hmac.compare_digest(supplied,self.config['hs_token']):raise web.HTTPUnauthorized()
  data=await request.json();events=data.get('events',[])
  self.store.matrix_transaction(request.match_info['txn'],events,self.g.owner['owner'])
  for e in events:
   if e.get('sender')!=self.g.owner['owner'] or e.get('type')!='m.room.message':continue
   c=e.get('content',{});room=self.store.db.execute('SELECT room_id FROM chats WHERE room_id=?',(e.get('room_id'),)).fetchone()
   if room and (c.get('msgtype')!='m.text' or len(str(self.store.outgoing_body(c)))>500 or c.get('m.relates_to',{}).get('rel_type')=='m.replace'):
    await self.matrix('PUT','/_matrix/client/v3/rooms/'+q(room['room_id'])+'/send/m.room.message/'+q('unsupported_'+digest(e.get('event_id'))),{'msgtype':'m.notice','body':'Пока можно отправлять обычный текст до 500 символов. Вложения и изменения сообщений отправляйте через приложение '+self.platform.title()+'.'})
    with self.store.db:self.store.db.execute("UPDATE outbox SET state='cancelled',error='Unsupported message' WHERE event_id=? AND state='pending'",(e.get('event_id'),))
  return web.json_response({})
 async def worker(self):
  await asyncio.sleep(5)
  while True:
   for slot in list(self.g.registry):
    if time.monotonic()-self.last_chat_poll.get(slot,0)>30:
     recovered=False
     try:
      await self.poll(slot)
      if self.g.states.get(slot,{}).get('state_event')=='CONNECTED':self.recovery.pop(slot,None)
     except asyncio.CancelledError:raise
     except Exception as e:
      auth_failed='IM_NOT_AUTHENTICATED' in str(e)
      self.failures[slot]=self.failures.get(slot,0)+1
      self.g.states[slot]={'state_event':'BAD_CREDENTIALS' if auth_failed else 'CONNECTING','error':'Нужен повторный вход по QR-коду' if auth_failed else 'Синхронизация '+self.platform.title()+' выполняется повторно','timestamp':int(time.time())};print('SYNC_ERROR',type(e).__name__,flush=True)
      if auth_failed or self.failures[slot]>=3:
       try:recovered=await self.recover_native(slot)
       except Exception:pass
     self.last_chat_poll[slot]=0 if recovered else time.monotonic()
   try:await self.outgoing();await self.incoming();await self.report_send_errors()
   except asyncio.CancelledError:raise
   except Exception as e:
    for slot in self.g.registry:
     if self.g.states.get(slot,{}).get('state_event')=='CONNECTED':self.g.states[slot]={'state_event':'TRANSIENT_DISCONNECT','error':'Не удалось передать сообщения в Matrix; очередь сохранена','timestamp':int(time.time())}
    print('DELIVERY_ERROR',type(e).__name__,flush=True)
   await asyncio.sleep(1)
