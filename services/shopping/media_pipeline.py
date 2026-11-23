"""Native attachment locators -> private temporary bytes -> authenticated MXC.

Signed CDN URLs and native credentials never enter SQLite, Matrix events or logs.
Only the already-authorized account can resolve a stored attachment locator.
"""
import asyncio,contextlib,ipaddress,json,mimetypes,re,tempfile,time,calendar
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit,urljoin,quote,urlencode
from aiohttp import ClientSession,ClientTimeout,TCPConnector
from aiohttp.resolver import DefaultResolver
from media_utils import inspect_file

MAX_BYTES=250*1024*1024
HOSTS={
 'vk':('vkuserphoto.ru','vkuserphoto.net','vkuseraudio.net','vkuseraudio.ru','userapi.com','vk-cdn.net','vk.com','vk.ru'),
 'ok':('okcdn.ru','mycdn.me','ok.ru'),
 'threads':('cdninstagram.com','fbcdn.net','threads.net','threads.com','instagram.com'),
 'taobao':('alicdn.com','taobaocdn.com','taobao.com','tbcdn.cn','aliimg.com','alibaba.com'),
 'pinduoduo':('pddpic.com','pinduoduo.com','yangkeduo.com','pddcdn.com','pddimg.com','chat-img.pddugc.com')}

def safe_url(value,platform):
 if not isinstance(value,str) or len(value)>12000:raise ValueError('Invalid media URL')
 u=urlsplit(value);host=(u.hostname or '').lower()
 if u.scheme!='https' or u.username or u.password or u.port not in (None,443) or u.fragment or not any(host==h or host.endswith('.'+h) for h in HOSTS.get(platform,())):raise ValueError('Untrusted media host')
 return value

def filename(value):
 name=str(value or 'attachment').replace('\\','/').split('/')[-1]
 return ''.join(c for c in name if ord(c)>=32 and ord(c)!=127).strip(' .')[:160] or 'attachment'

def expired(ts,now=None):
 d=datetime.fromtimestamp(ts/1000,timezone.utc);index=d.year*12+d.month-1+2;year,month=divmod(index,12);month+=1
 return d.replace(year=year,month=month,day=min(d.day,calendar.monthrange(year,month)[1]))<=datetime.fromtimestamp(now or time.time(),timezone.utc)

class PublicResolver(DefaultResolver):
 async def resolve(self,host,port=0,family=0):
  rows=await super().resolve(host,port,family)
  if not rows or any(not ipaddress.ip_address(r['host']).is_global for r in rows):raise OSError('Media host must resolve to public addresses')
  return rows

