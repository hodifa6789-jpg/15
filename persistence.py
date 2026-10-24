"""SQLite transactions, optimistic revisions and durable update/outbox records.

Designed for a single polling process on one persistent volume, not multi-replica.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time

class ConcurrentWrite(RuntimeError): pass

class SessionStore:
    def __init__(self,path: Path | str,retention_hours: int=24):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.retention_seconds=retention_hours*3600
        if os.name!='nt': self.path.parent.chmod(0o700)
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sessions(
                    user_id INTEGER PRIMARY KEY, payload TEXT NOT NULL, revision INTEGER NOT NULL,
                    updated_at REAL NOT NULL, last_message_id INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS updates(
                    user_id INTEGER NOT NULL, update_id INTEGER NOT NULL, result TEXT,
                    delivered INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                    PRIMARY KEY(user_id,update_id));
                CREATE INDEX IF NOT EXISTS update_expiry ON updates(created_at);
                PRAGMA user_version=2;
            ''')
        if os.name!='nt': self.path.chmod(0o600)
        self.purge()

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        db.execute('PRAGMA secure_delete=ON')
        try:
            with db: yield db
        finally:
            db.close()

    def load(self,user_id):
        with self.connection() as db:
            row=db.execute('SELECT * FROM sessions WHERE user_id=? AND updated_at>?',
                           (user_id,time.time()-self.retention_seconds)).fetchone()
        if not row: return None,0,0
        return json.loads(row['payload']),row['revision'],row['last_message_id']

    def seen(self,user_id,update_id):
        with self.connection() as db:
            row=db.execute('SELECT result,delivered FROM updates WHERE user_id=? AND update_id=?',
                           (user_id,update_id)).fetchone()
        if row is None: return None
        return {'delivered':bool(row['delivered']),'result':json.loads(row['result']) if row['result'] else None}

    def commit(self,user_id,update_id,payload,result,expected_revision,message_id=0,erase=False,clear_history=False):
        now=time.time()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT revision FROM sessions WHERE user_id=?',(user_id,)).fetchone()
            actual=row['revision'] if row else 0
            if actual!=expected_revision: raise ConcurrentWrite('Session revision changed')
            if clear_history:
                db.execute('UPDATE updates SET delivered=1,result=NULL WHERE user_id=?',(user_id,))
            if erase:
                db.execute('DELETE FROM sessions WHERE user_id=?',(user_id,))
                db.execute('UPDATE updates SET delivered=1,result=NULL WHERE user_id=?',(user_id,))
            else:
                db.execute('INSERT INTO sessions VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET '
                    'payload=excluded.payload,revision=excluded.revision,updated_at=excluded.updated_at,last_message_id=excluded.last_message_id',
                    (user_id,json.dumps(payload,ensure_ascii=False),actual+1,now,message_id))
            db.execute('INSERT INTO updates VALUES(?,?,?,0,?)',
                       (user_id,update_id,json.dumps(result,ensure_ascii=False),now))

    def delivered(self,user_id,update_id):
        with self.connection() as db:
            # Successful delivery retains only a dedup tombstone, not another complaint copy.
            db.execute('UPDATE updates SET delivered=1,result=NULL WHERE user_id=? AND update_id=?',(user_id,update_id))

    def pending(self,user_id):
        with self.connection() as db:
            rows=db.execute('SELECT update_id,result FROM updates WHERE user_id=? AND delivered=0 ORDER BY update_id',(user_id,)).fetchall()
        return [(row['update_id'],json.loads(row['result'])) for row in rows]

    def purge(self):
        cutoff=time.time()-self.retention_seconds
        with self.connection() as db:
            db.execute('DELETE FROM sessions WHERE updated_at<?',(cutoff,))
            db.execute('DELETE FROM updates WHERE created_at<?',(cutoff,))
        with self.connection() as db:
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')

    def erase_all(self):
        with self.connection() as db:
            db.execute('DELETE FROM sessions')
            db.execute('DELETE FROM updates')
        with self.connection() as db:
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.execute('VACUUM')
