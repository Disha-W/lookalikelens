"""Live-consumer tests: run consume_live against a LOCAL MOCK websocket feed.

The mock feed is throwaway and its database lives in a pytest tmp dir, so
nothing here ever touches the real brand_watch.db. Records produced by this
test are not real live findings - they exist only inside the test run.
"""
import asyncio
import json
import os
import re
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))

websockets = pytest.importorskip("websockets")

import consume_live
from store import open_db, prune_ct_events, save_event

WATCH = "butterpaperbakes"
OWNED = "butterpaperbakes.shop"

MOCK_EVENTS = [
    {"message_type": "certificate_update", "data": {
        "leaf_cert": {"sha256": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "all_domains": ["butterpaperbakes-login.example.test"],
                      "issuer": {"aggregated": "/CN=Mock CA"}},
        "cert_index": 1, "seen": 1.0, "submission_timestamp": 1.0, "source": {"name": "Mock log (test)"}}},
    {"message_type": "certificate_update", "data": {
        "leaf_cert": {"sha256": "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB", "all_domains": ["unrelated.example.test"],
                      "issuer": {"aggregated": "/CN=Mock CA"}},
        "cert_index": 2, "seen": 2.0, "submission_timestamp": 2.0, "source": {"name": "Mock log (test)"}}},
    {"message_type": "heartbeat"},
]


def _start_mock_server():
    async def serve():
        async def handler(ws):
            for e in MOCK_EVENTS:
                await ws.send(json.dumps(e))
            await asyncio.sleep(2)

        async with websockets.serve(handler, "127.0.0.1", 8899):
            await asyncio.Future()

    t = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
    t.start()


def test_consumer_saves_mock_feed_as_live(monkeypatch, tmp_path):
    db_path = str(tmp_path / "live.db")
    monkeypatch.setenv("CERTSTREAM_URL", "ws://127.0.0.1:8899/")
    monkeypatch.setenv("DB_PATH", db_path)
    monkeypatch.setenv("MAX_EVENTS", "2")
    monkeypatch.setenv("WATCH_TERMS", WATCH)
    monkeypatch.setenv("OWNED_DOMAINS", OWNED)
    _start_mock_server()
    import time
    time.sleep(1)  # let the mock server bind
    asyncio.run(consume_live.run())
    db = open_db(db_path)
    assert db.execute("SELECT count(*) FROM ct_events").fetchone()[0] == 2
    # a mock-run case is labelled "live" only because the consumer path is live;
    # this row exists solely in this test's tmp database
    assert db.execute("SELECT domain, source FROM cases").fetchall() == [
        ("butterpaperbakes-login.example.test", "live")
    ]


def test_prune_keeps_newest_and_protects_case_certs(tmp_path):
    db = open_db(str(tmp_path / "t.db"))
    for i in range(8):
        save_event(db, {"message_type": "certificate_update", "data": {
            "leaf_cert": {"sha256": f"EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE{i:02X}", "all_domains": [f"unrelated-{i}.example.test"],
                          "issuer": {"aggregated": "/CN=Mock"}},
            "cert_index": i, "seen": float(i), "submission_timestamp": float(i),
            "source": {"name": "Mock log (test)"}}}, [WATCH], {OWNED}, source="live")
    save_event(db, {"message_type": "certificate_update", "data": {
        "leaf_cert": {"sha256": "ECECECECECECECECECECECECECECECECECECECECECECECECECECECECECECECEC", "all_domains": ["butterpaperbakes.example.test"],
                      "issuer": {"aggregated": "/CN=Mock"}},
        "cert_index": 99, "seen": 0.5, "submission_timestamp": 0.5,
        "source": {"name": "Mock log (test)"}}}, [WATCH], {OWNED}, source="live")
    assert db.execute("SELECT count(*) FROM ct_events").fetchone()[0] == 9
    pruned = prune_ct_events(db, 5)
    assert pruned == 4
    remaining = {r[0] for r in db.execute("SELECT sha256 FROM ct_events").fetchall()}
    assert remaining == {"EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE04", "EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE05", "EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE06", "EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE07", "ECECECECECECECECECECECECECECECECECECECECECECECECECECECECECECECEC"}
    assert "ECECECECECECECECECECECECECECECECECECECECECECECECECECECECECECECEC" in remaining  # case evidence survives even though it is oldest


