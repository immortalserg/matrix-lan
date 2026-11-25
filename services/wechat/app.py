"""Owner-only Matrix bridge for the local Agent WeChat native API.

Native credentials never leave the server. Transactions and message mappings
are durable; native sends use one stable idempotency key per Matrix event.
"""
import asyncio, base64, calendar, hashlib, hmac, json, logging, os, secrets, sqlite3, time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
import aiohttp
from aiohttp import web
from urllib.parse import urlsplit

log = logging.getLogger('wechat-matrix')
def q(value): return quote(str(value), safe='')
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
MAX_MEDIA = 50 * 1024 * 1024

def has_media(message):
    if message.get('type') in (3,34,43,47):return True
    return message.get('type')==49 and (message.get('appMessageType') in (6,51) or bool(message.get('media')))


class ApiError(Exception):
    def __init__(self, status, code): self.status, self.code = status, str(code)[:250]

class Bridge:
    def __init__(self, directory):
        self.root = Path(directory)
        self.c = json.loads((self.root/'config.json').read_text())
        self.key = Path(self.c['native_key_file']).read_text().strip()
        self.native_url = self.c.get('native_url', 'http://127.0.0.1:6174').rstrip('/')
        self.db = sqlite3.connect(self.root/'bridge.sqlite3')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1, space TEXT, cursor TEXT NOT NULL DEFAULT '0', name TEXT, error TEXT, last_ok REAL);
        CREATE TABLE IF NOT EXISTS logins(id TEXT PRIMARY KEY, account TEXT, operation TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS portals(account TEXT, thread TEXT, room TEXT UNIQUE, name TEXT, kind TEXT, PRIMARY KEY(account,thread));
        CREATE TABLE IF NOT EXISTS messages(account TEXT, native TEXT, event TEXT, PRIMARY KEY(account,native));
        CREATE TABLE IF NOT EXISTS transactions(id TEXT PRIMARY KEY, payload TEXT, done INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS outbox(event TEXT PRIMARY KEY, account TEXT, thread TEXT, room TEXT, payload TEXT, state TEXT DEFAULT 'pending', result TEXT);
        CREATE TABLE IF NOT EXISTS assets(account TEXT, asset TEXT, created REAL, PRIMARY KEY(account,asset));
        CREATE TABLE IF NOT EXISTS media_queue(account TEXT, message TEXT, room TEXT, sender TEXT, state TEXT DEFAULT 'pending', job TEXT, attempts INTEGER DEFAULT 0, PRIMARY KEY(account,message));
        CREATE TABLE IF NOT EXISTS removals(account TEXT PRIMARY KEY, nonce TEXT, logout TEXT, forget TEXT);
        ''')
        columns={x[1] for x in self.db.execute('PRAGMA table_info(outbox)')}
        for col in ['source','native_job']:
            if col not in columns:self.db.execute('ALTER TABLE outbox ADD COLUMN '+col+' TEXT')
        media_columns={x[1] for x in self.db.execute('PRAGMA table_info(media_queue)')}
        if 'next_retry' not in media_columns:self.db.execute('ALTER TABLE media_queue ADD COLUMN next_retry REAL NOT NULL DEFAULT 0')
        self.db.commit()
        self.locks = defaultdict(asyncio.Lock)
        self.auth = {}; self.catalog_cache = {}; self.tasks = []
        self.bot = '@wechatbot:'+self.c['server_name']
    def rows(self, sql, args=()): return [dict(x) for x in self.db.execute(sql,args)]
    def one(self, sql, args=()): return next(iter(self.rows(sql,args)),None)
    def write(self, sql, args=()):
        self.db.execute(sql,args); self.db.commit()
    async def request(self, base, path, token, method='GET', body=None, raw=False, headers=None):
        h={'Authorization':'Bearer '+token, **(headers or {})}
        kwargs={'headers':h, 'allow_redirects':False}
        if isinstance(body, bytes): kwargs['data']=body
        elif body is not None: kwargs['json']=body
        async with self.http.request(method,base+path,**kwargs) as r:
            if r.status >= 300:
                try:
                    v=await r.json(); e=v.get('errcode') or v.get('error') or 'http_'+str(r.status)
                    if isinstance(e,dict): e=e.get('code','native_error')
                except Exception: e='http_'+str(r.status)
                raise ApiError(r.status,e)
            if raw:
                if r.content_length and r.content_length>MAX_MEDIA: raise ApiError(413,'media_too_large')
                out=bytearray()
                async for chunk in r.content.iter_chunked(65536):
                    out.extend(chunk)
                    if len(out)>MAX_MEDIA: raise ApiError(413,'media_too_large')
                return bytes(out)
            if r.status == 204: return {}
            return await r.json()
    async def native(self, path, method='GET', body=None, **kwargs):
        return await self.request(self.native_url,'/v1'+path,self.key,method,body,**kwargs)
    async def matrix(self,path,method='GET',body=None,user=None,**kwargs):
        if user: path+=('&' if '?' in path else '?')+'user_id='+q(user)
        return await self.request(self.c['homeserver'],path,self.c['as_token'],method,body,**kwargs)
    async def authenticate(self,r):
        token=r.headers.get('Authorization','').removeprefix('Bearer ')
        if not token: raise ApiError(401,'M_MISSING_TOKEN')
        origin=r.headers.get('Origin')
        if origin and origin not in self.c['origins']: raise ApiError(403,'origin_forbidden')
        if r.query.get('user_id')!=self.c['owner']: raise ApiError(403,'owner_only')
        key=digest(token)
        if self.auth.get(key,0)<time.monotonic():
            data=await self.request(self.c['homeserver'],'/_matrix/client/v3/account/whoami',token)
            if data.get('user_id')!=self.c['owner']: raise ApiError(403,'owner_only')
            self.auth={k:v for k,v in self.auth.items() if v>time.monotonic()}
            self.auth[key]=time.monotonic()+30
    async def register(self,localpart,name):
        try: await self.matrix('/_matrix/client/v3/register','POST',{'type':'m.login.application_service','username':localpart})
        except ApiError as e:
            if e.code!='M_USER_IN_USE': raise
        user='@'+localpart+':'+self.c['server_name']
        await self.matrix('/_matrix/client/v3/profile/'+q(user)+'/displayname','PUT',{'displayname':name[:200]},user=user)
        return user
    async def ensure_space(self,a,name):
        row=self.one('SELECT * FROM accounts WHERE id=?',(a,))
        if row and row['space']: return row['space']
        alias='wechat_account_'+digest(a)[:20]
        try: room=(await self.matrix('/_matrix/client/v3/directory/room/'+q('#'+alias+':'+self.c['server_name'])))['room_id']
        except ApiError as e:
            if e.status!=404: raise
            room=(await self.matrix('/_matrix/client/v3/createRoom','POST',{'visibility':'private','preset':'private_chat','room_alias_name':alias,'name':'WeChat ('+name+')','invite':[self.c['owner']],'creation_content':{'type':'m.space'},'initial_state':[{'type':'com.matrix.account','state_key':'','content':{'platform':'wechat','login_id':a}}]}))['room_id']
        self.write('UPDATE accounts SET space=? WHERE id=?',(room,a))
        return room
    async def bind(self,a):
        status=await self.native('/accounts/'+q(a))
        if not status.get('authenticated'): raise ApiError(409,'login_not_complete')
        name=status.get('ownUsername') or a
        try:
            contact=await self.native('/accounts/'+q(a)+'/contacts/'+q(name))
            name=contact.get('displayName') or name
        except ApiError: pass
        self.write('INSERT INTO accounts(id,name,enabled) VALUES(?,?,0) ON CONFLICT(id) DO UPDATE SET name=excluded.name',(a,name))
        await self.ensure_space(a,name)
        self.write('UPDATE accounts SET enabled=1 WHERE id=?',(a,))
        self.write('DELETE FROM removals WHERE account=?',(a,))
    async def whoami(self):
        live={x['id']:x for x in (await self.native('/accounts'))['items']}
        rows=[]
        for row in self.rows('SELECT * FROM accounts WHERE enabled=1'):
            st=live.get(row['id'],{})
            code='CONNECTED' if st.get('authenticated') and st.get('connection')=='online' else 'BAD_CREDENTIALS' if not st.get('authenticated') else 'TRANSIENT_DISCONNECT'
            error=row['error'] or ''
            if code=='CONNECTED' and (not row['last_ok'] or time.time()-row['last_ok']>120):
                code='CONNECTING' if not row['last_ok'] else 'TRANSIENT_DISCONNECT';error='Ожидается проверка синхронизации сообщений'
            if row['error']: code='UNKNOWN_ERROR'
            rows.append({'id':row['id'],'name':row['name'],'profile':{'name':row['name'],'username':st.get('ownUsername') or ''},'space_room':row['space'],'state':{'state_event':code,'error':error, 'timestamp':time.time()}})
        return {'bridge_bot':self.bot,'logins':rows,'login_flows':[{'id':'qr','name':'Добавить WeChat по QR-коду'}],'max_accounts':40}
    async def step(self,job):
        op=await self.native('/accounts/'+q(job['account'])+'/login-attempts/'+q(job['operation']))
        if op['state']=='succeeded':
            await self.bind(job['account'])
            return {'type':'complete','login_id':job['id'],'complete':{'id':job['account']},'instructions':'WeChat подключён. Переписки доступны в списке аккаунтов.'}
        if op['state'] in ['failed','cancelled','needs_review']:
            raise ApiError(409,'WeChat: '+str(op.get('result',{}).get('error') or op['state']))
        data=None
        if op.get('result',{}).get('qrAvailable'):
            png=await self.native('/accounts/'+q(job['account'])+'/login-attempts/'+q(job['operation'])+'/qr',raw=True)
            if not png.startswith(b'\x89PNG'): raise ApiError(502,'invalid_qr_image')
            data='data:image/png;base64,'+base64.b64encode(png).decode()
        return {'type':'display_and_wait','login_id':job['id'],'step_id':'qr','instructions':'На телефоне: WeChat → «+» → Сканировать. Подтвердите вход Matrix LAN.','display_and_wait':{'type':'qr_image' if data else 'wait','data':data}}
    async def provisioning(self,r):
        await self.authenticate(r)
        path=r.match_info['tail']; body=await r.json() if r.can_read_body else {}
        expected='GET' if path in ['whoami','login/flows','chats'] else 'POST'
        if r.method!=expected:raise ApiError(405,'method_not_allowed')
        if path in ['whoami','login/flows']: return await self.whoami()
        if path.startswith('login/start/'):
            pending=self.rows('SELECT * FROM logins WHERE created>?',(time.time()-360,))
            if pending:
                try:
                    previous=await self.step(pending[-1])
                    if previous['type']!='complete': return previous
                except ApiError as e:
                    if e.status>=500: raise
                self.write('DELETE FROM logins WHERE id=?',(pending[-1]['id'],))
            a=body.get('login_id') or r.query.get('login_id')
            if a:
                if not self.one('SELECT * FROM accounts WHERE id=? AND enabled=1',(a,)): raise ApiError(404,'account_not_found')
            else:
                if len(self.rows('SELECT id FROM accounts WHERE enabled=1'))>=40: raise ApiError(409,'account_limit')
                a=(await self.native('/accounts','POST',{}))['id']
            op=await self.native('/accounts/'+q(a)+'/login-attempts','POST',{'strategy':'qr','forceQr':True,'allowQr':True,'computerName':'Matrix LAN','timeoutSeconds':300})
            j={'id':secrets.token_urlsafe(20),'account':a,'operation':op['id'],'created':time.time()}
            self.write('INSERT INTO logins VALUES(?,?,?,?)',tuple(j.values()))
            return await self.step(j)
        if path.startswith('login/step/') or path.startswith('login/cancel/'):
            jid=path.split('/')[2]; j=self.one('SELECT * FROM logins WHERE id=?',(jid,))
            if not j: raise ApiError(404,'login_not_found')
            if path.startswith('login/cancel/'):
                await self.native('/accounts/'+q(j['account'])+'/login-attempts/'+q(j['operation'])+'/cancel','POST',{})
                self.write('DELETE FROM logins WHERE id=?',(jid,)); return {}
            await asyncio.sleep(2)
            return await self.step(j)
        if path.startswith('logout/'):
            a=path.split('/',1)[1]
            if not self.one('SELECT * FROM accounts WHERE id=? AND enabled=1',(a,)): raise ApiError(404,'account_not_found')
            async with self.locks[a]:
                try:
                    async with asyncio.timeout(14):await self.remove_native(a)
                except asyncio.TimeoutError:raise ApiError(504,'Удаление ещё не подтверждено. Повторите удаление для проверки.')
                self.write('UPDATE accounts SET enabled=0,error=NULL WHERE id=?',(a,))
                self.write("UPDATE outbox SET state='cancelled' WHERE account=? AND state='pending'",(a,))
                self.write("UPDATE media_queue SET state='cancelled' WHERE account=? AND state='pending'",(a,))
                self.write('DELETE FROM logins WHERE account=?',(a,))
            return {'removed':True}
        if path in ['chats','chats/open']:
            a=r.query.get('login_id') or body.get('login_id')
            if not self.one('SELECT id FROM accounts WHERE id=? AND enabled=1',(a,)): raise ApiError(404,'select_account')
            items=await self.catalog(a)
            if path=='chats':
                search=r.query.get('search','').casefold(); matches=[x for x in items if search in (x['name']+' '+x['id']).casefold()]
                portals={x['thread']:x['room'] for x in self.rows('SELECT * FROM portals WHERE account=?',(a,))}
                return {'chats':[{**x,'room_id':portals.get(x['id'])} for x in matches[:200]],'total':len(matches)}
            item=next((x for x in items if x['id']==body.get('id')),None)
            if not item: raise ApiError(404,'chat_not_found')
            async with self.locks[a]:
                room=await self.portal(a,item['id'],item)
                # Only messages actually present in the new native profile can be imported.
                try:
                    history=await self.native('/accounts/'+q(a)+'/threads/'+q(item['id'])+'/messages?limit=50')
                except ApiError: history={'items':[]}
                for m in sorted(history.get('items',[]),key=lambda x:x.get('createdAt') or ''):
                    await self.incoming(a,m)
            return {'room_id':room}
        raise ApiError(404,'M_NOT_FOUND')
    async def remove_native(self,a):
        try:initial=await self.native('/accounts/'+q(a))
        except ApiError as e:
            if e.status==404:return
            raise
        self.write('INSERT OR IGNORE INTO removals(account,nonce) VALUES(?,?)',(a,secrets.token_urlsafe(20)))
        for kind,route,body in [('logout','logout',{}),('forget','forget-credentials',{'confirm':True})]:
            if kind=='logout' and not initial.get('authenticated'):continue
            entry=self.one('SELECT * FROM removals WHERE account=?',(a,))
            opid=entry[kind]
            if not opid:
                op=await self.native('/accounts/'+q(a)+'/'+route,'POST',body,headers={'Idempotency-Key':'matrix-'+kind+'-'+entry['nonce']})
                opid=op['id'];self.write('UPDATE removals SET '+kind+'=? WHERE account=?',(opid,a))
            while True:
                op=await self.native('/accounts/'+q(a)+'/operations/'+q(opid))
                if op['state']=='succeeded':break
                if op['state'] in ['failed','cancelled','needs_review']:
                    if op['state']!='needs_review':self.write('UPDATE removals SET '+kind+'=NULL,nonce=? WHERE account=?',(secrets.token_urlsafe(20),a))
                    raise ApiError(409,'WeChat не подтвердил '+route)
                await asyncio.sleep(.3)
        status=await self.native('/accounts/'+q(a))
        if status.get('authenticated'):raise ApiError(409,'WeChat по-прежнему авторизован')
        await self.native('/accounts/'+q(a),'DELETE',{'confirmAccountId':a})
    async def catalog(self,a):
        cached=self.catalog_cache.get(a)
        if cached and cached[0]>time.monotonic(): return cached[1]
        items={}
        for kind in ['contacts','groups','threads']:
            cursor=None
            for _ in range(100):
                value=await self.native('/accounts/'+q(a)+'/'+kind+'?limit=200'+('&cursor='+q(cursor) if cursor else ''))
                for x in value.get('items',[]):
                    ident=x.get('id') or x.get('username') or x.get('peerId')
                    if not ident or ident==self.c.get('own_username'): continue
                    group=kind=='groups' or x.get('kind')=='group' or ident.endswith('@chatroom')
                    name=x.get('displayName') or x.get('remark') or x.get('nickname') or x.get('name') or ident
                    items[ident]={'id':ident,'name':('[Группа] ' if group else '')+name,'kind':'group' if group else 'direct'}
                cursor=value.get('nextCursor')
                if not cursor: break
        result=sorted(items.values(),key=lambda x:x['name'].casefold())
        self.catalog_cache[a]=(time.monotonic()+45,result)
        return result
    async def portal(self,a,thread,item=None):
        old=self.one('SELECT * FROM portals WHERE account=? AND thread=?',(a,thread))
        if old:
            if old['kind']=='direct':await self.ensure_contact_member(a,thread,old['room'],old['name'])
            return old['room']
        if item is None:
            try: t=await self.native('/accounts/'+q(a)+'/threads/'+q(thread))
            except ApiError: t={}
            group=t.get('kind')=='group' or thread.endswith('@chatroom')
            item={'name':('[Группа] ' if group else '')+(t.get('displayName') or thread),'kind':'group' if group else 'direct'}
        alias='wechat_portal_'+digest(a+'|'+thread)[:28]
        info={'bridgebot':self.bot,'creator':self.bot,'protocol':{'id':'wechat','displayname':'WeChat'},'channel':{'id':thread,'displayname':item['name'],'fi.mau.receiver':a}}
        try: room=(await self.matrix('/_matrix/client/v3/directory/room/'+q('#'+alias+':'+self.c['server_name'])))['room_id']
        except ApiError as e:
            if e.status!=404: raise
            room=(await self.matrix('/_matrix/client/v3/createRoom','POST',{'visibility':'private','preset':'private_chat','room_alias_name':alias,'name':item['name'],'topic':'WeChat · '+('Группа' if item['kind']=='group' else 'Личная переписка'),'invite':[self.c['owner']],'initial_state':[{'type':'m.bridge','state_key':'wechat://'+a+'/'+thread,'content':info},{'type':'com.matrix.wechat.chat','state_key':'','content':{'kind':item['kind'],'account':a}}]}))['room_id']
        self.write('INSERT INTO portals VALUES(?,?,?,?,?)',(a,thread,room,item['name'],item['kind']))
        account=self.one('SELECT * FROM accounts WHERE id=?',(a,))
        space=await self.ensure_space(a,account['name'])
        await self.matrix('/_matrix/client/v3/rooms/'+q(space)+'/state/m.space.child/'+q(room),'PUT',{'via':[self.c['server_name']]})
        await self.matrix('/_matrix/client/v3/rooms/'+q(room)+'/state/m.space.parent/'+q(space),'PUT',{'via':[self.c['server_name']],'canonical':True})
        if item['kind']=='direct':await self.ensure_contact_member(a,thread,room,item['name'])
        return room
    async def ensure_contact_member(self,a,peer,room,name=None):
        """A direct peer is represented before they reply, using the same ghost
        identity that incoming messages use. Never invite a native username as
        an arbitrary Matrix user or share this room with a different account.
        """
        local='wechat_'+digest(a+'|'+peer)[:30]
        user='@'+local+':'+self.c['server_name']
        path='/_matrix/client/v3/rooms/'+q(room)
        try:member=await self.matrix(path+'/state/m.room.member/'+q(user))
        except ApiError as e:
            if e.status!=404:raise
            member={}
        if member.get('membership')=='join':return user
        if not name:
            try:
                contact=await self.native('/accounts/'+q(a)+'/contacts/'+q(peer))
                name=contact.get('displayName') or peer
            except ApiError:name=peer
        await self.register(local,name+' (WeChat)')
        if member.get('membership')!='invite':
            try:await self.matrix(path+'/invite','POST',{'user_id':user})
            except ApiError:
                current=await self.matrix(path+'/state/m.room.member/'+q(user))
                if current.get('membership') not in ['invite','join']:raise
        await self.matrix(path+'/join','POST',{},user=user)
        return user
    async def emit(self,room,key,content,user=None):
        return (await self.matrix('/_matrix/client/v3/rooms/'+q(room)+'/send/m.room.message/'+digest(key),'PUT',content,user=user))['event_id']
    async def incoming(self,a,m):
        mid=str(m['id'])
        if self.one('SELECT event FROM messages WHERE account=? AND native=?',(a,mid)): return
        thread=m['threadId']; room=await self.portal(a,thread)
        sender=m.get('senderId'); user=None
        if sender and m.get('direction')!='outgoing':
            user=await self.ensure_contact_member(a,sender,room)
        content={'msgtype':'m.text','body':m.get('body') or '[Сообщение WeChat: тип '+str(m.get('type'))+']'}
        event=await self.emit(room,a+'|'+mid,content,user)
        if has_media(m):
            self.write('INSERT OR IGNORE INTO media_queue(account,message,room,sender) VALUES(?,?,?,?)',(a,mid,room,user))
        self.write('INSERT OR IGNORE INTO messages VALUES(?,?,?)',(a,mid,event))
    async def process_events(self,a):
        async with self.locks[a]:
            row=self.one('SELECT * FROM accounts WHERE id=? AND enabled=1',(a,))
            if not row: return
            data=await self.native('/accounts/'+q(a)+'/events?after='+q(row['cursor'])+'&limit=100')
            for e in data.get('items',[]):
                if e['kind']=='message.committed':
                    m=await self.native('/accounts/'+q(a)+'/messages/'+q(e['data']['messageId']))
                    await self.incoming(a,m)
                self.write('UPDATE accounts SET cursor=?,last_ok=?,error=NULL WHERE id=?',(e['cursor'],time.time(),a))
            if data.get('items'):
                await self.native('/accounts/'+q(a)+'/consumers/matrix/ack','POST',{'cursor':data['items'][-1]['cursor']})
            self.write('UPDATE accounts SET last_ok=?,error=NULL WHERE id=?',(time.time(),a))
    async def source(self,row,c):
        if row.get('source'):return row['source']
        if c.get('file'):raise ApiError(400,'Зашифрованное вложение: отправьте файл в незашифрованную комнату этого моста')
        u=urlsplit(c.get('url',''))
        if u.scheme!='mxc' or u.netloc!=self.c['server_name'] or not u.path.strip('/') or '/' in u.path.strip('/'):
            raise ApiError(400,'invalid_matrix_media_url')
        data=await self.matrix('/_matrix/client/v1/media/download/'+q(u.netloc)+'/'+q(u.path.strip('/')),raw=True)
        form=aiohttp.FormData();form.add_field('file',data,filename=Path(c.get('filename') or c.get('body') or 'attachment').name[:180],content_type=c.get('info',{}).get('mimetype') or 'application/octet-stream')
        async with self.http.post(self.native_url+'/v1/accounts/'+q(row['account'])+'/uploads',headers={'Authorization':'Bearer '+self.key},data=form) as response:
            if response.status>=300:raise ApiError(response.status,'native_media_upload_failed')
            source=(await response.json())['id']
        self.write('UPDATE outbox SET source=? WHERE event=?',(source,row['event']))
        return source
    async def media_emit(self,a,*args):
        async with self.locks[a]:
            if not self.one('SELECT id FROM accounts WHERE id=? AND enabled=1',(a,)):return None
            return await self.emit(*args)
    async def publish_attachment(self,row,asset,content):
        # The owner is a real Matrix user. Never impersonate them or echo an
        # attachment they already sent through Matrix back into WeChat.
        if row.get('sender')==self.c['owner']:return None
        original=self.one('SELECT event FROM messages WHERE account=? AND native=?',(row['account'],row['message']))
        if original and asset.get('kind')=='video':
            saved=await self.matrix('/_matrix/client/v3/rooms/'+q(row['room'])+'/event/'+q(original['event']),user=row.get('sender'))
            caption=saved.get('content',{}).get('body')
            if isinstance(caption,str) and caption and not caption.startswith('[Сообщение WeChat:'):
                content={**content,'body':caption}
        if original:
            content={**content,'m.new_content':dict(content),'m.relates_to':{'rel_type':'m.replace','event_id':original['event']}}
        return await self.media_emit(row['account'],row['room'],'media-edit|'+row['account']+'|'+row['message']+'|'+asset['id'],content,row['sender'])
    def defer_attachment(self,row,reset_job=False):
        attempts=int(row.get('attempts') or 0)+1
        delay=min(300,5*(2**min(attempts-1,6)))
        self.write("UPDATE media_queue SET state='pending',attempts=?,next_retry=?,job=? WHERE account=? AND message=?",(attempts,time.time()+delay,None if reset_job else row.get('job'),row['account'],row['message']))
    async def media_worker(self):
        while True:
            for row in self.rows("SELECT m.* FROM media_queue m JOIN accounts a ON m.account=a.id WHERE m.state='pending' AND m.next_retry<=? AND a.enabled=1 ORDER BY m.next_retry LIMIT 10",(time.time(),)):
                a=row['account'];mid=row['message']
                try:
                    items=(await self.native('/accounts/'+q(a)+'/messages/'+q(mid)+'/media'))['items']
                    if not items:
                        self.defer_attachment(row);continue
                    # A Channels card's cover must not prevent fetching its video.
                    video=[x for x in items if x.get('kind')=='video']
                    if video:items=video
                    available=[x for x in items if x['state']=='available']
                    non_thumb=[x for x in available if x.get('variant')!='thumbnail']
                    need_transfer=(not non_thumb) and (bool(row['job']) or any(x.get('variant')!='thumbnail' and (x['state']=='pending' or x.get('retryable')) for x in items) or all(x.get('variant')=='thumbnail' for x in items))
                    if need_transfer:
                        if not row['job']:
                            job=await self.native('/accounts/'+q(a)+'/messages/'+q(mid)+'/media-transfers','POST',{'messageId':mid,'quality':'normal' if video else 'best'})
                            self.write('UPDATE media_queue SET job=? WHERE account=? AND message=?',(job['id'],a,mid))
                            self.defer_attachment({**row,'job':job['id']});continue
                        else:
                            job=await self.native('/accounts/'+q(a)+'/jobs/'+q(row['job']))
                            if job['state'] in ['failed','expired','unsupported','needs_review','cancelled']:
                                if job.get('retryable') or any(x.get('retryable') for x in items):
                                    self.defer_attachment(row,reset_job=True);continue
                                if not available:
                                    raise ApiError(410,job.get('reason') or job['state'])
                            elif job['state']=='available' and job.get('assetId'):
                                asset=await self.native('/accounts/'+q(a)+'/media/'+q(job['assetId']))
                                if asset['state']=='available':
                                    available=[asset];non_thumb=[asset]
                            else:
                                self.defer_attachment(row);continue
                    if non_thumb:available=non_thumb
                    elif not available:continue
                    candidates=available or items
                    order={'original':0,'normal':1,'standard':2,'thumbnail':3}
                    candidates=sorted(candidates,key=lambda x:order.get(x.get('variant'),2))[:1]
                    for asset in candidates:
                        if asset['state']!='available':
                            await self.media_emit(a,row['room'],'media-unavailable|'+a+'|'+mid+'|'+asset['id'],{'msgtype':'m.notice','body':'Вложение WeChat недоступно: '+str(asset.get('reason') or asset['state'])},row['sender']);continue
                        if asset.get('byteLength',0) and asset['byteLength']>MAX_MEDIA:raise ApiError(413,'Размер вложения превышает 50 МБ')
                        data=await self.native('/accounts/'+q(a)+'/media/'+q(asset['id'])+'/content',raw=True)
                        mime=asset.get('contentType') or 'application/octet-stream';name=asset.get('filename') or 'WeChat-'+mid
                        msgtype='m.image' if mime.startswith('image/') else 'm.video' if mime.startswith('video/') else 'm.audio' if mime.startswith('audio/') else 'm.file'
                        if name.startswith('WeChat-msg_') or name.startswith('msg_'):
                            name={'m.image':'Фотография','m.video':'Видео','m.audio':'Голосовое сообщение','m.file':'Файл'}[msgtype]+{'image/jpeg':'.jpg','image/png':'.png','audio/wav':'.wav','audio/ogg':'.ogg','video/mp4':'.mp4','application/pdf':'.pdf'}.get(mime,'')
                        result=await self.matrix('/_matrix/media/v3/upload?filename='+q(name),'POST',data,headers={'Content-Type':mime})
                        content={'msgtype':msgtype,'body':name,'filename':name,'url':result['content_uri'],'info':{'mimetype':mime,'size':len(data)}}
                        if asset.get('kind')=='voice':content['org.matrix.msc3245.voice']={}
                        await self.publish_attachment(row,asset,content)
                    self.write("UPDATE media_queue SET state='done' WHERE account=? AND message=?",(a,mid))
                except ApiError as e:
                    if e.status>=500 or e.status in (408,429):self.defer_attachment(row);continue
                    try:
                        await self.media_emit(a,row['room'],'media-error|'+a+'|'+mid,{'msgtype':'m.notice','body':'Вложение WeChat не загружено: '+e.code},row['sender'])
                        self.write("UPDATE media_queue SET state='unavailable' WHERE account=? AND message=?",(a,mid))
                    except Exception:log.warning('attachment notice pending')
                except Exception as e:
                    self.defer_attachment(row);log.warning('attachment synchronization: %s',type(e).__name__)
            await asyncio.sleep(3)
    async def transaction(self,r):
        token=r.headers.get('Authorization','').removeprefix('Bearer ') or r.query.get('access_token','')
        if not hmac.compare_digest(token,self.c['hs_token']): raise ApiError(401,'M_UNKNOWN_TOKEN')
        data=await r.json()
        self.write('INSERT OR IGNORE INTO transactions(id,payload) VALUES(?,?)',(r.match_info['txn'],json.dumps(data)))
        return {}
    async def drain(self):
        for row in self.rows('SELECT * FROM transactions WHERE done=0 LIMIT 30'):
            for e in json.loads(row['payload']).get('events',[]):
                if e.get('sender')!=self.c['owner'] or e.get('type')!='m.room.message': continue
                if (e.get('content',{}).get('m.relates_to') or {}).get('rel_type')=='m.replace':continue
                p=self.one('SELECT p.* FROM portals p JOIN accounts a ON p.account=a.id WHERE p.room=? AND a.enabled=1',(e['room_id'],))
                if not p: continue
                self.write('INSERT OR IGNORE INTO outbox(event,account,thread,room,payload) VALUES(?,?,?,?,?)',(e['event_id'],p['account'],p['thread'],p['room'],json.dumps(e['content'])))
            self.write('UPDATE transactions SET done=1,payload=? WHERE id=?',('{}',row['id']))
        for row in self.rows("SELECT * FROM outbox WHERE state='pending' LIMIT 20"):
            a=row['account']
            async with self.locks[a]:
                if not self.one('SELECT id FROM accounts WHERE id=? AND enabled=1',(a,)): continue
                c=json.loads(row['payload']); key='matrix-'+digest(row['event'])
                try:
                    if c.get('msgtype') in ['m.text','m.notice','m.emote']:body={'threadId':row['thread'],'text':c.get('body','')}
                    elif c.get('msgtype') in ['m.image','m.video','m.file','m.audio']:
                        source=await self.source(row,c)
                        body={'threadId':row['thread'],'kind':{'m.image':'image','m.video':'video','m.file':'file','m.audio':'file'}[c['msgtype']],'sourceId':source}
                    else:raise ApiError(400,'Неподдерживаемый тип сообщения Matrix')
                    result=await self.native('/accounts/'+q(a)+'/sends','POST',body,headers={'Idempotency-Key':key})
                    if result.get('localMessageId'): self.write('INSERT OR IGNORE INTO messages VALUES(?,?,?)',(a,str(result['localMessageId']),row['event']))
                    for _ in range(15):
                        if result.get('state') not in ['queued','submitting','running']: break
                        await asyncio.sleep(1); result=await self.native('/accounts/'+q(a)+'/sends/'+q(result['id']))
                    state=result.get('state','needs_review')
                    if state=='accepted':
                        details=result.get('result') if isinstance(result.get('result'),dict) else {}
                        local=result.get('localMessageId') or details.get('localMessageId')
                        if local: self.write('INSERT OR IGNORE INTO messages VALUES(?,?,?)',(a,str(local),row['event']))
                        self.write("UPDATE outbox SET state='accepted',result=?,payload='{}' WHERE event=?",(json.dumps(result),row['event']))
                        saved=self.one('SELECT source FROM outbox WHERE event=?',(row['event'],))
                        if saved.get('source'):
                            try:await self.native('/accounts/'+q(a)+'/uploads/'+q(saved['source']),'DELETE')
                            except ApiError:pass
                    elif state in ['failed','cancelled','needs_review','expired','unsupported']:
                        raise ApiError(409,'WeChat: '+str(result.get('errorCode') or state))
                    # Pending native intent is retried with exactly the same key.
                except ApiError as e:
                    if e.status>=500: continue
                    self.write("UPDATE outbox SET state='needs_review',result=? WHERE event=?",(e.code,row['event']))
                    await self.emit(row['room'],'send-error|'+row['event'],{'msgtype':'m.notice','body':'Не удалось подтвердить отправку в WeChat: '+e.code+'. Проверьте чат в WeChat перед повторной отправкой.'})
    async def worker(self):
        while True:
            try:
                await self.drain()
                for row in self.rows('SELECT id FROM accounts WHERE enabled=1'):
                    try:
                        if not self.one("SELECT event FROM outbox WHERE account=? AND state='pending'",(row['id'],)):
                            await self.process_events(row['id'])
                    except Exception as e:
                        self.write('UPDATE accounts SET error=? WHERE id=?',(e.code if isinstance(e,ApiError) else type(e).__name__,row['id']))
                        log.warning('event synchronization failed: %s',type(e).__name__)
            except Exception as e: log.warning('worker: %s',type(e).__name__)
            await asyncio.sleep(2)
    async def start(self,app):
        self.http=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=65,connect=8))
        await self.register('wechatbot','WeChat')
        # Adopt only explicitly authorized bootstrap profile; never claim every native profile.
        bootstrap=self.c.get('bootstrap_account')
        existing=self.one('SELECT * FROM accounts WHERE id=?',(bootstrap,)) if bootstrap else None
        if bootstrap and (not existing or not existing['space']):await self.bind(bootstrap)
        self.tasks=[asyncio.create_task(self.worker()),asyncio.create_task(self.media_worker())]
    async def stop(self,app):
        for task in self.tasks: task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        await self.http.close(); self.db.close()

@web.middleware
async def errors(request,handler):
    try:
        result=await handler(request)
        return result if isinstance(result,web.StreamResponse) else web.json_response(result,headers={'Cache-Control':'private, no-store'})
    except ApiError as e: return web.json_response({'error':e.code},status=e.status,headers={'Cache-Control':'no-store'})
    except (ValueError,KeyError): return web.json_response({'error':'invalid_request'},status=400)
    except (aiohttp.ClientError,asyncio.TimeoutError): return web.json_response({'error':'Сервис временно недоступен'},status=502)

def application(directory):
    b=Bridge(directory); app=web.Application(middlewares=[errors],client_max_size=2*1024*1024)
    app['bridge']=b
    app.router.add_route('*','/_matrix/provision/v3/{tail:.*}',b.provisioning)
    app.router.add_put('/_matrix/app/v1/transactions/{txn}',b.transaction)
    async def health(r): return {'ok':True}
    app.router.add_get('/health',health)
    app.on_startup.append(b.start); app.on_cleanup.append(b.stop)
    return app

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    web.run_app(application(os.getenv('WECHAT_DATA','/wechat-data')),host='0.0.0.0',port=int(os.getenv('BRIDGE_PORT','29391')),access_log=None)
