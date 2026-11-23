"""Persist adjacent account groups using Element's native per-room order.

Only spaces from the authenticated bridge inventory are changed. Other user
spaces and their preferences are never written. Re-run when inventory changes,
so a new account is inserted into its platform's block without DOM manipulation.
"""
import hashlib,json,time,os
from urllib.parse import quote

SPACE_ORDER='org.matrix.msc3230.space_order'

class AccountGrouping:
 def __init__(self,monitor,directory):
  self.m=monitor;self.path=directory/'account-grouping.json';self.saved={}
  if self.path.exists():
   try:self.saved=json.loads(self.path.read_text(encoding='utf8'))
   except (OSError,ValueError):pass
 def plan(self,rows):
  platforms=list(self.m.c['bridges']);rank={p:i for i,p in enumerate(platforms)}
  known=[];seen=set()
  for row in rows:
   room=row.get('space_room')
   if row.get('platform') not in rank or not room or row.get('space_error') or room in seen:continue
   seen.add(room);known.append(row)
  known.sort(key=lambda a:(rank[a['platform']],str(a.get('name') or a.get('login_id') or '').casefold(),a.get('key','')))
  indexes={};plan=[]
  for row in known:
   platform=row['platform'];index=indexes.get(platform,0);indexes[platform]=index+1
   plan.append({'room_id':row['space_room'],'platform':platform,'account':row.get('key'),'order':f'mca.{rank[platform]:04d}.{index:04d}'})
  return plan
 def apply(self,rows):
  plan=self.plan(rows)
  fingerprint=hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()
  if self.saved.get('fingerprint')==fingerprint:return self.status(plan)
  owner=quote(self.m.c['owner'],safe='')
  for item in plan:
   path='/_matrix/client/v3/user/'+owner+'/rooms/'+quote(item['room_id'],safe='')+'/account_data/'+SPACE_ORDER
   try:old=self.m.matrix(path)
   except RuntimeError as exc:
    if 'M_NOT_FOUND' not in str(exc):raise
    old={}
   if not isinstance(old,dict):raise RuntimeError('Invalid space order')
   if old.get('order')!=item['order']:self.m.matrix(path,'PUT',dict(old,order=item['order']))
  saved={'fingerprint':fingerprint,'updated_at':time.time(),'groups':plan}
  temp=self.path.with_suffix('.tmp');fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
  with os.fdopen(fd,'w',encoding='utf8') as f:json.dump(saved,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
  os.replace(temp,self.path);self.saved=saved
  return self.status(plan)
 def status(self,plan):
  return {'enabled':True,'grouped_accounts':len(plan),'platforms':list(dict.fromkeys(p['platform'] for p in plan)),'updated_at':self.saved.get('updated_at')}
