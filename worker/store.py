"""SQLite storage for certificate events and candidate cases.

Same schema as the canonical live consumer, plus a `source` column on cases
("live" by default, "sample" for replayed fixture data) so sample rows can
never be mistaken for live findings.

Every certificate event lands in ct_events (dedupe by the certificate's
SHA-256, so replays and restarts are safe). An event whose domains match the
watched brand is ALSO saved in cases as a candidate to investigate - never a
confirmed fake. Re-saving the same certificate never duplicates a case.
"""
import json
import os
import sqlite3
import time
from pathlib import Path

from match import find_match

# Default database location: one file at the repo root, so the replay script
# and the web app share the same data no matter which folder you run them
# from. Override with the DB_PATH environment variable (Docker sets this).
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "brand_watch.db"


def open_db(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE IF NOT EXISTS ct_events (
            sha256 TEXT PRIMARY KEY,
            domains TEXT NOT NULL,
            issuer TEXT,
            log_source TEXT,
            cert_index INTEGER,
            seen_at REAL,
            log_submitted_at REAL,
            saved_at REAL NOT NULL
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS cases (
            sha256 TEXT PRIMARY KEY,
            domain TEXT NOT NULL,
            matched_term TEXT NOT NULL,
            first_seen_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            source TEXT NOT NULL DEFAULT 'live'
        )"""
    )
    db.commit()
    return db


def save_event(db, message, watch_terms, owned_domains, source="live"):
    """Save one certstream-shaped message. Returns (is_new_certificate, is_new_case).

    `source` is recorded on any case created: "live" for the real certificate
    stream, "sample" for replayed fixture data.
    """
    if message.get("message_type") != "certificate_update":
        return (False, False)
    data = message.get("data") or {}
    leaf = data.get("leaf_cert") or {}
    sha256 = leaf.get("sha256")
    if not sha256:
        return (False, False)
    src = data.get("source") or {}
    issuer = (leaf.get("issuer") or {}).get("aggregated")
    domains = leaf.get("all_domains") or []
    cur = db.execute(
        "INSERT OR IGNORE INTO ct_events VALUES (?,?,?,?,?,?,?,?)",
        (
            sha256,
            json.dumps(domains),
            issuer,
            src.get("name") or src.get("url"),
            data.get("cert_index"),
            data.get("seen"),
            data.get("submission_timestamp"),
            time.time(),
        ),
    )
    is_new_cert = cur.rowcount == 1
    is_new_case = False
    match = find_match(domains, watch_terms, owned_domains)
    if match:
        domain, term = match
        cur = db.execute(
            "INSERT OR IGNORE INTO cases (sha256, domain, matched_term, first_seen_at, status, source)"
            " VALUES (?,?,?,?,?,?)",
            (sha256, domain, term, time.time(), "new", source),
        )
        is_new_case = cur.rowcount == 1
    db.commit()
    return (is_new_cert, is_new_case)
