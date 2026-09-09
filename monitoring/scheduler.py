"""Once per local calendar day. Run as a service on an always-on machine."""
import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import DEFAULT_CONFIG, ROOT, load_config
from .store import connect, now
from .runner import run


def due(local, at, last_date):
    return local.strftime('%H:%M') >= at and last_date != local.date().isoformat()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',default=str(DEFAULT_CONFIG))
    args=parser.parse_args()
    root=Path(os.environ.get('QA_DATA_DIR',ROOT/'monitor-data'))
    while True:
        db=connect(root)
        try:
            cfg=load_config(args.config)
            local=datetime.now(ZoneInfo(cfg['timezone']))
            db.execute("INSERT OR IGNORE INTO scheduler VALUES(1,?,?,?)",(now(),'', 'Waiting for daily run'))
            db.execute("UPDATE scheduler SET heartbeat=? WHERE id=1",(now(),))
            db.commit()
            # Claim the date transactionally, so two accidental schedulers cannot
            # launch the same daily run. One attempt per date, including failures.
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM scheduler WHERE id=1').fetchone()
            should_run=due(local,cfg['daily_at'],row['last_date'])
            if should_run:
                db.execute("UPDATE scheduler SET last_date=?,message=? WHERE id=1",(local.date().isoformat(),'Daily run starting'))
            db.commit()
            if should_run:
                try:
                    result=run(args.config,root)
                    message=f"Daily run {result['run_id']}: {result['status']}"
                except Exception as exc:
                    message=f'Daily run blocked: {exc}'
                db.execute('UPDATE scheduler SET heartbeat=?,message=? WHERE id=1',(now(),message[:1500]))
                db.commit()
                print(message,flush=True)
        except Exception as exc:
            db.rollback()
            print(f'Scheduler configuration error: {exc}',flush=True)
        finally:
            db.close()
        time.sleep(30)


if __name__=='__main__':
    main()
