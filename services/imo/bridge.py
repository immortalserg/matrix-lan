"""Single-owner IMO Web text bridge. No credentials are exposed to Element.

Browser credentials live in /data/browser. Incoming messages and Matrix events
are committed to SQLite before processing. Stable Matrix transaction IDs make
inbound retries idempotent. Ambiguous IMO sends are reconciled, never blindly
replayed. This adapter depends on a checked version of the unofficial web client.
"""
import asyncio, contextlib, hashlib, json, logging, os, re, secrets, sqlite3, time
from pathlib import Path
from urllib.parse import quote
from urllib.parse import urlsplit
from aiohttp import web, ClientSession, ClientTimeout
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
log=logging.getLogger('imo-bridge')
DATA=Path(os.getenv('IMO_DATA','/data')); DATA.mkdir(exist_ok=True)
CONFIG=json.loads((DATA/'config.json').read_text())
OWNER=CONFIG['owner']; SERVER=CONFIG['server_name']; BOT='@imobot:'+SERVER
HS=CONFIG.get('homeserver','http://synapse:8008')
PINNED_ENTRY='index-DfHwkxRy.js'
HOOK=(Path(__file__).resolve().parent / 'hook.js').read_text()
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def q(value): return quote(str(value),safe='')
MEDIA_TYPES={'photo_uploaded':'Фото','photo_uploaded_2':'Фото','video_uploaded':'Видео','video_uploaded_2':'Видео','audio_uploaded':'Голосовое сообщение','file_uploaded':'Файл','bigo_uploaded':'Вложение','sticker':'Стикер'}
def message_body(data):
 kind=(data.get('imdata') or {}).get('type','im')
 if kind in ['im','reply','link']:
  text=data.get('msg');return (text,False) if isinstance(text,str) and text else (None,False)
 if kind in MEDIA_TYPES:return '['+MEDIA_TYPES[kind]+' в IMO — просмотр вложений пока не поддерживается]',True
 return None,False
def group_chat(buid):return str(buid).endswith(';')