def test_prune_zero_keeps_everything(tmp_path):
    db = open_db(str(tmp_path / "t.db"))
    for i in range(3):
        save_event(db, {"message_type": "certificate_update", "data": {
            "leaf_cert": {"sha256": f"FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF{i:02X}", "all_domains": ["x.example.test"],
                          "issuer": {"aggregated": "/CN=Mock"}},
            "cert_index": i, "seen": 1.0, "submission_timestamp": 1.0,
            "source": {"name": "Mock"}}}, [WATCH], {OWNED}, source="live")
    assert prune_ct_events(db, 0) == 0
    assert db.execute("SELECT count(*) FROM ct_events").fetchone()[0] == 3


# certstream emits a full sha256: colon-separated uppercase hex, 32 bytes
REAL_SHA256 = re.compile(r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")


def test_sample_fixture_hashes_cannot_collide_with_real_certs():
    """Sample SHAs stay outside the documented real hash format.

    store.save_event enforces this at write time in both directions (live
    refuses non-hex hashes, sample refuses realistic ones), and this test
    pins the fixture side of that contract."""
    with open(os.path.join(os.path.dirname(__file__), "..", "worker", "sample_events.json")) as f:
        doc = json.load(f)
    for event in doc["events"]:
        sha = event["data"]["leaf_cert"]["sha256"]
        assert not REAL_SHA256.match(sha), f"sample hash looks real: {sha}"
    # and a full-length documented-format value does match the pattern
    assert REAL_SHA256.match(":".join(["AB"] * 32))


def test_live_path_refuses_non_hex_hash(tmp_path):
    db = open_db(str(tmp_path / "t.db"))
    bad = {"message_type": "certificate_update", "data": {
        "leaf_cert": {"sha256": "NOT-A-REAL-HASH", "all_domains": ["x.example.test"],
                      "issuer": {"aggregated": "/CN=Mock"}},
        "cert_index": 1, "seen": 1.0, "submission_timestamp": 1.0, "source": {"name": "Mock"}}}
    assert save_event(db, bad, [WATCH], {OWNED}, source="live") == (False, False)
    assert db.execute("SELECT count(*) FROM ct_events").fetchone()[0] == 0


def test_sample_path_refuses_realistic_hash(tmp_path):
    db = open_db(str(tmp_path / "t.db"))
    bad = {"message_type": "certificate_update", "data": {
        "leaf_cert": {"sha256": "AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB:AB", "all_domains": ["x.example.test"],
                      "issuer": {"aggregated": "/CN=Mock"}},
        "cert_index": 1, "seen": 1.0, "submission_timestamp": 1.0, "source": {"name": "Mock"}}}
    with pytest.raises(ValueError):
        save_event(db, bad, [WATCH], {OWNED}, source="sample")


def test_watch_config_falls_back_to_environment(tmp_path, monkeypatch):
    """No dashboard settings: the compose environment decides."""
    db = open_db(str(tmp_path / "t.db"))
    terms, owned, source = consume_live.load_watch_config(
        db, {"WATCH_TERMS": "envterm, second", "OWNED_DOMAINS": "env.example"})
    assert terms == ["envterm", "second"]
    assert owned == {"env.example"}
    assert source == "environment"


def test_watch_config_prefers_dashboard_settings(tmp_path):
    """Saved dashboard settings override the environment."""
    db = open_db(str(tmp_path / "t.db"))
    from store import set_setting
    set_setting(db, "watch_term", "dashterm")
    set_setting(db, "watch_domain", "dash.example")
    terms, owned, source = consume_live.load_watch_config(
        db, {"WATCH_TERMS": "envterm", "OWNED_DOMAINS": "env.example"})
    assert terms == ["dashterm"]
    assert owned == {"dash.example"}
    assert source == "dashboard"


def test_watch_config_treats_half_written_pair_as_unset(tmp_path):
    """A domain without a term must not silently re-point the watcher."""
    db = open_db(str(tmp_path / "t.db"))
    from store import set_setting
    set_setting(db, "watch_domain", "dash.example")
    terms, owned, source = consume_live.load_watch_config(
        db, {"WATCH_TERMS": "envterm", "OWNED_DOMAINS": ""})
    assert terms == ["envterm"]
    assert source == "environment"