class MediaPipeline:
 def __init__(self,bridge):self.b=bridge;self.db=bridge.store.db
 async def download(self,descriptor,folder):
  url=safe_url(descriptor['url'],self.b.platform);path=folder/filename(descriptor.get('name'))
  async with ClientSession(connector=TCPConnector(resolver=PublicResolver()),timeout=ClientTimeout(total=240,connect=20),trust_env=False) as http:
   for _ in range(6):
    async with http.get(url,allow_redirects=False,headers={'User-Agent':'Matrix LAN attachment bridge/1.0'}) as response:
     if response.status in (301,302,303,307,308):url=safe_url(urljoin(url,response.headers.get('Location','')),self.b.platform);continue
     if response.status!=200:raise RuntimeError('Attachment download failed')
     if response.content_length is not None and response.content_length>MAX_BYTES:raise ValueError('Attachment exceeds limit')
     mime=response.headers.get('Content-Type','').split(';')[0].strip().lower()
     if mime in ('text/html','application/xhtml+xml','application/json'):raise ValueError('Attachment is a webpage')
     size=0
     with path.open('wb') as target:
      async for chunk in response.content.iter_chunked(256*1024):
       size+=len(chunk)
       if size>MAX_BYTES:raise ValueError('Attachment exceeds limit')
       target.write(chunk)
     if not size:raise ValueError('Empty attachment')
     return path,mime
   raise ValueError('Too many redirects')
 async def upload(self,path,mime,name):
  b=self.b
  with path.open('rb') as source:
   async with b.g.http.post(b.homeserver+'/_matrix/media/v3/upload',params={'filename':name,'user_id':b.bot},headers={'Authorization':'Bearer '+b.config['as_token'],'Content-Type':mime,'Content-Length':str(path.stat().st_size)},data=source,timeout=ClientTimeout(total=180)) as response:
    data=await response.json(content_type=None)
    if response.status!=200 or not re.fullmatch(r'mxc://[^/?#]+/[A-Za-z0-9_-]+',data.get('content_uri','')):raise RuntimeError('Matrix upload failed')
    return data['content_uri']
 async def content(self,row):
  b=self.b
  async with b.g.locks.setdefault(row['slot'],asyncio.Lock()):
   if row['slot'] not in b.g.registry:raise RuntimeError('Account removed')
   native=await b.native(row['slot'],'media',{'cid':row['cid'],'id':row['msgid'],'timestamp':row['ts'],**json.loads(row['descriptor'])})
  with tempfile.TemporaryDirectory(prefix='matrix-attachment-') as temporary:
   path,mime=await self.download(native,Path(temporary));kind=native.get('kind','file')
   if kind not in ['image','audio','video','file']:raise ValueError('Invalid media kind')
   path,kind,info,thumbnail=await inspect_file(path,kind,mime)
   if native.get('kind')=='image' and kind!='image':raise ValueError('Invalid native image')
   if native.get('duration') and not info.get('duration'):info['duration']=int(native['duration'])
   name=filename(native.get('name') or path.name);uri=await self.upload(path,info['mimetype'],name)
   content={'msgtype':'m.'+kind,'body':name,'filename':name,'url':uri,'info':info,'com.matrix.social':{'platform':b.platform,'native_id':row['msgid'],'part':row['part'],'own':bool(row['own']),'timestamp':row['ts']},'com.matrix.media.retention':{'months':2,'from':'upload'}}
   if kind=='audio' and native.get('voice'):
    waveform=native.get('waveform') or [];waveform=[max(0,min(1024,round(float(v)*1024/31))) for v in waveform[:1024] if isinstance(v,(int,float))]
    content['org.matrix.msc3245.voice']={};content['org.matrix.msc1767.audio']={'duration':info.get('duration',0),'waveform':waveform}
   if thumbnail:
    from PIL import Image
    with Image.open(thumbnail) as image:thumbinfo={'w':image.width,'h':image.height,'size':thumbnail.stat().st_size,'mimetype':'image/jpeg'}
    with contextlib.suppress(Exception):content['info'].update(thumbnail_url=await self.upload(thumbnail,'image/jpeg','preview.jpg'),thumbnail_info=thumbinfo)
   return content
 async def sender(self,row):
  b=self.b
  from matrix_bridge import digest,q
  user='@'+b.platform+'_'+digest(row['slot'],row['sender'])+':'+b.server
  name=('Вы · '+b.g.registry[row['slot']]['name']) if row['own'] else row['sender_name'] or row['sender']
  await b.ensure_user(user,name+' ('+b.g.label+')')
  with contextlib.suppress(RuntimeError):await b.matrix('POST','/_matrix/client/v3/rooms/'+q(row['room_id'])+'/invite',{'user_id':user})
  await b.matrix('POST','/_matrix/client/v3/rooms/'+q(row['room_id'])+'/join',{},user=user);return user
 async def process(self,row):
  b=self.b
  from matrix_bridge import q
  if row['slot'] not in b.g.registry:return
  # Native history can be old when an attachment reaches Matrix for the first
  # time. Synapse expires stored bytes by upload time. This queue age guard only
  # prevents resurrecting a transfer that itself has been stuck for two months.
  queued_at=row['queued_at'] if 'queued_at' in row.keys() else row['ts']
  if expired(queued_at):
   with self.db:self.db.execute("UPDATE media_parts SET event_id='expired' WHERE slot=? AND cid=? AND msgid=? AND part=?",(row['slot'],row['cid'],row['msgid'],row['part']))
   return
  if row['own'] and b.store.is_outbound_echo(row['slot'],row['cid'],row['msgid']):
   with self.db:self.db.execute("UPDATE media_parts SET event_id='outbound-echo' WHERE slot=? AND cid=? AND msgid=? AND part=?",(row['slot'],row['cid'],row['msgid'],row['part']))
   return
  content=json.loads(row['content']) if row['content'] else await self.content(row)
  with self.db:self.db.execute('UPDATE media_parts SET content=? WHERE slot=? AND cid=? AND msgid=? AND part=?',(json.dumps(content),row['slot'],row['cid'],row['msgid'],row['part']))
  user=await self.sender(row);result=await b.matrix('PUT','/_matrix/client/v3/rooms/'+q(row['room_id'])+'/send/m.room.message/'+b.store.txn(row['slot'],row['cid'],row['msgid']+':media:'+str(row['part'])),content,user=user,timestamp=row['ts'])
  with self.db:self.db.execute('UPDATE media_parts SET event_id=?,error=NULL WHERE slot=? AND cid=? AND msgid=? AND part=?',(result['event_id'],row['slot'],row['cid'],row['msgid'],row['part']))
  remaining=self.db.execute('SELECT 1 FROM media_parts WHERE slot=? AND cid=? AND msgid=? AND event_id IS NULL',(row['slot'],row['cid'],row['msgid'])).fetchone()
  if not remaining:
   for status in self.db.execute('SELECT DISTINCT fallback_event FROM media_parts WHERE slot=? AND cid=? AND msgid=? AND fallback_event IS NOT NULL',(row['slot'],row['cid'],row['msgid'])).fetchall():
    with contextlib.suppress(RuntimeError):await b.matrix('PUT','/_matrix/client/v3/rooms/'+q(row['room_id'])+'/redact/'+q(status['fallback_event'])+'/'+b.store.txn(row['slot'],row['cid'],row['msgid']+':status-clear'),{'reason':'Вложение загружено.'},user=user)
  if row['original_event'] and row['original_event'].startswith('$') and row['kind']=='media' and not self.db.execute('SELECT 1 FROM media_parts WHERE slot=? AND cid=? AND msgid=? AND event_id IS NULL',(row['slot'],row['cid'],row['msgid'])).fetchone():
   original=await b.matrix('GET','/_matrix/client/v3/rooms/'+q(row['room_id'])+'/event/'+q(row['original_event']),user=user)
   if original.get('sender')==user and original.get('content',{}).get('msgtype')=='m.notice' and original.get('content',{}).get('body','').startswith('[Вложение'):
    await b.matrix('PUT','/_matrix/client/v3/rooms/'+q(row['room_id'])+'/redact/'+q(row['original_event'])+'/'+b.store.txn(row['slot'],row['cid'],row['msgid']+':media-repair'),{'reason':'Вложение загружено и теперь доступно в переписке.'},user=user)
    b.store.mark_incoming(row['slot'],row['cid'],row['msgid'],'media-repaired')
 async def worker(self):
  await asyncio.sleep(10)
  while True:
   try:
    active=[slot for slot in self.b.g.registry if self.b.g.states.get(slot,{}).get('state_event')=='CONNECTED'];row=None
    if active:row=self.db.execute('SELECT p.*,i.own,i.ts,i.sender,i.sender_name,i.kind,i.event_id AS original_event,c.room_id FROM media_parts p JOIN inbox i USING(slot,cid,msgid) JOIN chats c USING(slot,cid) WHERE p.event_id IS NULL AND p.next_retry<=? AND p.slot IN ('+','.join('?' for _ in active)+') ORDER BY i.ts DESC LIMIT 1',[time.time(),*active]).fetchone()
    if row:
     try:await self.process(row)
     except asyncio.CancelledError:raise
     except Exception as error:
      attempts=row['attempts']+1
      with self.db:self.db.execute('UPDATE media_parts SET attempts=?,next_retry=?,error=? WHERE slot=? AND cid=? AND msgid=? AND part=?',(attempts,time.time()+min(86400,30*2**min(attempts,12)),type(error).__name__,row['slot'],row['cid'],row['msgid'],row['part']))
      print('MEDIA_RETRY',self.b.platform,type(error).__name__,flush=True)
      if attempts>=3 and not row['fallback_event'] and not (row['original_event'] or '').startswith('$'):
       await self.report_pending(row)
    if active:
     repair=self.db.execute('SELECT p.*,i.body,i.kind,i.own,i.ts,i.sender,i.sender_name,c.room_id FROM message_repairs p JOIN inbox i USING(slot,cid,msgid) JOIN chats c USING(slot,cid) WHERE p.done=0 AND p.slot IN ('+','.join('?' for _ in active)+') LIMIT 1',active).fetchone()
     if repair:await self.repair_text(repair)
   except asyncio.CancelledError:raise
   except Exception as error:print('MEDIA_WORKER_RETRY',type(error).__name__,flush=True)
   await asyncio.sleep(1)
 async def repair_text(self,row):
  from matrix_bridge import q
  b=self.b;user=await self.sender(row);original=await b.matrix('GET','/_matrix/client/v3/rooms/'+q(row['room_id'])+'/event/'+q(row['old_event']),user=user)
  if original.get('sender')==user and original.get('content',{}).get('msgtype')=='m.notice' and original.get('content',{}).get('body','').startswith('[Вложение'):
   content={'msgtype':'m.text' if row['kind']=='text' else 'm.notice','body':row['body'],'com.matrix.social':{'platform':b.platform,'native_id':row['msgid'],'own':bool(row['own']),'timestamp':row['ts']}}
   await b.matrix('PUT','/_matrix/client/v3/rooms/'+q(row['room_id'])+'/send/m.room.message/'+b.store.txn(row['slot'],row['cid'],row['msgid']+':text-repair'),{**content,'body':'* '+row['body'],'m.new_content':content,'m.relates_to':{'rel_type':'m.replace','event_id':row['old_event']}},user=user)
  with self.db:self.db.execute('UPDATE message_repairs SET done=1 WHERE slot=? AND cid=? AND msgid=?',(row['slot'],row['cid'],row['msgid']))
 async def report_pending(self,row):
  from matrix_bridge import q
  b=self.b;user=await self.sender(row)
  result=await b.matrix('PUT','/_matrix/client/v3/rooms/'+q(row['room_id'])+'/send/m.room.message/'+b.store.txn(row['slot'],row['cid'],row['msgid']+':media-status'),{'msgtype':'m.notice','body':'Файл пока не удалось загрузить. Повторим автоматически. Оригинал доступен в '+b.g.label+'.','com.matrix.social':{'platform':b.platform,'native_id':row['msgid'],'media_status':True}},user=user)
  with self.db:self.db.execute('UPDATE media_parts SET fallback_event=? WHERE slot=? AND cid=? AND msgid=?',(result['event_id'],row['slot'],row['cid'],row['msgid']))
