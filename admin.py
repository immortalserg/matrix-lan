#!/usr/bin/env python3
"""Loopback-only Matrix-LAN bridge administration. Docker socket access is privileged."""
import base64
import hmac
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / 'runtime'
AUTH = RUNTIME / 'admin.private.json'
LOCK = threading.Lock()
WA_CONFIG = RUNTIME / 'state' / 'whatsapp' / 'config.yaml'

def whatsapp_settings():
    content = WA_CONFIG.read_text()
    def value(pattern):
        match = re.search(pattern, content, re.M)
        if not match:
            raise ValueError('WhatsApp configuration field missing')
        return match.group(1)
    backfill = content.split('\\nbackfill:', 1)
    if len(backfill) != 2:
        raise ValueError('WhatsApp backfill section missing')
    return {
        'request_full_sync': value(r'^\\s+request_full_sync:\\s*(true|false)\\s*
def load_auth():
    if not AUTH.exists():
        raise RuntimeError('Admin credentials missing; run admin-init first.')
    return json.loads(AUTH.read_text())

def initialize():
    if AUTH.exists():
        print('Admin credentials already exist.')
        return
    if not RUNTIME.is_dir():
        raise RuntimeError('Prepare Matrix-LAN first.')
    password = secrets.token_urlsafe(30)
    fd = os.open(AUTH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump({'password': password}, f)
    print('Admin credentials created. Use admin-password to retrieve them.')

def password():
    print(load_auth()['password'])

PAGE = """<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Matrix-LAN — Мессенджеры</title>
<style>body{font:16px system-ui;background:#101820;color:#f5f6fa;max-width:680px;margin:40px auto;padding:0 18px}
article{background:#202d39;border-radius:12px;padding:20px;margin:14px 0}
button,input{padding:10px;border-radius:7px;border:1px solid #718096;font:inherit}
button{cursor:pointer;background:#54d4a1;color:#082018;font-weight:700}
input{display:block;width:95%;margin:10px 0;background:#fff;color:#111}
small{color:#b7c7d4}#result{white-space:pre-wrap}</style>
<h1>Matrix-LAN</h1><p>Управление мостами мессенджеров</p>
<article><h2>WhatsApp</h2><p id="wa">Проверка...</p><button onclick="install('whatsapp')">Установить мост</button>
<small><p>После запуска привяжите телефон через чат с ботом WhatsApp в Element.</p></small></article>
<article><h2>Настройки WhatsApp</h2>
<label><input id="full" type="checkbox" style="width:auto;display:inline"> Запрашивать полную историю при новой привязке</label>
<label><input id="backfill" type="checkbox" style="width:auto;display:inline"> Импортировать старые сообщения</label>
<label>Максимум сообщений на чат<select id="limit"><option value="50">50</option><option value="100">100</option><option value="500">500</option><option value="-1">Без ограничения</option></select></label>
<p><button onclick="saveWhatsApp()">Сохранить и перезапустить WhatsApp</button></p>
<small>Сохранение не удаляет авторизацию. Повторный запрос полной истории может потребовать новой привязки телефона.</small></article>
<article><h2>Telegram</h2><p id="tg">Проверка...</p>
<label>API ID<input id="apiid" type="number" min="1" autocomplete="off"></label>
<label>API Hash<input id="apihash" type="password" autocomplete="off"></label>
<button onclick="install('telegram')">Установить мост</button>
<small><p>Получите API ID и API Hash на my.telegram.org/apps.</p></small></article>
<article><h3>Результат</h3><div id="result">Готово к работе.</div></article>
<script>
async function request(path, data) {
 const options=data?{method:'POST',headers:{'Content-Type':'application/json','X-Matrix-LAN-Action':'1'},body:JSON.stringify(data)}:{};
 const response=await fetch(path,options);const result=await response.json();
 if(!response.ok)throw Error(result.error||'HTTP '+response.status);return result;
}
async function refresh(){try{const d=await request('/api/status');
 document.getElementById('wa').textContent=d.whatsapp?'Установлен':'Не установлен';
 document.getElementById('tg').textContent=d.telegram?'Установлен':'Не установлен';
 }catch(e){document.getElementById('result').textContent=e.message;}}
async function loadWhatsApp(){try{const s=await request('/api/whatsapp/settings');
 document.getElementById('full').checked=s.request_full_sync;
 document.getElementById('backfill').checked=s.backfill_enabled;
 document.getElementById('limit').value=String(s.max_initial_messages);
 }catch(e){document.getElementById('result').textContent=e.message;}}
async function saveWhatsApp(){try{
 const d=await request('/api/whatsapp/settings',{request_full_sync:document.getElementById('full').checked,
 backfill_enabled:document.getElementById('backfill').checked,max_initial_messages:Number(document.getElementById('limit').value)});
 document.getElementById('result').textContent=d.message;
 }catch(e){document.getElementById('result').textContent=e.message;}}
async function install(name){
 const body={bridge:name};
 if(name==='telegram'){body.api_id=Number(document.getElementById('apiid').value);
 body.api_hash=document.getElementById('apihash').value;}
 document.getElementById('result').textContent='Установка...';
 try{const d=await request('/api/install',body);document.getElementById('result').textContent=d.message;
 document.getElementById('apihash').value='';await refresh();}
 catch(e){document.getElementById('result').textContent=e.message;}
}
refresh();loadWhatsApp();
</script></html>"""

class Handler(BaseHTTPRequestHandler):
    def send(self, status, data, kind='application/json; charset=utf-8'):
        body = json.dumps(data).encode() if kind.startswith('application/json') else data.encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        header = self.headers.get('Authorization','')
        try:
            scheme, token = header.split(' ',1)
            decoded = base64.b64decode(token,validate=True).decode()
            user, passwd = decoded.split(':',1)
            return scheme.lower()=='basic' and user=='admin' and hmac.compare_digest(passwd,load_auth()['password'])
        except (ValueError, UnicodeError, OSError):
            return False

    def check(self):
        if self.headers.get('Host','').split(':')[0] not in ('localhost','127.0.0.1'):
            self.send(403,{'error':'Localhost only'});return False
        if not self.authorized():
            self.send_response(401)
            self.send_header('WWW-Authenticate','Basic realm="Matrix-LAN Admin"')
            self.send_header('Content-Length','0')
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            return False
        return True

    def do_GET(self):
        if not self.check():return
        path=urlsplit(self.path).path
        if path=='/':
            self.send(200,PAGE,'text/html; charset=utf-8')
        elif path=='/api/whatsapp/settings':
            try:self.send(200,whatsapp_settings())
            except (OSError,ValueError,AttributeError):self.send(404,{'error':'WhatsApp settings unavailable'})
        elif path=='/api/status':
            try:
                data=json.loads((RUNTIME/'bootstrap.private.json').read_text())
                services=data['services']
                self.send(200,{'whatsapp':'mautrix-whatsapp' in services,'telegram':'mautrix-telegram' in services})
            except (OSError,KeyError,ValueError):
                self.send(500,{'error':'Cannot read installation state'})
        else:self.send(404,{'error':'Not found'})

    def do_POST(self):
        if not self.check():return
        path=urlsplit(self.path).path
        if path not in ('/api/install','/api/whatsapp/settings'):
            self.send(404,{'error':'Not found'});return
        origin=self.headers.get('Origin','')
        if origin not in ('http://localhost:18082','http://127.0.0.1:18082') or self.headers.get('X-Matrix-LAN-Action')!='1':
            self.send(403,{'error':'Invalid request origin'});return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<=0 or size>2048:raise ValueError('Invalid request size')
            data=json.loads(self.rfile.read(size))
            if path=='/api/whatsapp/settings':
                with LOCK:
                    message=save_whatsapp_settings(data)
                self.send(200,{'message':message})
                return
            bridge=data.get('bridge')
            if bridge not in ('whatsapp','telegram'):raise ValueError('Unknown bridge')
            args=['python3',str(ROOT/'install.py'),'enable-bridge',bridge]
            with LOCK:
                if bridge=='telegram':
                    api_id=data.get('api_id')
                    api_hash=data.get('api_hash')
                    if type(api_id)!=int or api_id<=0 or not isinstance(api_hash,str) or len(api_hash)!=32 or any(c not in '0123456789abcdefABCDEF' for c in api_hash):
                        raise ValueError('Enter a valid Telegram API ID and 32-character API Hash')
                    fd, path=tempfile.mkstemp(prefix='telegram-',suffix='.json',dir=RUNTIME)
                    try:
                        with os.fdopen(fd,'w') as f:json.dump({'api_id':api_id,'api_hash':api_hash},f)
                        os.chmod(path,0o600)
                        proc=subprocess.run(args+['--telegram-config',path],capture_output=True,text=True,timeout=180)
                    finally:os.unlink(path)
                else:
                    proc=subprocess.run(args,capture_output=True,text=True,timeout=180)
            if proc.returncode:
                # Do not expose raw subprocess logs: they may contain secrets.
                self.send(500,{'error':'Bridge setup failed. Check installer logs on the host.'})
            else:self.send(200,{'message':bridge.capitalize()+' bridge installed. Connect your account in Element.'})
        except (ValueError,TypeError,json.JSONDecodeError) as exc:self.send(400,{'error':str(exc)})
        except subprocess.TimeoutExpired:self.send(504,{'error':'Bridge installation timed out'})
        except Exception:self.send(500,{'error':'Internal installation error'})

    def log_message(self, fmt, *args):
        # Avoid logging request headers or credentials.
        print('%s %s' % (self.address_string(),fmt % args),flush=True)

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='admin-init':initialize()
    elif len(sys.argv)>1 and sys.argv[1]=='admin-password':password()
    else:
        print('Matrix-LAN admin listening on 0.0.0.0:18082 (host mapping must be loopback only)',flush=True)
        ThreadingHTTPServer(('0.0.0.0',18082),Handler).serve_forever()
) == 'true',
        'backfill_enabled': bool(re.search(r'^    enabled:\\s*true\\s*
def load_auth():
    if not AUTH.exists():
        raise RuntimeError('Admin credentials missing; run admin-init first.')
    return json.loads(AUTH.read_text())

def initialize():
    if AUTH.exists():
        print('Admin credentials already exist.')
        return
    if not RUNTIME.is_dir():
        raise RuntimeError('Prepare Matrix-LAN first.')
    password = secrets.token_urlsafe(30)
    fd = os.open(AUTH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump({'password': password}, f)
    print('Admin credentials created. Use admin-password to retrieve them.')

def password():
    print(load_auth()['password'])

PAGE = """<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Matrix-LAN — Мессенджеры</title>
<style>body{font:16px system-ui;background:#101820;color:#f5f6fa;max-width:680px;margin:40px auto;padding:0 18px}
article{background:#202d39;border-radius:12px;padding:20px;margin:14px 0}
button,input{padding:10px;border-radius:7px;border:1px solid #718096;font:inherit}
button{cursor:pointer;background:#54d4a1;color:#082018;font-weight:700}
input{display:block;width:95%;margin:10px 0;background:#fff;color:#111}
small{color:#b7c7d4}#result{white-space:pre-wrap}</style>
<h1>Matrix-LAN</h1><p>Управление мостами мессенджеров</p>
<article><h2>WhatsApp</h2><p id="wa">Проверка...</p><button onclick="install('whatsapp')">Установить мост</button>
<small><p>После запуска привяжите телефон через чат с ботом WhatsApp в Element.</p></small></article>
<article><h2>Telegram</h2><p id="tg">Проверка...</p>
<label>API ID<input id="apiid" type="number" min="1" autocomplete="off"></label>
<label>API Hash<input id="apihash" type="password" autocomplete="off"></label>
<button onclick="install('telegram')">Установить мост</button>
<small><p>Получите API ID и API Hash на my.telegram.org/apps.</p></small></article>
<article><h3>Результат</h3><div id="result">Готово к работе.</div></article>
<script>
async function request(path, data) {
 const options=data?{method:'POST',headers:{'Content-Type':'application/json','X-Matrix-LAN-Action':'1'},body:JSON.stringify(data)}:{};
 const response=await fetch(path,options);const result=await response.json();
 if(!response.ok)throw Error(result.error||'HTTP '+response.status);return result;
}
async function refresh(){try{const d=await request('/api/status');
 document.getElementById('wa').textContent=d.whatsapp?'Установлен':'Не установлен';
 document.getElementById('tg').textContent=d.telegram?'Установлен':'Не установлен';
 }catch(e){document.getElementById('result').textContent=e.message;}}
async function install(name){
 const body={bridge:name};
 if(name==='telegram'){body.api_id=Number(document.getElementById('apiid').value);
 body.api_hash=document.getElementById('apihash').value;}
 document.getElementById('result').textContent='Установка...';
 try{const d=await request('/api/install',body);document.getElementById('result').textContent=d.message;
 document.getElementById('apihash').value='';await refresh();}
 catch(e){document.getElementById('result').textContent=e.message;}
}
refresh();
</script></html>"""

class Handler(BaseHTTPRequestHandler):
    def send(self, status, data, kind='application/json; charset=utf-8'):
        body = json.dumps(data).encode() if kind.startswith('application/json') else data.encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        header = self.headers.get('Authorization','')
        try:
            scheme, token = header.split(' ',1)
            decoded = base64.b64decode(token,validate=True).decode()
            user, passwd = decoded.split(':',1)
            return scheme.lower()=='basic' and user=='admin' and hmac.compare_digest(passwd,load_auth()['password'])
        except (ValueError, UnicodeError, OSError):
            return False

    def check(self):
        if self.headers.get('Host','').split(':')[0] not in ('localhost','127.0.0.1'):
            self.send(403,{'error':'Localhost only'});return False
        if not self.authorized():
            self.send_response(401)
            self.send_header('WWW-Authenticate','Basic realm="Matrix-LAN Admin"')
            self.send_header('Content-Length','0')
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            return False
        return True

    def do_GET(self):
        if not self.check():return
        path=urlsplit(self.path).path
        if path=='/':
            self.send(200,PAGE,'text/html; charset=utf-8')
        elif path=='/api/status':
            try:
                data=json.loads((RUNTIME/'bootstrap.private.json').read_text())
                services=data['services']
                self.send(200,{'whatsapp':'mautrix-whatsapp' in services,'telegram':'mautrix-telegram' in services})
            except (OSError,KeyError,ValueError):
                self.send(500,{'error':'Cannot read installation state'})
        else:self.send(404,{'error':'Not found'})

    def do_POST(self):
        if not self.check():return
        if urlsplit(self.path).path!='/api/install':
            self.send(404,{'error':'Not found'});return
        origin=self.headers.get('Origin','')
        if origin not in ('http://localhost:18082','http://127.0.0.1:18082') or self.headers.get('X-Matrix-LAN-Action')!='1':
            self.send(403,{'error':'Invalid request origin'});return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<=0 or size>2048:raise ValueError('Invalid request size')
            data=json.loads(self.rfile.read(size))
            bridge=data.get('bridge')
            if bridge not in ('whatsapp','telegram'):raise ValueError('Unknown bridge')
            args=['python3',str(ROOT/'install.py'),'enable-bridge',bridge]
            with LOCK:
                if bridge=='telegram':
                    api_id=data.get('api_id')
                    api_hash=data.get('api_hash')
                    if type(api_id)!=int or api_id<=0 or not isinstance(api_hash,str) or len(api_hash)!=32 or any(c not in '0123456789abcdefABCDEF' for c in api_hash):
                        raise ValueError('Enter a valid Telegram API ID and 32-character API Hash')
                    fd, path=tempfile.mkstemp(prefix='telegram-',suffix='.json',dir=RUNTIME)
                    try:
                        with os.fdopen(fd,'w') as f:json.dump({'api_id':api_id,'api_hash':api_hash},f)
                        os.chmod(path,0o600)
                        proc=subprocess.run(args+['--telegram-config',path],capture_output=True,text=True,timeout=180)
                    finally:os.unlink(path)
                else:
                    proc=subprocess.run(args,capture_output=True,text=True,timeout=180)
            if proc.returncode:
                # Do not expose raw subprocess logs: they may contain secrets.
                self.send(500,{'error':'Bridge setup failed. Check installer logs on the host.'})
            else:self.send(200,{'message':bridge.capitalize()+' bridge installed. Connect your account in Element.'})
        except (ValueError,TypeError,json.JSONDecodeError) as exc:self.send(400,{'error':str(exc)})
        except subprocess.TimeoutExpired:self.send(504,{'error':'Bridge installation timed out'})
        except Exception:self.send(500,{'error':'Internal installation error'})

    def log_message(self, fmt, *args):
        # Avoid logging request headers or credentials.
        print('%s %s' % (self.address_string(),fmt % args),flush=True)

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='admin-init':initialize()
    elif len(sys.argv)>1 and sys.argv[1]=='admin-password':password()
    else:
        print('Matrix-LAN admin listening on 0.0.0.0:18082 (host mapping must be loopback only)',flush=True)
        ThreadingHTTPServer(('0.0.0.0',18082),Handler).serve_forever()
, backfill[1], re.M)),
        'max_initial_messages': int(re.search(r'^    max_initial_messages:\\s*(-?\\d+)\\s*
def load_auth():
    if not AUTH.exists():
        raise RuntimeError('Admin credentials missing; run admin-init first.')
    return json.loads(AUTH.read_text())

def initialize():
    if AUTH.exists():
        print('Admin credentials already exist.')
        return
    if not RUNTIME.is_dir():
        raise RuntimeError('Prepare Matrix-LAN first.')
    password = secrets.token_urlsafe(30)
    fd = os.open(AUTH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump({'password': password}, f)
    print('Admin credentials created. Use admin-password to retrieve them.')

def password():
    print(load_auth()['password'])

PAGE = """<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Matrix-LAN — Мессенджеры</title>
<style>body{font:16px system-ui;background:#101820;color:#f5f6fa;max-width:680px;margin:40px auto;padding:0 18px}
article{background:#202d39;border-radius:12px;padding:20px;margin:14px 0}
button,input{padding:10px;border-radius:7px;border:1px solid #718096;font:inherit}
button{cursor:pointer;background:#54d4a1;color:#082018;font-weight:700}
input{display:block;width:95%;margin:10px 0;background:#fff;color:#111}
small{color:#b7c7d4}#result{white-space:pre-wrap}</style>
<h1>Matrix-LAN</h1><p>Управление мостами мессенджеров</p>
<article><h2>WhatsApp</h2><p id="wa">Проверка...</p><button onclick="install('whatsapp')">Установить мост</button>
<small><p>После запуска привяжите телефон через чат с ботом WhatsApp в Element.</p></small></article>
<article><h2>Telegram</h2><p id="tg">Проверка...</p>
<label>API ID<input id="apiid" type="number" min="1" autocomplete="off"></label>
<label>API Hash<input id="apihash" type="password" autocomplete="off"></label>
<button onclick="install('telegram')">Установить мост</button>
<small><p>Получите API ID и API Hash на my.telegram.org/apps.</p></small></article>
<article><h3>Результат</h3><div id="result">Готово к работе.</div></article>
<script>
async function request(path, data) {
 const options=data?{method:'POST',headers:{'Content-Type':'application/json','X-Matrix-LAN-Action':'1'},body:JSON.stringify(data)}:{};
 const response=await fetch(path,options);const result=await response.json();
 if(!response.ok)throw Error(result.error||'HTTP '+response.status);return result;
}
async function refresh(){try{const d=await request('/api/status');
 document.getElementById('wa').textContent=d.whatsapp?'Установлен':'Не установлен';
 document.getElementById('tg').textContent=d.telegram?'Установлен':'Не установлен';
 }catch(e){document.getElementById('result').textContent=e.message;}}
async function install(name){
 const body={bridge:name};
 if(name==='telegram'){body.api_id=Number(document.getElementById('apiid').value);
 body.api_hash=document.getElementById('apihash').value;}
 document.getElementById('result').textContent='Установка...';
 try{const d=await request('/api/install',body);document.getElementById('result').textContent=d.message;
 document.getElementById('apihash').value='';await refresh();}
 catch(e){document.getElementById('result').textContent=e.message;}
}
refresh();
</script></html>"""

class Handler(BaseHTTPRequestHandler):
    def send(self, status, data, kind='application/json; charset=utf-8'):
        body = json.dumps(data).encode() if kind.startswith('application/json') else data.encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        header = self.headers.get('Authorization','')
        try:
            scheme, token = header.split(' ',1)
            decoded = base64.b64decode(token,validate=True).decode()
            user, passwd = decoded.split(':',1)
            return scheme.lower()=='basic' and user=='admin' and hmac.compare_digest(passwd,load_auth()['password'])
        except (ValueError, UnicodeError, OSError):
            return False

    def check(self):
        if self.headers.get('Host','').split(':')[0] not in ('localhost','127.0.0.1'):
            self.send(403,{'error':'Localhost only'});return False
        if not self.authorized():
            self.send_response(401)
            self.send_header('WWW-Authenticate','Basic realm="Matrix-LAN Admin"')
            self.send_header('Content-Length','0')
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            return False
        return True

    def do_GET(self):
        if not self.check():return
        path=urlsplit(self.path).path
        if path=='/':
            self.send(200,PAGE,'text/html; charset=utf-8')
        elif path=='/api/status':
            try:
                data=json.loads((RUNTIME/'bootstrap.private.json').read_text())
                services=data['services']
                self.send(200,{'whatsapp':'mautrix-whatsapp' in services,'telegram':'mautrix-telegram' in services})
            except (OSError,KeyError,ValueError):
                self.send(500,{'error':'Cannot read installation state'})
        else:self.send(404,{'error':'Not found'})

    def do_POST(self):
        if not self.check():return
        if urlsplit(self.path).path!='/api/install':
            self.send(404,{'error':'Not found'});return
        origin=self.headers.get('Origin','')
        if origin not in ('http://localhost:18082','http://127.0.0.1:18082') or self.headers.get('X-Matrix-LAN-Action')!='1':
            self.send(403,{'error':'Invalid request origin'});return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<=0 or size>2048:raise ValueError('Invalid request size')
            data=json.loads(self.rfile.read(size))
            bridge=data.get('bridge')
            if bridge not in ('whatsapp','telegram'):raise ValueError('Unknown bridge')
            args=['python3',str(ROOT/'install.py'),'enable-bridge',bridge]
            with LOCK:
                if bridge=='telegram':
                    api_id=data.get('api_id')
                    api_hash=data.get('api_hash')
                    if type(api_id)!=int or api_id<=0 or not isinstance(api_hash,str) or len(api_hash)!=32 or any(c not in '0123456789abcdefABCDEF' for c in api_hash):
                        raise ValueError('Enter a valid Telegram API ID and 32-character API Hash')
                    fd, path=tempfile.mkstemp(prefix='telegram-',suffix='.json',dir=RUNTIME)
                    try:
                        with os.fdopen(fd,'w') as f:json.dump({'api_id':api_id,'api_hash':api_hash},f)
                        os.chmod(path,0o600)
                        proc=subprocess.run(args+['--telegram-config',path],capture_output=True,text=True,timeout=180)
                    finally:os.unlink(path)
                else:
                    proc=subprocess.run(args,capture_output=True,text=True,timeout=180)
            if proc.returncode:
                # Do not expose raw subprocess logs: they may contain secrets.
                self.send(500,{'error':'Bridge setup failed. Check installer logs on the host.'})
            else:self.send(200,{'message':bridge.capitalize()+' bridge installed. Connect your account in Element.'})
        except (ValueError,TypeError,json.JSONDecodeError) as exc:self.send(400,{'error':str(exc)})
        except subprocess.TimeoutExpired:self.send(504,{'error':'Bridge installation timed out'})
        except Exception:self.send(500,{'error':'Internal installation error'})

    def log_message(self, fmt, *args):
        # Avoid logging request headers or credentials.
        print('%s %s' % (self.address_string(),fmt % args),flush=True)

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='admin-init':initialize()
    elif len(sys.argv)>1 and sys.argv[1]=='admin-password':password()
    else:
        print('Matrix-LAN admin listening on 0.0.0.0:18082 (host mapping must be loopback only)',flush=True)
        ThreadingHTTPServer(('0.0.0.0',18082),Handler).serve_forever()
, backfill[1], re.M).group(1))
    }

def save_whatsapp_settings(data):
    if not WA_CONFIG.is_file():
        raise ValueError('WhatsApp bridge is not installed')
    if type(data.get('request_full_sync')) is not bool or type(data.get('backfill_enabled')) is not bool:
        raise ValueError('Invalid switches')
    count = data.get('max_initial_messages')
    if type(count) is not int or count not in (50, 100, 500, -1):
        raise ValueError('Invalid message limit')
    original = WA_CONFIG.read_text()
    sections = original.split('\\nbackfill:', 1)
    if len(sections) != 2:
        raise ValueError('Missing backfill section')
    head, tail = sections
    def sub_once(text, pattern, replacement):
        result, n = re.subn(pattern, replacement, text, count=1, flags=re.M)
        if n != 1:
            raise ValueError('Missing or ambiguous configuration field')
        return result
    head = sub_once(head, r'^(\\s+request_full_sync:\\s*)(true|false)(\\s*)
def load_auth():
    if not AUTH.exists():
        raise RuntimeError('Admin credentials missing; run admin-init first.')
    return json.loads(AUTH.read_text())

def initialize():
    if AUTH.exists():
        print('Admin credentials already exist.')
        return
    if not RUNTIME.is_dir():
        raise RuntimeError('Prepare Matrix-LAN first.')
    password = secrets.token_urlsafe(30)
    fd = os.open(AUTH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump({'password': password}, f)
    print('Admin credentials created. Use admin-password to retrieve them.')

def password():
    print(load_auth()['password'])

PAGE = """<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Matrix-LAN — Мессенджеры</title>
<style>body{font:16px system-ui;background:#101820;color:#f5f6fa;max-width:680px;margin:40px auto;padding:0 18px}
article{background:#202d39;border-radius:12px;padding:20px;margin:14px 0}
button,input{padding:10px;border-radius:7px;border:1px solid #718096;font:inherit}
button{cursor:pointer;background:#54d4a1;color:#082018;font-weight:700}
input{display:block;width:95%;margin:10px 0;background:#fff;color:#111}
small{color:#b7c7d4}#result{white-space:pre-wrap}</style>
<h1>Matrix-LAN</h1><p>Управление мостами мессенджеров</p>
<article><h2>WhatsApp</h2><p id="wa">Проверка...</p><button onclick="install('whatsapp')">Установить мост</button>
<small><p>После запуска привяжите телефон через чат с ботом WhatsApp в Element.</p></small></article>
<article><h2>Telegram</h2><p id="tg">Проверка...</p>
<label>API ID<input id="apiid" type="number" min="1" autocomplete="off"></label>
<label>API Hash<input id="apihash" type="password" autocomplete="off"></label>
<button onclick="install('telegram')">Установить мост</button>
<small><p>Получите API ID и API Hash на my.telegram.org/apps.</p></small></article>
<article><h3>Результат</h3><div id="result">Готово к работе.</div></article>
<script>
async function request(path, data) {
 const options=data?{method:'POST',headers:{'Content-Type':'application/json','X-Matrix-LAN-Action':'1'},body:JSON.stringify(data)}:{};
 const response=await fetch(path,options);const result=await response.json();
 if(!response.ok)throw Error(result.error||'HTTP '+response.status);return result;
}
async function refresh(){try{const d=await request('/api/status');
 document.getElementById('wa').textContent=d.whatsapp?'Установлен':'Не установлен';
 document.getElementById('tg').textContent=d.telegram?'Установлен':'Не установлен';
 }catch(e){document.getElementById('result').textContent=e.message;}}
async function install(name){
 const body={bridge:name};
 if(name==='telegram'){body.api_id=Number(document.getElementById('apiid').value);
 body.api_hash=document.getElementById('apihash').value;}
 document.getElementById('result').textContent='Установка...';
 try{const d=await request('/api/install',body);document.getElementById('result').textContent=d.message;
 document.getElementById('apihash').value='';await refresh();}
 catch(e){document.getElementById('result').textContent=e.message;}
}
refresh();
</script></html>"""

class Handler(BaseHTTPRequestHandler):
    def send(self, status, data, kind='application/json; charset=utf-8'):
        body = json.dumps(data).encode() if kind.startswith('application/json') else data.encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        header = self.headers.get('Authorization','')
        try:
            scheme, token = header.split(' ',1)
            decoded = base64.b64decode(token,validate=True).decode()
            user, passwd = decoded.split(':',1)
            return scheme.lower()=='basic' and user=='admin' and hmac.compare_digest(passwd,load_auth()['password'])
        except (ValueError, UnicodeError, OSError):
            return False

    def check(self):
        if self.headers.get('Host','').split(':')[0] not in ('localhost','127.0.0.1'):
            self.send(403,{'error':'Localhost only'});return False
        if not self.authorized():
            self.send_response(401)
            self.send_header('WWW-Authenticate','Basic realm="Matrix-LAN Admin"')
            self.send_header('Content-Length','0')
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            return False
        return True

    def do_GET(self):
        if not self.check():return
        path=urlsplit(self.path).path
        if path=='/':
            self.send(200,PAGE,'text/html; charset=utf-8')
        elif path=='/api/status':
            try:
                data=json.loads((RUNTIME/'bootstrap.private.json').read_text())
                services=data['services']
                self.send(200,{'whatsapp':'mautrix-whatsapp' in services,'telegram':'mautrix-telegram' in services})
            except (OSError,KeyError,ValueError):
                self.send(500,{'error':'Cannot read installation state'})
        else:self.send(404,{'error':'Not found'})

    def do_POST(self):
        if not self.check():return
        if urlsplit(self.path).path!='/api/install':
            self.send(404,{'error':'Not found'});return
        origin=self.headers.get('Origin','')
        if origin not in ('http://localhost:18082','http://127.0.0.1:18082') or self.headers.get('X-Matrix-LAN-Action')!='1':
            self.send(403,{'error':'Invalid request origin'});return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<=0 or size>2048:raise ValueError('Invalid request size')
            data=json.loads(self.rfile.read(size))
            bridge=data.get('bridge')
            if bridge not in ('whatsapp','telegram'):raise ValueError('Unknown bridge')
            args=['python3',str(ROOT/'install.py'),'enable-bridge',bridge]
            with LOCK:
                if bridge=='telegram':
                    api_id=data.get('api_id')
                    api_hash=data.get('api_hash')
                    if type(api_id)!=int or api_id<=0 or not isinstance(api_hash,str) or len(api_hash)!=32 or any(c not in '0123456789abcdefABCDEF' for c in api_hash):
                        raise ValueError('Enter a valid Telegram API ID and 32-character API Hash')
                    fd, path=tempfile.mkstemp(prefix='telegram-',suffix='.json',dir=RUNTIME)
                    try:
                        with os.fdopen(fd,'w') as f:json.dump({'api_id':api_id,'api_hash':api_hash},f)
                        os.chmod(path,0o600)
                        proc=subprocess.run(args+['--telegram-config',path],capture_output=True,text=True,timeout=180)
                    finally:os.unlink(path)
                else:
                    proc=subprocess.run(args,capture_output=True,text=True,timeout=180)
            if proc.returncode:
                # Do not expose raw subprocess logs: they may contain secrets.
                self.send(500,{'error':'Bridge setup failed. Check installer logs on the host.'})
            else:self.send(200,{'message':bridge.capitalize()+' bridge installed. Connect your account in Element.'})
        except (ValueError,TypeError,json.JSONDecodeError) as exc:self.send(400,{'error':str(exc)})
        except subprocess.TimeoutExpired:self.send(504,{'error':'Bridge installation timed out'})
        except Exception:self.send(500,{'error':'Internal installation error'})

    def log_message(self, fmt, *args):
        # Avoid logging request headers or credentials.
        print('%s %s' % (self.address_string(),fmt % args),flush=True)

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='admin-init':initialize()
    elif len(sys.argv)>1 and sys.argv[1]=='admin-password':password()
    else:
        print('Matrix-LAN admin listening on 0.0.0.0:18082 (host mapping must be loopback only)',flush=True)
        ThreadingHTTPServer(('0.0.0.0',18082),Handler).serve_forever()
, lambda m: m[1] + str(data['request_full_sync']).lower() + m[3])
    tail = sub_once(tail, r'^(    enabled:\\s*)(true|false)(\\s*)
def load_auth():
    if not AUTH.exists():
        raise RuntimeError('Admin credentials missing; run admin-init first.')
    return json.loads(AUTH.read_text())

def initialize():
    if AUTH.exists():
        print('Admin credentials already exist.')
        return
    if not RUNTIME.is_dir():
        raise RuntimeError('Prepare Matrix-LAN first.')
    password = secrets.token_urlsafe(30)
    fd = os.open(AUTH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump({'password': password}, f)
    print('Admin credentials created. Use admin-password to retrieve them.')

def password():
    print(load_auth()['password'])

PAGE = """<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Matrix-LAN — Мессенджеры</title>
<style>body{font:16px system-ui;background:#101820;color:#f5f6fa;max-width:680px;margin:40px auto;padding:0 18px}
article{background:#202d39;border-radius:12px;padding:20px;margin:14px 0}
button,input{padding:10px;border-radius:7px;border:1px solid #718096;font:inherit}
button{cursor:pointer;background:#54d4a1;color:#082018;font-weight:700}
input{display:block;width:95%;margin:10px 0;background:#fff;color:#111}
small{color:#b7c7d4}#result{white-space:pre-wrap}</style>
<h1>Matrix-LAN</h1><p>Управление мостами мессенджеров</p>
<article><h2>WhatsApp</h2><p id="wa">Проверка...</p><button onclick="install('whatsapp')">Установить мост</button>
<small><p>После запуска привяжите телефон через чат с ботом WhatsApp в Element.</p></small></article>
<article><h2>Telegram</h2><p id="tg">Проверка...</p>
<label>API ID<input id="apiid" type="number" min="1" autocomplete="off"></label>
<label>API Hash<input id="apihash" type="password" autocomplete="off"></label>
<button onclick="install('telegram')">Установить мост</button>
<small><p>Получите API ID и API Hash на my.telegram.org/apps.</p></small></article>
<article><h3>Результат</h3><div id="result">Готово к работе.</div></article>
<script>
async function request(path, data) {
 const options=data?{method:'POST',headers:{'Content-Type':'application/json','X-Matrix-LAN-Action':'1'},body:JSON.stringify(data)}:{};
 const response=await fetch(path,options);const result=await response.json();
 if(!response.ok)throw Error(result.error||'HTTP '+response.status);return result;
}
async function refresh(){try{const d=await request('/api/status');
 document.getElementById('wa').textContent=d.whatsapp?'Установлен':'Не установлен';
 document.getElementById('tg').textContent=d.telegram?'Установлен':'Не установлен';
 }catch(e){document.getElementById('result').textContent=e.message;}}
async function install(name){
 const body={bridge:name};
 if(name==='telegram'){body.api_id=Number(document.getElementById('apiid').value);
 body.api_hash=document.getElementById('apihash').value;}
 document.getElementById('result').textContent='Установка...';
 try{const d=await request('/api/install',body);document.getElementById('result').textContent=d.message;
 document.getElementById('apihash').value='';await refresh();}
 catch(e){document.getElementById('result').textContent=e.message;}
}
refresh();
</script></html>"""

class Handler(BaseHTTPRequestHandler):
    def send(self, status, data, kind='application/json; charset=utf-8'):
        body = json.dumps(data).encode() if kind.startswith('application/json') else data.encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        header = self.headers.get('Authorization','')
        try:
            scheme, token = header.split(' ',1)
            decoded = base64.b64decode(token,validate=True).decode()
            user, passwd = decoded.split(':',1)
            return scheme.lower()=='basic' and user=='admin' and hmac.compare_digest(passwd,load_auth()['password'])
        except (ValueError, UnicodeError, OSError):
            return False

    def check(self):
        if self.headers.get('Host','').split(':')[0] not in ('localhost','127.0.0.1'):
            self.send(403,{'error':'Localhost only'});return False
        if not self.authorized():
            self.send_response(401)
            self.send_header('WWW-Authenticate','Basic realm="Matrix-LAN Admin"')
            self.send_header('Content-Length','0')
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            return False
        return True

    def do_GET(self):
        if not self.check():return
        path=urlsplit(self.path).path
        if path=='/':
            self.send(200,PAGE,'text/html; charset=utf-8')
        elif path=='/api/status':
            try:
                data=json.loads((RUNTIME/'bootstrap.private.json').read_text())
                services=data['services']
                self.send(200,{'whatsapp':'mautrix-whatsapp' in services,'telegram':'mautrix-telegram' in services})
            except (OSError,KeyError,ValueError):
                self.send(500,{'error':'Cannot read installation state'})
        else:self.send(404,{'error':'Not found'})

    def do_POST(self):
        if not self.check():return
        if urlsplit(self.path).path!='/api/install':
            self.send(404,{'error':'Not found'});return
        origin=self.headers.get('Origin','')
        if origin not in ('http://localhost:18082','http://127.0.0.1:18082') or self.headers.get('X-Matrix-LAN-Action')!='1':
            self.send(403,{'error':'Invalid request origin'});return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<=0 or size>2048:raise ValueError('Invalid request size')
            data=json.loads(self.rfile.read(size))
            bridge=data.get('bridge')
            if bridge not in ('whatsapp','telegram'):raise ValueError('Unknown bridge')
            args=['python3',str(ROOT/'install.py'),'enable-bridge',bridge]
            with LOCK:
                if bridge=='telegram':
                    api_id=data.get('api_id')
                    api_hash=data.get('api_hash')
                    if type(api_id)!=int or api_id<=0 or not isinstance(api_hash,str) or len(api_hash)!=32 or any(c not in '0123456789abcdefABCDEF' for c in api_hash):
                        raise ValueError('Enter a valid Telegram API ID and 32-character API Hash')
                    fd, path=tempfile.mkstemp(prefix='telegram-',suffix='.json',dir=RUNTIME)
                    try:
                        with os.fdopen(fd,'w') as f:json.dump({'api_id':api_id,'api_hash':api_hash},f)
                        os.chmod(path,0o600)
                        proc=subprocess.run(args+['--telegram-config',path],capture_output=True,text=True,timeout=180)
                    finally:os.unlink(path)
                else:
                    proc=subprocess.run(args,capture_output=True,text=True,timeout=180)
            if proc.returncode:
                # Do not expose raw subprocess logs: they may contain secrets.
                self.send(500,{'error':'Bridge setup failed. Check installer logs on the host.'})
            else:self.send(200,{'message':bridge.capitalize()+' bridge installed. Connect your account in Element.'})
        except (ValueError,TypeError,json.JSONDecodeError) as exc:self.send(400,{'error':str(exc)})
        except subprocess.TimeoutExpired:self.send(504,{'error':'Bridge installation timed out'})
        except Exception:self.send(500,{'error':'Internal installation error'})

    def log_message(self, fmt, *args):
        # Avoid logging request headers or credentials.
        print('%s %s' % (self.address_string(),fmt % args),flush=True)

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='admin-init':initialize()
    elif len(sys.argv)>1 and sys.argv[1]=='admin-password':password()
    else:
        print('Matrix-LAN admin listening on 0.0.0.0:18082 (host mapping must be loopback only)',flush=True)
        ThreadingHTTPServer(('0.0.0.0',18082),Handler).serve_forever()
, lambda m: m[1] + str(data['backfill_enabled']).lower() + m[3])
    tail = sub_once(tail, r'^(    max_initial_messages:\\s*)-?\\d+(\\s*)
def load_auth():
    if not AUTH.exists():
        raise RuntimeError('Admin credentials missing; run admin-init first.')
    return json.loads(AUTH.read_text())

def initialize():
    if AUTH.exists():
        print('Admin credentials already exist.')
        return
    if not RUNTIME.is_dir():
        raise RuntimeError('Prepare Matrix-LAN first.')
    password = secrets.token_urlsafe(30)
    fd = os.open(AUTH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump({'password': password}, f)
    print('Admin credentials created. Use admin-password to retrieve them.')

def password():
    print(load_auth()['password'])

PAGE = """<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Matrix-LAN — Мессенджеры</title>
<style>body{font:16px system-ui;background:#101820;color:#f5f6fa;max-width:680px;margin:40px auto;padding:0 18px}
article{background:#202d39;border-radius:12px;padding:20px;margin:14px 0}
button,input{padding:10px;border-radius:7px;border:1px solid #718096;font:inherit}
button{cursor:pointer;background:#54d4a1;color:#082018;font-weight:700}
input{display:block;width:95%;margin:10px 0;background:#fff;color:#111}
small{color:#b7c7d4}#result{white-space:pre-wrap}</style>
<h1>Matrix-LAN</h1><p>Управление мостами мессенджеров</p>
<article><h2>WhatsApp</h2><p id="wa">Проверка...</p><button onclick="install('whatsapp')">Установить мост</button>
<small><p>После запуска привяжите телефон через чат с ботом WhatsApp в Element.</p></small></article>
<article><h2>Telegram</h2><p id="tg">Проверка...</p>
<label>API ID<input id="apiid" type="number" min="1" autocomplete="off"></label>
<label>API Hash<input id="apihash" type="password" autocomplete="off"></label>
<button onclick="install('telegram')">Установить мост</button>
<small><p>Получите API ID и API Hash на my.telegram.org/apps.</p></small></article>
<article><h3>Результат</h3><div id="result">Готово к работе.</div></article>
<script>
async function request(path, data) {
 const options=data?{method:'POST',headers:{'Content-Type':'application/json','X-Matrix-LAN-Action':'1'},body:JSON.stringify(data)}:{};
 const response=await fetch(path,options);const result=await response.json();
 if(!response.ok)throw Error(result.error||'HTTP '+response.status);return result;
}
async function refresh(){try{const d=await request('/api/status');
 document.getElementById('wa').textContent=d.whatsapp?'Установлен':'Не установлен';
 document.getElementById('tg').textContent=d.telegram?'Установлен':'Не установлен';
 }catch(e){document.getElementById('result').textContent=e.message;}}
async function install(name){
 const body={bridge:name};
 if(name==='telegram'){body.api_id=Number(document.getElementById('apiid').value);
 body.api_hash=document.getElementById('apihash').value;}
 document.getElementById('result').textContent='Установка...';
 try{const d=await request('/api/install',body);document.getElementById('result').textContent=d.message;
 document.getElementById('apihash').value='';await refresh();}
 catch(e){document.getElementById('result').textContent=e.message;}
}
refresh();
</script></html>"""

class Handler(BaseHTTPRequestHandler):
    def send(self, status, data, kind='application/json; charset=utf-8'):
        body = json.dumps(data).encode() if kind.startswith('application/json') else data.encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        header = self.headers.get('Authorization','')
        try:
            scheme, token = header.split(' ',1)
            decoded = base64.b64decode(token,validate=True).decode()
            user, passwd = decoded.split(':',1)
            return scheme.lower()=='basic' and user=='admin' and hmac.compare_digest(passwd,load_auth()['password'])
        except (ValueError, UnicodeError, OSError):
            return False

    def check(self):
        if self.headers.get('Host','').split(':')[0] not in ('localhost','127.0.0.1'):
            self.send(403,{'error':'Localhost only'});return False
        if not self.authorized():
            self.send_response(401)
            self.send_header('WWW-Authenticate','Basic realm="Matrix-LAN Admin"')
            self.send_header('Content-Length','0')
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            return False
        return True

    def do_GET(self):
        if not self.check():return
        path=urlsplit(self.path).path
        if path=='/':
            self.send(200,PAGE,'text/html; charset=utf-8')
        elif path=='/api/status':
            try:
                data=json.loads((RUNTIME/'bootstrap.private.json').read_text())
                services=data['services']
                self.send(200,{'whatsapp':'mautrix-whatsapp' in services,'telegram':'mautrix-telegram' in services})
            except (OSError,KeyError,ValueError):
                self.send(500,{'error':'Cannot read installation state'})
        else:self.send(404,{'error':'Not found'})

    def do_POST(self):
        if not self.check():return
        if urlsplit(self.path).path!='/api/install':
            self.send(404,{'error':'Not found'});return
        origin=self.headers.get('Origin','')
        if origin not in ('http://localhost:18082','http://127.0.0.1:18082') or self.headers.get('X-Matrix-LAN-Action')!='1':
            self.send(403,{'error':'Invalid request origin'});return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<=0 or size>2048:raise ValueError('Invalid request size')
            data=json.loads(self.rfile.read(size))
            bridge=data.get('bridge')
            if bridge not in ('whatsapp','telegram'):raise ValueError('Unknown bridge')
            args=['python3',str(ROOT/'install.py'),'enable-bridge',bridge]
            with LOCK:
                if bridge=='telegram':
                    api_id=data.get('api_id')
                    api_hash=data.get('api_hash')
                    if type(api_id)!=int or api_id<=0 or not isinstance(api_hash,str) or len(api_hash)!=32 or any(c not in '0123456789abcdefABCDEF' for c in api_hash):
                        raise ValueError('Enter a valid Telegram API ID and 32-character API Hash')
                    fd, path=tempfile.mkstemp(prefix='telegram-',suffix='.json',dir=RUNTIME)
                    try:
                        with os.fdopen(fd,'w') as f:json.dump({'api_id':api_id,'api_hash':api_hash},f)
                        os.chmod(path,0o600)
                        proc=subprocess.run(args+['--telegram-config',path],capture_output=True,text=True,timeout=180)
                    finally:os.unlink(path)
                else:
                    proc=subprocess.run(args,capture_output=True,text=True,timeout=180)
            if proc.returncode:
                # Do not expose raw subprocess logs: they may contain secrets.
                self.send(500,{'error':'Bridge setup failed. Check installer logs on the host.'})
            else:self.send(200,{'message':bridge.capitalize()+' bridge installed. Connect your account in Element.'})
        except (ValueError,TypeError,json.JSONDecodeError) as exc:self.send(400,{'error':str(exc)})
        except subprocess.TimeoutExpired:self.send(504,{'error':'Bridge installation timed out'})
        except Exception:self.send(500,{'error':'Internal installation error'})

    def log_message(self, fmt, *args):
        # Avoid logging request headers or credentials.
        print('%s %s' % (self.address_string(),fmt % args),flush=True)

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='admin-init':initialize()
    elif len(sys.argv)>1 and sys.argv[1]=='admin-password':password()
    else:
        print('Matrix-LAN admin listening on 0.0.0.0:18082 (host mapping must be loopback only)',flush=True)
        ThreadingHTTPServer(('0.0.0.0',18082),Handler).serve_forever()
, lambda m: m[1] + str(count) + m[2])
    updated = head + '\\nbackfill:' + tail
    # Atomic replacement with the existing file mode, preserving a rollback copy.
    import shutil
    shutil.copy2(WA_CONFIG, WA_CONFIG.with_suffix('.yaml.bak'))
    fd, temp = tempfile.mkstemp(prefix='.config-', dir=WA_CONFIG.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            out.write(updated)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temp, WA_CONFIG.stat().st_mode & 0o777)
        os.replace(temp, WA_CONFIG)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    # Find the unique running Compose service; never restart unrelated containers.
    proc = subprocess.run(['docker', 'ps', '--filter', 'label=com.docker.compose.service=mautrix-whatsapp',
                           '--format', '{{.Names}}'], capture_output=True, text=True, timeout=15)
    names = [name for name in proc.stdout.splitlines() if name.endswith('-mautrix-whatsapp-1') and name.startswith('matrix-lan-')]
    if proc.returncode or len(names) != 1:
        return 'Настройки сохранены. Перезапустите WhatsApp-мост вручную (контейнер не определён однозначно).'
    restart = subprocess.run(['docker', 'restart', names[0]], capture_output=True, text=True, timeout=60)
    if restart.returncode:
        return 'Настройки сохранены, но перезапуск не удался. Проверьте контейнер WhatsApp.'
    return 'Настройки сохранены. WhatsApp-мост перезапущен. Для нового запроса полной истории может потребоваться повторная привязка.'

def load_auth():
    if not AUTH.exists():
        raise RuntimeError('Admin credentials missing; run admin-init first.')
    return json.loads(AUTH.read_text())

def initialize():
    if AUTH.exists():
        print('Admin credentials already exist.')
        return
    if not RUNTIME.is_dir():
        raise RuntimeError('Prepare Matrix-LAN first.')
    password = secrets.token_urlsafe(30)
    fd = os.open(AUTH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump({'password': password}, f)
    print('Admin credentials created. Use admin-password to retrieve them.')

def password():
    print(load_auth()['password'])

PAGE = """<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Matrix-LAN — Мессенджеры</title>
<style>body{font:16px system-ui;background:#101820;color:#f5f6fa;max-width:680px;margin:40px auto;padding:0 18px}
article{background:#202d39;border-radius:12px;padding:20px;margin:14px 0}
button,input{padding:10px;border-radius:7px;border:1px solid #718096;font:inherit}
button{cursor:pointer;background:#54d4a1;color:#082018;font-weight:700}
input{display:block;width:95%;margin:10px 0;background:#fff;color:#111}
small{color:#b7c7d4}#result{white-space:pre-wrap}</style>
<h1>Matrix-LAN</h1><p>Управление мостами мессенджеров</p>
<article><h2>WhatsApp</h2><p id="wa">Проверка...</p><button onclick="install('whatsapp')">Установить мост</button>
<small><p>После запуска привяжите телефон через чат с ботом WhatsApp в Element.</p></small></article>
<article><h2>Telegram</h2><p id="tg">Проверка...</p>
<label>API ID<input id="apiid" type="number" min="1" autocomplete="off"></label>
<label>API Hash<input id="apihash" type="password" autocomplete="off"></label>
<button onclick="install('telegram')">Установить мост</button>
<small><p>Получите API ID и API Hash на my.telegram.org/apps.</p></small></article>
<article><h3>Результат</h3><div id="result">Готово к работе.</div></article>
<script>
async function request(path, data) {
 const options=data?{method:'POST',headers:{'Content-Type':'application/json','X-Matrix-LAN-Action':'1'},body:JSON.stringify(data)}:{};
 const response=await fetch(path,options);const result=await response.json();
 if(!response.ok)throw Error(result.error||'HTTP '+response.status);return result;
}
async function refresh(){try{const d=await request('/api/status');
 document.getElementById('wa').textContent=d.whatsapp?'Установлен':'Не установлен';
 document.getElementById('tg').textContent=d.telegram?'Установлен':'Не установлен';
 }catch(e){document.getElementById('result').textContent=e.message;}}
async function install(name){
 const body={bridge:name};
 if(name==='telegram'){body.api_id=Number(document.getElementById('apiid').value);
 body.api_hash=document.getElementById('apihash').value;}
 document.getElementById('result').textContent='Установка...';
 try{const d=await request('/api/install',body);document.getElementById('result').textContent=d.message;
 document.getElementById('apihash').value='';await refresh();}
 catch(e){document.getElementById('result').textContent=e.message;}
}
refresh();
</script></html>"""

class Handler(BaseHTTPRequestHandler):
    def send(self, status, data, kind='application/json; charset=utf-8'):
        body = json.dumps(data).encode() if kind.startswith('application/json') else data.encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        header = self.headers.get('Authorization','')
        try:
            scheme, token = header.split(' ',1)
            decoded = base64.b64decode(token,validate=True).decode()
            user, passwd = decoded.split(':',1)
            return scheme.lower()=='basic' and user=='admin' and hmac.compare_digest(passwd,load_auth()['password'])
        except (ValueError, UnicodeError, OSError):
            return False

    def check(self):
        if self.headers.get('Host','').split(':')[0] not in ('localhost','127.0.0.1'):
            self.send(403,{'error':'Localhost only'});return False
        if not self.authorized():
            self.send_response(401)
            self.send_header('WWW-Authenticate','Basic realm="Matrix-LAN Admin"')
            self.send_header('Content-Length','0')
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            return False
        return True

    def do_GET(self):
        if not self.check():return
        path=urlsplit(self.path).path
        if path=='/':
            self.send(200,PAGE,'text/html; charset=utf-8')
        elif path=='/api/status':
            try:
                data=json.loads((RUNTIME/'bootstrap.private.json').read_text())
                services=data['services']
                self.send(200,{'whatsapp':'mautrix-whatsapp' in services,'telegram':'mautrix-telegram' in services})
            except (OSError,KeyError,ValueError):
                self.send(500,{'error':'Cannot read installation state'})
        else:self.send(404,{'error':'Not found'})

    def do_POST(self):
        if not self.check():return
        if urlsplit(self.path).path!='/api/install':
            self.send(404,{'error':'Not found'});return
        origin=self.headers.get('Origin','')
        if origin not in ('http://localhost:18082','http://127.0.0.1:18082') or self.headers.get('X-Matrix-LAN-Action')!='1':
            self.send(403,{'error':'Invalid request origin'});return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<=0 or size>2048:raise ValueError('Invalid request size')
            data=json.loads(self.rfile.read(size))
            bridge=data.get('bridge')
            if bridge not in ('whatsapp','telegram'):raise ValueError('Unknown bridge')
            args=['python3',str(ROOT/'install.py'),'enable-bridge',bridge]
            with LOCK:
                if bridge=='telegram':
                    api_id=data.get('api_id')
                    api_hash=data.get('api_hash')
                    if type(api_id)!=int or api_id<=0 or not isinstance(api_hash,str) or len(api_hash)!=32 or any(c not in '0123456789abcdefABCDEF' for c in api_hash):
                        raise ValueError('Enter a valid Telegram API ID and 32-character API Hash')
                    fd, path=tempfile.mkstemp(prefix='telegram-',suffix='.json',dir=RUNTIME)
                    try:
                        with os.fdopen(fd,'w') as f:json.dump({'api_id':api_id,'api_hash':api_hash},f)
                        os.chmod(path,0o600)
                        proc=subprocess.run(args+['--telegram-config',path],capture_output=True,text=True,timeout=180)
                    finally:os.unlink(path)
                else:
                    proc=subprocess.run(args,capture_output=True,text=True,timeout=180)
            if proc.returncode:
                # Do not expose raw subprocess logs: they may contain secrets.
                self.send(500,{'error':'Bridge setup failed. Check installer logs on the host.'})
            else:self.send(200,{'message':bridge.capitalize()+' bridge installed. Connect your account in Element.'})
        except (ValueError,TypeError,json.JSONDecodeError) as exc:self.send(400,{'error':str(exc)})
        except subprocess.TimeoutExpired:self.send(504,{'error':'Bridge installation timed out'})
        except Exception:self.send(500,{'error':'Internal installation error'})

    def log_message(self, fmt, *args):
        # Avoid logging request headers or credentials.
        print('%s %s' % (self.address_string(),fmt % args),flush=True)

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='admin-init':initialize()
    elif len(sys.argv)>1 and sys.argv[1]=='admin-password':password()
    else:
        print('Matrix-LAN admin listening on 0.0.0.0:18082 (host mapping must be loopback only)',flush=True)
        ThreadingHTTPServer(('0.0.0.0',18082),Handler).serve_forever()
