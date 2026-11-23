"""Fixed calendar-month retention for Synapse's media, never message events.

Uses the installed Synapse repository's deletion operations. Database interactions
only SELECT candidates and protected current avatars. The remote removal follows
MediaRepository.delete_old_remote_media in the pinned Synapse 1.164.1, selecting
by creation time rather than last access. No external storage providers are used.
"""
import calendar, contextlib, json, logging, os, re, shutil, time
from datetime import datetime, timezone
from pathlib import Path

log=logging.getLogger('matrix.media_retention')

def shift_months(timestamp_ms,months):
 d=datetime.fromtimestamp(timestamp_ms/1000,timezone.utc)
 index=d.year*12+d.month-1+months;year,month=divmod(index,12);month+=1
 return int(d.replace(year=year,month=month,day=min(d.day,calendar.monthrange(year,month)[1])).timestamp()*1000)

def expired(created_ms,now_ms,months=2):
 return shift_months(created_ms,months)<=now_ms

def protected_avatars(txn):
 txn.execute("SELECT avatar_url FROM profiles WHERE avatar_url IS NOT NULL UNION SELECT avatar FROM room_stats_state WHERE avatar IS NOT NULL UNION SELECT e.json::jsonb->'content'->>'avatar_url' FROM current_state_events c JOIN event_json e USING(event_id) WHERE c.type IN ('m.room.avatar','m.room.member')")
 return {r[0] for r in txn.fetchall() if isinstance(r[0],str) and r[0].startswith('mxc://')}

def candidates(txn,cutoff):
 # Use a deliberately broad cutoff, then check exact creation+2 calendar months
 # in Python (month-end clamping is not equivalent to subtracting two months).
 txn.execute('SELECT media_id,created_ts,safe_from_quarantine FROM local_media_repository WHERE created_ts<=? ORDER BY created_ts',(cutoff,))
 local=txn.fetchall()
 txn.execute('SELECT media_origin,media_id,filesystem_id,created_ts FROM remote_media_cache WHERE created_ts<=? ORDER BY created_ts',(cutoff,))
 return local,txn.fetchall(),protected_avatars(txn)

class MediaRetention:
 @staticmethod
 def parse_config(config):
  months=int(config.get('months',2));period=int(config.get('interval_seconds',60))
  if months!=2 or period<30:raise ValueError('Expected two calendar months and interval >=30 seconds')
  return {'months':months,'interval_seconds':period,'status_file':config.get('status_file','/data/media-retention-status.json')}
 def __init__(self,config,api):
  self.c=self.parse_config(config);self.api=api;self.running=False;self.deleted_total=0
  self.status={'enabled':True,'months':2,'scope':'all_matrix_media','clock':'created_at','messages_retained':True,'state':'starting'}
  self.write_status()
  api.looping_background_call(self.run,self.c['interval_seconds']*1000,desc='matrix_media_fixed_retention')
 def write_status(self):
  path=Path(self.c['status_file']);tmp=path.with_suffix('.tmp')
  tmp.write_text(json.dumps(self.status,ensure_ascii=False),encoding='utf8');os.replace(tmp,path)
 async def remove_remote(self,repo,origin,media_id,file_id):
  # IDs come from the database, but validate resolved paths before any removal.
  if not re.fullmatch(r'[A-Za-z0-9_-]+',file_id):raise ValueError('Invalid remote file ID')
  root=Path(self.api._hs.config.media.media_store_path).resolve()
  media=Path(repo.filepaths.remote_media_filepath(origin,file_id)).resolve()
  thumbs=Path(repo.filepaths.remote_media_thumbnail_dir(origin,file_id)).resolve()
  if not media.is_relative_to(root) or not thumbs.is_relative_to(root):raise ValueError('Media path outside repository')
  async with repo.remote_media_linearizer.queue((origin,media_id)):
   with contextlib.suppress(FileNotFoundError):media.unlink()
   if thumbs.exists():shutil.rmtree(thumbs)
   await repo.store.delete_remote_media(origin,media_id)
 async def run(self):
  if self.running:return
  self.running=True;now=int(time.time()*1000)
  try:
   repo=self.api._hs.get_media_repository();server=self.api._hs.hostname
   # Two calendar months are at least 59 days. Selecting a broader 58-day
   # range also handles leap years and timestamp boundaries safely.
   local,remote,avatars=await self.api.run_db_interaction('matrix_media_candidates',candidates,now-58*86400000)
   selected=[m for m,created,safe in local if not safe and expired(created,now,self.c['months']) and 'mxc://'+server+'/'+m not in avatars]
   selected_remote=[(origin,m,f) for origin,m,f,created in remote if expired(created,now,self.c['months']) and 'mxc://'+origin+'/'+m not in avatars]
   removed=0;failures=0
   for media_id in selected[:500]:
    try:
     _,count=await repo.delete_local_media_ids([media_id]);removed+=count
    except Exception:failures+=1;log.warning('One expired local media item will retry')
   for origin,media_id,file_id in selected_remote[:500]:
    try:await self.remove_remote(repo,origin,media_id,file_id);removed+=1
    except Exception:failures+=1;log.warning('One expired remote media item will retry')
   self.deleted_total+=removed
   self.status.update(state='ok' if not failures else 'retry',last_run_ms=now,next_run_ms=now+self.c['interval_seconds']*1000,eligible=len(selected)+len(selected_remote),deleted_last_run=removed,deleted_total=self.deleted_total,failures=failures,error=None)
  except Exception as exc:
   self.status.update(state='error',last_run_ms=now,error=type(exc).__name__)
   log.exception('Media retention check failed')
  finally:self.running=False;self.write_status()
