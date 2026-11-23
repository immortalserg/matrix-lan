"""Durable cursors and exact native-message identity. No body/time heuristics.

An interrupted native send is uncertain and is never automatically replayed.
This prevents duplicate seller messages after a timeout or process restart.
"""
import sqlite3,json,time,hashlib,re

class MessageStore:
 def __init__(self,path):
  self.db=sqlite3.connect(path);self.db.row_factory=sqlite3.Row
  self.db.executescript('''PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS chats(slot TEXT,cid TEXT,name TEXT,room_id TEXT,PRIMARY KEY(slot,cid),UNIQUE(room_id));
CREATE TABLE IF NOT EXISTS inbox(slot TEXT,cid TEXT,msgid TEXT,body TEXT NOT NULL,own INTEGER NOT NULL,ts INTEGER NOT NULL,kind TEXT NOT NULL,sender TEXT NOT NULL,event_id TEXT,PRIMARY KEY(slot,cid,msgid));
CREATE TABLE IF NOT EXISTS outbox(event_id TEXT PRIMARY KEY,slot TEXT,cid TEXT,body TEXT,state TEXT NOT NULL DEFAULT 'pending',native_id TEXT,error TEXT,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS transactions(txn_id TEXT PRIMARY KEY,received REAL NOT NULL);
CREATE TABLE IF NOT EXISTS cursors(slot TEXT,cid TEXT,value TEXT NOT NULL,PRIMARY KEY(slot,cid));
''')
  if 'notice_event' not in {r['name'] for r in self.db.execute('PRAGMA table_info(outbox)')}:self.db.execute('ALTER TABLE outbox ADD COLUMN notice_event TEXT')
  if 'sender_name' not in {r['name'] for r in self.db.execute('PRAGMA table_info(inbox)')}:self.db.execute('ALTER TABLE inbox ADD COLUMN sender_name TEXT')
  if 'attachments' not in {r['name'] for r in self.db.execute('PRAGMA table_info(inbox)')}:self.db.execute("ALTER TABLE inbox ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'")
  self.db.execute('CREATE TABLE IF NOT EXISTS media_parts(slot TEXT,cid TEXT,msgid TEXT,part INTEGER,descriptor TEXT NOT NULL,content TEXT,event_id TEXT,attempts INTEGER NOT NULL DEFAULT 0,next_retry REAL NOT NULL DEFAULT 0,error TEXT,PRIMARY KEY(slot,cid,msgid,part))')
  if 'fallback_event' not in {r['name'] for r in self.db.execute('PRAGMA table_info(media_parts)')}:self.db.execute('ALTER TABLE media_parts ADD COLUMN fallback_event TEXT')
  self.db.execute('CREATE TABLE IF NOT EXISTS message_repairs(slot TEXT,cid TEXT,msgid TEXT,old_event TEXT,done INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(slot,cid,msgid))')
  self.db.execute("UPDATE outbox SET state='uncertain',error='Соединение прервалось во время отправки; автоматический повтор отключён' WHERE state='sending'")
  self.db.commit()
 def close(self):self.db.close()
 @staticmethod
 def txn(slot,cid,msgid):return 'social_'+hashlib.sha256(json.dumps([slot,cid,msgid],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
 def ingest(self,slot,cid,messages,cursor=None):
  """Persist the entire valid batch before advancing its native cursor."""
  rows=[]
  for m in messages:
   if not isinstance(m.get('id'),str) or not m['id'] or not isinstance(m.get('own'),bool):raise ValueError('Native identity or message direction missing')
   if not isinstance(m.get('body'),str) or not isinstance(m.get('timestamp'),int):raise ValueError('Invalid native message')
   attachments=m.get('attachments',[])
   if not isinstance(attachments,list) or len(attachments)>30:raise ValueError('Invalid attachment list')
   for a in attachments:
    if not isinstance(a,dict) or set(a)-{'index','type'} or type(a.get('index')) is not int or not 0<=a['index']<100 or not isinstance(a.get('type'),str):raise ValueError('Only native attachment locators may persist')
   rows.append((slot,cid,m['id'],m['body'],int(m['own']),m['timestamp'],m.get('kind','text'),str(m.get('sender','')),str(m.get('name') or m.get('sender','')),json.dumps(attachments)))
  with self.db:
   self.db.executemany('INSERT OR IGNORE INTO inbox(slot,cid,msgid,body,own,ts,kind,sender,sender_name,attachments) VALUES(?,?,?,?,?,?,?,?,?,?)',rows)
   for row in rows:
    slot,cid,mid,body,own,ts,kind,sender,name,serialized=row
    attachments=json.loads(serialized)
    previous=self.db.execute('SELECT body,kind,event_id FROM inbox WHERE slot=? AND cid=? AND msgid=?',(slot,cid,mid)).fetchone()
    if previous['kind']=='notice' and previous['body'].startswith('[Вложение') and previous['body']!=body and not body.startswith('[Вложение'):
     if body and previous['event_id'] and previous['event_id'].startswith('$'):self.db.execute('INSERT OR IGNORE INTO message_repairs(slot,cid,msgid,old_event) VALUES(?,?,?,?)',(slot,cid,mid,previous['event_id']))
     self.db.execute('UPDATE inbox SET body=?,kind=? WHERE slot=? AND cid=? AND msgid=?',(body,kind,slot,cid,mid))
    if attachments:
     self.db.execute('UPDATE inbox SET attachments=? WHERE slot=? AND cid=? AND msgid=?',(serialized,slot,cid,mid))
     self.db.executemany('INSERT OR IGNORE INTO media_parts(slot,cid,msgid,part,descriptor) VALUES(?,?,?,?,?)',[(slot,cid,mid,a['index'],json.dumps(a)) for a in attachments])
   if cursor is not None:self.db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?,?)',(slot,cid,str(cursor)))
 def cursor(self,slot,cid):
  row=self.db.execute('SELECT value FROM cursors WHERE slot=? AND cid=?',(slot,cid)).fetchone();return row['value'] if row else None
 def link_chat(self,slot,cid,name,room):
  with self.db:self.db.execute('INSERT INTO chats VALUES(?,?,?,?) ON CONFLICT(slot,cid) DO UPDATE SET name=excluded.name,room_id=excluded.room_id',(slot,cid,name,room))
 def pending_incoming(self,limit=100):
  return self.db.execute('''SELECT i.*,c.room_id,c.name FROM inbox i JOIN chats c ON i.slot=c.slot AND i.cid=c.cid WHERE i.event_id IS NULL ORDER BY i.ts,i.msgid LIMIT ?''',(limit,)).fetchall()
 def is_outbound_echo(self,slot,cid,msgid):
  return bool(self.db.execute("SELECT 1 FROM outbox WHERE slot=? AND cid=? AND native_id=? AND state='sent'",(slot,cid,msgid)).fetchone())
 def mark_incoming(self,slot,cid,msgid,event):
  with self.db:self.db.execute('UPDATE inbox SET event_id=? WHERE slot=? AND cid=? AND msgid=?',(event,slot,cid,msgid))
 def matrix_transaction(self,txn_id,events,owner):
  """Only owner text in an already mapped portal can enter the send queue."""
  with self.db:
   if self.db.execute('SELECT 1 FROM transactions WHERE txn_id=?',(txn_id,)).fetchone():return False
   for e in events:
    if e.get('sender')!=owner or e.get('type')!='m.room.message' or not isinstance(e.get('event_id'),str):continue
    content=e.get('content',{})
    if content.get('m.relates_to',{}).get('rel_type')=='m.replace':continue
    if content.get('com.matrix.social') or content.get('msgtype')!='m.text' or not isinstance(content.get('body'),str):continue
    body=self.outgoing_body(content)
    if not body.strip() or len(body.encode())>16000:continue
    room=self.db.execute('SELECT slot,cid FROM chats WHERE room_id=?',(e.get('room_id'),)).fetchone()
    if not room:continue
    self.db.execute('INSERT OR IGNORE INTO outbox(event_id,slot,cid,body,created) VALUES(?,?,?,?,?)',(e['event_id'],room['slot'],room['cid'],body,time.time()))
   self.db.execute('INSERT INTO transactions VALUES(?,?)',(txn_id,time.time()))
  return True
 @staticmethod
 def outgoing_body(content):
  body=content.get('body','')
  if isinstance(body,str) and content.get('m.relates_to',{}).get('m.in_reply_to',{}).get('event_id'):
   return re.sub(r'^(?:>[^\n]*(?:\n|$))+\n','',body,count=1)
  return body
 def pending_outgoing(self):return self.db.execute("SELECT * FROM outbox WHERE state='pending' ORDER BY created LIMIT 1").fetchone()
 def start_send(self,event_id):
  with self.db:
   result=self.db.execute("UPDATE outbox SET state='sending' WHERE event_id=? AND state='pending'",(event_id,))
   return result.rowcount==1
 def finish_send(self,event_id,native_id):
  if not isinstance(native_id,str) or not native_id:raise ValueError('Native acceptance ID required')
  with self.db:self.db.execute("UPDATE outbox SET state='sent',native_id=?,error=NULL WHERE event_id=? AND state='sending'",(native_id,event_id))
 def uncertain_send(self,event_id):
  with self.db:self.db.execute("UPDATE outbox SET state='uncertain',error='Сервис не подтвердил отправку; проверьте чат перед повтором' WHERE event_id=? AND state='sending'",(event_id,))
 def delete_account(self,slot):
  # Preserve portal mapping and received history. Native logout revokes the
  # session separately, while queued sends must stop immediately.
  with self.db:self.db.execute("UPDATE outbox SET state='cancelled',error='Подключение удалено' WHERE slot=? AND state IN ('pending','sending')",(slot,))
