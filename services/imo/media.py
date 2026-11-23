"""Durable IMO media transfers. Only message locators, never remote URLs, persist.

Media bytes use a private temporary directory and are removed after every attempt.
The authoritative copy lives in Synapse and follows its fixed media retention.
"""
import asyncio, base64, contextlib, hashlib, json, mimetypes, os, re, tempfile, time
from pathlib import Path
from urllib.parse import urlsplit
from aiohttp import ClientTimeout
from PIL import Image
from bridge import Bridge, CONFIG, OWNER, BOT, SERVER, HS, digest, q, log, MEDIA_TYPES

MAX_BYTES=250*1024*1024
CHUNK=256*1024
MEDIA_TYPES['audio_uploaded_2']='Голосовое сообщение'

def filename(value,fallback='attachment.bin'):
 value=str(value or '').replace('\\','/').split('/')[-1]
 value=''.join(c for c in value if ord(c)>=32 and ord(c)!=127).strip(' .')[:180]
 return value or fallback

def parse_mxc(uri):
 if not isinstance(uri,str) or len(uri)>1024:raise ValueError('Invalid MXC URI')
 u=urlsplit(uri)
 if u.scheme!='mxc' or not u.netloc or u.username or u.password or u.query or u.fragment or not re.fullmatch(r'/[A-Za-z0-9_-]+',u.path):raise ValueError('Invalid MXC URI')
 return u.netloc,u.path[1:]

