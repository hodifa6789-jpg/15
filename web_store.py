"""Anonymous browser sessions: hashed bearer cookies, CAS and bounded replay cache."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

class StaleSession(Exception): pass

class WebStore:
    def __init__(self,path,retention_hours=24):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.ttl=retention_hours*3600
        if os.name!='nt': self.path.parent.chmod(0o700)
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS web_sessions(id TEXT PRIMARY KEY,payload TEXT NOT NULL,revision INTEGER NOT NULL,updated REAL NOT NULL)')
        if os.name!='nt': self.path.chmod(0o600)
        self.purge()

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.execute('PRAGMA synchronous=FULL')
        db.execute('PRAGMA secure_delete=ON')
        try:
            with db: yield db
        finally: db.close()

    @staticmethod
    def identifier(token): return hashlib.sha256(token.encode()).hexdigest()

    def create(self):
        token=secrets.token_urlsafe(32)
        state={'flow':None,'history':[],'revision':0,'csrf':secrets.token_urlsafe(24),'requests':{},'last_error':False}
        with self.db() as db:
            db.execute('INSERT INTO web_sessions VALUES(?,?,0,?)',(self.identifier(token),json.dumps(state),time.time()))
        return token,state

    def load(self,token):
        if not token or len(token)>100: return None
        with self.db() as db:
            row=db.execute('SELECT payload FROM web_sessions WHERE id=? AND updated>?',(self.identifier(token),time.time()-self.ttl)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self,token,state,revision):
        state['revision']=revision+1
        with self.db() as db:
            changed=db.execute('UPDATE web_sessions SET payload=?,revision=?,updated=? WHERE id=? AND revision=?',
                (json.dumps(state,ensure_ascii=False),revision+1,time.time(),self.identifier(token),revision)).rowcount
        if not changed: raise StaleSession()

    def delete(self,token):
        with self.db() as db: db.execute('DELETE FROM web_sessions WHERE id=?',(self.identifier(token),))

    def purge(self):
        with self.db() as db: db.execute('DELETE FROM web_sessions WHERE updated<?',(time.time()-self.ttl,))
        with self.db() as db: db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
