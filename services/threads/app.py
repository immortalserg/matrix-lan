"""Owner-authenticated social sessions. A successful login alone is not CONNECTED."""
import asyncio,base64,contextlib,json,os,re,secrets,time,shutil
from pathlib import Path
from urllib.parse import urlparse
from aiohttp import web,ClientSession,ClientTimeout
from playwright.async_api import async_playwright
P=os.environ.get('SOCIAL_PLATFORM','ok');LABELS={'ok':'Одноклассники','threads':'Threads','vk':'VK'};LABEL=LABELS[P];DATA=Path('/data')
SITES={'ok':'https://ok.ru/messages','threads':'https://www.threads.com/messages','vk':'https://m.vk.ru/login'}
def atomic(path,value):
 tmp=path.with_suffix('.tmp');fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
 with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
 os.replace(tmp,path)
@web.middleware
async def errors(request,handler):
 if request.headers.get('Origin') and not re.fullmatch(r'__PUBLIC_ORIGIN_RE__',request.headers['Origin']):return web.json_response({'error':'Недопустимый источник запроса'},status=403)
 try:r=await handler(request)
 except web.HTTPException as e:r=web.json_response({'error':e.reason},status=e.status)
 except asyncio.CancelledError:raise
 except Exception as e:print('REQUEST_ERROR',type(e).__name__,flush=True);r=web.json_response({'error':'Не удалось завершить действие. Проверьте состояние сервиса.'},status=503)
 r.headers['Cache-Control']='private, no-store';return r
