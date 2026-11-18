"""Tests for sample-feed matching and storage. Run: python -m pytest tests/"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))

from match import find_match, is_owned, squash
from store import open_db, save_event

BRAND = "butterpaperbakes"
WATCH = [BRAND]
OWNED = {"butterpaperbakes.shop"}
FIXTURES = os.path.join(os.path.dirname(__file__), "..", "worker", "sample_events.json")


def msg(domains, sha256="AA:BB"):
    return {"message_type": "certificate_update", "data": {
        "leaf_cert": {"sha256": sha256, "all_domains": domains,
                      "issuer": {"aggregated": "/CN=Sample Test CA"}},
        "cert_index": 5, "seen": 1.5, "submission_timestamp": 1.0,
        "source": {"name": "Sample CT log (fixture)"}}}


def fresh_db():
    return open_db(os.path.join(tempfile.mkdtemp(), "t.db"))


def load_fixtures():
    with open(FIXTURES) as f:
        doc = json.load(f)
    assert "SAMPLE" in doc["description"].upper()  # fixtures must self-label as sample
    return doc["events"]


def test_lookalike_is_flagged():
    assert find_match(["butterpaperbakes-shop.test"], WATCH, OWNED) == ("butterpaperbakes-shop.test", BRAND)


def test_lookalike_fold_characters_flagged():
    # 0 folded to o, 1 folded to l: still the brand's letters underneath
    assert find_match(["butterpaperbakes.test"], WATCH, OWNED)
    assert find_match(["butt3rpap3rbak3s.test"], WATCH, OWNED)


def test_owned_domain_itself_never_flagged():
    assert find_match(["butterpaperbakes.shop"], WATCH, OWNED) is None


def test_owned_subdomain_never_flagged():
    assert find_match(["login.butterpaperbakes.shop"], WATCH, OWNED) is None
    assert find_match(["deep.login.butterpaperbakes.shop"], WATCH, OWNED) is None


def test_unrelated_domain_not_flagged():
    assert find_match(["example.test", "*.example.test"], WATCH, OWNED) is None


def test_wildcard_lookalike_still_flagged():
    assert find_match(["*.butterpaperbakes-shop.test"], WATCH, OWNED)


def test_repeated_certificate_dedupes_event_and_case():
    db = fresh_db()
    m = msg(["butterpaperbakes-shop.test"], sha256="DE:AD")
    assert save_event(db, m, WATCH, OWNED) == (True, True)
    assert save_event(db, m, WATCH, OWNED) == (False, False)
    assert db.execute("SELECT count(*) FROM ct_events").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM cases").fetchone()[0] == 1


def test_fixture_file_end_to_end():
    db = fresh_db()
    results = [save_event(db, e, WATCH, OWNED, source="sample") for e in load_fixtures()]
    # 4 fixture events: lookalike, owned subdomain, unrelated, repeat of lookalike
    assert results == [(True, True), (True, False), (True, False), (False, False)]
    assert db.execute("SELECT count(*) FROM ct_events").fetchone()[0] == 3
    rows = db.execute("SELECT domain, status, source FROM cases").fetchall()
    assert rows == [("butterpaperbakes-shop.test", "new", "sample")]


def test_fixture_run_marks_cases_as_sample():
    db = fresh_db()
    for e in load_fixtures():
        save_event(db, e, WATCH, OWNED, source="sample")
    sources = {r[0] for r in db.execute("SELECT source FROM cases").fetchall()}
    assert sources == {"sample"}


def test_default_source_is_live():
    db = fresh_db()
    save_event(db, msg(["butterpaperbakes-shop.test"], sha256="LI:VE"), WATCH, OWNED)
    assert db.execute("SELECT source FROM cases").fetchone()[0] == "live"


def test_squash_strips_wildcard_and_case():
    assert squash("*.ButterPaperBakes-Shop.test") == "butterpaperbakesshoptest"
