"""Opt-in, attempt-limited reviews of cases that already have saved evidence.

No website capture, feed consumption or paid retry happens here. A persistent
claim is committed BEFORE a request. If the process dies after that commit,
the attempt remains consumed and is never retried automatically. Operators
must inspect uncertain jobs rather than assume an interrupted call was free.
"""
import os
import sqlite3
import time
from pathlib import Path

from claude_review import DEFAULT_MODEL, call_claude, build_prompt, parse_review
from store import open_db, DEFAULT_DB_PATH, get_case, newest_evidence, insert_review

MAX_PROMPT_BYTES = 16000
POLL_SECONDS = 10


def initialize(db, max_attempts):
    """Keep the original allowance across restarts; changing it needs review."""
    if max_attempts < 1:
        raise ValueError("a positive, owner-approved attempt allowance is required")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS auto_review_allowance (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            max_attempts INTEGER NOT NULL,
            used_attempts INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS auto_review_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_sha256 TEXT NOT NULL,
            evidence_id INTEGER NOT NULL,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at REAL NOT NULL,
            started_at REAL,
            finished_at REAL,
            review_id INTEGER,
            error TEXT,
            UNIQUE(case_sha256, evidence_id, model)
        );
    """)
    db.execute("INSERT OR IGNORE INTO auto_review_allowance VALUES (1, ?, 0)", (max_attempts,))
    db.commit()
    existing = db.execute("SELECT max_attempts FROM auto_review_allowance WHERE id=1").fetchone()[0]
    if existing != max_attempts:
        raise ValueError("saved attempt allowance differs; no automatic reset or increase is allowed")


def enqueue(db):
    """Only newest successful captures, never cases with a prior real review.

    Conservatively skip *all* evidence versions if a real review already
    exists. Legacy reviews do not record evidence IDs, so guessing which
    capture was reviewed could cause an unwanted charge after migration.
    """
    db.execute("""
        INSERT OR IGNORE INTO auto_review_jobs
            (case_sha256, evidence_id, model, status, created_at)
        SELECT c.sha256, e.id, ?, 'pending', ?
        FROM cases c JOIN evidence e ON e.case_sha256=c.sha256
        WHERE e.id=(SELECT e2.id FROM evidence e2 WHERE e2.case_sha256=c.sha256 ORDER BY e2.captured_at DESC,e2.id DESC LIMIT 1)
          AND e.status='ok' AND length(trim(coalesce(e.text_excerpt,'')))>0
          AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.case_sha256=c.sha256
                          AND r.model != 'offline-mock')
    """, (DEFAULT_MODEL, time.time()))
    db.commit()


def claim(db):
    """Atomically consume an allowance and claim one job across processes."""
    db.execute("BEGIN IMMEDIATE")
    try:
        # A manually completed review or a newer/failed capture invalidates
        # a pending job. Never review stale evidence behind the owner's back.
        db.execute("""UPDATE auto_review_jobs SET status='skipped', finished_at=?
            WHERE status='pending' AND (
                EXISTS (SELECT 1 FROM reviews r WHERE r.case_sha256=auto_review_jobs.case_sha256
                        AND r.model != 'offline-mock') OR
                evidence_id != (SELECT e.id FROM evidence e
                                WHERE e.case_sha256=auto_review_jobs.case_sha256
                                ORDER BY e.captured_at DESC,e.id DESC LIMIT 1))""", (time.time(),))
        allowance = db.execute("SELECT max_attempts,used_attempts FROM auto_review_allowance WHERE id=1").fetchone()
        # One in-flight request globally. A crash leaves a running claim
        # unresolved and blocks further spending until the owner checks it.
        in_flight = db.execute("SELECT 1 FROM auto_review_jobs WHERE status='running' LIMIT 1").fetchone()
        if in_flight or not allowance or allowance[1] >= allowance[0]:
            db.commit()
            return None
        row = db.execute("SELECT id,case_sha256,evidence_id FROM auto_review_jobs WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
        if row:
            db.execute("UPDATE auto_review_jobs SET status='running',started_at=? WHERE id=?", (time.time(), row[0]))
            db.execute("UPDATE auto_review_allowance SET used_attempts=used_attempts+1 WHERE id=1")
        db.commit()
        return row
    except BaseException:
        db.rollback()
        raise


def run_one(db, api_key, caller=call_claude):
    """At most one request. Any failure remains final, including timeout."""
    enqueue(db)
    job = claim(db)
    if not job:
        return False
    job_id, sha, evidence_id = job
    try:
        case = get_case(db, sha)
        evidence = newest_evidence(db, sha)
        if not evidence or evidence['id'] != evidence_id or evidence['status'] != 'ok':
            raise ValueError("evidence changed before review")
        # Use the case's matched brand, not mutable global watch settings.
        # The historic schema has no verified owner-domain snapshot per case.
        prompt = build_prompt(case, evidence, [])
        if len(prompt.encode('utf-8')) > MAX_PROMPT_BYTES:
            raise ValueError("prompt exceeds the configured size limit")
        review = parse_review(caller(prompt, api_key, DEFAULT_MODEL))
        review_id = insert_review(db, sha, review, DEFAULT_MODEL)
        db.execute("UPDATE auto_review_jobs SET status='done',finished_at=?,review_id=? WHERE id=?", (time.time(), review_id, job_id))
    except Exception as exc:
        # Do not store API bodies, page content or credentials in error text.
        label = 'Input or response was not usable' if isinstance(exc, ValueError) else 'Request failed or completion is uncertain'
        db.execute("UPDATE auto_review_jobs SET status='failed',finished_at=?,error=? WHERE id=?", (time.time(), label + '; no automatic retry.', job_id))
    db.commit()
    return True


def job_status(db, sha):
    """Read-only helper for existing case pages, including pre-feature DBs."""
    try:
        row = db.execute("SELECT status,error FROM auto_review_jobs WHERE case_sha256=? ORDER BY id DESC LIMIT 1", (sha,)).fetchone()
        allowance = db.execute("SELECT max_attempts,used_attempts FROM auto_review_allowance WHERE id=1").fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    status, error = row
    if status == 'pending' and allowance and allowance[1] >= allowance[0]:
        status = 'limit_reached'
    return {'status': status, 'error': error}


def main():
    if os.environ.get('AUTO_REVIEW_ENABLED', '').lower() != 'true':
        print('Automatic reviews disabled. No API request made.', flush=True)
        return 0
    limit = int(os.environ.get('AUTO_REVIEW_MAX_ATTEMPTS', '0'))
    if limit < 1:
        raise ValueError('approve a finite attempt allowance before enabling reviews')
    # File is mounted only into this service, never copied into the image.
    key_path = os.environ.get('AUTO_REVIEW_KEY_FILE', '/run/secrets/claude_review_key')
    api_key = Path(key_path).read_text().strip()
    if not api_key:
        raise ValueError('review key file is empty; no API request made')
    db = open_db(os.environ.get('DB_PATH', str(DEFAULT_DB_PATH)))
    try:
        initialize(db, limit)
        print('Automatic reviewer enabled with a persisted attempt allowance.', flush=True)
        while True:
            if not run_one(db, api_key):
                time.sleep(POLL_SECONDS)
    finally:
        db.close()


if __name__ == '__main__':
    raise SystemExit(main())
