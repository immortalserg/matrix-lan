"""Owner-authenticated contact lookup; no messages or automatic friend requests."""
import asyncio,json,re,time
from pathlib import Path
from urllib.parse import unquote
from aiohttp import web

LOOKUP=Path('/bridge/imo_lookup.js').read_text()

def error(status,code,text,**extra):
 return web.json_response({'errcode':code,'error':text,**extra},status=status)

async def handle(bridge,request):
 # Manager authenticates before selecting the exact login. Do not guess a login.
 if not request.query.get('login_id'):return error(400,'M_BAD_REQUEST','Выберите аккаунт IMO.')
 if bridge.state!='CONNECTED':return error(409,'M_NOT_CONNECTED','Требуется подключение IMO.')
 path=request.match_info['tail']
 if not hasattr(bridge,'contact_lookup_lock'):
  bridge.contact_lookup_lock=asyncio.Lock();bridge.contact_lookup_at=0;bridge.contact_lookup_cache={}
 now=time.monotonic()
 bridge.contact_lookup_cache={k:v for k,v in bridge.contact_lookup_cache.items() if v['expires']>now}
 if path.startswith('resolve_identifier/'):
  if request.method!='GET':return error(405,'M_UNRECOGNIZED','Нужен GET.')
  identifier=path[len('resolve_identifier/'):]
  if not isinstance(identifier,str) or len(identifier)>80:return error(400,'M_BAD_REQUEST','Проверьте номер или IMO ID.')
  if identifier.startswith('+'):
   if not re.fullmatch(r'\+[1-9][0-9]{6,14}',identifier):return error(400,'M_BAD_REQUEST','Проверьте международный номер.')
  elif not re.fullmatch(r'@[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',identifier):return error(400,'M_BAD_REQUEST','Введите IMO ID с @.')
  async with bridge.contact_lookup_lock:
   now=time.monotonic();wait=3.5-(now-bridge.contact_lookup_at)
   if wait>0:return error(429,'M_LIMIT_EXCEEDED','Подождите перед повторным поиском.',retry_after_ms=int(wait*1000)+1)
   bridge.contact_lookup_at=now
   try:result=await asyncio.wait_for(bridge.evaluate('('+LOOKUP+')',identifier),timeout=40)
   except (asyncio.TimeoutError,RuntimeError):return error(503,'M_UNAVAILABLE','IMO не ответил. Повторите позже.')
   if not isinstance(result,dict):return error(503,'M_UNAVAILABLE','IMO вернул неполный результат.')
   if result.get('error')=='rate_limited':
    bridge.contact_lookup_at=time.monotonic()+56.5
    return error(429,'M_LIMIT_EXCEEDED','IMO временно ограничил поиск.',retry_after_ms=60000)
   if result.get('error')=='invalid_phone':return error(400,'M_BAD_REQUEST','Проверьте номер и код страны.')
   if result.get('error')=='not_found':return error(404,'M_NOT_FOUND','Контакт не найден или поиск закрыт настройками IMO.')
   uid=result.get('id')
   if not isinstance(uid,str) or not re.fullmatch(r'[0-9]{1,30}',uid):return error(503,'M_UNAVAILABLE','Поиск IMO временно недоступен.')
   name=str(result.get('name') or identifier)[:160]
   bridge.contact_lookup_cache[uid]={'name':name,'expires':time.monotonic()+300}
   room=bridge.db.execute('SELECT room_id FROM portals WHERE buid=?',(uid,)).fetchone()
   return web.json_response({'id':uid,'name':name,'dm_room_mxid':room['room_id'] if room else None})
 if path.startswith('create_dm/'):
  if request.method!='POST':return error(405,'M_UNRECOGNIZED','Нужен POST.')
  uid=path[len('create_dm/'):]
  cached=bridge.contact_lookup_cache.get(uid)
  if not cached or cached['expires']<=time.monotonic():return error(404,'M_NOT_FOUND','Сначала выполните поиск контакта.')
  async with bridge.contact_lookup_lock:
   portal=await bridge.create_portal(uid,cached['name']);await bridge.link_portal(portal)
  return web.json_response({'id':uid,'name':cached['name'],'dm_room_mxid':portal['room_id']})
 return error(404,'M_NOT_FOUND','Неизвестный маршрут.')
