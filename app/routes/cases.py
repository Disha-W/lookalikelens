"""Case views for the owner: a JSON list, one HTML page per case, and its
captured screenshot.

Each case is a CANDIDATE found in certificate data - a lead for a human to
review, never a confirmed fake and never acted on automatically. Rows carry
their source ("live" or "sample") so sample data is always recognizable.
"""
import os
import sqlite3
import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates

# Share the worker's storage helpers (same schema, same provenance rules).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "worker"))
import evidence_path  # noqa: E402
import store  # noqa: E402

router = APIRouter()

# Same default database as worker/store.py: one file at the repo root, so the
# app reads what the replay script wrote regardless of working directory.
# Override with the DB_PATH environment variable (Docker sets this).
DB_PATH = os.environ.get("DB_PATH") or str(Path(__file__).resolve().parents[2] / "brand_watch.db")

# Where captured evidence lives (read-only mount in Docker). The database
# stores paths relative to this folder.
EVIDENCE_DIR = os.environ.get("EVIDENCE_DIR") or str(Path(__file__).resolve().parents[2] / "evidence")

APP_DIR = Path(__file__).resolve().parents[1]
templates = Jinja2Templates(directory=APP_DIR / "templates")


def _read_only_db():
    if not os.path.exists(DB_PATH):
        raise HTTPException(status_code=404, detail="no database yet - replay the sample events first")
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def _when(timestamp):
    return time.strftime("%b %d, %Y, %I:%M %p", time.localtime(timestamp)) if timestamp else "unknown"


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


@router.get("/cases/{case_sha256}")
def case_page(request: Request, case_sha256: str):
    """The owner-facing page for one case: details, Claude review, evidence."""
    db = _read_only_db()
    try:
        case = store.get_case(db, case_sha256)
        if case is None:
            raise HTTPException(status_code=404, detail="no such case")
        review = store.latest_review(db, case_sha256)
        evidence = store.list_evidence(db, case_sha256)
    finally:
        db.close()
    return templates.TemplateResponse(request, "case.html", {
        "case": case,
        "case_when": _when(case["first_seen_at"]),
        "review": review,
        "review_when": _when(review["created_at"]) if review else None,
        "review_is_mock": bool(review and review["model"] == "offline-mock"),
        "evidence": evidence,
    })


@router.get("/cases/{case_sha256}/evidence/{evidence_id}/screenshot")
def evidence_screenshot(case_sha256: str, evidence_id: int):
    """Serve one capture's screenshot from the evidence folder, read-only."""
    db = _read_only_db()
    try:
        row = db.execute(
            "SELECT screenshot_path FROM evidence WHERE id = ? AND case_sha256 = ?",
            (evidence_id, case_sha256),
        ).fetchone()
    finally:
        db.close()
    if row is None:
        raise HTTPException(status_code=404, detail="no such evidence")
    full = evidence_path.safe_screenshot_path(EVIDENCE_DIR, row[0])
    if full is None:
        raise HTTPException(status_code=404, detail="screenshot file not available")
    return FileResponse(full, media_type="image/png")
