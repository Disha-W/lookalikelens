"""Evidence capture: visit ONE address with an isolated browser and save what it shows.

Runs only inside the collector container (Playwright image). For one case it
writes, into the output folder:

  screenshot.png  - what the page looked like (viewport 1280x800)
  meta.json       - target URL, redirect chain, final URL, HTTP status, page
                    title, short text excerpt, content hashes, timestamps,
                    and a clear status ("ok" or "error" with the reason)

The collector never writes to the case database; worker/ingest_evidence.py
does that from the trusted app side.

Safety model (worker/netguard.py does the verdicts):
- only http/https URLs;
- the redirect chain is walked FIRST with our own tiny HTTP client, and every
  hop is guard-checked (DNS + scheme) before the browser goes anywhere -
  server redirects never reach the browser unchecked;
- the browser is then sent straight to the verified final URL, and any
  further document navigation (JS or meta-refresh redirect) is aborted;
- every page subresource (script, image, CSS, XHR, ...) is guard-checked per
  request against resolved IPs: loopback/private/link-local/reserved
  addresses are refused;
- an explicit --allow-host list (used only for the local demo fixture)
  bypasses the private-address rule for those exact hostnames;
- navigation timeout, no downloads, no clicks, no forms, no logins;
- a blocked or failed capture still writes meta.json with status "error" -
  we record what happened, never pretend a capture succeeded.

Residual limit, stated honestly: the guard resolves a hostname and the
browser resolves it again to connect, so a DNS answer that changes between
the two lookups (rebinding) is not fully closed. The skipped-chain browser
navigation also means cookies set mid-chain are not carried.

Exit codes: 0 = captured ok, 3 = blocked by the guard, 1 = any other failure.
"""
import argparse
import hashlib
import http.client
import json
import os
import sys
import time
from urllib.parse import urljoin, urlparse, urldefrag

from playwright.sync_api import sync_playwright

from netguard import check_url

MAX_TEXT_CHARS = 2000
NAVIGATION_TIMEOUT_MS = 20_000
PROBE_TIMEOUT_S = 10
MAX_REDIRECTS = 5
SETTLE_MS = 2_000
VIEWPORT = {"width": 1280, "height": 800}
USER_AGENT = "LookalikeLens-evidence-collector/1.0"
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class GuardBlocked(Exception):
    """The guard refused an address."""


def write_meta(out_dir, meta):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)


def base_meta(case_sha256, url):
    return {
        "case_sha256": case_sha256,
        "target_url": url,
        "captured_at": time.time(),
        "status": "error",
        "error": None,
        "final_url": None,
        "redirect_chain": [],
        "http_status": None,
        "title": None,
        "text_excerpt": None,
        "html_sha256": None,
        "screenshot_sha256": None,
    }


def probe_once(url):
    """One bare GET with no redirect following. Returns (status, location).

    Only headers are read; the body is never downloaded.
    """
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(parsed.hostname, port, timeout=PROBE_TIMEOUT_S)
    try:
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        conn.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        resp = conn.getresponse()
        return resp.status, resp.getheader("Location")
    finally:
        conn.close()


def walk_redirects(url, allow_hosts):
    """Verify the redirect chain hop by hop. Returns (final_url, chain).

    Every hop - starting with the target itself - must pass the egress guard
    before it is even probed. Raises GuardBlocked on the first refused hop.
    """
    chain = []
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        ok, reason = check_url(current, allow_hosts)
        if not ok:
            raise GuardBlocked(f"{reason} (at {current})")
        chain.append(current)
        status, location = probe_once(current)
        if status in REDIRECT_STATUSES and location:
            current = urljoin(current, location)
            continue
        return current, chain
    raise GuardBlocked(f"more than {MAX_REDIRECTS} redirects")


