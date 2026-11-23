"""Owner-only multi-account gateway. Each account owns its DB and browser storage.

The original account keeps its persistent Chromium profile and all room IDs.
Additional accounts use isolated contexts in a shared Chromium process. Browser
state stays on the server with mode 0600; it never enters provisioning responses.
"""
import asyncio, contextlib, json, os, re, secrets, time, shutil
from pathlib import Path
from aiohttp import web
from playwright.async_api import async_playwright, Error as BrowserError
from media import MediaBridge
from bridge import Bridge, DATA, CONFIG, BOT, OWNER, errors, log

MAX_ACCOUNTS=40

def atomic_json(path,value):
 temporary=path.with_suffix('.tmp')
 fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
 with os.fdopen(fd,'w',encoding='utf8') as f:
  json.dump(value,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
 os.replace(temporary,path)

class DuplicateAccount(Exception):pass

class AccountBridge(MediaBridge):
 def __init__(self,data,manager,slot,legacy=False):
  super().__init__(data);self.manager=manager;self.slot=slot;self.legacy=legacy
  self.before_bind=manager.bind;self.duplicate_uid='';self.last_save=0
 async def save_session(self):
  if not self.legacy and self.bound_uid and self.context and self.state=='CONNECTED':
   state=await self.context.storage_state(indexed_db=False)
   atomic_json(self.data/'session-state.json',state)
   self.last_save=time.monotonic()
 async def browser(self):
  if self.legacy:
   await super().browser();return
  while True:
   try:
    async with self.manager.launch_limit:
     browser=await self.manager.get_browser()
     state=self.data/'session-state.json'
     self.context=await browser.new_context(storage_state=str(state) if state.exists() else None,locale='ru-RU',viewport={'width':1280,'height':800})
     await self.context.route(re.compile(r'https://web\.imo\.im/cdn-assets-s/webapp/assets/index-[^/]+\.js(?:\?.*)?$'),self.patch_entry)
     self.page=await self.context.new_page()
     self.page.on('pageerror',lambda e:log.warning('IMO account page error: %s',type(e).__name__))
     await self.page.goto('https://web.imo.im/',wait_until='domcontentloaded',timeout=60000)
    self.generation+=1;self.browser_ready=True
    while True:
     await self.tick()
     if time.monotonic()-self.last_save>15:await self.save_session()
     await asyncio.sleep(1)
   except asyncio.CancelledError:raise
   except DuplicateAccount:
    self.state='DUPLICATE_ACCOUNT';self.browser_ready=False
    if self.context:await self.context.close()
    self.page=None;self.context=None
    await asyncio.Future()
   except Exception as exc:
    self.browser_ready=False;self.ready=False;self.state='DISCONNECTED';self.error='Связь с IMO прервана; выполняется повторное подключение.'
    log.warning('Account browser retry: %s',type(exc).__name__)
    if self.context:
     with contextlib.suppress(Exception):await self.context.close()
    self.page=None;self.context=None
    await asyncio.sleep(5)
 async def cleanup(self,app):
  with contextlib.suppress(Exception):await self.save_session()
  await super().cleanup(app)

class Manager:
 def __init__(self,data=DATA):
  self.data=Path(data);self.registry_path=self.data/'accounts-registry.json'
  self.registry=json.loads(self.registry_path.read_text(encoding='utf8')) if self.registry_path.exists() else {'primary':{'legacy':True}}
  if 'primary' not in self.registry:raise RuntimeError('Original IMO account is missing from registry')
  if any(not re.fullmatch(r'primary|[0-9a-f]{32}',s) for s in self.registry):raise RuntimeError('Invalid IMO account registry')
  self.account_change_lock=asyncio.Lock();self.accounts={};self.jobs={};self.browser=None;self.pw=None;self.browser_lock=asyncio.Lock();self.launch_limit=asyncio.Semaphore(2);self.media_limit=asyncio.Semaphore(2);self.housekeeper=None;self.started=False
  for slot,row in self.registry.items():self.make_bridge(slot,row.get('legacy',False))
 def make_bridge(self,slot,legacy=False):
  path=self.data if slot=='primary' else self.data/'accounts'/slot
  path.mkdir(parents=True,exist_ok=True);os.chmod(path,0o700)
  b=AccountBridge(path,self,slot,legacy);self.accounts[slot]=b;return b
 def persist(self):atomic_json(self.registry_path,self.registry)
 async def get_browser(self):
  async with self.browser_lock:
   if not self.browser or not self.browser.is_connected():
    self.browser=await self.pw.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
   return self.browser
 def resolve(self,login_id):
  matches=[b for b in self.accounts.values() if b.bound_uid and b.bound_uid==login_id]
  if len(matches)!=1:raise web.HTTPNotFound(text='Аккаунт IMO не найден.')
  return matches[0]
 def select(self,request):
  login_id=request.query.get('login_id')
  if login_id:return self.resolve(login_id)
  accounts=[b for b in self.accounts.values() if b.bound_uid]
  if len(accounts)!=1:raise web.HTTPBadRequest(text='Выберите аккаунт IMO.')
  return accounts[0]
 async def bind(self,b,uid,status):
  if b.bound_uid:return
  duplicate=next((x for x in self.accounts.values() if x is not b and x.bound_uid==uid),None)
  if duplicate:
   b.duplicate_uid=uid;raise DuplicateAccount()
  # This callback has no awaits: two simultaneous scans cannot claim one UID.
  b.bound_uid=uid;b.setmeta('imo_uid',uid)
  self.registry[b.slot]['uid']=uid;self.persist()
  b.setmeta('imo_name',status.get('name') or '')
  b.setmeta('phone_suffix',status.get('phone_suffix') or '')
  if not b.legacy:
   atomic_json(b.data/'session-state.json',await b.context.storage_state(indexed_db=False));b.last_save=time.monotonic()
 async def authorize(self,request):return await self.accounts['primary'].authorize(request)
 async def start(self,request):
  flow=request.match_info['tail'].split('/')[-1]
  if flow not in ['qr','new_qr']:raise web.HTTPNotFound()
  if sum(not b.bound_uid and not b.duplicate_uid for b in self.accounts.values())>=2:raise web.HTTPConflict(text='Завершите или отмените уже открытый вход IMO.')
  target=request.query.get('login_id')
  if target:
   b=self.resolve(target)
   if b.state=='CONNECTED':raise web.HTTPConflict(text='Этот аккаунт уже подключён. Для другого аккаунта нажмите «Добавить ещё IMO».')
  else:
   if sum(bool(b.bound_uid) or b.slot!='primary' for b in self.accounts.values())>=MAX_ACCOUNTS:raise web.HTTPConflict(text='Можно подключить до 40 аккаунтов IMO.')
   b=next((x for x in self.accounts.values() if not x.bound_uid and not any(j['slot']==x.slot for j in self.jobs.values())),None)
   if not b:
    slot=secrets.token_hex(16);self.registry[slot]={'legacy':False};self.persist();b=self.make_bridge(slot)
    await b.startup(None)
  job=secrets.token_urlsafe(24);self.jobs[job]={'slot':b.slot,'expires':time.monotonic()+600}
  for _ in range(50):
   if job not in self.jobs:raise web.HTTPGone()
   try:return web.json_response(await self.step(job))
   except (RuntimeError,BrowserError):await asyncio.sleep(.5)
  await self.discard(job)
  return web.json_response({'error':'QR-код ещё загружается. Нажмите «Начать заново».'},status=503)
 async def step(self,job):
  entry=self.jobs.get(job)
  if not entry or entry['expires']<time.monotonic():raise web.HTTPNotFound(text='Время входа истекло.')
  b=self.accounts[entry['slot']]
  if b.duplicate_uid:
   existing=self.resolve(b.duplicate_uid)
   return {'type':'complete','login_id':job,'step_id':'done','complete':{'id':existing.bound_uid,'name':existing.name or existing.getmeta('imo_name'),'already_connected':True}}
  if not b.browser_ready or not b.page:raise RuntimeError('QR-код IMO ещё загружается.')
  return await b.login_step(job)
 async def discard(self,job):
  entry=self.jobs.pop(job,None)
  if not entry:return
  slot=entry['slot'];b=self.accounts.get(slot)
  if not b or b.bound_uid or slot=='primary' or any(j['slot']==slot for j in self.jobs.values()):return
  # Close only this unbound context; never call native logout on an account.
  await b.cleanup(None);del self.accounts[slot];self.registry.pop(slot,None);self.persist()
 async def logout_account(self,login_id):
  async with self.account_change_lock:
   b=self.resolve(login_id);slot=b.slot;directory=b.data
   # Stop every task for this one account before deleting browser credentials.
   for job,entry in list(self.jobs.items()):
    if entry['slot']==slot:self.jobs.pop(job,None)
   b.bound_uid='';b.uid='';b.state='LOGGED_OUT';b.setmeta('imo_uid','')
   await b.cleanup(None)
   for name in ['session-state.json','browser']:
    target=directory/name
    if not target.resolve().is_relative_to(self.data.resolve()):raise RuntimeError('Invalid account credential path')
    if target.is_dir():shutil.rmtree(target)
    elif target.exists():target.unlink()
   # Keep the old room/message mapping as an inactive archive. It must never
   # be loaded into a subsequently connected account.
   archive=self.data/'removed-account-history'/(slot+'-'+secrets.token_hex(8));archive.mkdir(parents=True,mode=0o700)
   for name in ['bridge.sqlite3','bridge.sqlite3-wal','bridge.sqlite3-shm']:
    source=directory/name
    if source.exists():os.replace(source,archive/name)
   del self.accounts[slot]
   if slot=='primary':
    # The unbound primary adapter also supplies owner auth and appservice ping.
    # Recreate an empty adapter, with no old UID, cookie or portal mapping.
    self.registry['primary']={'legacy':True};self.persist()
    replacement=self.make_bridge('primary',True)
    if self.started:await replacement.startup(None)
   else:self.registry.pop(slot,None);self.persist()
   return web.json_response({'removed':True,'login_id':login_id})
 async def provisioning(self,request):
  await self.authorize(request);path=request.match_info['tail']
  flows=[{'id':'new_qr','name':'Добавить аккаунт IMO по QR-коду'}]
  if request.method=='GET' and path=='whoami':
   rows=[]
   for b in self.accounts.values():
    if b.bound_uid:rows.append({'id':b.bound_uid,'name':(b.name if b.state=='CONNECTED' else '') or b.getmeta('imo_name') or b.bound_uid,'state':{'state_event':b.state},'phone_suffix':b.getmeta('phone_suffix')})
   return web.json_response({'bridge_bot':BOT,'login_flows':flows,'logins':rows,'max_accounts':MAX_ACCOUNTS})
  if request.method=='POST' and path.startswith('logout/'):return await self.logout_account(path[len('logout/'):])
  if request.method=='GET' and path=='login/flows':return web.json_response({'flows':flows})
  if request.method=='POST' and path.startswith('login/start/'):return await self.start(request)
  if request.method=='POST' and path.startswith('login/cancel/'):
   await self.discard(path.split('/')[-1]);return web.json_response({})
  if request.method=='POST' and path.startswith('login/step/'):
   job=path.split('/')[2]
   for _ in range(20):
    step=await self.step(job)
    if step['type']=='complete':return web.json_response(step)
    await asyncio.sleep(1)
   return web.json_response(await self.step(job))
  if path.startswith(('resolve_identifier/','create_dm/')):
   from contact_search import handle
   return await handle(self.select(request),request)
  if path in ['chats','chats/open','chats/direct']:return await self.select(request).provisioning(request)
  raise web.HTTPNotFound()
 async def transaction(self,request):
  if request.headers.get('Authorization')!='Bearer '+CONFIG['hs_token']:raise web.HTTPUnauthorized()
  # Every account DB is open, including accounts that temporarily lost network.
  # Each adapter accepts only its own room IDs and commits before this ACK.
  async with self.account_change_lock:
   for b in list(self.accounts.values()):await b.transaction(request)
  return web.json_response({})
 async def health(self,request):
  return web.json_response({'service':'matrix-imo','max_accounts':MAX_ACCOUNTS,'accounts':len([b for b in self.accounts.values() if b.bound_uid]),'connected':len([b for b in self.accounts.values() if b.bound_uid and b.state=='CONNECTED']),'pending_logins':len([b for b in self.accounts.values() if not b.bound_uid]),'portals':sum(b.db.execute('SELECT count(*) FROM portals').fetchone()[0] for b in self.accounts.values()),'media_pending':sum(b.db.execute("SELECT count(*) FROM media_inbox WHERE status='pending'").fetchone()[0] for b in self.accounts.values()),'media_failed':sum(b.db.execute("SELECT count(*) FROM media_inbox WHERE status='error'").fetchone()[0] for b in self.accounts.values()),'media_sent':sum(b.db.execute("SELECT count(*) FROM media_inbox WHERE status='sent'").fetchone()[0] for b in self.accounts.values())})
 async def sweep(self):
  while True:
   await asyncio.sleep(10)
   for job,entry in list(self.jobs.items()):
    b=self.accounts.get(entry['slot'])
    # Retain complete steps briefly so a delayed browser can observe success.
    if entry['expires']<time.monotonic():await self.discard(job)
 async def startup(self,app):
  self.pw=await async_playwright().start();self.persist()
  # Unfinished QR logins are intentionally not restored after a service restart.
  for slot,b in list(self.accounts.items()):
   if slot!='primary' and not b.bound_uid:
    b.db.close();del self.accounts[slot];self.registry.pop(slot,None)
   else:await b.startup(None)
  self.persist();self.housekeeper=asyncio.create_task(self.sweep());self.started=True
 async def cleanup(self,app):
  if self.housekeeper:self.housekeeper.cancel();await asyncio.gather(self.housekeeper,return_exceptions=True)
  await asyncio.gather(*(b.cleanup(None) for b in list(self.accounts.values())))
  if self.browser:await self.browser.close()
  if self.pw:await self.pw.stop()

def create_app(manager):
 app=web.Application(middlewares=[errors],client_max_size=1024*1024)
 app.router.add_get('/healthz',manager.health)
 app.router.add_route('*','/_matrix/provision/v3/{tail:.*}',manager.provisioning)
 app.router.add_put('/_matrix/app/v1/transactions/{txn}',manager.transaction)
 app.router.add_post('/_matrix/app/v1/ping',manager.accounts['primary'].ping)
 app.on_startup.append(manager.startup);app.on_cleanup.append(manager.cleanup);return app

if __name__=='__main__':web.run_app(create_app(Manager()),host='0.0.0.0',port=29390,access_log=None)