class Bridge:
 def __init__(self,data=None):
  self.data=Path(data) if data else DATA;self.data.mkdir(parents=True,exist_ok=True)
  self.before_bind=None
  if os.getenv('BRIDGE_SQL_DSN'):
   from sql_storage import connect
   self.db=connect(self.data/'bridge.sqlite3')
  else:
   self.db=sqlite3.connect(self.data/'bridge.sqlite3'); self.db.row_factory=sqlite3.Row
  self.db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
   CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
   CREATE TABLE IF NOT EXISTS portals(buid TEXT PRIMARY KEY,room_id TEXT UNIQUE,name TEXT,start_ts TEXT,cursor TEXT);
   CREATE TABLE IF NOT EXISTS inbox(key TEXT PRIMARY KEY,buid TEXT,payload TEXT,status TEXT DEFAULT 'pending',event_id TEXT);
   CREATE TABLE IF NOT EXISTS outbox(event_id TEXT PRIMARY KEY,room_id TEXT,buid TEXT,body TEXT,msgid TEXT UNIQUE,status TEXT DEFAULT 'pending',imo_ts TEXT,error TEXT,notice_sent INTEGER DEFAULT 0);
   CREATE TABLE IF NOT EXISTS users(user_id TEXT PRIMARY KEY,name TEXT);
   CREATE TABLE IF NOT EXISTS portal_info(buid TEXT PRIMARY KEY,kind TEXT,linked INTEGER DEFAULT 0,bootstrapped INTEGER DEFAULT 0,metadata_done INTEGER DEFAULT 0);
   CREATE TABLE IF NOT EXISTS remote_pending(key TEXT PRIMARY KEY,buid TEXT,payload TEXT,own INTEGER,status TEXT DEFAULT 'pending');
   INSERT OR IGNORE INTO portal_info(buid,kind,bootstrapped) SELECT buid,CASE WHEN substr(buid,-1)=';' THEN 'group' ELSE 'dm' END,1 FROM portals;
  '''); self.db.commit()
  self.page=None;self.context=None;self.browser_ready=False;self.uid='';self.name='';self.state='STARTING';self.error=''
  self.status={};self.login_jobs={};self.auth_cache={};self.tasks=[];self.http=None
  self.portal_lock=asyncio.Lock();self.send_lock=asyncio.Lock();self.bound_uid=self.getmeta('imo_uid')
  self.last_history=0;self.generation=0
  self.net_errors=[]
  self.catalog={};self.catalog_at=0;self.ready=False;self.last_full_catchup=0;self.chat_retry={};self.link_retry={}
  self.live_pending=asyncio.Event()
 def getmeta(self,k):
  row=self.db.execute('SELECT value FROM meta WHERE key=?',(k,)).fetchone();return row[0] if row else ''
 def setmeta(self,k,v):
  self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(k,str(v)));self.db.commit()
 async def matrix(self,method,path,body=None,user=None,token=None,timestamp=None):
  params={'user_id':user} if user else {}
  if timestamp is not None:params['ts']=str(timestamp)
  async with self.http.request(method,HS+path,json=body,params=params,headers={'Authorization':'Bearer '+(token or CONFIG['as_token'])}) as res:
   value=await res.json(content_type=None)
   if res.status>=400: raise RuntimeError('Matrix HTTP '+str(res.status)+' '+str(value.get('errcode','')))
   return value
 async def ensure_user(self,user,name):
  row=self.db.execute('SELECT name FROM users WHERE user_id=?',(user,)).fetchone()
  if row: return
  try:await self.matrix('POST','/_matrix/client/v3/register',{'type':'m.login.application_service','username':user.split(':')[0][1:]})
  except RuntimeError as exc:
   if 'M_USER_IN_USE' not in str(exc):raise
  await self.matrix('PUT','/_matrix/client/v3/profile/'+q(user)+'/displayname',{'displayname':name},user=user)
  self.db.execute('INSERT OR REPLACE INTO users VALUES(?,?)',(user,name));self.db.commit()
 async def event(self,room,body,txn,user=BOT,event_type='m.room.message',timestamp=None):
  return await self.matrix('PUT','/_matrix/client/v3/rooms/'+q(room)+'/send/'+q(event_type)+'/'+q(txn),body,user=user,timestamp=timestamp)
 async def notice(self,room,text,txn):
  return await self.event(room,{'msgtype':'m.notice','body':text},txn)
 async def create_portal(self,buid,name):
  async with self.portal_lock:
   existing=self.db.execute('SELECT * FROM portals WHERE buid=?',(buid,)).fetchone()
   if existing:return dict(existing)
   await self.ensure_user(BOT,'IMO Bridge')
   alias='imo_'+digest(self.uid+'|'+buid)[:24]
   try:
    created=await self.matrix('POST','/_matrix/client/v3/createRoom',{'preset':'private_chat','visibility':'private','room_alias_name':alias,'name':('Группа · ' if group_chat(buid) else '')+name+' · IMO','topic':('Группа IMO. ' if group_chat(buid) else 'Переписка IMO. ')+'Новые сообщения синхронизируются автоматически.','invite':[OWNER],'initial_state':[{'type':'m.bridge','state_key':'imo://'+buid,'content':self.bridge_info(buid,name)}]},user=BOT)
    room=created['room_id']
   except RuntimeError as exc:
    if 'M_ROOM_IN_USE' not in str(exc):raise
    room=(await self.matrix('GET','/_matrix/client/v3/directory/room/'+q('#'+alias+':'+SERVER)))['room_id']
   # A persisted lower bound prevents backfilling old private conversations.
   start=self.getmeta('auto_since') or str(time.time_ns())
   self.db.execute('INSERT INTO portals VALUES(?,?,?,?,?)',(buid,room,name,start,start));self.db.commit()
   self.db.execute('INSERT OR IGNORE INTO portal_info(buid,kind,metadata_done) VALUES(?,?,1)',(buid,'group' if group_chat(buid) else 'dm'));self.db.commit()
   await self.notice(room,'IMO: переписка добавлена. Можно отправлять и открывать фото, видео и файлы. Вложения хранятся на сервере два месяца. Новые сообщения будут синхронизироваться в обе стороны.','welcome_'+alias)
   return dict(self.db.execute('SELECT * FROM portals WHERE buid=?',(buid,)).fetchone())
 def bridge_info(self,buid,name):
  return {'bridgebot':BOT,'creator':BOT,'protocol':{'id':'imo','displayname':'IMO'},'channel':{'id':buid,'displayname':name,'fi.mau.receiver':self.uid,'com.matrix.imo.kind':'group' if group_chat(buid) else 'dm'}}
 async def link_portal(self,portal):
  if self.link_retry.get(portal['buid'],0)>time.monotonic():return
  try:
   async with self.http.post('http://matrix-accounts:29400/_internal/imo-room',json={'room_id':portal['room_id'],'login_id':self.uid,'buid':portal['buid']},headers={'Authorization':'Bearer '+CONFIG['as_token']}) as res:
    data=await res.json()
    if res.status!=200:
     self.link_retry[portal['buid']]=time.monotonic()+30;return
   await self.matrix('PUT','/_matrix/client/v3/rooms/'+q(portal['room_id'])+'/state/m.space.parent/'+q(data['space_room']),{'via':[SERVER],'canonical':True},user=BOT)
   self.db.execute('UPDATE portal_info SET linked=1 WHERE buid=?',(portal['buid'],));self.db.commit()
  except Exception:
   self.link_retry[portal['buid']]=time.monotonic()+30;log.warning('IMO space linking will retry')
 async def prepare_chat(self,chat):
  buid=chat['id'];name=chat.get('name') or buid
  portal=await self.create_portal(buid,name)
  info=self.db.execute('SELECT * FROM portal_info WHERE buid=?',(buid,)).fetchone()
  if not info or not info['metadata_done'] or portal['name']!=name:
   await self.matrix('PUT','/_matrix/client/v3/rooms/'+q(portal['room_id'])+'/state/m.room.name',{'name':('Группа · ' if group_chat(buid) else '')+name+' · IMO'},user=BOT)
   await self.matrix('PUT','/_matrix/client/v3/rooms/'+q(portal['room_id'])+'/state/m.bridge/'+q('imo://'+buid),self.bridge_info(buid,name),user=BOT)
   self.db.execute('UPDATE portals SET name=? WHERE buid=?',(name,buid))
   self.db.execute('INSERT INTO portal_info(buid,kind,metadata_done) VALUES(?,?,1) ON CONFLICT(buid) DO UPDATE SET metadata_done=1,kind=excluded.kind',(buid,'group' if group_chat(buid) else 'dm'));self.db.commit()
  if not info or not info['linked']:await self.link_portal(portal)
  if not info or not info['bootstrapped']:
   rows=await self.evaluate('async(id)=>await window.__matrixIMO.history(id,"0",window.__matrixIMO.timestamp())',buid)
   if not isinstance(rows,list):raise RuntimeError('IMO history is not available yet')
   cached=await self.evaluate('(id)=>window.__matrixIMO.cached(id)',buid)
   unique={}
   for message in (rows if isinstance(rows,list) else [])+cached:
    ts=str(message.get('timestamp_nano_str') or '')
    if ts and message_body(message)[0] is not None:unique[(ts,str(message.get('author') or ''),bool(message.get('is_sent')))]=message
   recent=sorted(unique.values(),key=lambda m:str(m['timestamp_nano_str']))[-20:]
   if recent:
    lower=min(portal['start_ts'],str(recent[0]['timestamp_nano_str']))
    self.db.execute('UPDATE portals SET start_ts=?,cursor=? WHERE buid=?',(lower,lower,buid));self.db.commit()
    for message in recent:self.ingest(buid,message)
   self.db.execute('UPDATE portal_info SET bootstrapped=1 WHERE buid=?',(buid,));self.db.commit()
  return dict(self.db.execute('SELECT * FROM portals WHERE buid=?',(buid,)).fetchone())
 def queue_remote(self,data,own=False):
  buid=str(data.get('buid') or '');ts=str(data.get('timestamp_nano_str') or '')
  if not buid or len(buid)>256 or not ts.isdigit() or message_body(data)[0] is None:return
  cutoff=self.getmeta('auto_since')
  if cutoff and ts<cutoff:return
  safe={k:data[k] for k in ['buid','timestamp_nano_str','msg','msg_id','author','author_alias','is_sent'] if k in data}
  safe['imdata']={k:v for k,v in (data.get('imdata') or {}).items() if k in ['type','msg_id']}
  if safe['imdata'].get('type') in MEDIA_TYPES:safe['msg']=''
  key=digest(self.uid+'|'+buid+'|'+ts+'|'+str(data.get('author') or '')+'|'+str(own))
  self.db.execute('INSERT OR IGNORE INTO remote_pending(key,buid,payload,own) VALUES(?,?,?,?)',(key,buid,json.dumps(safe,ensure_ascii=False),int(own)));self.db.commit()
  self.live_pending.set()
 async def conversation_worker(self):
  while True:
   try:
    if self.state!='CONNECTED' or not self.ready:await asyncio.sleep(1);continue
    self.live_pending.clear()
    if time.monotonic()-self.catalog_at>10:
     chats=await self.evaluate('()=>{const a=window.__matrixIMO,m=new Map(a.contacts().filter(c=>c.is_group).map(c=>[c.id,c]));for(const c of a.sessions())if(c.last_ts||c.is_group)m.set(c.id,{...m.get(c.id),...c});return [...m.values()]}')
     self.catalog={c['id']:c for c in chats if c['id']!='1000000000' and not c.get('is_left') and not c.get('is_private')};self.catalog_at=time.monotonic()
    pending=list(self.db.execute('SELECT * FROM remote_pending WHERE status=\'pending\' LIMIT 50'))
    for row in pending:
     data=json.loads(row['payload']);chat=self.catalog.get(row['buid'])
     if not chat:
      chat=await self.evaluate('(id)=>{const a=window.__matrixIMO;return a.sessions().find(c=>c.id===id)||a.contacts().find(c=>c.id===id)||null}',row['buid'])
     if chat and (chat.get('is_private') or chat.get('is_left')):
      self.db.execute('UPDATE remote_pending SET status=\'ignored\' WHERE key=?',(row['key'],));self.db.commit();continue
     chat=chat or {'id':row['buid'],'name':data.get('author_alias') or row['buid']}
     if self.chat_retry.get(row['buid'],0)>time.monotonic():continue
     try:await self.prepare_chat(chat)
     except Exception:
      self.chat_retry[row['buid']]=time.monotonic()+30;continue
     self.ingest(row['buid'],data,bool(row['own']))
     self.db.execute('UPDATE remote_pending SET status=\'done\' WHERE key=?',(row['key'],));self.db.commit()
    # Newly discovered groups and conversations with existing messages.
    for chat in list(self.catalog.values()):
     if self.chat_retry.get(chat['id'],0)>time.monotonic():continue
     info=self.db.execute('SELECT p.name,i.linked,i.bootstrapped,i.metadata_done FROM portals p LEFT JOIN portal_info i USING(buid) WHERE p.buid=?',(chat['id'],)).fetchone()
     if not info or not info['linked'] or not info['bootstrapped'] or not info['metadata_done'] or info['name']!=chat['name']:
      try:await self.prepare_chat(chat)
      except Exception as exc:
       self.chat_retry[chat['id']]=time.monotonic()+30;log.warning('Chat initialization retry: %s',type(exc).__name__)
    with contextlib.suppress(asyncio.TimeoutError):await asyncio.wait_for(self.live_pending.wait(),timeout=1)
   except asyncio.CancelledError:raise
   except Exception as exc:log.warning('Conversation sync retry: %s',str(exc)[:140]);await asyncio.sleep(3)
 async def patch_entry(self,route):
  response=await route.fetch();text=await response.text()
  if not route.request.url.split('?')[0].endswith('/'+PINNED_ENTRY):
   if 'requestQrCode:' in text:
    self.error='Версия веб-клиента IMO изменилась; адаптер требует проверки.';self.state='UNSUPPORTED_CLIENT'
   await route.fulfill(response=response);return
  for marker in ['ce=oi("sessionStore"','Me=oi("chatStore"','wn=oi("contactStore"','sendImMessages:e=>','requestQrCode:','function Yi()','function el(e,t={})','IMO_NOTIFY_SIGNALING_BACKEND','filesPreUpload:i']:
   if marker not in text:raise RuntimeError('IMO client hook validation failed')
  await route.fulfill(response=response,body=text+HOOK)
 async def browser(self):
  async with async_playwright() as p:
   while True:
    try:
     self.context=await p.chromium.launch_persistent_context(str(self.data/'browser'),headless=True,locale='ru-RU',viewport={'width':1280,'height':800},args=['--no-sandbox','--disable-dev-shm-usage'])
     await self.context.route(re.compile(r'https://web\.imo\.im/cdn-assets-s/webapp/assets/index-[^/]+\.js(?:\?.*)?$'),self.patch_entry)
     self.page=self.context.pages[0] if self.context.pages else await self.context.new_page()
     def failed(req):
      self.net_errors.append({'host':urlsplit(req.url).netloc,'error':req.failure});self.net_errors=self.net_errors[-10:]
     def socket(ws):
      ws.on('socketerror',lambda err:self.net_errors.append({'host':urlsplit(ws.url).netloc,'error':str(err)[:200]}))
     self.page.on('requestfailed',failed);self.page.on('websocket',socket)
     self.page.on('pageerror',lambda err:log.warning('IMO page script error: %s',str(err)[:200]))
     await self.page.goto('https://web.imo.im/',wait_until='domcontentloaded',timeout=60000)
     self.generation+=1;self.browser_ready=True
     while True:
      await self.tick();await asyncio.sleep(.25)
    except asyncio.CancelledError:raise
    except Exception as exc:
     self.state='DISCONNECTED';self.error='Связь с IMO прервана; выполняется повторное подключение.'
     log.warning('Browser restart: %s',type(exc).__name__)
     self.browser_ready=False;self.uid=''
     if self.context:
      with contextlib.suppress(Exception):await self.context.close()
     await asyncio.sleep(5)
 async def evaluate(self,script,arg=None):
  if not self.page:raise RuntimeError('browser not ready')
  return await asyncio.wait_for(self.page.evaluate(script,arg),timeout=30)
 def ingest(self,buid,data,own=False):
  portal=self.db.execute('SELECT * FROM portals WHERE buid=?',(buid,)).fetchone()
  ts=str(data.get('timestamp_nano_str') or '')
  if not portal or not ts.isdigit() or ts<portal['start_ts']:return
  imdata=data.get('imdata') or {};text,unsupported=message_body(data)
  if text is None:return
  msgid=str(data.get('msg_id') or imdata.get('msg_id') or '')
  # Suppress echoes of messages that originated in Matrix, including reflections
  # received before the send response was returned to the worker.
  if msgid and self.db.execute('SELECT 1 FROM outbox WHERE msgid=?',(msgid,)).fetchone():return
  own=own or bool(data.get('is_sent')) or data.get('author')==self.uid
  author=str(data.get('author') or '').split(';')[0]
  sender=self.uid if own else author or ('unknown-group:'+buid if group_chat(buid) else buid)
  name='Я (IMO)' if own else str(data.get('author_alias') or (('Участник · '+author[-6:] if author else 'Участник группы') if group_chat(buid) else portal['name']))
  payload={'text':text,'sender':sender,'name':name,'ts':ts,'msgid':msgid,'own':own,'unsupported_media':unsupported}
  key=digest(self.uid+'|'+buid+'|'+ts+'|'+payload['sender'])
  self.db.execute('INSERT OR IGNORE INTO inbox(key,buid,payload) VALUES(?,?,?)',(key,buid,json.dumps(payload,ensure_ascii=False)))
  self.db.commit()
 async def tick(self):
  result=await self.evaluate('''()=>{const a=window.__matrixIMO;if(!a)return null;let s;try{s=a.status()}catch(_){return null}return {status:s,events:a.events.slice(0,500),qr:!!a.qr}}''')
  if result is None:
   if self.state!='UNSUPPORTED_CLIENT':self.state='STARTING'
   return
  status=result['status'];uid=status.get('uid') or ''
  self.uid=uid;self.name=status.get('name') or ''
  self.ready=bool(status.get('ready'))
  if not uid:
   self.state='LOGIN_REQUIRED'
   if not result['qr']:
    diagnostics=await self.evaluate('()=>({network:window.__matrixIMO?.network(),qr:window.__matrixIMO?.qrStatus||null,text:document.body.innerText.slice(0,2000)})')
    diagnostics['errors']=self.net_errors[-10:]
    (self.data/'login-diagnostic.json').write_text(json.dumps(diagnostics,ensure_ascii=False))
    if not (self.data/'login-diagnostic.png').exists():await self.page.screenshot(path=str(self.data/'login-diagnostic.png'))
   return
  if not status.get('connected'):self.state='DISCONNECTED';return
  if self.bound_uid and uid!=self.bound_uid:
   self.state='WRONG_ACCOUNT';self.error='Вошёл другой аккаунт IMO. Используйте первоначальный аккаунт.';return
  if self.before_bind:await self.before_bind(self,uid,status)
  if not self.bound_uid:self.bound_uid=uid;self.setmeta('imo_uid',uid)
  if self.getmeta('imo_name')!=self.name:self.setmeta('imo_name',self.name)
  self.state='CONNECTED';self.error=''
  await self.evaluate('()=>window.__matrixIMO.startCapture()')
  if self.ready and not self.getmeta('auto_since'):
   self.setmeta('auto_since',await self.evaluate('()=>window.__matrixIMO.timestamp()'))
  diagnostic_request=self.data/'diagnostic.request.json'
  if diagnostic_request.exists():
   request=json.loads(diagnostic_request.read_text())
   portal=self.db.execute('SELECT * FROM portals WHERE buid=?',(str(request.get('buid','')),)).fetchone()
   if portal:
    history=await self.evaluate('async(id)=>await window.__matrixIMO.history(id,"0",String(BigInt(Date.now())*1000000n))',portal['buid'])
    cached=await self.evaluate('(id)=>window.__matrixIMO.cached(id)',portal['buid'])
    recent=await self.evaluate('async(id)=>await window.__matrixIMO.recent(id)',portal['buid'])
    session=await self.evaluate('(id)=>window.__matrixIMO.sessions().find(s=>s.id===id)',portal['buid'])
    (self.data/'diagnostic.result.json').write_text(json.dumps({'history':history,'cached':cached,'recent':recent,'session':session,'events':result['events'],'status':status,'portal':dict(portal)},ensure_ascii=False))
   diagnostic_request.unlink()
  last_seq=0
  for e in result['events']:
   d=e['data'];kind=e['kind'];last_seq=e['seq']
   if kind in ['receive','reflect']:self.queue_remote(d,kind=='reflect')
   elif kind in ['delivered','seen']:
    self.db.execute('UPDATE outbox SET status=? WHERE buid=? AND imo_ts=? AND status IN (\'accepted_by_imo\',\'delivered\')',('seen' if kind=='seen' else 'delivered',str(d.get('buid') or ''),str(d.get('timestamp_nano_str') or '')));self.db.commit()
  if last_seq:await self.evaluate('(seq)=>{const a=window.__matrixIMO;a.events=a.events.filter(e=>e.seq>seq)}',last_seq)
 async def history_worker(self):
  # Slow history/network requests must never hold up the live receive loop.
  while True:
   try:
    if self.state=='CONNECTED' and self.ready and self.browser_ready:
     await self.catchup();self.last_history=time.monotonic()
     await asyncio.sleep(15)
    else:await asyncio.sleep(1)
   except asyncio.CancelledError:raise
   except Exception as exc:
    log.warning('Background history retry: %s',type(exc).__name__)
    await asyncio.sleep(3)
 async def catchup(self):
  force=time.monotonic()-self.last_full_catchup>600
  if force:self.last_full_catchup=time.monotonic()
  for portal in list(self.db.execute('SELECT * FROM portals')):
   try:await self.catchup_portal(portal,force)
   except asyncio.CancelledError:raise
   except Exception as exc:log.warning('History retry for one chat: %s',type(exc).__name__)
 async def catchup_portal(self,portal,force=False):
  # IMO can keep unread messages in its sync buffer before the archive API
  # exposes them. Read that buffer as well as archived history.
  cached=await self.evaluate('(id)=>window.__matrixIMO.cached(id)',portal['buid'])
  for message in cached:self.ingest(portal['buid'],message)
  latest=self.catalog.get(portal['buid'],{}).get('last_ts')
  ambiguous=self.db.execute("SELECT 1 FROM outbox WHERE buid=? AND status IN ('unknown','sending') LIMIT 1",(portal['buid'],)).fetchone()
  if not force and latest and latest<=(portal['cursor'] or portal['start_ts']) and not ambiguous:return
  start=portal['cursor'] or portal['start_ts'];end=str(time.time_ns());maximum=start
  # Walk backward until the persisted lower bound. Commit all pages before
  # advancing the cursor so interruption cannot skip a page.
  for _ in range(100):
   rows=await self.evaluate('async({id,start,end})=>await window.__matrixIMO.history(id,start,end)',{'id':portal['buid'],'start':start,'end':end})
   if not isinstance(rows,list):raise RuntimeError('Unexpected history response')
   for row in rows:
    self.ingest(portal['buid'],row)
    ts=str(row.get('timestamp_nano_str') or '')
    if ts>maximum:maximum=ts
    msgid=str(row.get('msg_id') or (row.get('imdata') or {}).get('msg_id') or '')
    if msgid and ts:
     self.db.execute('UPDATE outbox SET status=\'accepted_by_imo\',imo_ts=?,error=NULL WHERE msgid=? AND status IN (\'sending\',\'unknown\')',(ts,msgid));self.db.commit()
   if len(rows)<100:break
   next_end=min(str(row.get('timestamp_nano_str') or end) for row in rows)
   if next_end>=end:raise RuntimeError('History pagination did not advance')
   end=next_end
  else:raise RuntimeError('History catchup limit reached; cursor preserved')
  self.db.execute('UPDATE portals SET cursor=? WHERE buid=?',(maximum,portal['buid']));self.db.commit()
 async def inbound_worker(self):
  while True:
   try:
    row=self.db.execute('SELECT i.*,p.room_id FROM inbox i JOIN portals p USING(buid) WHERE i.status=\'pending\' LIMIT 1').fetchone()
    if not row:await asyncio.sleep(.1);continue
    data=json.loads(row['payload']);user='@imo_'+digest(data['sender'])[:24]+':'+SERVER
    await self.ensure_user(user,data['name'])
    with contextlib.suppress(RuntimeError):
     await self.matrix('POST','/_matrix/client/v3/rooms/'+q(row['room_id'])+'/invite',{'user_id':user},user=BOT)
    await self.matrix('POST','/_matrix/client/v3/rooms/'+q(row['room_id'])+'/join',{},user=user)
    result=await self.event(row['room_id'],{'msgtype':'m.notice' if data.get('unsupported_media') else 'm.text','body':data['text'],'com.matrix.imo':{'msg_id':data['msgid'],'timestamp':data['ts'],'own':data['own'],'kind':'group' if group_chat(row['buid']) else 'dm','unsupported_media':bool(data.get('unsupported_media'))}},'imo_'+row['key'],user=user,timestamp=int(data['ts'])//1000000)
    self.db.execute('UPDATE inbox SET status=\'sent\',event_id=? WHERE key=?',(result['event_id'],row['key']));self.db.commit()
   except asyncio.CancelledError:raise
   except Exception as exc:log.warning('Inbound retry: %s',str(exc)[:150]);await asyncio.sleep(3)
 async def outbound_worker(self):
  # A process may have died after IMO accepted a request. History reconciliation
  # can recover its timestamp; no automatic resend is made for such requests.
  self.db.execute('UPDATE outbox SET status=\'unknown\',error=\'Отправка была прервана; требуется сверка с IMO.\' WHERE status=\'sending\'');self.db.commit()
  while True:
   try:
    for failure in list(self.db.execute('SELECT * FROM outbox WHERE status IN (\'error\',\'unknown\') AND notice_sent=0')):
     await self.notice(failure['room_id'],'IMO: '+str(failure['error'])+' Сообщение автоматически повторно не отправлялось.','failure_'+digest(failure['event_id']))
     self.db.execute('UPDATE outbox SET notice_sent=1 WHERE event_id=?',(failure['event_id'],));self.db.commit()
    row=self.db.execute('SELECT * FROM outbox WHERE status=\'pending\' LIMIT 1').fetchone()
    if not row or self.state!='CONNECTED' or not self.browser_ready:await asyncio.sleep(.5);continue
    self.db.execute('UPDATE outbox SET status=\'sending\' WHERE event_id=?',(row['event_id'],));self.db.commit()
    try:
     async with self.send_lock:
      res=await self.evaluate('async({id,text,msgid})=>await window.__matrixIMO.send(id,text,msgid)',{'id':row['buid'],'text':row['body'],'msgid':row['msgid']})
     ts=str((res or {}).get('timestamp_nano_str') or '')
     if ts:
      self.db.execute('UPDATE outbox SET status=\'accepted_by_imo\',imo_ts=? WHERE event_id=?',(ts,row['event_id']));self.db.commit()
      await self.event(row['room_id'],{'event_id':row['event_id'],'status':'accepted_by_imo','delivery_confirmed':False},'status_'+digest(row['event_id']),event_type='com.matrix.imo.status')
     else:
      self.db.execute('UPDATE outbox SET status=\'error\',error=\'IMO отклонил сообщение.\' WHERE event_id=?',(row['event_id'],));self.db.commit()
    except Exception:
     self.db.execute('UPDATE outbox SET status=\'unknown\',error=\'Нет подтверждения принятия сообщения от IMO.\' WHERE event_id=?',(row['event_id'],));self.db.commit()
   except asyncio.CancelledError:raise
   except Exception as exc:log.warning('Outbound worker: %s',type(exc).__name__);await asyncio.sleep(3)
 async def authorize(self,request):
  origin=request.headers.get('Origin')
  if origin and origin not in ['__PUBLIC_ORIGIN__','__PUBLIC_ORIGIN__']:raise web.HTTPForbidden()
  auth=request.headers.get('Authorization','')
  if not auth.startswith('Bearer '):raise web.HTTPUnauthorized()
  token=auth[7:];key=digest(token)
  cached=self.auth_cache.get(key)
  if cached and cached[1]>time.monotonic():user=cached[0]
  else:
   try:user=(await self.matrix('GET','/_matrix/client/v3/account/whoami',token=token))['user_id']
   except Exception:raise web.HTTPUnauthorized()
   self.auth_cache[key]=(user,time.monotonic()+30)
  if user!=OWNER or request.query.get('user_id')!=user:raise web.HTTPForbidden()
  return user
 async def login_step(self,job):
  if self.state=='CONNECTED':return {'type':'complete','login_id':job,'step_id':'done','complete':{'id':self.uid,'name':self.name}}
  if self.state in ['WRONG_ACCOUNT','UNSUPPORTED_CLIENT']:raise web.HTTPConflict(text=self.error)
  qr=await self.evaluate('()=>window.__matrixIMO?.qr||null')
  if not qr:raise RuntimeError('QR-код IMO ещё загружается. Повторите через несколько секунд.')
  return {'type':'display_and_wait','login_id':job,'step_id':'qr','instructions':'На телефоне: IMO → «+» → «Сканировать QR-код». Подтвердите вход.','display_and_wait':{'type':'qr','data':qr['data']}}
 async def provisioning(self,request):
  await self.authorize(request);path=request.match_info['tail']
  if request.method=='GET' and path=='whoami':
   return web.json_response({'bridge_bot':BOT,'login_flows':[{'id':'qr','name':'QR-код IMO'}],'logins':[{'id':self.uid,'name':self.name,'state':{'state_event':self.state}}] if self.uid else [],'state':self.state,'error':self.error})
  if request.method=='GET' and path=='login/flows':return web.json_response({'flows':[{'id':'qr','name':'QR-код IMO'}]})
  if request.method=='POST' and path=='login/start/qr':
   self.login_jobs={k:v for k,v in self.login_jobs.items() if v>time.monotonic()}
   if len(self.login_jobs)>10:raise web.HTTPTooManyRequests()
   job=secrets.token_urlsafe(24);self.login_jobs[job]=time.monotonic()+600
   for _ in range(30):
    try:return web.json_response(await self.login_step(job))
    except RuntimeError:await asyncio.sleep(.5)
   raise web.HTTPServiceUnavailable(text='Не удалось получить QR-код IMO.')
  if request.method=='POST' and path.startswith('login/cancel/'):
   self.login_jobs.pop(path.split('/')[-1],None)
   if not self.login_jobs and self.state!='CONNECTED' and self.page:
    # Navigating away destroys the abandoned QR flow without logging out a
    # previously connected account or cancelling another tab's active flow.
    with contextlib.suppress(Exception):await self.evaluate('async()=>await window.__matrixIMO?.cancelQR()')
    await self.page.reload(wait_until='domcontentloaded')
   return web.json_response({})
  if request.method=='POST' and path.startswith('login/step/'):
   parts=path.split('/');job=parts[2]
   if self.login_jobs.get(job,0)<time.monotonic():raise web.HTTPNotFound()
   for _ in range(20):
    if self.state=='CONNECTED':break
    if job not in self.login_jobs:raise web.HTTPGone()
    await asyncio.sleep(1)
   return web.json_response(await self.login_step(job))
  if request.method=='GET' and path=='chats':
   if self.state!='CONNECTED':raise web.HTTPConflict(text='Требуется вход в IMO.')
   chats=await self.evaluate('()=>{const a=window.__matrixIMO;const m=new Map(a.contacts().map(c=>[c.id,c]));for(const c of a.sessions())m.set(c.id,{...m.get(c.id),...c});return [...m.values()]}')
   search=request.query.get('search','').casefold()
   if search:chats=[c for c in chats if search in (c['name']+' '+c['id']).casefold()]
   mapped={r['buid']:r['room_id'] for r in self.db.execute('SELECT * FROM portals')}
   chats.sort(key=lambda c:(0 if group_chat(c['id']) else 1 if c.get('last_ts') else 2,str(c['name']).casefold()))
   return web.json_response({'chats':[{**c,'name':('Группа · ' if group_chat(c['id']) else '')+c['name'],'is_group':group_chat(c['id']),'room_id':mapped.get(c['id'])} for c in chats[:100]],'total':len(chats)})
  if request.method=='POST' and path=='chats/direct':
   from direct import open_direct
   return web.json_response(await open_direct(self,await request.json(),SERVER))
  if request.method=='POST' and path=='chats/open':
   if self.state!='CONNECTED':raise web.HTTPConflict(text='Требуется вход в IMO.')
   payload=await request.json();buid=str(payload.get('id',''))
   known=await self.evaluate('(id)=>{const a=window.__matrixIMO;return a.sessions().find(c=>c.id===id)||a.contacts().find(c=>c.id===id)||null}',buid)
   if not known:raise web.HTTPNotFound(text='Переписка не найдена в IMO.')
   if known.get('is_private') or known.get('is_left'):raise web.HTTPConflict(text='Эта переписка недоступна веб-клиенту IMO.')
   return web.json_response(await self.prepare_chat(known))
  raise web.HTTPNotFound()
 async def transaction(self,request):
  if request.headers.get('Authorization')!='Bearer '+CONFIG['hs_token']:raise web.HTTPUnauthorized()
  data=await request.json()
  for e in data.get('events',[]):
   if e.get('sender')!=OWNER:continue
   portal=self.db.execute('SELECT * FROM portals WHERE room_id=?',(e.get('room_id'),)).fetchone()
   if not portal:continue
   if e.get('type')=='m.room.encrypted':
    await self.notice(portal['room_id'],'IMO: это сообщение зашифровано и не может быть отправлено этим прототипом.','encrypted_'+digest(e['event_id']));continue
   if e.get('type')!='m.room.message':continue
   content=e.get('content') or {};body=content.get('body');eid=e.get('event_id')
   if not eid or not isinstance(body,str):continue
   status='pending';error=None
   if content.get('msgtype')!='m.text' or content.get('m.relates_to',{}).get('rel_type')=='m.replace':status='error';error='Пока поддерживаются только новые текстовые сообщения.'
   if not body.strip() or len(body)>10000:status='error';error='Текст пустой или длиннее 10 000 символов.'
   self.db.execute('INSERT OR IGNORE INTO outbox(event_id,room_id,buid,body,msgid,status,error) VALUES(?,?,?,?,?,?,?)',(eid,portal['room_id'],portal['buid'],body,digest(eid)[:16],status,error))
  self.db.commit();return web.json_response({})
 async def health(self,request):return web.json_response({'service':'matrix-imo','state':self.state,'browser_ready':self.browser_ready,'error':self.error,'portals':self.db.execute('SELECT count(*) FROM portals').fetchone()[0],'sync_ready':self.ready,'catalog_count':len(self.catalog),'groups':self.db.execute("SELECT count(*) FROM portal_info WHERE kind='group'").fetchone()[0],'initializing':self.db.execute('SELECT count(*) FROM portal_info WHERE bootstrapped=0 OR linked=0').fetchone()[0],'pending_messages':self.db.execute("SELECT count(*) FROM remote_pending WHERE status='pending'").fetchone()[0]})
 async def ping(self,request):
  if request.headers.get('Authorization')!='Bearer '+CONFIG['hs_token']:raise web.HTTPUnauthorized()
  return web.json_response({})
 async def startup(self,app):
  self.http=ClientSession(timeout=ClientTimeout(total=25))
  self.tasks=[asyncio.create_task(self.browser()),asyncio.create_task(self.inbound_worker()),asyncio.create_task(self.outbound_worker()),asyncio.create_task(self.conversation_worker()),asyncio.create_task(self.history_worker())]
 async def cleanup(self,app):
  for task in self.tasks:task.cancel()
  await asyncio.gather(*self.tasks,return_exceptions=True)
  if self.context:
   with contextlib.suppress(Exception):await self.context.close()
  await self.http.close();self.db.close()

@web.middleware
async def errors(request,handler):
 try:res=await handler(request)
 except web.HTTPException as exc:res=web.json_response({'error':exc.text or exc.reason},status=exc.status)
 except Exception as exc:
  log.warning('Request failed %s: %s',request.path,type(exc).__name__)
  res=web.json_response({'error':str(exc)[:300] if isinstance(exc,RuntimeError) else 'Временная ошибка сервиса IMO.'},status=503)
 res.headers['Cache-Control']='private, no-store';return res

b=Bridge() if __name__=='__main__' else None;app=web.Application(middlewares=[errors],client_max_size=1024*1024)
if b:app.router.add_get('/healthz',b.health)
if b:app.router.add_route('*','/_matrix/provision/v3/{tail:.*}',b.provisioning)
if b:app.router.add_put('/_matrix/app/v1/transactions/{txn}',b.transaction)
if b:app.router.add_post('/_matrix/app/v1/ping',b.ping)
if b:app.on_startup.append(b.startup);app.on_cleanup.append(b.cleanup)
if __name__=='__main__':web.run_app(app,host='0.0.0.0',port=29390,access_log=None)

