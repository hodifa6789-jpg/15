"""Explicit administrator maintenance for synthetic demo data."""
import argparse
import os
from pathlib import Path
from persistence import SessionStore
from worker_lock import WorkerLock

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['purge-expired','erase-all'])
    parser.add_argument('--confirm-erase',action='store_true')
    args=parser.parse_args()
    if args.action=='erase-all' and not args.confirm_erase:
        parser.error('erase-all requires --confirm-erase; stop the worker first')
    directory=Path(os.getenv('DATA_DIR','data'))
    with WorkerLock(directory/'polling.lock'):
        store=SessionStore(directory/'sessions.sqlite3',int(os.getenv('DATA_RETENTION_HOURS','24')))
        store.erase_all() if args.action=='erase-all' else store.purge()
    print('Completed local database maintenance. Telegram copies and cloud-provider retention are separate.')

if __name__=='__main__': main()
