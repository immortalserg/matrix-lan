"""Persistent account outage notifications for the owner's MSB-CBMRS endpoint."""
import copy, datetime, hashlib, json, os, re, ssl, threading, time, uuid
from pathlib import Path
from account_diagnostics import diagnose
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, quote
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler

DEFAULTS = dict(enabled=False, endpoint='', site_url='__PUBLIC_ORIGIN__',
                api_key='', delay_seconds=120, repeat_seconds=1800,
                notify_recovery=True, notify_yellow=True, notify_red=True,
                stale_seconds=180, platforms=[], excluded_accounts=[], account_overrides={})
TZ = datetime.timezone(datetime.timedelta(hours=5))

def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.tmp')
    fd = os.open(temp, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf8') as output:
        json.dump(value, output, ensure_ascii=False)
        output.flush(); os.fsync(output.fileno())
    os.replace(temp, path)

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

def deliver(config, payload, ca_file):
    """A redirect or HTML login page is a failure, never a delivered notice."""
    context = ssl.create_default_context(cafile=str(ca_file) if ca_file.is_file() else None)
    opener = build_opener(HTTPSHandler(context=context), NoRedirect())
    req = Request(config['endpoint'], data=json.dumps(payload, ensure_ascii=False).encode(),
                  headers={'Authorization':'Bearer '+config['api_key'],
                           'X-MSB-Site':config['site_url'], 'Content-Type':'application/json'}, method='POST')
    try:
        with opener.open(req, timeout=18) as response:
            code = response.status
            if code not in (200, 201, 202): return False, code, 'API не подтвердил приём', None
            if 'application/json' not in response.headers.get('Content-Type',''):
                return False, code, 'API вернул страницу вместо JSON; проверьте доступ по ключу', None
            data = json.loads(response.read(65536))
            if not isinstance(data,dict) or data.get('ok') is False or data.get('success') is False or data.get('error'):
                return False, code, 'API сообщил об ошибке приёма', None
            request_id = data.get('request_id') or data.get('id')
            if not request_id:
                for key in ('request','item','data'):
                    if isinstance(data.get(key),dict):
                        request_id = data[key].get('id') or data[key].get('_id')
                        if request_id: break
            return True, code, 'Принято MSB-CBMRS', str(request_id)[:120] if request_id else None
    except HTTPError as exc:
        reason = ('API требует вход через браузер (/access_control/); Bearer-доступ не работает'
                  if 300 <= exc.code < 400 else
                  'API отклонил ключ или доступ к сайту' if exc.code in (401,403) else
                  'API отклонил формат уведомления' if exc.code in (400,422) else
                  'Ошибка HTTP '+str(exc.code))
        return False, exc.code, reason, None
    except ssl.SSLError:
        return False, 0, 'Не удалось проверить сертификат MSB-CBMRS', None
    except (URLError, TimeoutError, OSError, ValueError):
        return False, 0, 'MSB-CBMRS недоступен или вернул неверный ответ', None

class Notifier:
    def __init__(self, directory, snapshot, platforms, sender=None, now=time.time):
        self.directory = Path(directory); self.snapshot = snapshot; self.platforms = set(platforms)
        self.now = now; self.sender = sender or (lambda c,p: deliver(c,p,self.directory/'msb-cbmrs-ca.pem'))
        self.lock = threading.RLock(); self.tick_lock = threading.Lock(); self.stop = threading.Event()
        self.config = copy.deepcopy(DEFAULTS); self.storage_error = None
        self.state = {'accounts':{}, 'outbox':[], 'history':[], 'last_delivery':None}
        path = self.directory/'msb-cbmrs.json'
        if path.exists():
            try: self.config.update(json.loads(path.read_text(encoding='utf8')))
            except (ValueError,OSError,TypeError): self.storage_error='Не удалось прочитать настройки MSB-CBMRS; требуется восстановление файла'
        path = self.directory/'msb-cbmrs-state.json'
        if path.exists():
            # A damaged delivery ledger must not silently cause duplicate sends.
            try: self.state.update(json.loads(path.read_text(encoding='utf8')))
            except (ValueError,OSError,TypeError): self.storage_error='Не удалось прочитать очередь MSB-CBMRS; требуется восстановление файла'
        if self.storage_error:
            self.config['enabled']=False
            self.state['last_delivery']={'at':self.now(),'ok':False,'message':self.storage_error}

    def persist(self):
        if self.storage_error: raise ValueError(self.storage_error)
        atomic_json(self.directory/'msb-cbmrs-state.json', self.state)

    def settings(self):
        with self.lock:
            result = {k:copy.deepcopy(v) for k,v in self.config.items() if k != 'api_key'}
            result.update(api_key_set=bool(self.config['api_key']), queue_size=len(self.state['outbox']),
                          last_delivery=copy.deepcopy(self.state['last_delivery']),
                          history=copy.deepcopy(self.state['history'][-30:]),
                          active_incidents=sum(a.get('bad_since') is not None for a in self.state['accounts'].values()))
            return result

    def update(self, value):
        if self.storage_error: raise ValueError(self.storage_error)
        if not isinstance(value,dict): raise ValueError('Неверный формат настроек')
        with self.lock:
            c = copy.deepcopy(self.config)
            for key in DEFAULTS:
                if key in value and not (key=='api_key' and value[key]==''): c[key]=copy.deepcopy(value[key])
            origin = os.environ.get('MSB_CBMRS_ORIGIN', '')
            if not origin:
                raise ValueError('Настройте адрес MSB-CBMRS при установке Matrix LAN')
            if not isinstance(c['endpoint'],str) or not re.fullmatch(re.escape(origin)+r'/api/v1/site-requests/[a-fA-F0-9]{24}',c['endpoint']):
                raise ValueError('Укажите адрес API MSB-CBMRS на разрешённом сервере')
            if c['site_url'] != DEFAULTS['site_url']:
                raise ValueError('Адрес сайта должен совпадать с вашим Matrix')
            if not isinstance(c['api_key'],str) or not 16<=len(c['api_key'])<=4096 or any(ord(x)<33 or ord(x)>126 for x in c['api_key']):
                raise ValueError('Введите действующий API-ключ без пробелов')
            for key in ('enabled','notify_recovery','notify_yellow','notify_red'):
                if type(c[key]) is not bool: raise ValueError('Неверное значение переключателя')
            for key,low,high in [('delay_seconds',0,86400),('repeat_seconds',0,604800),('stale_seconds',60,86400)]:
                if type(c[key]) is not int or not low<=c[key]<=high: raise ValueError('Неверный интервал: '+key)
            if c['repeat_seconds'] and c['repeat_seconds']<60: raise ValueError('Повтор должен быть не чаще одного раза в минуту; 0 отключает повторы')
            for key in ('platforms','excluded_accounts'):
                if not isinstance(c[key],list) or len(c[key])>1000 or any(not isinstance(x,str) or len(x)>256 for x in c[key]):
                    raise ValueError('Неверный список аккаунтов или сервисов')
            if any(p not in self.platforms for p in c['platforms']): raise ValueError('Неизвестный сервис')
            if not isinstance(c['account_overrides'],dict) or len(c['account_overrides'])>1000: raise ValueError('Неверные подписи аккаунтов')
            clean = {}
            for key,item in c['account_overrides'].items():
                if not isinstance(key,str) or not isinstance(item,dict): raise ValueError('Неверная подпись аккаунта')
                name = str(item.get('name','')).strip(); phone = str(item.get('phone','')).strip()
                if len(name)>180 or phone and not re.fullmatch(r'\+?[0-9]{5,15}',phone): raise ValueError('Проверьте имя и телефон аккаунта')
                clean[key]={'name':name,'phone':phone}
            c['account_overrides']=clean
            reset = any(c[k]!=self.config[k] for k in ('enabled','endpoint','site_url'))
            self.config=c; atomic_json(self.directory/'msb-cbmrs.json', c)
            if reset: self.state['accounts']={}; self.state['outbox']=[]
            else:
                for job in self.state['outbox']: job['next_attempt']=0
            self.persist()
        return self.settings()

    def retire_account(self,key):
        # Wait for an already-started send, then remove every future job for
        # this connection. Deletion is not a recovery notification.
        with self.tick_lock,self.lock:
            retired=set(self.state.get('retired_accounts',[]));retired.add(key)
            self.state['retired_accounts']=sorted(retired)
            self.state['accounts'].pop(key,None)
            self.state['outbox']=[j for j in self.state['outbox'] if j['key']!=key]
            self.state['history']=[h for h in self.state['history'] if h.get('account_key')!=key]
            self.config['excluded_accounts']=[k for k in self.config['excluded_accounts'] if k!=key]
            self.config['account_overrides'].pop(key,None)
            atomic_json(self.directory/'msb-cbmrs.json',self.config);self.persist()

    def restore_account(self,key):
        with self.lock:
            self.state['retired_accounts']=[k for k in self.state.get('retired_accounts',[]) if k!=key]
            self.persist()

    def selected(self, row):
        if row.get('setup_pending'): return False
        c=self.config
        return row['key'] not in c['excluded_accounts'] and (not c['platforms'] or row['platform'] in c['platforms'])

    def payload(self, row, kind, external_id, since, number=1):
        c=self.config; override=c['account_overrides'].get(row['key'],{})
        name=override.get('name') or row.get('profile_name') or row.get('name') or row['login_id']
        phone=override.get('phone') or row.get('phone') or ''
        if not re.fullmatch(r'\+?[0-9]{5,15}',phone): phone=''
        network = {'whatsapp':'WhatsApp','telegram':'Telegram','imo':'IMO','twitter':'X / Twitter','tiktok':'TikTok','instagram':'Instagram','facebook':'Facebook','email':'Gmail'}.get(row['platform'],row['platform'])
        detail=diagnose(row,c['site_url'])
        title = 'СВЯЗЬ ВОССТАНОВЛЕНА' if kind=='recovery' else detail['title'].upper()
        action = 'Повторное подключение не требуется.' if kind=='recovery' else 'Откройте Matrix, проверьте аккаунт и при необходимости выполните повторный вход.'
        at=datetime.datetime.fromtimestamp(self.now(),TZ).strftime('%d.%m.%Y %H:%M:%S (UTC+5)')
        repair='\n'.join(str(i+1)+'. '+step for i,step in enumerate(detail['steps']))
        text='\n'.join([f'MATRIX — {title}',f'Сервис: {network}',f'Аккаунт: {name}',f'Телефон: {phone or "не предоставлен сервисом"}',
                        f'Логин: {row.get("username") or row["login_id"]}',f'ID аккаунта: {row["login_id"]}',
                        f'Состояние: {row.get("state","UNKNOWN")}',f'Проблема: {detail["title"]}',f'Что известно: {detail["reason"]}',f'Код сервиса: {row.get("provider_error") or "не указан"}',
                        f'Время: {at}',f'Длительность сбоя: {max(0,int(self.now()-since))} сек.',f'Уведомление: {number}', 'Как восстановить:', repair, detail['action_label']+': '+detail['action_url'],
                        'Matrix: '+c['site_url']+('/#/room/'+quote(row['space_room'],safe='') if row.get('space_room') else '/')])
        return dict(external_id=external_id, site_url=c['site_url'], name=f'Matrix · {network} · {name}'[:200],phone=phone,text=text[:6000])

    def enqueue(self, key, kind, payload, incident=None):
        self.state['outbox'].append(dict(id=payload['external_id'],key=key,kind=kind,incident=incident,payload=payload,attempts=0,next_attempt=0))

    def test(self):
        if self.storage_error: raise ValueError(self.storage_error)
        with self.lock:
            if not self.config['api_key'] or not self.config['endpoint']: raise ValueError('Сначала сохраните настройки API')
            pending=next((j for j in self.state['outbox'] if j['kind']=='test'),None)
            if pending:return {'queued':True,'external_id':pending['id'],'already_queued':True}
            external='matrix-test-'+uuid.uuid4().hex
            payload=dict(external_id=external,site_url=self.config['site_url'],name='Matrix · проверка MSB-CBMRS',phone='',
                         text='ТЕСТ MSB-CBMRS: проверка уведомлений о потере связи с аккаунтами Matrix. Это сообщение настройки интеграции.')
            self.enqueue('_test','test',payload);self.persist()
            return {'queued':True,'external_id':external}

    def reconcile(self, snapshot):
        now=self.now(); rows={a['key']:copy.deepcopy(a) for a in snapshot.get('accounts',[]) if a.get('key')}
        accounts=self.state['accounts']
        retired=set(self.state.get('retired_accounts',[]))
        rows={k:v for k,v in rows.items() if k not in retired}
        for key,a in list(accounts.items()):
            if key in retired:
                accounts.pop(key,None);continue
            if key not in rows and snapshot.get('platforms',{}).get(a['row']['platform'],{}).get('reachable') and now-snapshot.get('checked_at',now)<=self.config['stale_seconds']:
                accounts.pop(key,None)
                self.state['outbox']=[j for j in self.state['outbox'] if j['key']!=key]
                continue
            if key not in rows:
                rows[key]=copy.deepcopy(a['row']);rows[key].update(state='MISSING',color='yellow',status_text='Аккаунт исчез из списка подключений',checked_at=now)
        for key,row in rows.items():
            if not self.selected(row):
                self.state['outbox']=[j for j in self.state['outbox'] if j['key']!=key];accounts.pop(key,None);continue
            if now-row.get('checked_at',0)>self.config['stale_seconds']:
                row.update(state='STALE',color='yellow',status_text='Проверка соединения устарела')
            a=accounts.setdefault(key,{'row':row,'bad_since':None,'sent':False,'last_sent':0,'sequence':0})
            a['row']=row
            good=row.get('color')=='green' and row.get('state')=='CONNECTED'
            if good:
                self.state['outbox']=[j for j in self.state['outbox'] if not (j['key']==key and j['kind']=='outage')]
                if a['bad_since'] is not None and a['sent'] and self.config['notify_recovery']:
                    external='matrix-'+a['incident']+'-recovery'
                    self.enqueue(key,'recovery',self.payload(row,'recovery',external,a['bad_since']),a['incident'])
                a.update(bad_since=None,sent=False,last_sent=0,sequence=0)
                continue
            if not (self.config['notify_red'] if row.get('color')=='red' else self.config['notify_yellow']):
                self.state['outbox']=[j for j in self.state['outbox'] if j['key']!=key]
                a.update(bad_since=None,sent=False,last_sent=0,sequence=0);continue
            if a['bad_since'] is None:
                a.update(bad_since=now,incident=uuid.uuid4().hex,sent=False,last_sent=0,sequence=0)
                self.state['outbox']=[j for j in self.state['outbox'] if not (j['key']==key and j['kind']=='recovery')]
            pending=any(j['key']==key and j['kind']=='outage' for j in self.state['outbox'])
            repeat=self.config['repeat_seconds']
            if now-a['bad_since']>=self.config['delay_seconds'] and not pending and (not a['sent'] or repeat and now-a['last_sent']>=repeat):
                a['sequence']+=1;external='matrix-'+a['incident']+'-'+str(a['sequence'])
                self.enqueue(key,'outage',self.payload(row,'outage',external,a['bad_since'],a['sequence']),a['incident'])

    def tick(self):
        if self.storage_error: return
        if not self.tick_lock.acquire(False): return
        try:
            snapshot=self.snapshot()
            with self.lock:
                if self.config['enabled']: self.reconcile(snapshot)
                self.persist()
                job=next((j for j in self.state['outbox'] if j['next_attempt']<=self.now() and (self.config['enabled'] or j['kind']=='test')),None)
                if not job: return
                job=copy.deepcopy(job);config=copy.deepcopy(self.config)
            try: ok,code,message,request_id=self.sender(config,job['payload'])
            except Exception: ok,code,message,request_id=False,0,'Ошибка соединения с MSB-CBMRS',None
            with self.lock:
                current=next((j for j in self.state['outbox'] if j['id']==job['id']),None)
                if current is None: return
                event=dict(at=self.now(),account_key=job['key'],kind=job['kind'],ok=ok,http_status=code,
                           message=message,external_id=job['id'],request_id=request_id)
                self.state['last_delivery']=event;self.state['history'].append(event);self.state['history']=self.state['history'][-100:]
                if ok or job['kind']=='test':
                    self.state['outbox'].remove(current)
                    a=self.state['accounts'].get(job['key'])
                    if ok and job['kind']=='outage' and a and a.get('incident')==job['incident']:a.update(sent=True,last_sent=self.now())
                else:
                    current['attempts']+=1
                    delay=300 if 300<=code<400 or code in (401,403) else min(1800,30*2**min(current['attempts']-1,6))
                    current['next_attempt']=self.now()+delay
                self.persist()
        finally:self.tick_lock.release()

    def worker(self):
        while not self.stop.is_set():
            try:self.tick()
            except Exception:
                # Do not log tokens, response bodies or outgoing account data.
                with self.lock:self.state['last_delivery']={'at':self.now(),'ok':False,'message':'Ошибка локальной очереди уведомлений; проверьте сервис Matrix'}
            self.stop.wait(5)
