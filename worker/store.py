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
import re
import sqlite3
import time
from pathlib import Path

from match import find_match

# The certstream sidecar emits sha256 as colon-separated uppercase hex, 32
# bytes ("AB:CD:...:FF", 95 chars). Provenance between sample and live rows
# is enforced at write time by disjoint formats: the live path accepts only
# a full-length sha256 (colon-separated or plain 64-char hex), and the
# sample path refuses anything in that real format.
_LIVE_SHA256 = re.compile(r"^(([0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2}|[0-9A-Fa-f]{64})$")

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
    # Evidence captured for a case by the isolated collector. The collector
    # itself never writes here: it drops artifacts in an evidence folder and
    # a trusted ingestion step records them. Screenshots live as files;
    # this table holds their metadata only.
    db.execute(
        """CREATE TABLE IF NOT EXISTS evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_sha256 TEXT NOT NULL REFERENCES cases(sha256),
            captured_at REAL NOT NULL,
            target_url TEXT NOT NULL,
            final_url TEXT,
            http_status INTEGER,
            title TEXT,
            text_excerpt TEXT,
            html_sha256 TEXT,
            screenshot_path TEXT,
            screenshot_sha256 TEXT,
            status TEXT NOT NULL,
            error TEXT,
            source TEXT NOT NULL
        )"""
    )
    db.commit()
    return db


def get_case(db, case_sha256):
    """Return one case row as a dict, or None."""
    row = db.execute(
        "SELECT sha256, domain, matched_term, first_seen_at, status, source"
        " FROM cases WHERE sha256 = ?",
        (case_sha256,),
    ).fetchone()
    if row is None:
        return None
    keys = ("sha256", "domain", "matched_term", "first_seen_at", "status", "source")
    return dict(zip(keys, row))


def newest_sample_case(db):
    """Return the newest sample-marked case row as a dict, or None."""
    row = db.execute(
        "SELECT sha256, domain, matched_term, first_seen_at, status, source"
        " FROM cases WHERE source = 'sample' ORDER BY first_seen_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    keys = ("sha256", "domain", "matched_term", "first_seen_at", "status", "source")
    return dict(zip(keys, row))


def insert_evidence(db, case_sha256, meta, screenshot_path, screenshot_sha256):
    """Record one collector capture against a case. Returns the evidence id.

    `meta` is the collector's meta.json (untrusted input: only the fields we
    know are read, and the capture's provenance is always inherited from the
    case, never taken from the file).
    """
    case = get_case(db, case_sha256)
    if case is None:
        raise ValueError(f"no such case: {case_sha256}")
    cur = db.execute(
        """INSERT INTO evidence (
            case_sha256, captured_at, target_url, final_url, http_status,
            title, text_excerpt, html_sha256, screenshot_path,
            screenshot_sha256, status, error, source
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            case_sha256,
            float(meta.get("captured_at") or time.time()),
            str(meta.get("target_url") or ""),
            meta.get("final_url"),
            meta.get("http_status"),
            meta.get("title"),
            meta.get("text_excerpt"),
            meta.get("html_sha256"),
            screenshot_path,
            screenshot_sha256,
            str(meta.get("status") or "error"),
            meta.get("error"),
            case["source"],
        ),
    )
    db.commit()
    return cur.lastrowid


def prune_ct_events(db, keep):
    """Keep only the newest `keep` certificate events (0 = keep everything).

    Certificates referenced by a case are never pruned: a candidate's
    underlying evidence must survive. Cases themselves are never deleted here.
    """
    if not keep:
        return 0
    cur = db.execute(
        """DELETE FROM ct_events
           WHERE sha256 NOT IN (SELECT sha256 FROM cases)
             AND sha256 NOT IN (SELECT sha256 FROM ct_events ORDER BY saved_at DESC LIMIT ?)""",
        (keep,),
    )
    db.commit()
    return cur.rowcount


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
    if source == "live" and not _LIVE_SHA256.match(sha256):
        print(f"skipped event: hash not in certificate sha256 format: {sha256!r}", flush=True)
        return (False, False)
    if source == "sample" and _LIVE_SHA256.match(sha256):
        raise ValueError(f"sample data must not use a realistic certificate hash: {sha256!r}")
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
