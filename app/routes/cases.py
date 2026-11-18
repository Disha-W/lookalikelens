"""Read-only JSON view of candidate cases saved by the watcher.

Each row is a CANDIDATE found in certificate data - a lead for a human to
review, never a confirmed fake and never acted on automatically. Rows carry
their source ("live" or "sample") so sample data is always recognizable.
"""
import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

# Same default database as worker/store.py: one file at the repo root, so the
# app reads what the replay script wrote regardless of working directory.
# Override with the DB_PATH environment variable (Docker sets this).
DB_PATH = os.environ.get("DB_PATH") or str(Path(__file__).resolve().parents[2] / "brand_watch.db")


@router.get("/cases")
def list_cases():
    if not os.path.exists(DB_PATH):
        return {"cases": [], "note": "no database yet - run worker/preview_feed.py with sample data first"}
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = db.execute(
            "SELECT sha256, domain, matched_term, first_seen_at, status, source"
            " FROM cases ORDER BY first_seen_at DESC"
        ).fetchall()
    finally:
        db.close()
    return {
        "cases": [
            {"sha256": s, "domain": d, "matched_term": t, "first_seen_at": f, "status": st, "source": src}
            for s, d, t, f, st, src in rows
        ]
    }
