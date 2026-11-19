"""LookalikeLens web app - the pages the business owner sees.

The owner flow is three pages: enter the domain to watch, see the flagged
candidates on the dashboard, and open one for its details and evidence.
There is no sign-in in this demo build.
"""
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent

# Share the worker's storage helpers (same schema, same provenance rules).
sys.path.insert(0, str(BASE_DIR.parent / "worker"))
import store  # noqa: E402

from app.routes.cases import DB_PATH, router as cases_router  # noqa: E402

app = FastAPI(title="LookalikeLens")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(cases_router)
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# The watched pair the owner typed in: their domain (never flagged) and the
# watch name matched against new certificate records. The live worker reads
# the same settings, so saving here genuinely changes what it watches -
# one watched domain at a time, a new entry replaces the old.
WATCH_DOMAIN_KEY = "watch_domain"
WATCH_TERM_KEY = "watch_term"

# Plain hostname validation: labels of letters/digits/hyphens, at least one
# dot, letters-only TLD. No scheme, path, port or credentials.
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)

# Watch-name validation: a short name of letters/digits/hyphens, 3-63 chars.
_WATCH_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")


def derive_watch_name(domain):
    """Default watch name: the domain without its final label.

    Deliberately simple and VISIBLE - the owner can edit the result. Known
    limit: for example.co.uk it suggests "example.co", which misses
    lookalikes that do not contain "co"; editing the field is the fix.
    """
    labels = (domain or "").strip().lower().split(".")
    return ".".join(labels[:-1]) if len(labels) > 1 else ""

def valid_domain(value):
    """True when value is a plain domain name, nothing else."""
    return bool(_DOMAIN_RE.match((value or "").strip().lower()))


def valid_watch_name(value):
    """True when value is a usable watch name."""
    return bool(_WATCH_NAME_RE.match((value or "").strip().lower()))


def _get_watch_pair():
    """The saved (watch_term, watch_domain), falling back to the compose env.

    Mirrors the worker's precedence: dashboard settings first, then the
    environment, so the dashboard describes what the watcher really does.
    """
    term = domain = None
    if os.path.exists(DB_PATH):
        db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            term, domain = store.load_watch_settings(db)
        finally:
            db.close()
    if not term:
        term = os.environ.get("WATCH_TERMS", "").split(",")[0].strip() or None
        domain = os.environ.get("OWNED_DOMAINS", "").split(",")[0].strip() or None
    return term, domain


def _dashboard_cases():
    """All cases with their latest review verdict, newest first."""
    if not os.path.exists(DB_PATH):
        return []
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        try:
            rows = db.execute(
                "SELECT sha256, domain, matched_term, first_seen_at, source"
                " FROM cases ORDER BY first_seen_at DESC"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        cases = []
        for sha, domain, term, first_seen, source in rows:
            review = store.latest_review(db, sha)
            cases.append({
                "sha256": sha,
                "domain": domain,
                "matched_term": term,
                "first_seen": time.strftime("%b %d, %Y", time.localtime(first_seen)) if first_seen else "unknown",
                "source": source,
                "verdict": review["verdict"] if review else None,
                "review_is_mock": bool(review and review["model"] == "offline-mock"),
            })
        return cases
    finally:
        db.close()


@app.get("/healthz")
def healthz():
    return JSONResponse({"status": "ok"})


@app.get("/")
def start(request: Request):
    """Step 1: ask which domain and name to watch."""
    term, domain = _get_watch_pair()
    return templates.TemplateResponse(request, "start.html", {
        "watch_domain": domain,
        "watch_term": term,
        "suggested_term": None,
        "error": None,
    })


@app.post("/watch")
async def save_watch_domain(request: Request):
    """Save the typed domain and watch name, then show the dashboard.

    This genuinely re-points the running watcher: it reads these settings
    on its next feed message after its check interval, with no restart.
    """
    form = await request.form()
    domain = str(form.get("domain", "")).strip().lower()
    term = str(form.get("watch_term", "")).strip().lower() or derive_watch_name(domain)
    _, old_domain = _get_watch_pair()
    if not valid_domain(domain):
        return templates.TemplateResponse(request, "start.html", {
            "watch_domain": domain or old_domain,
            "watch_term": term,
            "suggested_term": None,
            "error": "Please enter a plain domain name, like yourstore.com - no http://, slashes or spaces.",
        }, status_code=400)
    if not valid_watch_name(term):
        return templates.TemplateResponse(request, "start.html", {
            "watch_domain": domain,
            "watch_term": term,
            "suggested_term": None,
            "error": "The watch name needs 3-63 letters, digits or hyphens - like yourstore.",
        }, status_code=400)
    db = store.open_db(DB_PATH)
    try:
        store.set_setting(db, WATCH_DOMAIN_KEY, domain)
        store.set_setting(db, WATCH_TERM_KEY, term)
    finally:
        db.close()
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/dashboard")
def dashboard(request: Request):
    """Step 2: the watched name and its flagged candidates.

    Cases keep the matched term they were found under, so findings from an
    earlier watch name are never presented as belonging to the current one.
    Staged practice rows stay in their own section regardless of term.
    """
    watch_term, watch_domain = _get_watch_pair()
    if not watch_domain and not watch_term:
        return RedirectResponse("/", status_code=303)
    cases = _dashboard_cases()
    current = [c for c in cases if c["source"] == "live" and c["matched_term"] == watch_term]
    earlier = [c for c in cases if c["source"] == "live" and c["matched_term"] != watch_term]
    staged = [c for c in cases if c["source"] == "sample"]
    return templates.TemplateResponse(request, "dashboard.html", {
        "watch_domain": watch_domain,
        "watch_term": watch_term,
        "current_cases": current,
        "earlier_cases": earlier,
        "staged_cases": staged,
    })
