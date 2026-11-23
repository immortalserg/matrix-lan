"""Owner-scoped account inventory and Matrix Space reconciliation.
Discovers bridge accounts every 30 seconds and reconciles Matrix Spaces. No chat sends.
"""
import concurrent.futures,hashlib,json,os,threading,time,secrets,re
from msb_cbmrs import Notifier
from account_lifecycle import AccountLifecycle
from grouping import AccountGrouping
from account_diagnostics import diagnose
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from urllib.parse import quote,urlsplit,parse_qs,urlencode
DATA=Path(os.getenv('ACCOUNTS_DATA','/data'))
def q(x):return quote(str(x),safe='')
def call(url,token,method='GET',body=None):
 req=Request(url,data=None if body is None else json.dumps(body).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method=method)
 try:
  with urlopen(req,timeout=18) as r:return r.status,json.load(r)
 except HTTPError as e:
  try:return e.code,json.load(e)
  except Exception:return e.code,{}

class Monitor:
 def __init__(self,config,directory=DATA):
  self.data=directory
  self.c=config;self.lock=threading.Lock();self.refresh_lock=threading.Lock();self.last_start=0;self.auth={};self.stop=threading.Event();self.refresh_pending=False;self.last_reconcile=0
  self.snapshot={'accounts':[],'platforms':{},'checked_at':0,'interval_seconds':30,'checking':True}
  self.lifecycle=AccountLifecycle(self,self.data)
  self.grouping=AccountGrouping(self,self.data)
  f=self.data/'snapshot.json'
  if f.exists():
   try:self.snapshot=json.loads(f.read_text());self.snapshot['checking']=True
   except Exception:pass
 def matrix(self,path,method='GET',body=None):
  status,value=call(self.c['homeserver']+path,self.c['token'],method,body)
  if status>=300:raise RuntimeError(value.get('errcode','matrix_error'))
  return value
 def platform(self,key,bridge):
  now=time.time()
  try:
   status,data=call(bridge['url']+'/_matrix/provision/v3/whoami?'+urlencode({'user_id':self.c['owner']}),self.c['token'])
   if status!=200:raise RuntimeError('bridge_http_'+str(status))
   rows=[]
   for raw in data.get('logins',[]):
    login=str(raw.get('id') or '')
    if not login:continue
    state=raw.get('state') or {};profile=raw.get('profile') or {}
    code=state.get('state_event') or raw.get('state_event') or data.get('state') or 'UNKNOWN'
    identifier=profile.get('phone') if key=='whatsapp' else profile.get('username') or raw.get('name') or profile.get('name') or login
    if key=='imo':identifier=str(identifier or login)+' · '+('…'+str(raw['phone_suffix']) if raw.get('phone_suffix') else login[-6:])
    phone=str(profile.get('phone') or raw.get('phone') or raw.get('phone_number') or '')
    if not phone and key=='whatsapp' and re.fullmatch(r'[+]?[0-9]{5,15}',login.split(':')[0].split('@')[0]):phone='+'+login.split(':')[0].split('@')[0].lstrip('+')
    name=str(bridge.get('label') or key)+' ('+str(identifier or login)+')'
    color='green' if code=='CONNECTED' else 'red' if code in ['BAD_CREDENTIALS','LOGGED_OUT','UNKNOWN_ERROR','FATAL_ERROR','WRONG_ACCOUNT'] else 'yellow'
    reason='Подключён' if color=='green' else 'Нужен повторный вход' if code in ['BAD_CREDENTIALS','LOGGED_OUT','WRONG_ACCOUNT'] else 'Нет соединения' if color=='red' else 'Соединение проверяется'
    rows.append({'key':key+':'+login,'platform':key,'login_id':login,'name':name,'profile_name':str(profile.get('name') or raw.get('name') or ''),'phone':phone,'username':str(profile.get('username') or ''),'state':code,'provider_error':str(state.get('error') or '')[:120],'color':color,'status_text':reason,'checked_at':now,'state_at':state.get('timestamp'),'space_room':raw.get('space_room') or raw.get('space_room_id'),'bot':data.get('bridge_bot') or bridge['bot']})
    if raw.get('setup_pending'):rows[-1].update(setup_pending=True,status_text='Завершение подключения')
   with self.lock:previous={a['key']:a for a in self.snapshot.get('accounts',[]) if a.get('platform')==key and a.get('key')}
   for row in rows:
    old=previous.get(row['key'],{})
    if old.get('managed_space') and old.get('space_room'):
     native=row.get('space_room')
     if native and native!=old['space_room'] and (native!=old.get('native_space_room') or (row.get('state')=='CONNECTED' and (old.get('state')!='CONNECTED' or now-old.get('native_retry_at',0)>=60))):
      row.update(previous_managed_space=old['space_room'],native_retry_at=now)
     else:row.update(space_room=old['space_room'],managed_space=True,native_space_room=old.get('native_space_room') or native,native_retry_at=old.get('native_retry_at',0))
   rows=self.lifecycle.filter_rows(key,rows)
   return rows,{'reachable':True,'checked_at':now}
  except Exception:
   with self.lock:old=[dict(x) for x in self.snapshot.get('accounts',[]) if x['platform']==key]
   for row in old:row.update(color='yellow',state='UNREACHABLE',status_text='Мост недоступен',checked_at=now)
   return old,{'reachable':False,'checked_at':now,'error':'Мост недоступен'}
 def space(self,row,known):
  sid=row.get('space_room')
  if sid:
   try:
    if sid not in known:
     self.matrix('/_matrix/client/v3/rooms/'+q(sid)+'/join','POST',{})
     known.add(sid)
    return sid
   except RuntimeError as exc:
    if not any(code in str(exc) for code in ['M_FORBIDDEN','M_NOT_FOUND','M_UNKNOWN']):raise
    row['native_space_room']=sid;row['space_room']=None;row['native_retry_at']=time.time()
  # Any provider without an accessible native space gets a stable owner space.
  # This also keeps expired accounts visible without changing native permissions.
  alias='matrix_account_'+hashlib.sha256((self.c['owner']+'|'+row['key']).encode()).hexdigest()[:24]
  status,result=call(self.c['homeserver']+'/_matrix/client/v3/directory/room/'+q('#'+alias+':'+self.c['server_name']),self.c['token'])
  if status==200:sid=result['room_id']
  elif status==404:
   sid=self.matrix('/_matrix/client/v3/createRoom','POST',{'creation_content':{'type':'m.space'},'visibility':'private','preset':'private_chat','room_alias_name':alias,'name':row['name'],'initial_state':[{'type':'com.matrix.account','state_key':'','content':{'platform':row['platform'],'login_id':row['login_id']}}]})['room_id']
  else:raise RuntimeError('space_lookup_failed')
  row['space_room']=sid;row['managed_space']=True;known.add(sid);return sid
 def reconcile(self,rows):
  known=set(self.matrix('/_matrix/client/v3/joined_rooms').get('joined_rooms',[]))
  for row in rows:
   try:row['space_room']=self.space(row,known)
   except Exception:row['space_error']='Не удалось открыть пространство аккаунта'
  # Add already joined bridged rooms only to their matching account's space.
  filt={'room':{'timeline':{'limit':0},'state':{'types':['m.room.create','m.bridge','uk.half-shot.bridge','m.space.child','m.room.member'],'lazy_load_members':True},'ephemeral':{'types':[]},'account_data':{'types':[]}},'presence':{'types':[]},'account_data':{'types':[]}}
  sync=self.matrix('/_matrix/client/v3/sync?'+urlencode({'timeout':'0','filter':json.dumps(filt)}))
  rooms=sync.get('rooms',{}).get('join',{});self.accept_native_invites(rows,rooms,sync.get('rooms',{}).get('invite',{}));pairs={}
  for sid,room in rooms.items():
   for e in room.get('state',{}).get('events',[]):
    if e['type']=='m.space.child' and e.get('content',{}).get('via'):pairs[sid,e['state_key']]=True
  for rid,room in rooms.items():
   events=room.get('state',{}).get('events',[])
   if any(e['type']=='m.room.create' and e.get('content',{}).get('type')=='m.space' for e in events):continue
   for e in events:
    if e['type'] not in ['m.bridge','uk.half-shot.bridge']:continue
    c=e.get('content',{});platform=c.get('protocol',{}).get('id');receiver=str(c.get('channel',{}).get('fi.mau.receiver') or '')
    candidates=[x for x in rows if x['platform']==platform and not x.get('space_error')]
    matches=[x for x in candidates if x['login_id']==receiver] if receiver else candidates if len(candidates)==1 else []
    for row in matches:
     sid=row.get('space_room')
     if sid and sid!=rid and (sid,rid) not in pairs:
      # Native bridge spaces are owned by the bridge. If owner lacks power,
      # report the issue rather than widening permissions or moving rooms.
      try:self.matrix('/_matrix/client/v3/rooms/'+q(sid)+'/state/m.space.child/'+q(rid),'PUT',{'via':[self.c['server_name']],'suggested':False});pairs[sid,rid]=True
      except Exception:row['space_warning']='Не все чаты удалось добавить в пространство'
 def accept_native_invites(self,rows,rooms,invites):
  accepted=set()
  for row in rows:
   if row.get('managed_space') or row.get('space_error'):continue
   children={e['state_key'] for e in rooms.get(row.get('space_room'),{}).get('state',{}).get('events',[]) if e['type']=='m.space.child' and e.get('content',{}).get('via')}
   for rid in children & set(invites) - accepted:
    events=invites[rid].get('invite_state',{}).get('events',[])
    valid=any(e['type']=='m.room.member' and e.get('state_key')==self.c['owner'] and e.get('sender')==row.get('bot') and e.get('content',{}).get('membership')=='invite' for e in events)
    if not valid:continue
    try:self.matrix('/_matrix/client/v3/rooms/'+q(rid)+'/join','POST',{});accepted.add(rid)
    except Exception:row['space_warning']='Не все приглашения моста удалось принять'
 def accept_pending_native_invites(self,rows):
  # New chats can arrive while the account inventory stays unchanged. Preserve
  # membership in stripped invite state, without loading every group member.
  filt={'room':{'timeline':{'limit':0},'state':{'types':['m.space.child','m.room.member'],'lazy_load_members':True},'ephemeral':{'types':[]},'account_data':{'types':[]}},'presence':{'types':[]},'account_data':{'types':[]}}
  sync=self.matrix('/_matrix/client/v3/sync?'+urlencode({'timeout':'0','filter':json.dumps(filt)}))
  self.accept_native_invites(rows,sync.get('rooms',{}).get('join',{}),sync.get('rooms',{}).get('invite',{}))
 def retire_empty_fallback(self,row):
  old=row.pop('previous_managed_space',None)
  if not old or old==row.get('space_room') or row.get('space_error'):return
  try:
   events=self.matrix('/_matrix/client/v3/rooms/'+q(old)+'/state')
   owned=any(e['type']=='m.room.create' and e.get('content',{}).get('type')=='m.space' and e.get('sender')==self.c['owner'] for e in events)
   marker=any(e['type']=='com.matrix.account' and e.get('content',{}).get('platform')==row['platform'] and e.get('content',{}).get('login_id')==row['login_id'] for e in events)
   children=any(e['type']=='m.space.child' and e.get('content',{}).get('via') for e in events)
   if not owned or not marker or children:return
   members=self.matrix('/_matrix/client/v3/rooms/'+q(old)+'/joined_members').get('joined',{})
   if set(members)!={self.c['owner']}:return
   # Only an empty, owner-created temporary space is left. Rooms and messages
   # are never deleted; the account now uses the bridge's real native space.
   self.matrix('/_matrix/client/v3/rooms/'+q(old)+'/leave','POST',{})
  except Exception:pass
 def refresh(self):
  if not self.refresh_lock.acquire(False):return
  self.last_start=time.monotonic()
  try:
   with self.lock:self.snapshot['checking']=True
   rows=[];platforms={}
   with concurrent.futures.ThreadPoolExecutor(max_workers=7) as pool:
    tasks={pool.submit(self.platform,k,b):k for k,b in self.c['bridges'].items()}
    for task in concurrent.futures.as_completed(tasks):
     result,state=task.result();rows+=result;platforms[tasks[task]]=state
   rows.sort(key=lambda r:(list(self.c['bridges']).index(r['platform']),r['name']))
   with self.lock:old_rows={a['key']:dict(a) for a in self.snapshot.get('accounts',[]) if a.get('key')}
   present={a['key'] for a in rows}
   for key,old in old_rows.items():
    if key not in present and platforms.get(old['platform'],{}).get('reachable'):
     self.lifecycle.complete(old)
   changed=set(old_rows)!={a['key'] for a in rows} or any(a.get('previous_managed_space') for a in rows)
   needs_repair=any(a.get('space_error') or a.get('space_warning') for a in old_rows.values())
   if changed or needs_repair or time.monotonic()-self.last_reconcile>=600:
    try:
     self.reconcile(rows);self.last_reconcile=time.monotonic()
     for row in rows:self.retire_empty_fallback(row)
    except Exception:
     for row in rows:row.setdefault('space_error','Список аккаунтов ещё обновляется')
   else:
    for row in rows:
     old=old_rows.get(row['key'],{})
     for field in ['space_room','managed_space','native_space_room','native_retry_at','space_warning','space_error']:
      if field in old:row[field]=old[field]
    try:self.accept_pending_native_invites(rows)
    except Exception:
     for row in rows:row.setdefault('space_warning','Не удалось проверить новые приглашения')
   try:grouping=self.grouping.apply(rows)
   except Exception:grouping={'enabled':True,'error':'Не удалось сгруппировать аккаунты'}
   snapshot={'accounts':rows,'platforms':platforms,'checked_at':time.time(),'interval_seconds':30,'checking':False,'grouping':grouping}
   with self.lock:
    self.snapshot=snapshot
    temp=self.data/'snapshot.tmp';temp.write_text(json.dumps(snapshot,ensure_ascii=False));os.replace(temp,self.data/'snapshot.json')
  finally:self.refresh_lock.release()
 def worker(self):
  while not self.stop.is_set():
   started=time.monotonic()
   try:self.refresh()
   except Exception:
    with self.lock:self.snapshot['checking']=False
   self.stop.wait(max(1,30-(time.monotonic()-started)))
 def request_refresh(self):
  with self.lock:
   if self.refresh_pending:return
   self.refresh_pending=True
  def requested():
   try:
    self.stop.wait(max(0,5-(time.monotonic()-self.last_start)))
    while self.refresh_lock.locked() and not self.stop.is_set():self.stop.wait(.5)
    if not self.stop.is_set():self.refresh()
   finally:
    with self.lock:self.refresh_pending=False
  threading.Thread(target=requested,daemon=True).start()
 def update_pending(self,action):
  path=self.data/'pending-onboarding.json'
  with self.lock:
   pending={}
   if path.exists():
    try:pending=json.loads(path.read_text())
    except Exception:pass
   clear=action.get('clear_pending_login') or {}
   platform=clear.get('platform');job=clear.get('login_id')
   if platform in pending and pending[platform].get('step',{}).get('login_id')==job:pending.pop(platform,None)
   item=action.get('pending_login') or {};platform=item.get('platform');step=item.get('step') or {}
   if platform in self.c['bridges'] and step.get('type') in ['user_input','cookies'] and isinstance(step.get('login_id'),str) and isinstance(step.get('step_id'),str):
    safe={k:step[k] for k in ['type','login_id','step_id','instructions'] if k in step}
    if step['type']=='user_input':
     fields=[]
     for field in (step.get('user_input') or {}).get('fields',[])[:20]:
      fields.append({k:field[k] for k in ['id','type','name','description','optional','pattern','min_length','max_length','options'] if k in field})
     safe['user_input']={'fields':fields}
    else:safe['cookies']={k:step['cookies'][k] for k in ['url','fields'] if k in step.get('cookies',{})}
    pending[platform]={'step':safe,'expires_at':time.time()+1200}
   temp=path.with_suffix('.tmp');fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
   with os.fdopen(fd,'w') as output:json.dump(pending,output)
   os.replace(temp,path)
 def read(self):
  with self.lock:result=json.loads(json.dumps(self.snapshot))
  now=time.time();result['pending_logins']={}
  pending=self.data/'pending-onboarding.json'
  if pending.exists():
   try:result['pending_logins']={platform:item['step'] for platform,item in json.loads(pending.read_text()).items() if platform in self.c['bridges'] and item.get('expires_at',0)>now}
   except Exception:pass
  for row in result['accounts']:
   if now-row.get('checked_at',0)>720:row.update(color='yellow',status_text='Проверка устарела',state='STALE')
  site='__PUBLIC_ORIGIN__'
  for row in result['accounts']:row['diagnostics']=diagnose(row,site)
  return result
 def authorize(self,token,user,origin):
  if origin and origin not in self.c['origins']:return 403
  if not token or user!=self.c['owner']:return 403 if token else 401
  key=hashlib.sha256(token.encode()).hexdigest();cached=self.auth.get(key)
  if cached and cached>time.monotonic():return 200
  status,data=call(self.c['homeserver']+'/_matrix/client/v3/account/whoami',token)
  if status!=200:return 401
  if data.get('user_id')!=user:return 403
  self.auth[key]=time.monotonic()+30;return 200
 def attach_imo(self,token,payload):
  expected=self.c.get('imo_bridge_token_hash','')
  if not expected or not secrets.compare_digest(hashlib.sha256(token.encode()).hexdigest(),expected):return 401,{'error':'Unauthorized bridge'}
  room=str(payload.get('room_id') or '');buid=str(payload.get('buid') or '');login=str(payload.get('login_id') or '')
  if not room.startswith('!') or len(room)>256 or not buid:return 400,{'error':'Invalid room'}
  with self.lock:account=next((dict(x) for x in self.snapshot.get('accounts',[]) if x['platform']=='imo' and x['login_id']==login),None)
  if not account:self.request_refresh();return 409,{'error':'IMO account is not available yet'}
  self.matrix('/_matrix/client/v3/rooms/'+q(room)+'/join','POST',{})
  events=self.matrix('/_matrix/client/v3/rooms/'+q(room)+'/state')
  bot=self.c['bridges']['imo']['bot']
  valid=any(e['type']=='m.bridge' and e.get('sender')==bot and e.get('content',{}).get('bridgebot')==bot and e.get('content',{}).get('protocol',{}).get('id')=='imo' and e.get('content',{}).get('channel',{}).get('id')==buid and e.get('content',{}).get('channel',{}).get('fi.mau.receiver')==login for e in events)
  if not valid:return 403,{'error':'Not an IMO portal'}
  sid=self.space(account,set(self.matrix('/_matrix/client/v3/joined_rooms').get('joined_rooms',[])))
  if sid==room:return 400,{'error':'Invalid parent'}
  self.matrix('/_matrix/client/v3/rooms/'+q(sid)+'/state/m.space.child/'+q(room),'PUT',{'via':[self.c['server_name']],'suggested':False})
  return 200,{'space_room':sid}

