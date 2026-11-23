from pathlib import Path
import asyncio,time,secrets,re
from matrix_bridge import MatrixBridge,q,digest
class SocialBridge(MatrixBridge):
 vk_text_implemented=True
 async def report_success(self,row):
  await self.matrix('PUT','/_matrix/client/v3/rooms/'+q(row['room_id'])+'/send/com.beeper.message_send_status/status_'+digest(row['event_id']),{'status':'SUCCESS','network':self.platform,'m.relates_to':{'rel_type':'m.reference','event_id':row['event_id']}})
 async def outgoing(self):
  row=self.store.pending_outgoing()
  if not row:return
  await super().outgoing()
  current=self.store.db.execute('SELECT state FROM outbox WHERE event_id=?',(row['event_id'],)).fetchone()
  if current and current['state']=='sent':
   room=self.store.db.execute('SELECT room_id FROM chats WHERE slot=? AND cid=?',(row['slot'],row['cid'])).fetchone()
   try:await self.report_success({**dict(row),'room_id':room['room_id']})
   except Exception:pass
 async def native(self,slot,op,args=None):
  page=await self.g.browser(slot);args=dict(args or {})
  if self.platform=='threads':
   if op=='chats':args['bootstrap_threads']=self.g.registry[slot].get('bootstrap_threads',[])
   if op=='send':
    pending=self.store.db.execute("SELECT event_id FROM outbox WHERE slot=? AND cid=? AND state='sending'",(slot,args['cid'])).fetchall()
    if len(pending)!=1:raise RuntimeError('SEND_QUEUE_NOT_EXCLUSIVE')
    self.store.db.execute('CREATE TABLE IF NOT EXISTS threads_send_ids (event_id TEXT PRIMARY KEY,offline_id TEXT NOT NULL UNIQUE)')
    with self.store.db:
     self.store.db.execute('INSERT OR IGNORE INTO threads_send_ids VALUES (?,?)',(pending[0]['event_id'],str((int(time.time()*1000)<<22)|secrets.randbits(22))))
    args['offline_id']=self.store.db.execute('SELECT offline_id FROM threads_send_ids WHERE event_id=?',(pending[0]['event_id'],)).fetchone()[0]
   return await page.evaluate(Path('/social/threads_adapter.js').read_text(),{'op':op,'args':args})
  if self.platform=='vk':
   args['expected_id']=slot
   if op=='send' and 'random_id' not in args:
    pending=self.store.db.execute("SELECT event_id FROM outbox WHERE slot=? AND cid=? AND state='sending'",(slot,args['cid'])).fetchall()
    if len(pending)!=1:raise RuntimeError('SEND_QUEUE_NOT_EXCLUSIVE')
    self.store.db.execute('CREATE TABLE IF NOT EXISTS vk_send_ids (event_id TEXT PRIMARY KEY,random_id INTEGER NOT NULL UNIQUE)')
    for _ in range(5):
     with self.store.db:self.store.db.execute('INSERT OR IGNORE INTO vk_send_ids VALUES (?,?)',(pending[0]['event_id'],secrets.randbelow(2147483646)+1))
     saved=self.store.db.execute('SELECT random_id FROM vk_send_ids WHERE event_id=?',(pending[0]['event_id'],)).fetchone()
     if saved:args['random_id']=saved[0];break
    if 'random_id' not in args:raise RuntimeError('SEND_ID_UNAVAILABLE')
   await page.wait_for_function("()=>typeof window.vkApi?.api==='function'&&Number(window.vk?.id)>0",timeout=15000)
   return await page.evaluate(Path('/social/vk_adapter.js').read_text(),{'op':op,'args':args})
  if self.platform!='ok':raise RuntimeError('VK_LOGIN_REQUIRED')
  source=Path('/social/ok_adapter.js').read_text()
  if op=='send':
   await page.evaluate(source,{'op':'select','args':{'cid':args['cid']}})
   await page.wait_for_function("cid=>String(document.querySelector('msg-page')?.state?.selectedChat?.id)===cid",arg=str(args['cid']),timeout=15000)
   editor=page.locator('[data-tsid="write_msg_input-input"][contenteditable]');await editor.fill(args['body'],timeout=15000)
   await page.wait_for_function("({cid,body})=>document.querySelector('msg-page')?.store?.app?.chat?.getPostingService().getPostingForm(Number(cid))?.text===body",arg=args,timeout=5000)
  return await page.evaluate(source,{'op':op,'args':args})
 async def refresh_chat(self,slot,chat):
  await self.portal(slot,chat);cursor=self.store.cursor(slot,chat['id']);since=int(cursor or self.g.registry[slot].get('connected_at',0))
  history=await self.native(slot,'history',{'cid':chat['id'],'since':since});self.store.ingest(slot,chat['id'],history['messages'],str(history['cursor']));self.last_native[(slot,chat['id'])]=(chat['last_id'],chat['modified'])
 async def poll(self,slot):
  if self.platform=='threads':
   return await self.poll_threads(slot)
  if self.platform=='vk':return await self.poll_vk(slot)
  if self.platform!='ok':
   async with self.g.locks.setdefault(slot,asyncio.Lock()):
    if not await self.g.logged_in(slot):self.g.states[slot]={'state_event':'BAD_CREDENTIALS','error':'Нужен повторный вход'};return
    await self.space(slot);self.g.states[slot]={'state_event':'CONNECTING','error':'Вход выполнен, но веб-чат Threads возвращает ошибку. Обмен сообщениями пока не подтверждён.' if self.platform=='threads' else 'Вход VK выполнен; обмен сообщениями ещё не проверен.','timestamp':int(time.time())};return
  await super().poll(slot)
 @staticmethod
 def rate_limited(error):
  text=str(error).casefold()
  return '1675004' in text or 'rate limit exceeded' in text or 'too many requests' in text
 async def poll_threads(self,slot):
  account=self.g.registry.get(slot)
  if not account:return
  # The one-off test seed must never force history requests for a chat that
  # disappears from the service's own inbox (for example after deletion).
  if account.pop('bootstrap_threads',None) is not None:self.g.persist()
  now=time.time();retry=account.get('retry_not_before',0)
  if now<retry:
   message='Threads временно не обрабатывает запросы сообщений. Соединение будет проверено автоматически.' if account.get('transport_pause_reason')=='service' else 'Threads ограничил частоту запросов. Запросы приостановлены; соединение восстановится автоматически.'
   self.g.states[slot]={'state_event':'TRANSIENT_DISCONNECT','error':message,'retry_at':retry,'timestamp':int(now)}
   return
  if time.monotonic()<getattr(self,'threads_next_poll',{}).get(slot,0):return
  async with self.g.locks.setdefault(slot,asyncio.Lock()):
   try:
    if not await self.g.logged_in(slot):self.g.states[slot]={'state_event':'BAD_CREDENTIALS','error':'Нужен повторный вход'};return
    identity=await self.native(slot,'identity')
    if identity['id']!=account['native_id']:raise RuntimeError('WRONG_ACCOUNT')
    self.identities[slot]=identity;await self.space(slot)
    chats=await self.native(slot,'chats');self.chats[slot]=chats;failed=[]
    seen=account.setdefault('seen_chats',{});checked=account.setdefault('history_checked_at',{})
    for chat in chats:
     fingerprint=[chat['last_id'],chat['modified']]
     if seen.get(chat['id'])==fingerprint and now-checked.get(chat['id'],0)<900:continue
     try:
      await self.refresh_chat(slot,chat);seen[chat['id']]=fingerprint;checked[chat['id']]=now
     except asyncio.CancelledError:raise
     except Exception as error:
      if self.rate_limited(error) or '1675012' in str(error):raise
      failed.append(chat['id'])
    self.g.states[slot]={'state_event':'TRANSIENT_DISCONNECT' if failed else 'CONNECTED','timestamp':int(time.time())}
    if failed:self.g.states[slot]['error']='Не удалось обновить переписки: '+str(len(failed))
    else:account['transport_verified']=True;account.pop('retry_not_before',None);account.pop('rate_limit_count',None);account.pop('transport_pause_reason',None);self.failures[slot]=0
    self.g.persist()
    if not hasattr(self,'threads_next_poll'):self.threads_next_poll={}
    self.threads_next_poll[slot]=time.monotonic()+90
   except asyncio.CancelledError:raise
   except Exception as error:
    temporary='1675012' in str(error)
    if not self.rate_limited(error) and not temporary:raise
    count=account.get('rate_limit_count',0)+1;account['rate_limit_count']=count;account['transport_pause_reason']='service' if temporary else 'rate';account['retry_not_before']=int(time.time())+min(900 if temporary else 3600,(120 if temporary else 900)*2**min(count-1,3));self.g.persist()
    message='Threads временно не обрабатывает запросы сообщений. Соединение будет проверено автоматически.' if temporary else 'Threads ограничил частоту запросов. Запросы приостановлены; соединение восстановится автоматически.'
    self.g.states[slot]={'state_event':'TRANSIENT_DISCONNECT','error':message,'retry_at':account['retry_not_before'],'timestamp':int(time.time())}
 async def recover_native(self,slot):
  if self.platform not in ['ok','threads','vk']:return False
  return await super().recover_native(slot)
 async def poll_vk(self,slot):
  account=self.g.registry.get(slot)
  if not account:return
  if account.get('auth_required') or account.get('checkpoint_required'):
   self.g.states[slot]={'state_event':'BAD_CREDENTIALS','error':'VK требует подтверждение входа. Откройте переподключение аккаунта.','timestamp':int(time.time())};return
  now=time.time();retry=account.get('retry_not_before',0)
  if now<retry:
   self.g.states[slot]={'state_event':'TRANSIENT_DISCONNECT','error':'VK ограничил запросы. После паузы будет выполнена повторная проверка.','retry_at':retry,'timestamp':int(now)};return
  if time.monotonic()<getattr(self,'vk_next_poll',{}).get(slot,0):return
  async with self.g.locks.setdefault(slot,asyncio.Lock()):
   try:
    if not await self.g.logged_in(slot):self.g.states[slot]={'state_event':'BAD_CREDENTIALS','error':'Нужен повторный вход VK по QR-коду'};return
    identity=await self.native(slot,'identity')
    if identity['id']!=account['native_id']:raise RuntimeError('WRONG_ACCOUNT')
    self.identities[slot]=identity
    if identity.get('nick') and account['name']!=identity['nick']:
     account['name']=identity['nick'];self.g.persist()
     if account.get('space_room'):await self.matrix('PUT','/_matrix/client/v3/rooms/'+q(account['space_room'])+'/state/m.room.name/',{'name':'VK ('+account['name']+')'})
    await self.space(slot);chats=await self.native(slot,'chats');self.chats[slot]=chats;seen=account.setdefault('seen_chats',{});checked=account.setdefault('history_checked_at',{});failed=[]
    for chat in chats:
     fingerprint=[chat['last_id'],chat['modified']]
     if seen.get(chat['id'])==fingerprint and now-checked.get(chat['id'],0)<900:continue
     try:await self.refresh_chat(slot,chat);seen[chat['id']]=fingerprint;checked[chat['id']]=now
     except asyncio.CancelledError:raise
     except Exception as error:
      if re.search(r'VK_API_(5|6|9|14|27|28)(?:\D|$)|VK_AUTH_REQUIRED',str(error)):raise
      failed.append(chat['id'])
    self.g.states[slot]={'state_event':'TRANSIENT_DISCONNECT' if failed else 'CONNECTED','timestamp':int(time.time())}
    if failed:self.g.states[slot]['error']='Не удалось обновить переписки VK: '+str(len(failed))
    else:account['transport_verified']=True;account.pop('retry_not_before',None);account.pop('rate_limit_count',None);self.failures[slot]=0
    self.g.persist()
    if not hasattr(self,'vk_next_poll'):self.vk_next_poll={}
    self.vk_next_poll[slot]=time.monotonic()+60
   except asyncio.CancelledError:raise
   except Exception as error:
    code=re.search(r'VK_API_(6|9)(?:\D|$)',str(error))
    if code:
     count=account.get('rate_limit_count',0)+1;account['rate_limit_count']=count;account['retry_not_before']=int(time.time())+min(3600,(60 if code[1]=='6' else 900)*2**min(count-1,3));self.g.persist();self.g.states[slot]={'state_event':'TRANSIENT_DISCONNECT','error':'VK ограничил запросы. После паузы будет выполнена повторная проверка.','retry_at':account['retry_not_before'],'timestamp':int(time.time())};return
    if 'VK_API_14' in str(error):account['checkpoint_required']=True;self.g.persist();self.g.states[slot]={'state_event':'BAD_CREDENTIALS','error':'VK требует ручную проверку безопасности. Откройте переподключение аккаунта.','timestamp':int(time.time())};return
    if re.search(r'VK_API_(5|27|28)(?:\D|$)|VK_AUTH_REQUIRED|IM_NOT_AUTHENTICATED|WRONG_ACCOUNT',str(error)):account['auth_required']=True;self.g.persist();self.g.states[slot]={'state_event':'BAD_CREDENTIALS','error':'В профиле открыт другой аккаунт VK. Переподключите исходный аккаунт.' if 'WRONG_ACCOUNT' in str(error) else 'VK запросил повторный вход по QR-коду','timestamp':int(time.time())};return
    raise
