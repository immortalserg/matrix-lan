"""Isolated buyer-session provisioning. Connected means verified message transport,
never merely a cookie or a completed QR login. No credentials in HTTP/log output.
"""
import asyncio,base64,contextlib,json,os,re,secrets,shutil,time
from pathlib import Path
from urllib.parse import unquote
from aiohttp import web,ClientSession,ClientTimeout
from playwright.async_api import async_playwright

PLATFORM=os.environ.get('SHOPPING_PLATFORM','taobao')
SITES={
 'taobao':{'label':'Taobao','url':'https://market.m.taobao.com/app/im/chat/index.html','login':'login.taobao.com','selector':'canvas','instructions':'Откройте Taobao на телефоне → сканер QR-кода → отсканируйте код и подтвердите вход.'},
 'pinduoduo':{'label':'Pinduoduo','url':'https://mobile.yangkeduo.com/chat_list.html','login':'/login.html','selector':'img.qr-code-box','instructions':'Откройте Pinduoduo на телефоне → сканер QR-кода → отсканируйте код и подтвердите вход.'},
}
if PLATFORM not in SITES:raise RuntimeError('Unsupported platform')
SITE=SITES[PLATFORM];DATA=Path(os.getenv('SHOPPING_DATA','/data'))

def atomic(path,value):
 path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_suffix('.tmp');fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
 with os.fdopen(fd,'w',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
 os.replace(tmp,path)

@web.middleware
async def errors(request,handler):
 if request.headers.get('Origin') and not re.fullmatch(r'__PUBLIC_ORIGIN_RE__',request.headers['Origin']):
  return web.json_response({'error':'Недопустимый источник запроса'},status=403)
 try:response=await handler(request)
 except web.HTTPException as e:response=web.json_response({'error':e.reason},status=e.status)
 except asyncio.CancelledError:raise
 except Exception as e:
  print('REQUEST_ERROR',type(e).__name__,flush=True)
  response=web.json_response({'error':'Не удалось выполнить шаг. Проверьте подключение или начните вход заново.'},status=503)
 response.headers['Cache-Control']='private, no-store'
 return response

class Gateway:
 def __init__(self,data=DATA):
  self.data=Path(data);self.data.mkdir(parents=True,exist_ok=True);self.data.chmod(0o700)
  self.owner=json.loads(Path(os.getenv('SHOPPING_OWNER_CONFIG','/run/matrix-owner.json')).read_text())
  self.registry_path=self.data/'accounts.json';self.registry=json.loads(self.registry_path.read_text()) if self.registry_path.exists() else {}
  self.contexts={};self.pages={};self.jobs={};self.states={};self.auth_cache={};self.locks={};self.pw=None;self.http=None;self.tasks=[];self.bridge=None
 def persist(self):atomic(self.registry_path,self.registry)
 async def authorize(self,request):
  raw=request.headers.get('Authorization','')
  if not raw.startswith('Bearer ') or len(raw)<12:raise web.HTTPUnauthorized(reason='Требуется вход Matrix')
  token=raw[7:];owner=self.owner['owner']
  if request.query.get('user_id')!=owner:raise web.HTTPForbidden(reason='Разрешено владельцу Matrix')
  cached=self.auth_cache.get(token,0)
  if cached>time.monotonic():return
  async with self.http.get(self.owner['homeserver']+'/_matrix/client/v3/account/whoami',headers={'Authorization':'Bearer '+token}) as r:
   if r.status!=200:raise web.HTTPUnauthorized(reason='Сессия Matrix истекла')
   data=await r.json()
  if data.get('user_id')!=owner:raise web.HTTPForbidden(reason='Разрешено владельцу Matrix')
  self.auth_cache={k:v for k,v in self.auth_cache.items() if v>time.monotonic()};self.auth_cache[token]=time.monotonic()+20
 async def browser(self,slot):
  if slot in self.pages and not self.pages[slot].is_closed():return self.pages[slot]
  if slot in self.contexts:
   ctx=self.contexts[slot]
   try:
    page=await ctx.new_page();self.pages[slot]=page;await page.goto(SITE['url'],wait_until='domcontentloaded',timeout=45000);return page
   except Exception:
    with contextlib.suppress(Exception):await ctx.close()
    self.contexts.pop(slot,None);self.pages.pop(slot,None)
  folder=self.data/'profiles'/slot;folder.mkdir(parents=True,exist_ok=True);folder.chmod(0o700)
  ctx=await self.pw.chromium.launch_persistent_context(str(folder),headless=True,locale='zh-CN',viewport={'width':1280,'height':850},args=['--no-sandbox','--disable-dev-shm-usage'])
  self.contexts[slot]=ctx;page=ctx.pages[0] if ctx.pages else await ctx.new_page();self.pages[slot]=page
  await page.goto(SITE['url'],wait_until='domcontentloaded',timeout=45000)
  return page
 def is_error_page(self,page):
  u=(getattr(page,'url','') or '').lower()
  return (not u) or u.startswith('about:') or u.startswith('chrome-error:') or u.startswith('data:')
 async def ensure_loaded(self,slot):
  page=await self.browser(slot)
  if self.is_error_page(page):
   await page.goto(SITE['url'],wait_until='domcontentloaded',timeout=45000)
   await asyncio.sleep(2)
  return page
 async def login_view(self,slot):
  page=await self.browser(slot)
  if PLATFORM=='pinduoduo' and '/login.html' in page.url and not await page.locator('img.qr-code-box').count():
   await page.get_by_text('扫码登录',exact=True).click(timeout=15000)
  return page
 async def logged_in(self,slot):
  page=self.pages.get(slot)
  if not page or page.is_closed():return False
  if PLATFORM=='taobao':
   if 'login.taobao.com' in page.url or '/app/im/chat/' not in page.url:return False
   cookies=await self.contexts[slot].cookies('https://www.taobao.com')
   return any(c['name']=='unb' and c['value'] for c in cookies)
  return 'mobile.yangkeduo.com/chat_list.html' in page.url and not await page.locator('#login').count()
 async def register_login(self,slot):
  if slot not in self.registry:
   name=SITE['label']+' · покупатель'
   if PLATFORM=='taobao':
    cookies=await self.contexts[slot].cookies('https://www.taobao.com')
    nick=next((unquote(c['value']) for c in cookies if c['name']=='tracknick'),'')
    if nick:name=nick[:100]
   self.registry[slot]={'id':slot,'name':name,'authenticated_at':time.time()};self.persist()
  if not self.registry[slot].get('transport_verified'):self.states[slot]={'state_event':'CONNECTING','error':'Вход выполнен; обмен сообщениями ещё проверяется','timestamp':int(time.time())}
 async def qr(self,page):
  if PLATFORM=='pinduoduo':
   await page.wait_for_function("""()=>{const i=document.querySelector('img.qr-code-box');return i&&i.complete&&i.naturalWidth>=100&&i.src.startsWith('data:image/png;base64,')}""",timeout=20000)
  locator=page.locator(SITE['selector']).first
  await locator.wait_for(state='visible',timeout=12000)
  raw=await locator.screenshot(timeout=12000)
  return 'data:image/png;base64,'+base64.b64encode(raw).decode()
 async def step(self,job,wait=False):
  row=self.jobs.get(job)
  if not row or row['expires']<time.monotonic():raise web.HTTPGone(reason='Время входа истекло. Начните заново.')
  slot=row['slot']
  async with self.locks.setdefault(slot,asyncio.Lock()):
   if job not in self.jobs:raise web.HTTPGone(reason='Вход отменён')
   page=await self.login_view(slot)
   if wait:await asyncio.sleep(3)
   if job not in self.jobs:raise web.HTTPGone(reason='Вход отменён')
   if await self.logged_in(slot):
    await self.register_login(slot);row['complete']=True
    return {'type':'complete','login_id':job,'step_id':'done','complete':{'id':slot,'name':self.registry[slot]['name'],'transport_pending':not self.registry[slot].get('transport_verified')}}
   text=await page.locator('body').inner_text()
   if any(s in page.url.lower() for s in ['punish','captcha','_____tmd_____']) or any(s in text for s in ['拖动滑块','请完成安全验证']):
    return {'type':'display_and_wait','login_id':job,'step_id':'verification','instructions':'Сервис запросил дополнительную проверку безопасности. Сообщения ещё не подключены. Не повторяйте вход многократно.','display_and_wait':{'type':'notice','data':''}}
   return {'type':'display_and_wait','login_id':job,'step_id':'qr','instructions':SITE['instructions'],'display_and_wait':{'type':'qr_image','data':await self.qr(page)}}
 async def close_slot(self,slot,remove=False):
  ctx=self.contexts.pop(slot,None);self.pages.pop(slot,None)
  if ctx:
   with contextlib.suppress(Exception):await ctx.close()
  if remove:
   target=(self.data/'profiles'/slot).resolve();root=(self.data/'profiles').resolve()
   if target.parent!=root or not re.fullmatch('[a-f0-9]{32}',slot):raise RuntimeError('Invalid profile path')
   if target.exists():shutil.rmtree(target)
 async def handler(self,request):
  await self.authorize(request);tail=request.match_info['tail']
  if tail=='whoami' and request.method=='GET':
   rows=[{key:value for key,value in dict(v,setup_pending=not v.get('transport_verified'),state=self.states.get(k,{'state_event':'CONNECTING','error':'Проверяется сохранённая сессия'})).items() if key not in ['seen_chats']} for k,v in self.registry.items()]
   return web.json_response({'bridge_bot':'@'+PLATFORM+'bot:'+self.owner['server_name'],'login_flows':[{'id':'qr','name':'QR-код '+SITE['label']}],'logins':rows})
  if tail=='login/flows' and request.method=='GET':return web.json_response({'flows':[{'id':'qr','name':'QR-код'}]})
  if tail in ['chats','chats/open']:
   slot=request.query.get('login_id') or (next(iter(self.registry)) if len(self.registry)==1 else '')
   if slot not in self.registry:raise web.HTTPNotFound(reason='Аккаунт не найден')
   if not self.bridge or slot not in self.bridge.chats:raise web.HTTPServiceUnavailable(reason='Список переписок ещё загружается. Повторите через минуту.')
   chats=self.bridge.chats[slot]
   if tail=='chats' and request.method=='GET':return web.json_response({'chats':chats})
   if tail=='chats/open' and request.method=='POST':
    body=await request.json();chat=next((x for x in chats if x['id']==body.get('id')),None)
    if not chat:raise web.HTTPNotFound(reason='Переписка не найдена')
    async with self.locks.setdefault(slot,asyncio.Lock()):await self.bridge.refresh_chat(slot,chat)
    rid=self.bridge.store.db.execute('SELECT room_id FROM chats WHERE slot=? AND cid=?',(slot,chat['id'])).fetchone()['room_id']
    return web.json_response({'room_id':rid})
  if tail=='diagnostics' and request.method=='GET':
   private_probe=self.data/'media-diagnostics.js'
   if private_probe.is_file():
    values=[]
    for slot in self.registry:
     async with self.locks.setdefault(slot,asyncio.Lock()):
      page=await self.browser(slot)
      target=next((f for f in page.frames if '/app/im/chat-core/' in f.url),None) if PLATFORM=='taobao' else self.bridge.detail_pages.get(slot)
      if target:values.append(await target.evaluate(private_probe.read_text()))
    return web.json_response(values)
   # This path is intentionally absent from the public Caddy allowlist.
   rows=[]
   for slot in list(self.registry):
    async with self.locks.setdefault(slot,asyncio.Lock()):
     page=await self.browser(slot);frames=[]
     for frame in page.frames:
      try:
       data=await frame.evaluate("""()=>{const s=window._imsdk,methods=o=>o?[...new Set([...Object.keys(o),...Object.getOwnPropertyNames(Object.getPrototypeOf(o)||{})])].filter(x=>typeof o[x]==='function').slice(0,80):[];return {url:location.origin+location.pathname,title:document.title,text:document.body?.innerText.slice(0,1500),sdk:!!s,sdkMethods:methods(s),base:s?{currentUserId:s.context?.base?.currentUserId,userId:s.context?.base?.userId}:null,msgMethods:methods(s?.getOriginSdk?.()?.getMsgService?.()),conversationMethods:methods(s?.getConversationService?.()),conversations:document.querySelectorAll('.conversation-item').length,globals:Object.keys(window).filter(x=>/sdk|store|chat|titan|__init|__ssr/i.test(x)).slice(0,50)}}""")
       frames.append(data)
      except Exception as e:frames.append({'error':type(e).__name__})
     rows.append({'slot':slot,'logged_in':await self.logged_in(slot),'frames':frames})
   return web.json_response({'accounts':rows})
  if tail=='login/start/qr' and request.method=='POST':
   # One authenticated buyer account per platform until duplicate-account detection
   # has been calibrated against the native IM identity.
   if self.registry:
    slot=next(iter(self.registry))
    if self.states.get(slot,{}).get('state_event')!='CONNECTED':
     async with self.locks.setdefault(slot,asyncio.Lock()):
      page=await self.browser(slot);await page.goto(SITE['url'],wait_until='domcontentloaded',timeout=45000)
   else:
    pending=[j for j in self.jobs.values() if not j.get('complete') and j['expires']>time.monotonic()]
    if pending:raise web.HTTPConflict(reason='Уже открыт вход. Завершите его или закройте окно подключения.')
    slot=secrets.token_hex(16)
   job=secrets.token_urlsafe(24);self.jobs[job]={'slot':slot,'expires':time.monotonic()+600}
   return web.json_response(await self.step(job))
  m=re.fullmatch(r'login/step/([A-Za-z0-9_-]+)/([a-z]+)/display_and_wait',tail)
  if m and request.method=='POST':return web.json_response(await self.step(m[1],True))
  m=re.fullmatch(r'login/cancel/([A-Za-z0-9_-]+)',tail)
  if m and request.method=='POST':
   row=self.jobs.pop(m[1],None)
   if row:
    async with self.locks.setdefault(row['slot'],asyncio.Lock()):
     if row['slot'] not in self.registry:await self.close_slot(row['slot'],True)
   return web.json_response({})
  m=re.fullmatch(r'logout/([a-f0-9]{32})',tail)
  if m and request.method=='POST':
   slot=m[1]
   async with self.locks.setdefault(slot,asyncio.Lock()):
    self.registry.pop(slot,None);self.states.pop(slot,None);self.persist()
    if self.bridge:self.bridge.store.delete_account(slot)
    self.jobs={k:v for k,v in self.jobs.items() if v['slot']!=slot};await self.close_slot(slot,True)
   return web.json_response({})
  raise web.HTTPNotFound()
 async def maintenance(self):
  while True:
   for slot in list(self.registry):
    try:
     async with self.locks.setdefault(slot,asyncio.Lock()):
      page=await self.ensure_loaded(slot)
      if not await self.logged_in(slot):
       if self.is_error_page(page):
        self.states[slot]={'state_event':'TRANSIENT_DISCONNECT','error':'Страница сервиса временно не загрузилась, повторяем попытку','timestamp':int(time.time())}
       else:
        self.states[slot]={'state_event':'BAD_CREDENTIALS','error':'Нужен повторный вход по QR-коду','timestamp':int(time.time())}
    except asyncio.CancelledError:raise
    except Exception as e:self.states[slot]={'state_event':'UNREACHABLE','error':'Страница сервиса недоступна','timestamp':int(time.time())};print('BROWSER_ERROR',type(e).__name__,flush=True)
   for job,row in list(self.jobs.items()):
    if row['expires']<time.monotonic():
     self.jobs.pop(job,None)
     async with self.locks.setdefault(row['slot'],asyncio.Lock()):
      if row['slot'] not in self.registry:await self.close_slot(row['slot'],True)
   await asyncio.sleep(15)
 async def startup(self,app):
  self.http=ClientSession(timeout=ClientTimeout(total=20));self.pw=await async_playwright().start();self.tasks=[asyncio.create_task(self.maintenance())]
  if (self.data/'matrix.json').exists():
   from matrix_bridge import MatrixBridge
   self.bridge=MatrixBridge(self,PLATFORM);self.label=SITE['label'];self.tasks.extend([asyncio.create_task(self.bridge.worker()),asyncio.create_task(self.bridge.media.worker())])
 async def cleanup(self,app):
  for t in self.tasks:t.cancel()
  await asyncio.gather(*self.tasks,return_exceptions=True)
  for slot in list(self.contexts):await self.close_slot(slot)
  if self.pw:await self.pw.stop()
  if self.http:await self.http.close()
  if self.bridge:self.bridge.store.close()
 async def transaction(self,request):
  if not self.bridge:raise web.HTTPServiceUnavailable()
  return await self.bridge.as_transaction(request)
 def app(self):
  a=web.Application(middlewares=[errors],client_max_size=8*1024*1024);a.router.add_get('/health',lambda r:web.json_response({'ok':True,'platform':PLATFORM}))
  a.router.add_put('/_matrix/app/v1/transactions/{txn}',self.transaction)
  a.router.add_route('*','/_matrix/provision/v3/{tail:.*}',self.handler);a.on_startup.append(self.startup);a.on_cleanup.append(self.cleanup);return a

if __name__=='__main__':web.run_app(Gateway().app(),host='0.0.0.0',port=29392,access_log=None)