async def inspect_file(path,kind,mime,allow_convert=True):
 info={'size':path.stat().st_size,'mimetype':(mimetypes.guess_type(path.name)[0] if not mime or mime=='application/octet-stream' else mime) or 'application/octet-stream'}
 thumbnail=None
 if kind=='image':
  try:
   with Image.open(path) as image:
    image.verify()
   with Image.open(path) as image:
    info.update(w=image.width,h=image.height,mimetype=Image.MIME.get(image.format,'image/jpeg'))
  except Exception:kind='file'
 if kind in ['video','audio']:
  process=await asyncio.create_subprocess_exec('ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
  try:stdout,_=await asyncio.wait_for(process.communicate(),30)
  except BaseException:
   process.kill();await process.wait();raise
  if process.returncode==0:
   metadata=json.loads(stdout);duration=float(metadata.get('format',{}).get('duration') or 0)
   if duration>0:info['duration']=int(duration*1000)
   video=next((s for s in metadata.get('streams',[]) if s.get('codec_type')=='video'),None)
   if kind=='video' and video:
    if allow_convert and (video.get('codec_name') not in ['h264','vp8','vp9','av1'] or video.get('pix_fmt') not in ['yuv420p','yuvj420p'] or info['mimetype'] not in ['video/mp4','video/webm']):
     derived=Path(tempfile.mkdtemp(prefix='derived-',dir=path.parent));converted=derived/'video.mp4'
     conversion=await asyncio.create_subprocess_exec('ffmpeg','-nostdin','-v','error','-y','-threads','2','-i',str(path),'-map','0:v:0','-map','0:a?','-vf',"scale='min(1280,iw)':-2",'-c:v','libx264','-threads','2','-preset','veryfast','-crf','24','-pix_fmt','yuv420p','-c:a','aac','-b:a','128k','-movflags','+faststart',str(converted),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
     try:await asyncio.wait_for(conversion.wait(),600)
     except BaseException:
      conversion.kill();await conversion.wait();raise
     if conversion.returncode==0 and 0<converted.stat().st_size<=MAX_BYTES:return await inspect_file(converted,'video','video/mp4',False)
     kind='file'
    info.update(w=int(video.get('width') or 0),h=int(video.get('height') or 0))
    thumbnail=Path(tempfile.mkdtemp(prefix='preview-',dir=path.parent))/'preview.jpg'
    process=await asyncio.create_subprocess_exec('ffmpeg','-nostdin','-v','error','-y','-threads','2','-i',str(path),'-frames:v','1','-vf','scale=320:320:force_original_aspect_ratio=decrease','-q:v','4',str(thumbnail),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
    try:await asyncio.wait_for(process.wait(),40)
    except BaseException:
     process.kill();await process.wait();raise
    if process.returncode or not thumbnail.exists():thumbnail=None
   elif kind=='video':kind='file'
  else:kind='file'
 return path,kind,info,thumbnail

class MediaBridge(Bridge):
 def __init__(self,data=None):
  super().__init__(data)
  self.db.executescript('''
   CREATE TABLE IF NOT EXISTS media_inbox(key TEXT PRIMARY KEY,buid TEXT,ts TEXT,payload TEXT,status TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,next_retry REAL DEFAULT 0,content TEXT,event_id TEXT,error TEXT);
   CREATE TABLE IF NOT EXISTS media_outbox(event_id TEXT PRIMARY KEY,payload TEXT);
  ''');self.db.commit()
  self.media_fallback_limit=asyncio.Semaphore(1)
  # Existing notices are kept in history. Recover their real attachments once,
  # using a separate stable transaction, without sending anything back to IMO.
  for row in self.db.execute("SELECT * FROM inbox WHERE json_extract(payload,'$.unsupported_media')=1"):
   data=json.loads(row['payload'])
   self.db.execute('INSERT OR IGNORE INTO media_inbox(key,buid,ts,payload) VALUES(?,?,?,?)',(row['key'],row['buid'],data['ts'],row['payload']))
  self.db.commit()
 def ingest(self,buid,data,own=False):
  super().ingest(buid,data,own)
  if (data.get('imdata') or {}).get('type') not in MEDIA_TYPES:return
  ts=str(data.get('timestamp_nano_str') or '')
  own=own or bool(data.get('is_sent')) or data.get('author')==self.uid
  author=str(data.get('author') or '').split(';')[0]
  sender=self.uid if own else author or ('unknown-group:'+buid if buid.endswith(';') else buid)
  key=digest(self.uid+'|'+buid+'|'+ts+'|'+sender)
  row=self.db.execute('SELECT * FROM inbox WHERE key=?',(key,)).fetchone()
  if not row:return
  self.db.execute('INSERT OR IGNORE INTO media_inbox(key,buid,ts,payload) VALUES(?,?,?,?)',(key,buid,ts,row['payload']))
  self.db.execute("UPDATE inbox SET status='media_pending' WHERE key=? AND status='pending'",(key,));self.db.commit()
 async def media_eval(self,script,arg=None):
  if not self.page or not self.browser_ready:raise RuntimeError('IMO browser is not ready')
  return await asyncio.wait_for(self.page.evaluate(script,arg),30)
 async def poll_media(self,handle,seconds=300):
  for _ in range(seconds*2):
   status=await self.media_eval('(h)=>window.__matrixIMO.mediaPoll(h)',handle)
   if not status:raise RuntimeError('IMO media transfer was interrupted')
   if status['state'] in ['ready','complete']:return status
   if status['state']=='error':raise RuntimeError(status.get('error') or 'IMO media transfer failed')
   await asyncio.sleep(.5)
  raise RuntimeError('IMO media transfer timed out')
 async def upload_matrix(self,path,mime,name):
  with path.open('rb') as source:
   async with self.http.post(HS+'/_matrix/media/v3/upload',params={'filename':name,'user_id':BOT},data=source,headers={'Authorization':'Bearer '+CONFIG['as_token'],'Content-Type':mime,'Content-Length':str(path.stat().st_size)},timeout=ClientTimeout(total=180)) as response:
    result=await response.json(content_type=None)
    if response.status!=200:raise RuntimeError('Matrix media upload HTTP '+str(response.status))
    uri=result.get('content_uri');parse_mxc(uri);return uri
 async def fetch_imo_media(self,row,directory):
  handle=None
  try:
   handle=await self.media_eval('(p)=>window.__matrixIMO.mediaBeginDownload(p)',{'buid':row['buid'],'ts':row['ts'],'limit':MAX_BYTES})
   status=await self.poll_media(handle);meta=status['info'];name=filename(meta.get('name'),row['ts']+'.bin');path=directory/name
   size=int(meta['size'])
   if size<1 or size>MAX_BYTES:raise RuntimeError('Attachment exceeds 250 MB')
   with path.open('wb') as target:
    for offset in range(0,size,CHUNK):
     encoded=await self.media_eval('(p)=>window.__matrixIMO.mediaRead(p)',{'handle':handle,'offset':offset,'length':min(CHUNK,size-offset)})
     chunk=base64.b64decode(encoded,validate=True)
     if len(chunk)!=min(CHUNK,size-offset):raise RuntimeError('Incomplete IMO attachment')
     target.write(chunk)
   return path,meta
  finally:
   if handle:
    with contextlib.suppress(Exception):await self.media_eval('(h)=>window.__matrixIMO.mediaRelease(h)',handle)
 async def post_media(self,row):
  data=json.loads(row['payload']);portal=self.db.execute('SELECT * FROM portals WHERE buid=?',(row['buid'],)).fetchone()
  if not portal:raise RuntimeError('Media portal is unavailable')
  user='@imo_'+digest(data['sender'])[:24]+':'+SERVER
  await self.ensure_user(user,data['name'])
  with contextlib.suppress(RuntimeError):await self.matrix('POST','/_matrix/client/v3/rooms/'+q(portal['room_id'])+'/invite',{'user_id':user},user=BOT)
  await self.matrix('POST','/_matrix/client/v3/rooms/'+q(portal['room_id'])+'/join',{},user=user)
  content=json.loads(row['content']) if row['content'] else None
  if not content:
   with tempfile.TemporaryDirectory(prefix='imo-media-') as temporary:
    path,meta=await self.fetch_imo_media(row,Path(temporary))
    path,kind,info,thumbnail=await inspect_file(path,meta['kind'],meta.get('mime'))
    uri=await self.upload_matrix(path,info['mimetype'],path.name)
    content={'msgtype':'m.'+kind,'body':path.name,'filename':path.name,'url':uri,'info':info,'com.matrix.imo':{'msg_id':data['msgid'],'timestamp':data['ts'],'own':data['own'],'kind':'group' if row['buid'].endswith(';') else 'dm','media':True},'com.matrix.media.retention':{'months':2,'from':'upload'}}
    self.db.execute('UPDATE media_inbox SET content=? WHERE key=?',(json.dumps(content,ensure_ascii=False),row['key']));self.db.commit()
    if thumbnail:
     try:
      thumb_uri=await self.upload_matrix(thumbnail,'image/jpeg','preview.jpg')
      with Image.open(thumbnail) as image:thumb_info={'w':image.width,'h':image.height,'mimetype':'image/jpeg','size':thumbnail.stat().st_size}
      content['info'].update(thumbnail_url=thumb_uri,thumbnail_info=thumb_info)
      self.db.execute('UPDATE media_inbox SET content=? WHERE key=?',(json.dumps(content,ensure_ascii=False),row['key']));self.db.commit()
     except Exception:log.warning('Video thumbnail unavailable; original video remains available')
  result=await self.event(portal['room_id'],content,'imo_media_'+row['key'],user=user,timestamp=int(data['ts'])//1000000)
  self.db.execute("UPDATE media_inbox SET status='sent',event_id=?,error=NULL WHERE key=?",(result['event_id'],row['key']))
  self.db.execute("UPDATE inbox SET status='sent',event_id=? WHERE key=? AND status='media_pending'",(result['event_id'],row['key']));self.db.commit()
 async def media_worker(self):
  while True:
   try:
    if self.state!='CONNECTED' or not self.ready or not self.browser_ready:await asyncio.sleep(1);continue
    row=self.db.execute("SELECT * FROM media_inbox WHERE status IN ('pending','error') AND next_retry<=? ORDER BY ts DESC LIMIT 1",(time.time(),)).fetchone()
    if not row:await asyncio.sleep(1);continue
    try:
     limit=getattr(getattr(self,'manager',None),'media_limit',self.media_fallback_limit)
     async with limit:await self.post_media(row)
    except asyncio.CancelledError:raise
    except Exception as exc:
     attempts=row['attempts']+1;status='error' if attempts>=6 else 'pending'
     self.db.execute('UPDATE media_inbox SET attempts=?,status=?,next_retry=?,error=? WHERE key=?',(attempts,status,time.time()+(86400 if status=='error' else min(3600,10*2**attempts)),type(exc).__name__,row['key']))
     if status=='error':
      original=self.db.execute('SELECT * FROM inbox WHERE key=?',(row['key'],)).fetchone()
      if original and original['status']=='media_pending':
       payload=json.loads(original['payload']);payload['text']='[Не удалось загрузить вложение IMO. Оно может быть удалено в IMO или превышать 250 МБ.]'
       self.db.execute("UPDATE inbox SET status='pending',payload=? WHERE key=?",(json.dumps(payload,ensure_ascii=False),row['key']))
     self.db.commit();log.warning('IMO media will retry: %s',type(exc).__name__)
   except asyncio.CancelledError:raise
   except Exception:log.exception('IMO media worker retry');await asyncio.sleep(3)
 async def transaction(self,request):
  if request.headers.get('Authorization')!='Bearer '+CONFIG['hs_token']:
   return await super().transaction(request)
  data=await request.json()
  for event in data.get('events',[]):
   if event.get('sender')!=OWNER or event.get('type')!='m.room.message':continue
   content=event.get('content') or {};msgtype=content.get('msgtype')
   if msgtype not in ['m.image','m.video','m.file','m.audio']:continue
   portal=self.db.execute('SELECT * FROM portals WHERE room_id=?',(event.get('room_id'),)).fetchone();eid=event.get('event_id')
   if not portal or not eid:continue
   status='pending';error=None
   try:parse_mxc(content.get('url'))
   except ValueError:status='error';error='Не удалось прочитать вложение Matrix: отсутствует доступный файл.'
   if content.get('m.relates_to',{}).get('rel_type')=='m.replace':status='error';error='Редактирование уже отправленных вложений пока не поддерживается.'
   safe={'msgtype':msgtype,'url':content.get('url') if status=='pending' else None,'name':filename(content.get('filename') or content.get('body')),'mime':str((content.get('info') or {}).get('mimetype') or 'application/octet-stream')[:120]}
   self.db.execute('INSERT OR IGNORE INTO outbox(event_id,room_id,buid,body,msgid,status,error) VALUES(?,?,?,?,?,?,?)',(eid,portal['room_id'],portal['buid'],safe['name'],digest(eid)[:16],status,error))
   self.db.execute('INSERT OR IGNORE INTO media_outbox VALUES(?,?)',(eid,json.dumps(safe,ensure_ascii=False)))
  self.db.commit()
  return await super().transaction(request)
 async def stage_outgoing(self,row,media):
  server,media_id=parse_mxc(media['url']);handle=None
  with tempfile.TemporaryDirectory(prefix='imo-outgoing-') as temporary:
   path=Path(temporary)/media['name'];size=0
   async with self.http.get(HS+'/_matrix/client/v1/media/download/'+q(server)+'/'+q(media_id),headers={'Authorization':'Bearer '+CONFIG['as_token']},allow_redirects=False,timeout=ClientTimeout(total=180)) as response:
    if response.status!=200:raise RuntimeError('Matrix media download HTTP '+str(response.status))
    if response.content_length and response.content_length>MAX_BYTES:raise RuntimeError('Файл больше 250 МБ.')
    with path.open('wb') as output:
     async for chunk in response.content.iter_chunked(CHUNK):
      size+=len(chunk)
      if size>MAX_BYTES:raise RuntimeError('Файл больше 250 МБ.')
      output.write(chunk)
   if not size:raise RuntimeError('Пустой файл не может быть отправлен.')
   meta=None;cover=None
   if media['msgtype']=='m.video':
    path,kind,info,thumbnail=await inspect_file(path,'video',media['mime'])
    media['send_msgtype']='m.video' if kind=='video' else 'm.file'
    size=path.stat().st_size;media['mime']=info['mimetype']
    if kind=='video':
     meta={'videoWidth':info.get('w',0),'videoHeight':info.get('h',0),'duration':info.get('duration',0)/1000}
     if thumbnail:cover=base64.b64encode(thumbnail.read_bytes()).decode()
     if path.suffix=='.mp4':media['name']=filename(Path(media['name']).stem+'.mp4')
   handle=await self.media_eval('(p)=>window.__matrixIMO.mediaStage(p)',{'name':media['name'],'mime':media['mime'],'size':size,'limit':MAX_BYTES,'meta':meta,'cover':cover})
   try:
    with path.open('rb') as source:
     while chunk:=source.read(CHUNK):await self.media_eval('(p)=>window.__matrixIMO.mediaAppend(p)',{'handle':handle,'data':base64.b64encode(chunk).decode()})
    return handle
   except BaseException:
    with contextlib.suppress(Exception):await self.media_eval('(h)=>window.__matrixIMO.mediaRelease(h)',handle)
    raise
 async def outbound_worker(self):
  self.db.execute("UPDATE outbox SET status='unknown',error='Отправка была прервана; требуется сверка с IMO.' WHERE status='sending'");self.db.commit()
  while True:
   handle=None
   try:
    for failure in list(self.db.execute("SELECT * FROM outbox WHERE status IN ('error','unknown') AND notice_sent=0")):
     await self.notice(failure['room_id'],'IMO: '+str(failure['error'])+' Сообщение автоматически повторно не отправлялось.','failure_'+digest(failure['event_id']))
     self.db.execute('UPDATE outbox SET notice_sent=1 WHERE event_id=?',(failure['event_id'],));self.db.commit()
    row=self.db.execute("SELECT * FROM outbox WHERE status='pending' LIMIT 1").fetchone()
    if not row or self.state!='CONNECTED' or not self.browser_ready:await asyncio.sleep(.5);continue
    media_row=self.db.execute('SELECT payload FROM media_outbox WHERE event_id=?',(row['event_id'],)).fetchone()
    media=json.loads(media_row[0]) if media_row else None
    async with self.send_lock:
     if media:
      try:handle=await self.stage_outgoing(row,media)
      except Exception:
       self.db.execute("UPDATE outbox SET status='error',error='Не удалось подготовить файл. Проверьте, что он доступен в Matrix и не превышает 250 МБ.' WHERE event_id=?",(row['event_id'],));self.db.commit();continue
     uid=await self.evaluate('()=>window.__matrixIMO.status().uid')
     if uid!=self.bound_uid:raise RuntimeError('IMO account changed before sending')
     self.db.execute("UPDATE outbox SET status='sending' WHERE event_id=?",(row['event_id'],));self.db.commit()
     try:
      if media:
       await self.media_eval('(p)=>window.__matrixIMO.mediaSend(p)',{'handle':handle,'buid':row['buid'],'msgid':row['msgid'],'msgtype':media.get('send_msgtype',media['msgtype'])})
       result=(await self.poll_media(handle,600))['result']
      else:result=await self.evaluate('async({id,text,msgid})=>await window.__matrixIMO.send(id,text,msgid)',{'id':row['buid'],'text':row['body'],'msgid':row['msgid']})
      ts=str((result or {}).get('timestamp_nano_str') or '')
      if ts:
       self.db.execute("UPDATE outbox SET status='accepted_by_imo',imo_ts=?,error=NULL WHERE event_id=?",(ts,row['event_id']));self.db.commit()
       await self.event(row['room_id'],{'event_id':row['event_id'],'status':'accepted_by_imo','delivery_confirmed':False},'status_'+digest(row['event_id']),event_type='com.matrix.imo.status')
      else:
       self.db.execute("UPDATE outbox SET status='error',error='IMO отклонил сообщение.' WHERE event_id=?",(row['event_id'],));self.db.commit()
     except Exception:
      self.db.execute("UPDATE outbox SET status='unknown',error='Нет подтверждения принятия сообщения от IMO.' WHERE event_id=?",(row['event_id'],));self.db.commit()
   except asyncio.CancelledError:raise
   except Exception as exc:log.warning('Media outbound worker: %s',type(exc).__name__);await asyncio.sleep(3)
   finally:
    if handle:
     with contextlib.suppress(Exception):await self.media_eval('(h)=>window.__matrixIMO.mediaRelease(h)',handle)
 async def startup(self,app):
  await super().startup(app);self.tasks.append(asyncio.create_task(self.media_worker()))
