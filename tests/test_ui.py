"""Unit tests for the owner UI routes, settings storage and domain validation.

Runs against a temporary seeded database; no Docker, browser or network.
Note: the seeded database is shared by this module's tests, so tests that
need the "no domain saved yet" state must run before any POST /watch test.
"""
import os
import sys
import tempfile
import time

import pytest

REPO_ROOT = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, 'worker'))

import store  # noqa: E402

# Point the app at a temporary seeded database BEFORE importing it: the
# cases router reads DB_PATH/EVIDENCE_DIR from the environment at import.
_TMP = tempfile.mkdtemp(prefix="ll-ui-test-")
os.environ["DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["EVIDENCE_DIR"] = _TMP

CASE_SHA = "SAMPLELOOKALIKE00000000000000000000000000000000000000000000000001"

_db = store.open_db(os.environ["DB_PATH"])
_db.execute(
    "INSERT INTO cases VALUES (?,?,?,?,?,?)",
    (CASE_SHA, "butterpaperbakes-shop.test", "butterpaperbakes",
     time.time(), "new", "sample"),
)
store.insert_review(_db, CASE_SHA, {
    "verdict": "suspicious",
    "suspicious_reasons": ["Practice signal: gift-card payment push."],
    "innocent_explanations": ["An unrelated business can share a similar name."],
    "unknowns": ["Whether the page copies the real shop's branding."],
}, "offline-mock")
_db.close()

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app, derive_watch_name, valid_domain, valid_watch_name  # noqa: E402

client = TestClient(app)


@pytest.mark.parametrize("value", [
    "butterpaperbakes.shop",
    "sub.domain.co.uk",
    "my-store123.com",
])
def test_valid_domain_accepts_plain_domains(value):
    assert valid_domain(value)


@pytest.mark.parametrize("value", [
    "",
    "not a domain",
    "http://butterpaperbakes.shop",
    "butterpaperbakes.shop/path",
    "butterpaperbakes.shop:8080",
    "-bad.com",
    "bad..com",
    "localhost",
])
def test_valid_domain_rejects_everything_else(value):
    assert not valid_domain(value)


@pytest.mark.parametrize("value,expected", [
    ("butterpaperbakes.shop", "butterpaperbakes"),
    ("shop.example.co.uk", "shop.example.co"),  # documented naive limit, owner edits
    ("a-b.example", "a-b"),
])
def test_derive_watch_name(value, expected):
    assert derive_watch_name(value) == expected


@pytest.mark.parametrize("value", ["butterpaperbakes", "my-store123", "abc"])
def test_valid_watch_name_accepts(value):
    assert valid_watch_name(value)


@pytest.mark.parametrize("value", ["", "ab", "has space", "dots.not-allowed", "-bad", "x" * 64])
def test_valid_watch_name_rejects(value):
    assert not valid_watch_name(value)


def test_settings_roundtrip(tmp_path):
    db = store.open_db(str(tmp_path / "s.db"))
    assert store.get_setting(db, "watch_domain") is None  # no table yet
    store.set_setting(db, "watch_domain", "a.shop")
    assert store.get_setting(db, "watch_domain") == "a.shop"
    store.set_setting(db, "watch_domain", "b.shop")
    assert store.get_setting(db, "watch_domain") == "b.shop"
    db.close()


def test_start_page_renders():
    response = client.get("/")
    assert response.status_code == 200
    assert "Which website is yours?" in response.text


def test_dashboard_redirects_until_a_domain_is_saved():
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_watch_rejects_bad_domain():
    response = client.post("/watch", data={"domain": "http://nope"})
    assert response.status_code == 400
    assert "plain domain name" in response.text


def test_watch_saves_domain_and_dashboard_lists_sample_case():
    response = client.post("/watch", data={"domain": "ButterPaperBakes.SHOP"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"

    db = store.open_db(os.environ["DB_PATH"])
    assert store.get_setting(db, "watch_domain") == "butterpaperbakes.shop"
    db.close()

    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "butterpaperbakes.shop" in page.text
    assert "butterpaperbakes-shop.test" in page.text
    assert "sample" in page.text
    assert "verdict-suspicious" in page.text
    # Demo profile matches, so no mismatch note.
    assert "matched the staged demo name" not in page.text


def test_dashboard_scopes_staged_rows_when_domain_differs():
    client.post("/watch", data={"domain": "other-bakery.com", "watch_term": "other-bakery"})
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "Staged practice cases" in page.text
    assert "No stored findings for this watch name" in page.text


def test_case_page_renders_with_mock_provenance():
    page = client.get(f"/cases/{CASE_SHA}")
    assert page.status_code == 200
    assert "butterpaperbakes-shop.test" in page.text
    assert "Offline practice review" in page.text
    assert "sample case" in page.text
    assert "gift-card payment push" in page.text


def test_missing_screenshot_file_shows_unavailable_note():
    """A recorded screenshot whose file is gone gets an explicit note, not a broken image."""
    db = store.open_db(os.environ["DB_PATH"])
    db.execute(
        "INSERT INTO evidence (case_sha256, captured_at, target_url, status,"
        " source, screenshot_path)"
        " VALUES (?, 0, 'http://fixture:8000/', 'ok', 'sample', 'gone/shot.png')",
        (CASE_SHA,),
    )
    db.commit()
    db.close()
    page = client.get(f"/cases/{CASE_SHA}")
    assert page.status_code == 200
    assert "not available on this server" in page.text
    assert '<img class="shot"' not in page.text


def test_start_page_explains_watch_name_and_no_site_visits():
    """The entry page says what is watched and that the site is never visited."""
    page = client.get("/")
    assert page.status_code == 200
    assert "never visits your website" in page.text
    assert "Business or brand name" in page.text
    assert "example.co.uk" in page.text  # the derivation limit is disclosed


def test_watch_saves_domain_and_term_settings():
    client.post("/watch", data={"domain": "myrealshop.example", "watch_term": "myrealshop"})
    db = store.open_db(os.environ["DB_PATH"])
    assert store.load_watch_settings(db) == ("myrealshop", "myrealshop.example")
    db.close()


def test_watch_derives_term_when_left_blank():
    client.post("/watch", data={"domain": "butterpaperbakes.shop", "watch_term": ""})
    db = store.open_db(os.environ["DB_PATH"])
    assert store.load_watch_settings(db) == ("butterpaperbakes", "butterpaperbakes.shop")
    db.close()


def test_watch_rejects_bad_watch_name():
    response = client.post("/watch", data={"domain": "butterpaperbakes.shop", "watch_term": "ab"})
    assert response.status_code == 400
    assert "watch name" in response.text


def test_dashboard_states_owner_action_copy():
    client.post("/watch", data={"domain": "butterpaperbakes.shop"})
    page = client.get("/dashboard")
    assert page.status_code == 200
    flat = " ".join(page.text.split())
    assert "Review potential lookalikes of" in flat
    assert "butterpaperbakes.shop" in flat
    assert "Open a case to see the available evidence" in flat
    assert "decide whether it needs attention" in flat


CASE2_SHA = "SAMPLEMULTICAPTURE00000000000000000000000000000000000000000000000002"
CASE3_SHA = "SAMPLESINGLECAPTURE0000000000000000000000000000000000000000000000003"


_PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _seed_case_with_captures(sha, captures, with_screenshots=False):
    import base64
    db = store.open_db(os.environ["DB_PATH"])
    db.execute(
        "INSERT INTO cases VALUES (?,?,?,?,?,?)",
        (sha, "multi-capture.test", "butterpaperbakes",
         time.time(), "new", "sample"),
    )
    shot_path = None
    if with_screenshots:
        shot_path = f"case-{sha}/shot.png"
        full = os.path.join(os.environ["EVIDENCE_DIR"], shot_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(base64.b64decode(_PNG_1PX))
    for i in range(captures):
        store.insert_evidence(db, sha, {
            "captured_at": time.time() + i,
            "target_url": "http://fixture:8000/",
            "final_url": "http://fixture:8000/",
            "http_status": 200,
            "title": "Butter Paper Bakes - Best Cheap Cakes Online",
            "text_excerpt": "GRAND OPENING",
            "status": "ok",
        }, shot_path, None)
    db.close()


def test_case_page_folds_older_captures():
    _seed_case_with_captures(CASE2_SHA, 2, with_screenshots=True)
    page = client.get(f"/cases/{CASE2_SHA}")
    assert page.status_code == 200
    assert "Previous captures (1)" in page.text
    # latest capture still rendered openly, older one folded away
    assert page.text.count('class="evidence"') == 2
    assert "<details" in page.text


def test_case_page_single_capture_has_no_fold():
    _seed_case_with_captures(CASE3_SHA, 1)
    page = client.get(f"/cases/{CASE3_SHA}")
    assert page.status_code == 200
    assert "Previous captures" not in page.text
    assert page.text.count('class="evidence"') == 1


CASE4_SHA = "SAMPLECHRONOLOGY000000000000000000000000000000000000000000000000004"


def test_latest_capture_follows_capture_time_not_insert_order():
    db = store.open_db(os.environ["DB_PATH"])
    db.execute(
        "INSERT INTO cases VALUES (?,?,?,?,?,?)",
        (CASE4_SHA, "chronology.test", "butterpaperbakes",
         time.time(), "new", "sample"),
    )
    # inserted FIRST but with the LATER capture time: must be shown openly
    store.insert_evidence(db, CASE4_SHA, {
        "captured_at": time.time() + 3600,
        "target_url": "http://fixture:8000/later",
        "final_url": "http://fixture:8000/later",
        "http_status": 200, "title": "Later capture", "status": "ok",
    }, None, None)
    # inserted SECOND but with an EARLIER capture time: must be folded
    store.insert_evidence(db, CASE4_SHA, {
        "captured_at": time.time(),
        "target_url": "http://fixture:8000/earlier",
        "final_url": "http://fixture:8000/earlier",
        "http_status": 200, "title": "Earlier capture", "status": "ok",
    }, None, None)
    db.close()
    page = client.get(f"/cases/{CASE4_SHA}")
    assert page.status_code == 200
    text = page.text
    assert "Previous captures (1)" in text
    assert text.index("http://fixture:8000/later") < text.index("Previous captures")
    assert text.index("http://fixture:8000/earlier") > text.index("Previous captures")


def test_case_page_numbers_evidence_links_and_overlay():
    page = client.get(f"/cases/{CASE2_SHA}")
    assert page.status_code == 200
    # two captures: oldest keeps the stable label Evidence 1, latest is Evidence 2
    assert page.text.count('class="evidence-link"') == 2
    assert "Evidence 1" in page.text and "Evidence 2" in page.text
    assert 'id="shot-overlay"' in page.text and 'role="dialog"' in page.text
    assert "Escape" in page.text


def test_case_page_without_screenshot_shows_no_evidence_link():
    page = client.get(f"/cases/{CASE3_SHA}")
    assert page.status_code == 200
    # capture seeded without a screenshot file: no link, no inline image
    assert 'class="evidence-link"' not in page.text
    assert "not available on this server" not in page.text
