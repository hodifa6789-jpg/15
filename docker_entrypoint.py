"""Fix ownership of the mounted volume, then permanently drop root privileges."""
import os
from pathlib import Path
import runpy

def main():
    if os.name=='posix' and os.getuid()==0:
        data=Path(os.getenv('DATA_DIR','/data'))
        if data!=Path('/data') or data.is_symlink():
            raise RuntimeError('Container DATA_DIR must be /data and not a symlink')
        data.mkdir(exist_ok=True)
        os.chown(data,10001,10001)
        os.chmod(data,0o700)
        for name in ('web.sqlite3','web.sqlite3-wal','web.sqlite3-shm','web.lock'):
            path=data/name
            if path.is_symlink(): raise RuntimeError('Unexpected data symlink')
            if path.exists(): os.chown(path,10001,10001)
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
    os.umask(0o077)
    runpy.run_module('web_app',run_name='__main__')

if __name__=='__main__': main()
