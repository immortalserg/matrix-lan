"""Owner-scoped removal: native session, account spaces and alert bookkeeping."""
import copy,json,threading,time
from urllib.parse import urlencode,quote
from msb_cbmrs import atomic_json

class AccountLifecycle:
    def __init__(self,monitor,directory):
        self.m=monitor;self.path=directory/'account-deletions.json';self.lock=threading.RLock();self.entries={};self.error=None
        if self.path.exists():
            try:self.entries=json.loads(self.path.read_text(encoding='utf8'))
            except (ValueError,OSError):self.error='Не удалось прочитать журнал удаления аккаунтов'
    def persist(self):atomic_json(self.path,self.entries)
    def notifier(self):return getattr(self.m,'notifier',None)
    def filter_rows(self,platform,rows):
        with self.lock:
            for row in rows:
                entry=self.entries.get(row['key'])
                if not entry:continue
                if entry['status']=='removed':
                    # Native deletion was confirmed. A subsequent new native login
                    # with the same ID is a fresh connection, not a permanent ban.
                    del self.entries[row['key']];self.persist()
                    if self.notifier():self.notifier().restore_account(row['key'])
                elif entry['status']=='pending':row.update(deletion_pending=True,status_text='Удаление подключения…')
                else:row.update(deletion_error=entry.get('error','Удаление не завершено'))
        return rows
    def spaces(self,row):
        errors=[]
        for sid in set(filter(None,[row.get('space_room'),row.get('native_space_room')])):
            try:
                try:events=self.m.matrix('/_matrix/client/v3/rooms/'+quote(sid,safe='')+'/state')
                except RuntimeError:
                    # A previously-left room can still be forgotten; only use
                    # server-recorded space IDs, never room IDs from the client.
                    events=None
                if events is not None:
                    if not any(e['type']=='m.room.create' and e.get('content',{}).get('type')=='m.space' for e in events):continue
                    marker=any(e['type']=='com.matrix.account' and e.get('content',{}).get('platform')==row['platform'] and e.get('content',{}).get('login_id')==row['login_id'] for e in events)
                    native=sid==row.get('native_space_room') or not row.get('managed_space') and sid==row.get('space_room')
                    if not (marker or native):continue
                try:self.m.matrix('/_matrix/client/v3/rooms/'+quote(sid,safe='')+'/leave','POST',{})
                except RuntimeError:pass
                self.m.matrix('/_matrix/client/v3/rooms/'+quote(sid,safe='')+'/forget','POST',{})
            except Exception:errors.append(sid)
        return errors
    def complete(self,row):
        if self.notifier():self.notifier().retire_account(row['key'])
        with self.lock:
            self.entries[row['key']]={'status':'removed','at':time.time(),'row':copy.deepcopy(row)};self.persist()
        cleanup=self.spaces(row)
        with self.m.lock:
            self.m.snapshot['accounts']=[a for a in self.m.snapshot['accounts'] if a['key']!=row['key']]
            atomic_json(self.path.parent/'snapshot.json',self.m.snapshot)
        return {'removed':True,'key':row['key'],'history_preserved':True,'space_cleanup_pending':bool(cleanup)}
    def delete(self,payload):
        if self.error:raise ValueError(self.error)
        key=payload.get('key');confirmed=payload.get('confirm_key')
        if not isinstance(key,str) or len(key)>256 or ':' not in key or confirmed!=key:raise ValueError('Подтвердите конкретное подключение')
        platform,login=key.split(':',1)
        if platform not in self.m.c['bridges'] or not login or login in ['all','*']:raise ValueError('Неверное подключение')
        from monitor import call
        bridge=self.m.c['bridges'][platform];query='?'+urlencode({'user_id':self.m.c['owner']})
        with self.m.refresh_lock:
            with self.m.lock:row=next((copy.deepcopy(a) for a in self.m.snapshot['accounts'] if a['key']==key),None)
            with self.lock:previous=self.entries.get(key)
            if not row and previous and previous['status']=='removed':return self.complete(previous['row'])
            if not row:raise ValueError('Подключение не найдено. Обновите список.')
            status,info=call(bridge['url']+'/_matrix/provision/v3/whoami'+query,self.m.c['token'])
            if status!=200:raise RuntimeError('Мост недоступен; удаление не подтверждено')
            native=next((x for x in info.get('logins',[]) if str(x.get('id'))==login),None)
            if native and native.get('space_room') and native['space_room']!=row.get('space_room'):row['native_space_room']=native['space_room']
            with self.lock:self.entries[key]={'status':'pending','at':time.time(),'row':row};self.persist()
            if self.notifier():self.notifier().retire_account(key)
            try:
                if native:
                    status,_=call(bridge['url']+'/_matrix/provision/v3/logout/'+quote(login,safe='')+query,self.m.c['token'],'POST',{})
                    if status not in (200,404):raise RuntimeError('Мост не выполнил удаление подключения')
                status,after=call(bridge['url']+'/_matrix/provision/v3/whoami'+query,self.m.c['token'])
                if status!=200 or any(str(x.get('id'))==login for x in after.get('logins',[])):raise RuntimeError('Мост не подтвердил удаление. Повторите проверку.')
                return self.complete(row)
            except Exception:
                with self.lock:self.entries[key].update(status='failed',error='Удаление не подтверждено мостом; повторите попытку');self.persist()
                raise