class UserMonitors:
 """Resolve Matrix identity before selecting any account state."""
 def __init__(self,config):
  self.config=config;self.lock=threading.Lock();self.users={}
 def resolve(self,token,user,origin):
  if origin and origin not in self.config['origins']:return 403,None
  if not token:return 401,None
  status,identity=call(self.config['homeserver']+'/_matrix/client/v3/account/whoami',token)
  if status!=200:return 401,None
  mxid=identity.get('user_id')
  if not mxid or mxid!=user or not mxid.endswith(':'+self.config['server_name']):return 403,None
  with self.lock:
   monitor=self.users.get(mxid)
   if monitor is None:
    directory=DATA/'users'/hashlib.sha256(mxid.encode()).hexdigest()
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    config=dict(self.config,owner=mxid,token=token)
    monitor=Monitor(config,directory)
    monitor.notifier=Notifier(directory,monitor.read,config['bridges'])
    self.users[mxid]=monitor
   else:monitor.c['token']=token
  return 200,monitor

class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def reply(self,status,data):
  raw=json.dumps(data,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','private, no-store');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 def handle_api(self):
  path=urlsplit(self.path)
  if path.path=='/healthz' and self.command=='GET':return self.reply(200,{'ok':True})
  token=self.headers.get('Authorization','');token=token[7:] if token.startswith('Bearer ') else ''
  if path.path=='/_internal/imo-room' and self.command=='POST':
   size=int(self.headers.get('Content-Length','0'))
   if size<1 or size>4096:return self.reply(400,{'error':'Invalid request'})
   try:payload=json.loads(self.rfile.read(size))
   except Exception:return self.reply(400,{'error':'Invalid JSON'})
   return self.reply(503,{'error':'Legacy IMO callback disabled pending user routing'})
  status,monitor=self.server.registry.resolve(token,parse_qs(path.query).get('user_id',[''])[0],self.headers.get('Origin'))
  if status!=200:return self.reply(status,{'error':'Недостаточно прав' if status==403 else 'Требуется вход в Matrix'})
  if path.path=='/v1/accounts/delete' and self.command=='POST':
   size=int(self.headers.get('Content-Length','0'))
   if size<1 or size>2048:return self.reply(400,{'error':'Неверный запрос удаления'})
   try:
    payload=json.loads(self.rfile.read(size));result=monitor.lifecycle.delete(payload)
   except ValueError as e:return self.reply(400,{'error':str(e)})
   except Exception:return self.reply(409,{'error':'Удаление не подтверждено. Проверьте подключение и повторите попытку.'})
   return self.reply(200,result)
  if path.path=='/v1/msb-cbmrs/settings' and self.command=='GET':
   result=monitor.notifier.settings()
   result['accounts']=[{k:a.get(k) for k in ['key','platform','login_id','name','profile_name','phone','username','color','state','status_text','provider_error','diagnostics','deletion_pending','deletion_error']} for a in monitor.read()['accounts']]
   return self.reply(200,result)
  if path.path in ['/v1/msb-cbmrs/settings','/v1/msb-cbmrs/test'] and self.command=='POST':
   size=int(self.headers.get('Content-Length','0'))
   if size<0 or size>131072:return self.reply(400,{'error':'Слишком большой запрос'})
   try:
    value=json.loads(self.rfile.read(size)) if size else {}
    result=monitor.notifier.test() if path.path.endswith('/test') else monitor.notifier.update(value)
   except (ValueError,TypeError):return self.reply(400,{'error':'Проверьте адрес API, интервалы, ключ и подписи аккаунтов'})
   return self.reply(202 if path.path.endswith('/test') else 200,result)
  if path.path=='/v1/accounts' and self.command=='GET':
   if time.monotonic()-monitor.last_start>=30:monitor.request_refresh()
   return self.reply(200,monitor.read())
  if path.path=='/v1/refresh' and self.command=='POST':
   size=int(self.headers.get('Content-Length','0'))
   if size>32768:return self.reply(400,{'error':'Invalid request'})
   if size:
    try:action=json.loads(self.rfile.read(size))
    except Exception:return self.reply(400,{'error':'Invalid JSON'})
    if action.get('pending_login') or action.get('clear_pending_login'):monitor.update_pending(action)
   monitor.request_refresh()
   return self.reply(202,{'checking':True})
  self.reply(404,{'error':'Не найдено'})
 def do_GET(self):
  try:self.handle_api()
  except Exception:self.reply(503,{'error':'Сервис проверки недоступен'})
 do_POST=do_GET
 def do_DELETE(self):self.reply(405,{'error':'Метод запрещён'})

if __name__=='__main__':
 DATA.mkdir(exist_ok=True);config=json.loads((DATA/'config.json').read_text())
 server=ThreadingHTTPServer(('0.0.0.0',29400),Handler);server.registry=UserMonitors(config);server.serve_forever()
