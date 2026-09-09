import contextlib
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "monitor.sqlite3", timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript('''
    CREATE TABLE IF NOT EXISTS runs (
      id TEXT PRIMARY KEY, started TEXT, finished TEXT, status TEXT, summary TEXT);
    CREATE TABLE IF NOT EXISTS results (
      id INTEGER PRIMARY KEY, run_id TEXT, campaign TEXT, brand TEXT, device TEXT,
      status TEXT, report TEXT);
    CREATE TABLE IF NOT EXISTS issues (
      fingerprint TEXT PRIMARY KEY, campaign TEXT, brand TEXT, device TEXT,
      check_id TEXT, title TEXT, severity TEXT, status TEXT, first_seen TEXT,
      last_seen TEXT, occurrences INTEGER, evidence TEXT);
    CREATE TABLE IF NOT EXISTS scheduler (id INTEGER PRIMARY KEY CHECK(id=1),
      heartbeat TEXT, last_date TEXT, message TEXT);
    ''')
    return db


@contextlib.contextmanager
def run_lock(root):
    """OS lock is released even when a process crashes. Shared by UI and scheduler."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    f = open(root / "runner.lock", "a+b")
    try:
        if __import__('os').name == "nt":
            import msvcrt
            f.seek(0)
            if not f.read(1):
                f.write(b"0"); f.flush()
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise RuntimeError("Another monitoring run is already in progress.")
    try:
        yield
    finally:
        f.close()


def save_result(db, run_id, report):
    db.execute("INSERT INTO results(run_id,campaign,brand,device,status,report) VALUES(?,?,?,?,?,?)",
               (run_id, report['campaign'], report['brand'], report['device'], report['status'], json.dumps(report)))
    # Resolve ONLY checks that were actually executed and passed. A blocked or skipped
    # check never clears an existing issue.
    for check in report['checks']:
        key = hashlib.sha256(f"{report['campaign']}|{report['device']}|{check['id']}".encode()).hexdigest()[:20]
        if check['status'] in ('failed', 'blocked', 'review', 'flaky'):
            old = db.execute("SELECT * FROM issues WHERE fingerprint=?", (key,)).fetchone()
            evidence = json.dumps({**check, 'run_id': run_id})
            if old:
                db.execute("UPDATE issues SET status='Open',last_seen=?,occurrences=occurrences+1,evidence=?,title=?,severity=? WHERE fingerprint=?",
                           (now(), evidence, check['title'], check['severity'], key))
            else:
                db.execute("INSERT INTO issues VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                    key, report['campaign'], report['brand'], report['device'], check['id'],
                    check['title'], check['severity'], 'Open', now(), now(), 1, evidence))
        elif check['status'] == 'passed':
            db.execute("UPDATE issues SET status='Resolved' WHERE fingerprint=?", (key,))
    db.commit()
