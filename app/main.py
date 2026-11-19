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

# The dashboard's watched domain is the one the owner typed in. The live
# certificate worker still uses its own configured watch terms (the compose
# environment); the two are deliberately separate in this demo build.
WATCH_DOMAIN_KEY = "watch_domain"

# Plain hostname validation: labels of letters/digits/hyphens, at least one
# dot, letters-only TLD. No scheme, path, port or credentials.
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)

# The staged demo's own profile. Sample cases always belong to this name,
# never to whatever domain the owner typed.
DEMO_OWNED_DOMAIN = os.environ.get("OWNED_DOMAINS", "butterpaperbakes.shop").split(",")[0].strip()
DEMO_WATCH_TERM = os.environ.get("WATCH_TERMS", "butterpaperbakes").split(",")[0].strip()


def valid_domain(value):
    """True when value is a plain domain name, nothing else."""
    return bool(_DOMAIN_RE.match((value or "").strip().lower()))


def _get_watch_domain():
    """The domain the owner entered, or None when the database/table is absent."""
    if not os.path.exists(DB_PATH):
        return None
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        return store.get_setting(db, WATCH_DOMAIN_KEY)
    finally:
        db.close()


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
    """Step 1: ask which domain to watch over."""
    return templates.TemplateResponse(request, "start.html", {
        "watch_domain": _get_watch_domain(),
        "error": None,
    })


@app.post("/watch")
async def save_watch_domain(request: Request):
    """Save the typed domain, then show the dashboard."""
    form = await request.form()
    domain = str(form.get("domain", "")).strip().lower()
    if not valid_domain(domain):
        return templates.TemplateResponse(request, "start.html", {
            "watch_domain": domain or _get_watch_domain(),
            "error": "Please enter a plain domain name, like yourstore.com - no http://, slashes or spaces.",
        }, status_code=400)
    db = sqlite3.connect(DB_PATH)
    try:
        store.set_setting(db, WATCH_DOMAIN_KEY, domain)
    finally:
        db.close()
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/dashboard")
def dashboard(request: Request):
    """Step 2: the watched domain and its flagged candidates."""
    watch_domain = _get_watch_domain()
    if not watch_domain:
        return RedirectResponse("/", status_code=303)
    cases = _dashboard_cases()
    # Honesty guard: sample cases belong to the staged demo name. If the
    # owner typed a different domain, say so instead of letting the staged
    # rows read as findings for their domain.
    watching_demo_name = watch_domain == DEMO_OWNED_DOMAIN
    return templates.TemplateResponse(request, "dashboard.html", {
        "watch_domain": watch_domain,
        "watching_demo_name": watching_demo_name,
        "demo_watch_term": DEMO_WATCH_TERM,
        "cases": cases,
        "has_sample_cases": any(c["source"] == "sample" for c in cases),
    })
