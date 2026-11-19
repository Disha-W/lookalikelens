"""Boundary and end-to-end tests for the evidence collector.

These run INSIDE the collector container against the local fixture, so they
exercise the real browser path, not just the guard helpers:

  docker compose run --rm --build collector python -m pytest /tests -q

Gate rule: arbitrary live captures are only offered after this file passes.
"""
import json
import os
import subprocess
import sys

import pytest

# Hostname of the fixture service: "fixture" inside Docker; override with
# FIXTURE_HOST (e.g. localhost) to run the same tests against a local server.
FIXTURE_HOST = os.environ.get("FIXTURE_HOST", "fixture")
FIXTURE = f"http://{FIXTURE_HOST}:8000/"
CASE = "TEST-CASE-SHA"


def run_capture(out_dir, url, allow=()):
    capture_py = os.environ.get("CAPTURE_PY", "/app/capture.py")
    # In the container netguard.py sits next to capture.py (the Dockerfile
    # copies it). Locally it lives in worker/, so put it on PYTHONPATH.
    worker_dir = os.path.join(os.path.dirname(os.path.dirname(capture_py)), "worker")
    env = dict(os.environ, PYTHONPATH=worker_dir + os.pathsep + os.environ.get("PYTHONPATH", ""))
    cmd = [sys.executable, capture_py, "--case", CASE, "--url", url,
           "--out", str(out_dir)]
    for host in allow:
        cmd += ["--allow-host", host]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
    meta = json.loads((out_dir / "meta.json").read_text())
    return proc, meta


def test_fixture_is_blocked_without_allowlist(tmp_path):
    # The fixture lives on a private compose address: by default the guard
    # must refuse it through the full browser path.
    proc, meta = run_capture(tmp_path, FIXTURE)
    assert proc.returncode == 3
    assert meta["status"] == "error"
    assert "non-public" in meta["error"]


def test_fixture_capture_succeeds_with_allowlist(tmp_path):
    proc, meta = run_capture(tmp_path, FIXTURE, allow=[FIXTURE_HOST])
    assert proc.returncode == 0, meta.get("error")
    assert meta["status"] == "ok"
    assert meta["http_status"] == 200
    assert "Butter Paper Bakes" in (meta["title"] or "")
    assert "Truffle cake" in (meta["text_excerpt"] or "")
    shot = tmp_path / "screenshot.png"
    assert shot.exists() and shot.stat().st_size > 1000
    assert meta["screenshot_sha256"]


@pytest.mark.parametrize("target", [
    "http://127.0.0.1:8000/",                    # loopback
    "http://169.254.169.254/latest/meta-data",   # cloud metadata
    "http://0.0.0.0/",                           # unspecified
])
def test_redirect_to_private_is_blocked(tmp_path, target):
    # The fixture itself is allowlisted; its /redirect endpoint then tries to
    # send the browser somewhere it must never go.
    proc, meta = run_capture(
        tmp_path, f"http://{FIXTURE_HOST}:8000/redirect?to={target}", allow=[FIXTURE_HOST])
    assert proc.returncode != 0
    assert meta["status"] == "error"
    assert not (tmp_path / "screenshot.png").exists()


def test_safe_redirect_still_works(tmp_path):
    proc, meta = run_capture(
        tmp_path, f"http://{FIXTURE_HOST}:8000/redirect?to={FIXTURE}",
        allow=[FIXTURE_HOST])
    assert proc.returncode == 0, meta.get("error")
    assert meta["status"] == "ok"
    assert meta["final_url"] == FIXTURE


def test_bad_scheme_is_blocked(tmp_path):
    proc, meta = run_capture(tmp_path, "file:///etc/passwd")
    assert proc.returncode == 3
    assert "scheme" in meta["error"]


def test_meta_refresh_navigation_is_blocked(tmp_path):
    # The fixture's meta-refresh page tries a client-side redirect to
    # loopback AFTER loading. The navigation must be aborted and the capture
    # recorded as an error - never a screenshot of the browser's error page.
    proc, meta = run_capture(
        tmp_path, f"http://{FIXTURE_HOST}:8000/meta-refresh.html", allow=[FIXTURE_HOST])
    assert proc.returncode == 1
    assert meta["status"] == "error"
    assert "navigate away" in meta["error"]
    assert "127.0.0.1" in meta["error"]
    assert not (tmp_path / "screenshot.png").exists()


def test_private_subresources_blocked_page_still_captured(tmp_path):
    # A page embedding private-address subresources: those requests are
    # refused, the page itself is still captured.
    proc, meta = run_capture(
        tmp_path, f"http://{FIXTURE_HOST}:8000/evil-subresource.html", allow=[FIXTURE_HOST])
    assert proc.returncode == 0, meta.get("error")
    assert meta["status"] == "ok"
    assert "blocked subresource" in proc.stdout
    assert "169.254.169.254" in proc.stdout
    assert (tmp_path / "screenshot.png").exists()
