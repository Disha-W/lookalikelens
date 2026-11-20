"""Mocked review automation, never makes network calls or reads real keys."""
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'worker'))
import auto_review as ar
import store

REPLY = json.dumps({'verdict': 'unclear', 'suspicious_reasons': [],
                    'innocent_explanations': ['MOCK TEST ONLY'], 'unknowns': []})


@pytest.fixture
def db(tmp_path):
    conn = store.open_db(str(tmp_path / 'test.db'))
    ar.initialize(conn, 2)
    yield conn
    conn.close()


def seed(db, sha='SAMPLE-A', status='ok', text='Labelled fixture text'):
    db.execute("INSERT INTO cases VALUES (?,?,?,?,?,?)", (sha, 'test.invalid', 'test', 1, 'new', 'sample'))
    db.commit()
    evidence(db, sha, status, text)
    return sha


def evidence(db, sha, status='ok', text='Labelled fixture text'):
    return store.insert_evidence(db, sha, {'captured_at': 1, 'target_url': 'http://fixture:8000/',
        'final_url': 'http://fixture:8000/', 'http_status': 200, 'title': 'Test fixture',
        'text_excerpt': text, 'status': status}, None, None)


def used(db):
    return db.execute('SELECT used_attempts FROM auto_review_allowance').fetchone()[0]


def status(db, sha='SAMPLE-A'):
    return ar.job_status(db, sha)['status']


def test_one_call_saved_with_provenance_and_no_repeat(db):
    sha = seed(db)
    calls = []
    def mock(prompt, key, model):
        calls.append((prompt, key, model))
        return REPLY
    assert ar.run_one(db, 'dummy-key', mock)
    assert not ar.run_one(db, 'dummy-key', mock)
    assert len(calls) == 1 and used(db) == 1 and status(db) == 'done'
    assert 'STAGED PRACTICE CASE' in calls[0][0]
    assert "owner's real domain(s): unknown" in calls[0][0]
    review = store.latest_review(db, sha)
    assert review['source'] == 'sample' and review['model'] == ar.DEFAULT_MODEL


@pytest.mark.parametrize('capture_status,text', [('error', 'x'), ('ok', ''), ('ok', '   ')])
def test_no_call_for_unready_evidence(db, capture_status, text):
    seed(db, status=capture_status, text=text)
    assert not ar.run_one(db, 'dummy', lambda *a: pytest.fail('must not call'))
    assert used(db) == 0


def test_no_evidence(db):
    sha = seed(db)
    db.execute('DELETE FROM evidence'); db.commit()
    assert not ar.run_one(db, 'dummy', lambda *a: pytest.fail('must not call'))


def test_existing_real_review_skipped_even_new_capture(db):
    sha = seed(db)
    store.insert_review(db, sha, json.loads(REPLY), 'claude-haiku-4-5')
    evidence(db, sha)
    assert not ar.run_one(db, 'dummy', lambda *a: pytest.fail('must not call'))
    assert used(db) == 0


def test_mock_review_does_not_block_real_eligibility(db):
    sha = seed(db)
    store.insert_review(db, sha, json.loads(REPLY), 'offline-mock')
    assert ar.run_one(db, 'dummy', lambda *a: REPLY)
    assert used(db) == 1


@pytest.mark.parametrize('failure', [TimeoutError('dummy-secret'), RuntimeError('dummy-secret'), ValueError('dummy-secret')])
def test_failures_consume_attempt_and_never_retry(db, failure):
    seed(db)
    def fail(*args):
        raise failure
    assert ar.run_one(db, 'dummy', fail)
    assert status(db) == 'failed' and used(db) == 1
    assert 'dummy-secret' not in ar.job_status(db, 'SAMPLE-A')['error']
    assert not ar.run_one(db, 'dummy', lambda *a: pytest.fail('no retry'))
    assert not db.execute('SELECT 1 FROM reviews').fetchone()


def test_malformed_reply_not_retried(db):
    seed(db)
    assert ar.run_one(db, 'dummy', lambda *a: 'not json')
    assert status(db) == 'failed'
    assert not ar.run_one(db, 'dummy', lambda *a: pytest.fail('no retry'))


def test_budget_persists_across_restart_and_cannot_be_raised(db):
    for name in ['SAMPLE-A', 'SAMPLE-B', 'SAMPLE-C']:
        seed(db, name)
    assert ar.run_one(db, 'dummy', lambda *a: REPLY)
    assert ar.run_one(db, 'dummy', lambda *a: REPLY)
    assert not ar.run_one(db, 'dummy', lambda *a: pytest.fail('no budget'))
    ar.initialize(db, 2)
    assert used(db) == 2 and status(db, 'SAMPLE-C') == 'limit_reached'
    with pytest.raises(ValueError):
        ar.initialize(db, 3)


def test_claim_crash_does_not_retry_or_allow_parallel_spend(db):
    seed(db); seed(db, 'SAMPLE-B'); ar.enqueue(db)
    assert ar.claim(db)
    assert ar.claim(db) is None
    assert not ar.run_one(db, 'dummy', lambda *a: pytest.fail('uncertain request blocks spend'))
    assert used(db) == 1 and status(db) == 'running'


def test_atomic_competing_claims(db):
    seed(db); seed(db, 'SAMPLE-B'); ar.enqueue(db)
    path = db.execute('PRAGMA database_list').fetchone()[2]
    def contender(_):
        conn = sqlite3.connect(path, timeout=3)
        try:
            return ar.claim(conn)
        finally:
            conn.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(contender, range(2)))
    assert sum(j is not None for j in jobs) == 1 and used(db) == 1


def test_stale_pending_job_skipped(db):
    sha = seed(db); ar.enqueue(db)
    evidence(db, sha, status='error')
    assert not ar.run_one(db, 'dummy', lambda *a: pytest.fail('stale evidence'))
    assert status(db) == 'skipped' and used(db) == 0


def test_prompt_limit_fails_closed(db):
    seed(db, text='x')
    db.execute('UPDATE evidence SET title=?', ('x' * 20000,)); db.commit()
    assert ar.run_one(db, 'dummy', lambda *a: pytest.fail('oversized prompt'))
    assert status(db) == 'failed' and used(db) == 1


def test_read_only_status_on_legacy_database(tmp_path):
    path = str(tmp_path / 'legacy.db')
    conn = store.open_db(path); conn.close()
    conn = sqlite3.connect('file:' + path + '?mode=ro', uri=True)
    try:
        assert ar.job_status(conn, 'absent') is None
    finally:
        conn.close()


def test_disabled_by_default_never_reads_key(monkeypatch):
    monkeypatch.delenv('AUTO_REVIEW_ENABLED', raising=False)
    monkeypatch.setattr(Path, 'read_text', lambda *a, **k: pytest.fail('no key read'))
    assert ar.main() == 0


def test_zero_allowance_refuses_before_key(monkeypatch):
    monkeypatch.setenv('AUTO_REVIEW_ENABLED', 'true')
    monkeypatch.setenv('AUTO_REVIEW_MAX_ATTEMPTS', '0')
    monkeypatch.setattr(Path, 'read_text', lambda *a, **k: pytest.fail('no key read'))
    with pytest.raises(ValueError):
        ar.main()


def test_capture_timestamp_order_matches_case_page(db):
    sha = seed(db)
    db.execute('UPDATE evidence SET captured_at=100'); db.commit()
    evidence(db, sha, status='error')  # Inserted later, but captured earlier.
    assert ar.run_one(db, 'dummy', lambda *a: REPLY)
    assert status(db) == 'done'
