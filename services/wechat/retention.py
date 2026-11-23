"""Remove attachment bytes older than two calendar months, never profile DBs.

Paths match the pinned native client's wechat-store/media_files.rs and
wechat-profile/media.rs. Run daily. Does not traverse symlinks.
"""
import calendar,json,os,re,time
from pathlib import Path
from datetime import datetime,timezone

def cutoff(now):
    dt=datetime.fromtimestamp(now,timezone.utc);month=dt.year*12+dt.month-1-2
    year,month=divmod(month,12);month+=1
    return dt.replace(year=year,month=month,day=min(dt.day,calendar.monthrange(year,month)[1])).timestamp()

def clean(root,now=None,dry=False):
    root=Path(root).absolute()
    if root.is_symlink():raise ValueError('native data root must not be a symlink')
    base=root.resolve();threshold=cutoff(time.time() if now is None else now);removed=0;freed=0
    accounts=base/'accounts'
    if accounts.is_symlink() or not accounts.exists():return {'removed':0,'bytes':0}
    for account in accounts.iterdir():
        if account.is_symlink() or not account.is_dir() or not re.fullmatch(r'[A-Za-z0-9_-]+',account.name):continue
        dirs=[account/'agent/media']
        profiles=account/'profile/xwechat_files'
        if profiles.exists() and not profiles.is_symlink():
            for profile in profiles.iterdir():
                if profile.is_dir() and not profile.is_symlink():dirs.extend(profile/('msg/'+name) for name in ['attach','file','video'])
        for directory in dirs:
            if not directory.exists() or directory.is_symlink():continue
            relative=directory.relative_to(base);cursor=base;unsafe=False
            for part in relative.parts:
                cursor=cursor/part
                if cursor.is_symlink():unsafe=True;break
            if unsafe or not directory.resolve().is_relative_to(base):continue
            for parent,subdirs,files in os.walk(directory,followlinks=False):
                subdirs[:]=[n for n in subdirs if not (Path(parent)/n).is_symlink()]
                for name in files:
                    path=Path(parent)/name
                    if path.is_symlink() or not path.is_file():continue
                    if directory==account/'agent/media' and not re.fullmatch(r'[0-9a-f]{32}\.bin',name):continue
                    stat=path.stat()
                    if stat.st_mtime>=threshold:continue
                    if not dry:path.unlink()
                    removed+=1;freed+=stat.st_size
    return {'removed':removed,'bytes':freed}

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    print(json.dumps(clean(args.root,dry=not args.apply)))
