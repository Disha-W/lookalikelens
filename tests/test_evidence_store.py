"""Unit tests for evidence storage and ingestion (no browser, tmp database)."""
import hashlib
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'worker'))

import ingest_evidence  # noqa: E402
import store  # noqa: E402

SAMPLE_META = {
    "case_sha256": "SAMPLE-SHA",
    "captured_at": 1700000000.0,
    "target_url": "http://fixture:8000/",
    "final_url": "http://fixture:8000/",
    "http_status": 200,
    "title": "Butter Paper Bakes - Best Cheap Cakes",
    "text_excerpt": "Welcome to Butter Paper Bakes",
    "html_sha256": "ab" * 32,
    "screenshot_sha256": None,  # filled per-test
    "status": "ok",
    "error": None,
}


@pytest.fixture
def db(tmp_path):
    conn = store.open_db(str(tmp_path / "test.db"))
    conn.execute(
        "INSERT INTO cases VALUES (?,?,?,?,?,?)",
        ("SAMPLE-SHA", "butterpaperbakes-shop.test", "butterpaperbakes",
         time.time(), "new", "sample"),
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def capture_dir(tmp_path):
    """A fake collector output folder for case SAMPLE-SHA."""
    d = tmp_path / "case-SAMPLE-SHA"
    d.mkdir()
    (d / "screenshot.png").write_bytes(b"fake-png-bytes")
    return d


def write_meta(capture_dir, **overrides):
    meta = dict(SAMPLE_META, **overrides)
    if meta["screenshot_sha256"] is None and (capture_dir / "screenshot.png").exists():
        meta["screenshot_sha256"] = hashlib.sha256(b"fake-png-bytes").hexdigest()
    (capture_dir / "meta.json").write_text(json.dumps(meta))
    return meta


def test_ingest_ok_records_row_with_case_provenance(db, capture_dir):
    write_meta(capture_dir)
    meta, shot_rel, shot_sha = ingest_evidence.load_capture(
        "SAMPLE-SHA", evidence_dir=str(capture_dir.parent))
    evidence_id = store.insert_evidence(db, "SAMPLE-SHA", meta, shot_rel, shot_sha)
    row = db.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
    assert row is not None
    cols = [c[1] for c in db.execute("PRAGMA table_info(evidence)")]
    rec = dict(zip(cols, row))
    assert rec["source"] == "sample"           # inherited from the case
    assert rec["status"] == "ok"
    assert rec["title"] == SAMPLE_META["title"]
    assert rec["screenshot_path"] == os.path.join("case-SAMPLE-SHA", "screenshot.png")
    assert rec["screenshot_sha256"] == hashlib.sha256(b"fake-png-bytes").hexdigest()


def test_ingest_refuses_mismatched_screenshot_hash(db, capture_dir):
    write_meta(capture_dir, screenshot_sha256="ff" * 32)
    with pytest.raises(ValueError, match="does not match"):
        ingest_evidence.load_capture("SAMPLE-SHA", evidence_dir=str(capture_dir.parent))


def test_ingest_refuses_meta_for_other_case(db, capture_dir):
    write_meta(capture_dir, case_sha256="OTHER-SHA")
    with pytest.raises(ValueError, match="different case"):
        ingest_evidence.load_capture("SAMPLE-SHA", evidence_dir=str(capture_dir.parent))


def test_ingest_missing_capture_is_clear_error(db, tmp_path):
    with pytest.raises(FileNotFoundError, match="no capture found"):
        ingest_evidence.load_capture("SAMPLE-SHA", evidence_dir=str(tmp_path))


def test_insert_evidence_rejects_unknown_case(db):
    with pytest.raises(ValueError, match="no such case"):
        store.insert_evidence(db, "NOPE", dict(SAMPLE_META), None, None)


def test_error_capture_is_recorded_not_dropped(db, capture_dir):
    # A blocked/failed capture has no screenshot; the error row still lands.
    (capture_dir / "screenshot.png").unlink()
    write_meta(capture_dir, status="error", error="blocked: resolves to a non-public address",
               screenshot_sha256=None, final_url=None, http_status=None,
               title=None, text_excerpt=None, html_sha256=None)
    meta, shot_rel, shot_sha = ingest_evidence.load_capture(
        "SAMPLE-SHA", evidence_dir=str(capture_dir.parent))
    assert shot_rel is None and shot_sha is None
    evidence_id = store.insert_evidence(db, "SAMPLE-SHA", meta, shot_rel, shot_sha)
    status, error = db.execute(
        "SELECT status, error FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
    assert status == "error"
    assert "blocked" in error


def test_case_info_helpers(db):
    case = store.get_case(db, "SAMPLE-SHA")
    assert case["domain"] == "butterpaperbakes-shop.test"
    assert store.newest_sample_case(db)["sha256"] == "SAMPLE-SHA"
    assert store.get_case(db, "NOPE") is None
