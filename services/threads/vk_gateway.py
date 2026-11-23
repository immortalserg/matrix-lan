"""Isolated persistent VK profiles; QR/code entry always belongs to one login job."""
import asyncio,base64,contextlib,json,re,secrets,shutil,time,hashlib
from pathlib import Path
from urllib.parse import urlparse
from aiohttp import web
from app import Gateway,atomic

ALLOWED={'vk.ru','www.vk.ru','m.vk.ru','id.vk.ru','login.vk.ru','vk.com','www.vk.com','m.vk.com','id.vk.com','login.vk.com'}
CODE_SELECTOR='input[autocomplete="one-time-code"],input[name="code"],input[name="otp"],input[inputmode="numeric"],input[maxlength="1"][type="text"],input[maxlength="1"][type="tel"],input[maxlength="6"]'
IDENTITY="""()=>{const id=window.vk?.id;return Number.isSafeInteger(Number(id))&&Number(id)>0?{id:String(id),name:window.vk?.user?.first_name||'VK · '+id}:null}"""

class VKGateway(Gateway):
 def __init__(self,*args,**kwargs):
  super().__init__(*args,**kwargs);self.contexts={};self.pages={};self.used={};self.context_lock=asyncio.Lock();self.login_profile='legacy'
  for uid,row in self.registry.items():row.setdefault('profile_key','legacy');self.locks[uid]=self.profile_lock(row['profile_key'])
 def profile_key(self,slot='primary'):
  key=self.login_profile if slot=='primary' else self.registry.get(slot,{}).get('profile_key',slot)
  if key!='legacy' and not re.fullmatch('[a-f0-9]{24}',str(key)):raise web.HTTPBadRequest(reason='Неизвестный профиль VK')
  return key
 def profile_path(self,key):
  key=self.profile_key(key);return self.data/'profile' if key=='legacy' else self.data/'profiles'/key
 def profile_lock(self,key):return self.locks.setdefault(key,asyncio.Lock())
 def pinned(self):return {j['profile'] for j in self.jobs.values() if not j.get('complete') and j['expires']>time.monotonic()}
 async def browser(self,slot='primary'):
  key=self.profile_key(slot)
  async with self.context_lock:
   if key in self.pages and not self.pages[key].is_closed():self.used[key]=time.monotonic();return self.pages[key]
   if len(self.contexts)>=2:
    candidates=[k for k in self.contexts if k not in self.pinned() and not self.profile_lock(k).locked()]
    if not candidates:raise web.HTTPConflict(reason='Завершите текущий вход VK и повторите действие')
    victim=min(candidates,key=lambda k:self.used.get(k,0));await self.contexts.pop(victim).close();self.pages.pop(victim,None);self.used.pop(victim,None)
   folder=self.profile_path(key);folder.mkdir(parents=True,exist_ok=True)
   context=await self.pw.chromium.launch_persistent_context(str(folder),headless=True,locale='ru-RU',viewport={'width':1280,'height':900},args=['--no-sandbox','--disable-dev-shm-usage'])
   page=context.pages[0] if context.pages else await context.new_page();self.contexts[key]=context;self.pages[key]=page;self.used[key]=time.monotonic()
   try:await page.goto('https://vk.ru/',wait_until='domcontentloaded',timeout=60000)
   except Exception:
    await context.close();self.contexts.pop(key,None);self.pages.pop(key,None);raise
   return page
 async def identity(self,slot='primary'):
  page=await self.browser(slot)
  for frame in page.frames:
   address=urlparse(frame.url)
   if address.hostname not in {'vk.ru','www.vk.ru','m.vk.ru','vk.com','www.vk.com','m.vk.com'} or address.path.startswith(('/auth','/login','/challenge')):continue
   with contextlib.suppress(Exception):
    info=await frame.evaluate(IDENTITY)
    if info:return info
  return None
 async def logged_in(self,slot='primary'):return bool(await self.identity(slot))
 async def register_profile(self,key,requested=None):
  info=await self.identity(key)
  if not info:return None
  uid=info['id']
  if requested and uid!=requested:raise web.HTTPConflict(reason='Вошли в другой аккаунт VK. Для переподключения выберите прежний аккаунт.')
  existing=self.registry.get(uid)
  if existing and existing.get('profile_key','legacy')!=key:raise web.HTTPConflict(reason='Этот аккаунт VK уже подключён. Выберите другой аккаунт на телефоне.')
  if not existing:self.registry[uid]={'id':uid,'native_id':uid,'name':info['name'],'profile_key':key,'connected_at':int(time.time()*1000),'transport_verified':False}
  self.locks[uid]=self.profile_lock(key);self.persist();return self.registry[uid]
 async def register(self):return await self.register_profile(self.login_profile)
 async def code_frame(self,page):
  for frame in page.frames:
   if urlparse(frame.url).hostname not in ALLOWED:continue
   text=await frame.locator('body').inner_text()
   if re.search(r'(введите.{0,35}код|код.{0,30}телефона|код подтверждения)',text,re.I|re.S) and await frame.locator(CODE_SELECTOR).filter(visible=True).count():return frame
  return None
 async def login_step(self,job):
  row=self.jobs.get(job)
  if not row or row['expires']<time.monotonic():raise web.HTTPGone(reason='Время входа истекло. Начните новый вход VK.')
  key=row['profile'];self.login_profile=key;page=await self.browser(key)
  account=await self.register_profile(key,row.get('requested'))
  if account:
   row['complete']=True;ready=account.get('transport_verified') and self.states.get(account['id'],{}).get('state_event')=='CONNECTED'
   return {'type':'complete','login_id':job,'step_id':'done','complete':{'id':account['id'],'name':account['name'],'transport_pending':not ready}}
  frame=await self.code_frame(page)
  if frame:
   step=self.fields(job,'code',[('code','Код подтверждения VK','2fa_code')]);step['instructions']='Введите здесь код с телефона или из сообщения VK и нажмите «Продолжить». Код передаётся только официальной странице VK.';return step
  qr=await self.qr_step(job,page)
  if qr:return qr
  return await self.browser_login_step(job,page)
 async def qr_step(self,job,page):
  for frame in page.frames:
   if urlparse(frame.url).hostname not in ALLOWED:continue
   text=await frame.locator('body').inner_text()
   if not re.search(r'QR[-‑ ]?код',text,re.I) and urlparse(frame.url).path!='/qr_auth':continue
   candidates=frame.locator('svg,canvas,img').filter(visible=True)
   for index in range(min(await candidates.count(),20)):
    target=candidates.nth(index);rect=await target.bounding_box()
    if not rect or not 90<=rect['width']<=500 or abs(rect['width']-rect['height'])>20:continue
    shot=await target.screenshot(type='png',timeout=3000)
    return {'type':'display_and_wait','login_id':job,'step_id':'qr','instructions':'Отсканируйте QR-код в приложении VK и подтвердите вход. Если появится код, введите его в следующем поле Matrix.','display_and_wait':{'type':'qr_image','data':'data:image/png;base64,'+base64.b64encode(shot).decode()}}
  return None
 async def submit_code(self,page,values):
  code=values.get('code')
  if not isinstance(code,str) or not re.fullmatch('[0-9]{4,8}',code):raise web.HTTPBadRequest(reason='Введите код VK целиком: от 4 до 8 цифр')
  frame=await self.code_frame(page)
  if not frame:raise web.HTTPConflict(reason='VK сейчас не ожидает код. Обновите экран входа.')
  inputs=frame.locator(CODE_SELECTOR).filter(visible=True);count=await inputs.count();length=await inputs.first.get_attribute('maxlength');buttons=frame.get_by_role('button',name=re.compile('^(Продолжить|Подтвердить|Войти)$')).filter(visible=True)
  explicit=await buttons.count()>0
  if count>=len(code) and length=='1':
   for index,char in enumerate(code):await inputs.nth(index).fill(char)
  else:await inputs.first.fill(code)
  values.clear()
  if explicit:await buttons.first.click()
  await asyncio.sleep(2)
 async def handler(self,request):
  await self.authorize(request);tail=request.match_info['tail']
  if tail=='whoami':
   rows=[{**{k:v for k,v in a.items() if k in ['id','name','space_room']},'state':self.states.get(uid,{'state_event':'CONNECTING'}),'setup_pending':not a.get('transport_verified')} for uid,a in self.registry.items()]
   return web.json_response({'bridge_bot':'@vkbot:'+self.owner['server_name'],'logins':rows,'login_flows':[{'id':'qr','name':'Вход по QR-коду VK'}],'capabilities':{'text':bool(getattr(self.bridge,'vk_text_implemented',False)),'media':False}})
  if tail=='login/flows':return web.json_response({'flows':[{'id':'qr','name':'Вход по QR-коду VK'}]})
  if tail=='self-message' and request.method=='POST':
   uid=request.query.get('login_id')
   if not uid and len(self.registry)==1:uid=next(iter(self.registry))
   if uid not in self.registry:raise web.HTTPBadRequest()
   values=await request.json();key=values.get('idempotency_key');body=values.get('text')
   if not isinstance(key,str) or not re.fullmatch('[A-Za-z0-9_-]{8,80}',key) or not isinstance(body,str) or not body.startswith('VK-APP-TEST ') or len(body)>200:raise web.HTTPBadRequest()
   path=self.data/'self-tests.json'
   async with self.locks[uid]:
    ledger=json.loads(path.read_text()) if path.exists() else {};identity=uid+':'+key
    if identity in ledger:return web.json_response(ledger[identity])
    random_id=int(hashlib.sha256(identity.encode()).hexdigest()[:8],16)%2147483646+1;ledger[identity]={'state':'attempting','random_id':random_id};atomic(path,ledger)
    result=await self.bridge.native(uid,'send',{'cid':uid,'body':body,'random_id':random_id});ledger[identity]={'state':'sent','native_id':result['id']};atomic(path,ledger);return web.json_response(ledger[identity])
  if tail in ['login/start/qr','login/start/web-login'] and request.method=='POST':
   if self.pinned():raise web.HTTPConflict(reason='Сначала завершите или закройте текущий вход VK')
   value=await request.json();requested=value.get('login_id')
   if requested and requested not in self.registry:raise web.HTTPNotFound(reason='Этот аккаунт VK не подключён')
   reset=False
   if requested:
    reset=self.states.get(requested,{}).get('state_event')=='BAD_CREDENTIALS'
    for flag in ['auth_required','checkpoint_required']:self.registry[requested].pop(flag,None)
    self.persist()
   key=self.profile_key(requested) if requested else 'legacy' if not self.registry else secrets.token_hex(12)
   job=secrets.token_urlsafe(24);self.jobs[job]={'profile':key,'requested':requested,'expires':time.monotonic()+600};self.login_profile=key
   async with self.profile_lock(key):
    existed=key in self.pages;page=await self.browser(key)
    if existed and (reset or not await self.logged_in(key)):await page.goto('https://vk.ru/',wait_until='domcontentloaded',timeout=60000)
    return web.json_response(await self.login_step(job))
  match=re.fullmatch(r'login/step/([A-Za-z0-9_-]+)/([a-z]+)/([a-z_]+)',tail)
  if match and request.method=='POST':
   job,step,kind=match.groups();row=self.jobs.get(job)
   if not row:raise web.HTTPNotFound(reason='Попытка входа VK не найдена')
   if row['expires']<time.monotonic():raise web.HTTPGone(reason='Время входа VK истекло')
   key=row['profile'];self.login_profile=key
   async with self.profile_lock(key):
    page=await self.browser(key)
    if kind=='user_input':
     values=await request.json()
     if step=='code':
      try:await self.submit_code(page,values)
      except Exception:
       if not await self.logged_in(key):raise
     elif step=='browser':await self.browser_login_action(job,page,values)
     else:raise web.HTTPBadRequest()
    elif kind=='display_and_wait':await asyncio.sleep(2)
    else:raise web.HTTPBadRequest()
    return web.json_response(await self.login_step(job))
  match=re.fullmatch(r'login/cancel/([A-Za-z0-9_-]+)',tail)
  if match and request.method=='POST':self.jobs.pop(match[1],None);return web.json_response({})
  match=re.fullmatch(r'logout/([A-Za-z0-9_-]+)',tail)
  if match and request.method=='POST':
   uid=match[1]
   if uid not in self.registry:raise web.HTTPNotFound()
   key=self.profile_key(uid)
   async with self.profile_lock(key):
    self.registry.pop(uid);self.states.pop(uid,None);self.persist();self.bridge.store.delete_account(uid)
    for job in [j for j,row in self.jobs.items() if row['profile']==key]:self.jobs.pop(job)
    async with self.context_lock:
     context=self.contexts.pop(key,None)
     if context:await context.close()
     self.pages.pop(key,None);self.used.pop(key,None)
    folder=self.profile_path(key).resolve();assert folder.parent in [self.data.resolve(),(self.data/'profiles').resolve()]
    if folder.exists():shutil.rmtree(folder)
   return web.json_response({})
  if tail in ['chats','chats/open']:
   uid=request.query.get('login_id')
   if not uid and len(self.registry)==1:uid=next(iter(self.registry))
   if uid not in self.registry:raise web.HTTPBadRequest(reason='Выберите аккаунт VK')
   chats=self.bridge.chats.get(uid,[])
   if tail=='chats':return web.json_response({'chats':chats})
   value=await request.json();chat=next((c for c in chats if c['id']==value.get('id')),None)
   if not chat:raise web.HTTPNotFound()
   async with self.locks[uid]:room=await self.bridge.portal(uid,chat)
   return web.json_response({'room_id':room})
  return await super().handler(request)
 async def maintenance(self):
  while True:
   for job,row in list(self.jobs.items()):
    if row['expires']<time.monotonic():self.jobs.pop(job,None);continue
    key=row['profile']
    if key not in self.contexts or row.get('complete'):continue
    try:
     async with self.profile_lock(key):
      account=await self.register_profile(key,row.get('requested'))
      if account:row['complete']=True
    except asyncio.CancelledError:raise
    except Exception as error:print('VK_SESSION_CHECK',type(error).__name__,flush=True)
   await asyncio.sleep(10)
 async def cleanup(self,app):
  for task in self.tasks:task.cancel()
  await asyncio.gather(*self.tasks,return_exceptions=True)
  for context in self.contexts.values():
   with contextlib.suppress(Exception):await context.close()
  if self.pw:await self.pw.stop()
  if self.http:await self.http.close()
  if self.bridge:self.bridge.store.close()