class Gateway:
 def __init__(self,data=DATA):
  self.data=Path(data);self.label=LABEL;self.owner=json.loads(Path('/run/matrix-owner.json').read_text());self.registry_path=self.data/'accounts.json';self.registry=json.loads(self.registry_path.read_text()) if self.registry_path.exists() else {};self.states={};self.context=None;self.page=None;self.http=None;self.pw=None;self.jobs={};self.locks={};self.tasks=[];self.auth_cache={};self.bridge=None;self.backoff_until=0
 def persist(self):atomic(self.registry_path,self.registry)
 def account(self):return next(iter(self.registry.values()),None)
 async def authorize(self,request):
  token=request.headers.get('Authorization','').removeprefix('Bearer ')
  if request.query.get('user_id')!=self.owner['owner']:raise web.HTTPForbidden(reason='Разрешено владельцу Matrix')
  if len(token)<12:raise web.HTTPUnauthorized(reason='Требуется вход Matrix')
  if self.auth_cache.get(token,0)>time.monotonic():return
  async with self.http.get(self.owner['homeserver']+'/_matrix/client/v3/account/whoami',headers={'Authorization':'Bearer '+token}) as r:
   if r.status!=200:raise web.HTTPUnauthorized(reason='Сессия Matrix истекла')
   value=await r.json()
  if value.get('user_id')!=self.owner['owner']:raise web.HTTPForbidden(reason='Разрешено владельцу Matrix')
  self.auth_cache={token:time.monotonic()+20}
 async def browser(self,slot='primary'):
  if self.page and not self.page.is_closed():return self.page
  folder=self.data/'profile';folder.mkdir(exist_ok=True)
  self.context=await self.pw.chromium.launch_persistent_context(str(folder),headless=True,locale='ru-RU',viewport={'width':1280,'height':900},args=['--no-sandbox','--disable-dev-shm-usage'])
  self.page=self.context.pages[0] if self.context.pages else await self.context.new_page();await self.page.goto(SITES[P],wait_until='domcontentloaded',timeout=60000);return self.page
 async def logged_in(self,slot='primary'):
  page=await self.browser()
  if P=='ok':return await page.evaluate("()=>!!document.querySelector('msg-page')?.store?.data?.auth?.profile?.id")
  if P=='threads':return any(c['name']=='ds_user_id' and c['value'] not in ['', '0'] for c in await self.context.cookies('https://www.threads.com'))
  return await page.evaluate("()=>!!window.vk?.id&&Number(window.vk.id)>0")
 async def identity(self):
  page=await self.browser()
  if P=='ok':return await page.evaluate("()=>{const p=document.querySelector('msg-page')?.store?.data?.auth?.profile;return p?{id:String(p.id),name:p.name}:null}")
  if P=='threads':
   uid=next((c['value'] for c in await self.context.cookies('https://www.threads.com') if c['name']=='ds_user_id'),None)
   return {'id':uid,'name':f'Threads · …{uid[-4:]}'} if uid else None
  return await page.evaluate("()=>window.vk?.id>0?{id:String(vk.id),name:vk?.user?.first_name||'VK'}:null")
 async def register(self):
  info=await self.identity()
  if not info or not info['id']:return
  uid=str(info['id']);self.locks[uid]=self.locks.setdefault('primary',asyncio.Lock())
  if not self.registry:self.registry[uid]={'id':uid,'name':info['name'],'native_id':uid,'connected_at':int(time.time()*1000),'transport_verified':False};self.persist()
  elif self.account()['id']!=uid:raise web.HTTPConflict(reason='Открыт другой аккаунт. Сначала удалите прежнее подключение.')
 async def login_step(self,job):
  row=self.jobs.get(job)
  if not row or row['expires']<time.monotonic():raise web.HTTPGone(reason='Время входа истекло')
  page=await self.browser()
  if await self.logged_in():
   await self.register();row['complete']=True;account=self.account();ready=account.get('transport_verified') and self.states.get(account['id'],{}).get('state_event')=='CONNECTED';return {'type':'complete','login_id':job,'step_id':'done','complete':{'id':account['id'],'name':account['name'],'transport_pending':not ready}}
  text=await page.locator('body').inner_text()
  if P=='vk':return await self.browser_login_step(job,page)
  if 'Flood control' in text:self.backoff_until=time.monotonic()+300;return self.notice(job,'VK ограничил вход: Flood control [9]. Повторите позже; автоматические попытки остановлены.')
  if any(x in text.casefold() for x in ['captcha','проверку безопасности','не робот','подтвердите, что вы']):return self.notice(job,'Сервис запросил проверку безопасности. Выполните её в официальном приложении; автоматический вход остановлен.')
  if P=='ok' and await page.locator('#field_password').count():return self.fields(job,'credentials',[('username','Логин или телефон','username'),('password','Пароль','password')])
  if P=='threads' and await page.locator('input[type=password]').count():return self.fields(job,'credentials',[('username','Логин Instagram / Threads','username'),('password','Пароль','password')])
  if P=='vk' and await page.locator('input[name=login]').count():return self.fields(job,'phone',[('username','Телефон VK ID','phone_number')])
  codes=page.locator('input[autocomplete=one-time-code],input[name=code],input[name=otp],input[inputmode=numeric]')
  if await codes.count():return self.fields(job,'code',[('code','Код подтверждения','2fa_code')])
  return self.notice(job,'Сервис ожидает подтверждение или ещё загружает форму. Нажмите «Проверить ещё раз» через несколько секунд.')
 def notice(self,job,text):return {'type':'display_and_wait','login_id':job,'step_id':'waiting','instructions':text,'display_and_wait':{'type':'notice','data':''}}
 async def browser_login_step(self,job,page):
  if P!='vk' or urlparse(page.url).hostname not in ['vk.ru','m.vk.ru','id.vk.ru','login.vk.ru','vk.com','m.vk.com','id.vk.com','login.vk.com']:raise web.HTTPForbidden(reason='Вход разрешён только на официальной странице VK')
  self.jobs[job]['screen_mode']=True
  shot=await page.screenshot(type='png');size=page.viewport_size
  return {'type':'display_and_wait','login_id':job,'step_id':'browser','instructions':'Официальный экран VK в браузере вашего сервера. Нажимайте прямо на изображение. Проверку безопасности выполните самостоятельно; затем войдите по QR-коду или телефону.','display_and_wait':{'type':'browser_image','data':'data:image/png;base64,'+base64.b64encode(shot).decode(),'width':size['width'],'height':size['height']}}
 async def browser_login_action(self,job,page,values):
  if P!='vk' or not self.jobs[job].get('screen_mode'):raise web.HTTPForbidden()
  if await self.logged_in():return
  if urlparse(page.url).hostname not in ['vk.ru','m.vk.ru','id.vk.ru','login.vk.ru','vk.com','m.vk.com','id.vk.com','login.vk.com']:raise web.HTTPForbidden()
  self.jobs[job]['expires']=time.monotonic()+600
  action=values.get('action');size=page.viewport_size
  if action=='click':
   x,y=values.get('x'),values.get('y')
   if not isinstance(x,(int,float)) or not isinstance(y,(int,float)) or not 0<=x<size['width'] or not 0<=y<size['height']:raise web.HTTPBadRequest()
   await page.mouse.click(x,y)
  elif action=='text':
   text=values.get('text')
   if not isinstance(text,str) or not 0<len(text)<=512:raise web.HTTPBadRequest()
   await page.keyboard.insert_text(text)
  elif action=='key' and values.get('key') in ['Enter','Tab','Backspace','ArrowUp','ArrowDown','ArrowLeft','ArrowRight']:await page.keyboard.press(values['key'])
  elif action=='scroll' and isinstance(values.get('dy'),int) and abs(values['dy'])<=900:await page.mouse.wheel(0,values['dy'])
  elif action!='refresh':raise web.HTTPBadRequest()
  await asyncio.sleep(.4)
 def fields(self,job,step,fields):return {'type':'user_input','login_id':job,'step_id':step,'instructions':'Войдите в '+LABEL+'. Пароль и коды передаются только этому сервису и не сохраняются в форме Matrix.','user_input':{'fields':[{'id':i,'name':n,'type':t} for i,n,t in fields]}}
 async def handler(self,request):
  await self.authorize(request);tail=request.match_info['tail']
  if tail=='diagnostics' and request.method=='GET':
   page=await self.browser();local=self.data/'diagnostics.js';return web.json_response(await page.evaluate((local if local.exists() else Path('/social/diagnostics.js')).read_text()))
  if tail=='self-message' and request.method=='POST':
   if P!='ok' or not self.account():raise web.HTTPNotFound()
   values=await request.json();key=values.get('idempotency_key');body=values.get('text')
   if not isinstance(key,str) or not re.fullmatch('[A-Za-z0-9_-]{8,80}',key) or not isinstance(body,str) or not body.startswith('OK-APP-TEST ') or len(body)>200:raise web.HTTPBadRequest()
   path=self.data/'self-tests.json'
   async with self.locks.setdefault('primary',asyncio.Lock()):
    ledger=json.loads(path.read_text()) if path.exists() else {}
    if key in ledger:return web.json_response(ledger[key])
    identity=await self.identity();assert identity['id']==self.account()['id'];ledger[key]={'state':'attempting'};atomic(path,ledger)
    result=await self.bridge.native(identity['id'],'send',{'cid':identity['id'],'body':body});ledger[key]={'state':'sent','native_id':result['id']};atomic(path,ledger);return web.json_response(ledger[key])
  if tail=='whoami':
   rows=[{k:v for k,v in dict(a,state=self.states.get(slot,{'state_event':'CONNECTING'}),setup_pending=not a.get('transport_verified')).items() if k in ['id','name','state','setup_pending','space_room']} for slot,a in self.registry.items()]
   return web.json_response({'bridge_bot':'@'+P+'bot:'+self.owner['server_name'],'logins':rows,'login_flows':[{'id':'web-login','name':'Вход в '+LABEL}],'capabilities':{'text':P in ['ok','threads'],'media':False}})
  if tail=='login/flows':return web.json_response({'flows':[{'id':'web-login','name':'Вход в '+LABEL}]})
  if tail=='login/start/web-login' and request.method=='POST':
   if any(not j.get('complete') and j['expires']>time.monotonic() for j in self.jobs.values()):raise web.HTTPConflict(reason='Закройте прежнюю попытку входа')
   if time.monotonic()<self.backoff_until:raise web.HTTPTooManyRequests(reason='VK временно ограничил вход. Повторите позднее.')
   page=await self.browser()
   if P=='threads' and not await self.logged_in():await page.goto('https://www.threads.com/login',wait_until='domcontentloaded',timeout=45000)
   job=secrets.token_urlsafe(24);self.jobs[job]={'expires':time.monotonic()+600};return web.json_response(await self.login_step(job))
  m=re.fullmatch(r'login/step/([A-Za-z0-9_-]+)/([a-z]+)/([a-z_]+)',tail)
  if m and request.method=='POST':
   job,step,kind=m.groups()
   if job not in self.jobs:raise web.HTTPNotFound(reason='Попытка входа не найдена')
   if self.jobs[job]['expires']<time.monotonic():raise web.HTTPGone(reason='Время входа истекло')
   async with self.locks.setdefault('primary',asyncio.Lock()):
    page=await self.browser()
    if step=='browser' and kind=='user_input':
     await self.browser_login_action(job,page,await request.json());return web.json_response(await self.login_step(job))
    if kind=='user_input':
     values=await request.json()
     if step=='credentials':
      if P=='ok':await page.locator('#field_email').fill(values['username']);await page.locator('#field_password').fill(values['password'])
      else:await page.locator('input[type=text]').fill(values['username']);await page.locator('input[type=password]').fill(values['password'])
      await page.get_by_role('button',name='Войти',exact=True).click()
     elif step=='phone':await page.locator('input[name=login]').fill(values['username']);await page.get_by_role('button',name=re.compile('^(Войти|Продолжить)$')).click()
     elif step=='code':await page.locator('input[autocomplete=one-time-code],input[name=code],input[name=otp],input[inputmode=numeric]').first.fill(values['code']);await page.get_by_role('button',name=re.compile('^(Продолжить|Подтвердить|Войти)$')).click()
     else:raise web.HTTPBadRequest()
     values.clear();await asyncio.sleep(4)
    elif P!='vk':await asyncio.sleep(15)
    return web.json_response(await self.login_step(job))
  m=re.fullmatch(r'login/cancel/([A-Za-z0-9_-]+)',tail)
  if m and request.method=='POST':self.jobs.pop(m[1],None);return web.json_response({})
  m=re.fullmatch(r'logout/([A-Za-z0-9_-]+)',tail)
  if m and request.method=='POST':
   if not self.registry or self.account()['id']!=m[1]:raise web.HTTPNotFound()
   async with self.locks.setdefault('primary',asyncio.Lock()):
    self.registry.clear();self.states.clear();self.persist();self.jobs.clear();self.bridge.store.delete_account(m[1])
    if self.context:await self.context.close()
    self.context=self.page=None;profile=(self.data/'profile').resolve();assert profile.parent==self.data.resolve();shutil.rmtree(profile)
   return web.json_response({})
  if tail in ['chats','chats/open']:
   slot=self.account()['id'] if self.account() else '';chats=self.bridge.chats.get(slot,[])
   if tail=='chats':return web.json_response({'chats':chats})
   value=await request.json();chat=next((c for c in chats if c['id']==value.get('id')),None)
   if not chat:raise web.HTTPNotFound(reason='Переписка не найдена')
   async with self.locks.setdefault('primary',asyncio.Lock()):rid=await self.bridge.portal(slot,chat)
   return web.json_response({'room_id':rid})
  raise web.HTTPNotFound()
 async def maintenance(self):
  while True:
   try:
    async with self.locks.setdefault('primary',asyncio.Lock()):
     if await self.logged_in():await self.register()
     for job,row in list(self.jobs.items()):
      if row['expires']<time.monotonic():self.jobs.pop(job,None)
   except asyncio.CancelledError:raise
   except Exception as e:print('SESSION_CHECK_ERROR',type(e).__name__,flush=True)
   await asyncio.sleep(30)
 async def startup(self,app):
  self.http=ClientSession(timeout=ClientTimeout(total=30));self.pw=await async_playwright().start()
  from social_bridge import SocialBridge
  self.bridge=SocialBridge(self,P);self.tasks=[asyncio.create_task(self.maintenance()),asyncio.create_task(self.bridge.worker()),asyncio.create_task(self.bridge.media.worker())]
 async def cleanup(self,app):
  for t in self.tasks:t.cancel()
  await asyncio.gather(*self.tasks,return_exceptions=True)
  if self.context:await self.context.close()
  if self.pw:await self.pw.stop()
  if self.http:await self.http.close()
  if self.bridge:self.bridge.store.close()
 def app(self):
  app=web.Application(middlewares=[errors],client_max_size=1024*1024);app.router.add_get('/health',lambda r:web.json_response({'ok':True,'platform':P}));app.router.add_put('/_matrix/app/v1/transactions/{txn}',self.transaction);app.router.add_route('*','/_matrix/provision/v3/{tail:.*}',self.handler);app.on_startup.append(self.startup);app.on_cleanup.append(self.cleanup);return app
 async def transaction(self,request):return await self.bridge.as_transaction(request)
if __name__=='__main__':
 if P=='vk':
  import sys
  sys.modules.setdefault('app',sys.modules[__name__])
  from vk_gateway import VKGateway
  gateway=VKGateway()
 else:gateway=Gateway()
 web.run_app(gateway.app(),host='0.0.0.0',port=29393,access_log=None)
