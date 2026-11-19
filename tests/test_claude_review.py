"""Unit tests for the Claude review stage.

No network and no real API key anywhere here: the HTTP layer is mocked, and
the offline practice mode is exercised directly. Everything the mock mode
stores must stay unmistakably labelled.
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'worker'))

import claude_review  # noqa: E402
import evidence_path  # noqa: E402
import store  # noqa: E402

CASE_SHA = "SAMPLE-SHA"
GOOD_REPLY = json.dumps({
    "verdict": "suspicious",
    "suspicious_reasons": ["Copies the brand name", "Pushes gift-card payment"],
    "innocent_explanations": ["Could be an unrelated business with a similar name"],
    "unknowns": ["Whether the branding is copied"],
})


@pytest.fixture
def db(tmp_path):
    conn = store.open_db(str(tmp_path / "test.db"))
    conn.execute(
        "INSERT INTO cases VALUES (?,?,?,?,?,?)",
        (CASE_SHA, "butterpaperbakes-shop.test", "butterpaperbakes",
         time.time(), "new", "sample"),
    )
    conn.commit()
    yield conn
    conn.close()


def add_evidence(db, status="ok", error=None):
    meta = {
        "captured_at": time.time(),
        "target_url": "http://fixture:8000/",
        "final_url": "http://fixture:8000/",
        "http_status": 200 if status == "ok" else None,
        "title": "Butter Paper Bakes - Best Cheap Cakes Online",
        "text_excerpt": "Pay by gift card or bank transfer only.",
        "html_sha256": "ab" * 32,
        "status": status,
        "error": error,
    }
    return store.insert_evidence(db, CASE_SHA, meta, None, None)


# --- parsing -------------------------------------------------------------

def test_parse_review_valid():
    review = claude_review.parse_review(GOOD_REPLY)
    assert review["verdict"] == "suspicious"
    assert len(review["suspicious_reasons"]) == 2
    assert review["innocent_explanations"] == ["Could be an unrelated business with a similar name"]
    assert review["unknowns"] == ["Whether the branding is copied"]


def test_parse_review_tolerates_prose_around_json():
    review = claude_review.parse_review(f"Here is my reading:\n{GOOD_REPLY}\nHope that helps.")
    assert review["verdict"] == "suspicious"


def test_parse_review_rejects_unknown_verdict():
    bad = json.loads(GOOD_REPLY)
    bad["verdict"] = "definitely-fake"
    with pytest.raises(ValueError, match="unknown verdict"):
        claude_review.parse_review(json.dumps(bad))


def test_parse_review_rejects_missing_lists():
    bad = json.loads(GOOD_REPLY)
    bad["unknowns"] = "nothing"
    with pytest.raises(ValueError, match="not a list"):
        claude_review.parse_review(json.dumps(bad))


def test_parse_review_rejects_non_json():
    with pytest.raises(ValueError, match="no JSON object"):
        claude_review.parse_review("I cannot decide.")


def test_parse_review_caps_reason_lists():
    bad = json.loads(GOOD_REPLY)
    bad["unknowns"] = [f"q{i}" for i in range(50)]
    review = claude_review.parse_review(json.dumps(bad))
    assert len(review["unknowns"]) == claude_review.MAX_REASONS


# --- storage -------------------------------------------------------------

def test_insert_and_latest_review(db):
    review = claude_review.parse_review(GOOD_REPLY)
    review_id = store.insert_review(db, CASE_SHA, review, "claude-haiku-4-5")
    loaded = store.latest_review(db, CASE_SHA)
    assert loaded["id"] == review_id
    assert loaded["verdict"] == "suspicious"
    assert loaded["model"] == "claude-haiku-4-5"
    assert loaded["source"] == "sample"  # inherited from the case
    assert loaded["suspicious_reasons"] == review["suspicious_reasons"]


def test_insert_review_rejects_unknown_verdict(db):
    with pytest.raises(ValueError, match="unknown verdict"):
        store.insert_review(db, CASE_SHA, {"verdict": "fake"}, "any-model")


def test_insert_review_rejects_missing_case(db):
    with pytest.raises(ValueError, match="no such case"):
        store.insert_review(db, "NOPE", claude_review.parse_review(GOOD_REPLY), "m")


def test_latest_review_none_when_absent(db):
    assert store.latest_review(db, CASE_SHA) is None


# --- evidence selection ---------------------------------------------------

def test_newest_evidence_prefers_successful_capture(db):
    add_evidence(db, status="ok")
    add_evidence(db, status="error", error="timed out")
    newest = store.newest_evidence(db, CASE_SHA)
    assert newest["status"] == "ok"


def test_newest_evidence_falls_back_to_failed_capture(db):
    add_evidence(db, status="error", error="timed out")
    newest = store.newest_evidence(db, CASE_SHA)
    assert newest["status"] == "error"
    assert newest["error"] == "timed out"


def test_newest_evidence_none(db):
    assert store.newest_evidence(db, CASE_SHA) is None


# --- the API call (mocked HTTP) -------------------------------------------

class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _api_payload(text):
    return {"content": [{"type": "text", "text": text}]}


def test_call_claude_sends_key_and_returns_text(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["key"] = request.headers.get("X-api-key") or request.headers.get("x-api-key")
        seen["version"] = request.headers.get("Anthropic-version")
        return _FakeResponse(_api_payload(GOOD_REPLY))

    monkeypatch.setattr(claude_review.urllib.request, "urlopen", fake_urlopen)
    text = claude_review.call_claude("prompt", "test-key", "claude-haiku-4-5")
    assert json.loads(text)["verdict"] == "suspicious"
    assert seen["key"] == "test-key"
    assert seen["version"] == claude_review.API_VERSION


def test_call_claude_http_error_without_key_leak(monkeypatch):
    import io
    import urllib.error

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            claude_review.API_URL, 429, "rate limited", {}, io.BytesIO(b'{"error": "slow down"}'))

    monkeypatch.setattr(claude_review.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="HTTP 429") as excinfo:
        claude_review.call_claude("prompt", "test-key", "claude-haiku-4-5")
    assert "test-key" not in str(excinfo.value)


def test_call_claude_rejects_empty_reply(monkeypatch):
    monkeypatch.setattr(claude_review.urllib.request, "urlopen",
                        lambda request, timeout: _FakeResponse({"content": []}))
    with pytest.raises(RuntimeError, match="no text"):
        claude_review.call_claude("prompt", "test-key", "claude-haiku-4-5")


# --- the command line -----------------------------------------------------

def run_main(db, monkeypatch, tmp_path, argv):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    return claude_review.main(argv)


def test_main_mock_stores_labelled_practice_review(db, monkeypatch, tmp_path, capsys):
    add_evidence(db)
    code = run_main(db, monkeypatch, tmp_path, [CASE_SHA, "--mock"])
    assert code == 0
    review = store.latest_review(db, CASE_SHA)
    assert review["model"] == claude_review.MOCK_MODEL
    assert review["verdict"] == "suspicious"  # fixture text pushes gift cards
    assert all("Practice signal" in s for s in review["suspicious_reasons"])
    assert "PRACTICE" in capsys.readouterr().out


def test_main_mock_makes_no_network_call(db, monkeypatch, tmp_path):
    add_evidence(db)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def explode(request, timeout):
        raise AssertionError("network call in mock mode")

    monkeypatch.setattr(claude_review.urllib.request, "urlopen", explode)
    assert run_main(db, monkeypatch, tmp_path, [CASE_SHA, "--mock"]) == 0


def test_main_without_key_fails_and_stores_nothing(db, monkeypatch, tmp_path, capsys):
    add_evidence(db)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    code = run_main(db, monkeypatch, tmp_path, [CASE_SHA])
    assert code == 1
    assert store.latest_review(db, CASE_SHA) is None
    assert "--mock" in capsys.readouterr().err


def test_main_real_call_stores_review(db, monkeypatch, tmp_path):
    add_evidence(db)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(claude_review.urllib.request, "urlopen",
                        lambda request, timeout: _FakeResponse(_api_payload(GOOD_REPLY)))
    code = run_main(db, monkeypatch, tmp_path, [CASE_SHA])
    assert code == 0
    review = store.latest_review(db, CASE_SHA)
    assert review["model"] == "claude-haiku-4-5"
    assert review["verdict"] == "suspicious"


def test_main_api_failure_stores_nothing(db, monkeypatch, tmp_path, capsys):
    add_evidence(db)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def fake_urlopen(request, timeout):
        raise claude_review.urllib.error.URLError("offline")

    monkeypatch.setattr(claude_review.urllib.request, "urlopen", fake_urlopen)
    code = run_main(db, monkeypatch, tmp_path, [CASE_SHA])
    assert code == 1
    assert store.latest_review(db, CASE_SHA) is None
    assert "nothing stored" in capsys.readouterr().err


def test_main_unknown_case(db, monkeypatch, tmp_path, capsys):
    code = run_main(db, monkeypatch, tmp_path, ["NOPE", "--mock"])
    assert code == 1
    assert "no such case" in capsys.readouterr().err


# --- screenshot path safety (the app's evidence route) --------------------

def test_safe_screenshot_path_allows_inside(tmp_path):
    shot = tmp_path / "case-x" / "screenshot.png"
    shot.parent.mkdir()
    shot.write_bytes(b"png")
    assert evidence_path.safe_screenshot_path(str(tmp_path), "case-x/screenshot.png") == str(shot)


def test_safe_screenshot_path_refuses_traversal(tmp_path):
    outside = tmp_path.parent / "secret.png"
    outside.write_bytes(b"x")
    assert evidence_path.safe_screenshot_path(str(tmp_path), "../secret.png") is None
    assert evidence_path.safe_screenshot_path(str(tmp_path), str(outside)) is None
    assert evidence_path.safe_screenshot_path(str(tmp_path), None) is None
    assert evidence_path.safe_screenshot_path(str(tmp_path), "case-x/missing.png") is None