def capture(case_sha256, url, out_dir, allow_hosts=()):
    """Capture one page. Returns the process exit code."""
    meta = base_meta(case_sha256, url)

    try:
        final_url, chain = walk_redirects(url, allow_hosts)
    except GuardBlocked as exc:
        meta["error"] = f"blocked: {exc}"
        write_meta(out_dir, meta)
        print(meta["error"], flush=True)
        return 3
    except Exception as exc:
        meta["error"] = f"could not reach the address: {type(exc).__name__}: {exc}"[:500]
        write_meta(out_dir, meta)
        print(f"capture failed: {meta['error']}", flush=True)
        return 1
    meta["redirect_chain"] = chain
    meta["final_url"] = final_url

    # The browser goes straight to the verified final URL. Document requests
    # for anything else (a fresh server/JS/meta redirect) are aborted;
    # subresources get the normal per-host guard.
    verified_documents = {urldefrag(u)[0] for u in chain}
    verdict_cache = {}
    nav_blocked = {"url": None}

    def guard_request(route, request):
        req_url = urldefrag(request.url)[0]
        if request.resource_type == "document":
            if req_url in verified_documents:
                route.continue_()
            else:
                print(f"blocked further navigation to {request.url}", flush=True)
                nav_blocked["url"] = request.url
                route.abort("blockedbyclient")
            return
        if req_url not in verdict_cache:
            verdict_cache[req_url] = check_url(req_url, allow_hosts)
        allowed, why = verdict_cache[req_url]
        if allowed:
            route.continue_()
        else:
            print(f"blocked subresource {request.url}: {why}", flush=True)
            route.abort("blockedbyclient")

    try:
        with sync_playwright() as pw:
            # Container runs its bundled Chromium; COLLECTOR_BROWSER_CHANNEL
            # only exists so the same script can be smoke-run on a dev
            # machine with a system browser (e.g. "chrome").
            channel = os.environ.get("COLLECTOR_BROWSER_CHANNEL") or None
            browser = pw.chromium.launch(channel=channel)
            try:
                context = browser.new_context(
                    viewport=VIEWPORT,
                    ignore_https_errors=False,
                    user_agent=USER_AGENT,
                )
                page = context.new_page()
                page.route("**/*", guard_request)
                response = page.goto(final_url, timeout=NAVIGATION_TIMEOUT_MS,
                                     wait_until="domcontentloaded")
                page.wait_for_timeout(SETTLE_MS)
                if nav_blocked["url"]:
                    # The page tried to send the browser somewhere off the
                    # verified chain (JS/meta redirect). The abort leaves a
                    # browser error page behind, which would be misleading
                    # evidence - record the attempt, keep no screenshot.
                    meta["error"] = ("page tried to navigate away to "
                                     f"{nav_blocked['url']}; blocked (not on the "
                                     "verified redirect chain)")
                    write_meta(out_dir, meta)
                    print(f"capture failed: {meta['error']}", flush=True)
                    return 1
                meta["final_url"] = page.url
                meta["http_status"] = response.status if response else None
                try:
                    meta["title"] = page.title() or None
                except Exception:
                    meta["title"] = None
                try:
                    meta["text_excerpt"] = page.inner_text("body")[:MAX_TEXT_CHARS]
                except Exception:
                    meta["text_excerpt"] = None
                try:
                    meta["html_sha256"] = hashlib.sha256(page.content().encode()).hexdigest()
                except Exception:
                    meta["html_sha256"] = None
                shot_path = os.path.join(out_dir, "screenshot.png")
                os.makedirs(out_dir, exist_ok=True)
                page.screenshot(path=shot_path)
                with open(shot_path, "rb") as fh:
                    meta["screenshot_sha256"] = hashlib.sha256(fh.read()).hexdigest()
                meta["status"] = "ok"
                meta["error"] = None
            finally:
                browser.close()
    except Exception as exc:  # navigation failure, crash, aborted redirect, ...
        meta["error"] = f"{type(exc).__name__}: {exc}"[:500]
        write_meta(out_dir, meta)
        print(f"capture failed: {meta['error']}", flush=True)
        return 1

    write_meta(out_dir, meta)
    print(f"captured: {meta['final_url']} (HTTP {meta['http_status']})", flush=True)
    return 0


def main(argv):
    parser = argparse.ArgumentParser(description="Capture one page as case evidence.")
    parser.add_argument("--case", required=True, help="case sha256 the evidence belongs to")
    parser.add_argument("--url", required=True, help="address to visit (http/https)")
    parser.add_argument("--out", required=True, help="output folder for artifacts")
    parser.add_argument("--allow-host", action="append", default=[],
                        help="exact hostname allowed despite a private address "
                             "(demo fixture only; repeatable)")
    args = parser.parse_args(argv)
    return capture(args.case, args.url, args.out, allow_hosts=set(args.allow_host))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
