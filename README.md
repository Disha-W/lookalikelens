# lookalikelens

LookalikeLens watches Certificate Transparency logs for addresses that look
like a brand's own, and saves them as candidate cases for a human to review.
A candidate is a lead, never a confirmed fake; nothing is reported or acted
on automatically.

Current stage: the live certificate feed connects and saves real events;
detection and evidence capture are proven on clearly marked sample data.
Claude's assessment, showing evidence in the app, and report export are not
built yet.

## Run it with Docker (recommended - no Python setup needed)

```sh
docker compose build

# run the tests (expect: 47 passed)
docker compose run --rm app python -m pytest tests/ -q

# replay the MADE-UP sample events into the shared database
docker compose run --rm app python worker/preview_feed.py

# start the app, then open http://localhost:8000/cases
docker compose up
```

The replay and the app share one database (a Docker volume), so /cases shows
what the replay saved. Sample rows are marked "source": "sample".

## Run the worker tests without Docker

The worker scripts and tests are standard-library Python (3.9+). From the
repo root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install "pytest<9"
python3 worker/preview_feed.py
python3 -m pytest tests/
```

The web app itself needs Python 3.10+ (its pinned packages require it) or
Docker.

## Connect the real certificate feed (live mode)

The `certstream` sidecar streams real Certificate Transparency events; the
`worker` service saves them (marked `"source": "live"`).

```sh
# smoke test: worker stops after saving 1 real certificate
docker compose up --build

# then set MAX_EVENTS: "0" in docker-compose.yml for continuous watching
```

Honest limit: a look-alike address appears on the live feed only if a real
CA actually issues such a certificate, which may never happen during the
demo. The live feed proves the connection works (the saved-certificate count
grows); the "we caught one" moment still comes from the clearly marked
sample data. Storage use is bounded for the event log only: the newest 10000
certificates are kept (RETENTION_MAX_EVENTS). Cases and their underlying
certificates are kept permanently, so total size still grows with the
number of cases.

## Capture evidence for a case (screenshot + page details)

A saved case can get a snapshot of what the flagged address actually shows:
a screenshot, the final URL after redirects, HTTP status, page title, a
short text excerpt, and content hashes. An isolated browser (the `collector`
service) does the visiting; a separate trusted step records the result in
the database. A failed or blocked capture is saved as an error row - never
pretended away. Screenshots live in the `bw-evidence` Docker volume; the
database holds their details, linked to the case and keeping its sample/live
label.

Prove it on the demo fixture first - a fake look-alike bakery page that
lives only inside Docker, never a real site:

```sh
sh bin/demo-evidence.sh
```

Expected: "evidence #N saved for case ... (status: ok, source: sample)".

The boundary tests (redirects, private addresses, subresources, meta
refreshes - 9 of them) run inside the collector container:

```sh
docker compose run --rm --build collector python -m pytest /tests -q   # 9 passed
```

Capturing a REAL flagged address is intentionally NOT enabled yet. The
redirect and subresource blocks are proven, but a DNS answer that changes
between our check and the browser's own lookup (rebinding) is not closed;
enabling live captures first needs the browser pinned to the verified IP.
The live feed never triggers a capture, and the demo path above is complete
and safe: the fixture is a fixed address on Docker's own network.

Safety, in short: http/https only; the redirect chain is walked and checked
hop by hop before the browser moves; the browser then goes straight to the
verified final address and any further navigation (JS or meta redirects) is
aborted; every page subresource is checked against its resolved IP;
loopback, private, link-local (including cloud metadata) and reserved
addresses are refused. No clicks, logins, forms or downloads.

Honest limits: the page's own scripts DO run inside the sandboxed browser -
they are needed to render real pages - but captured content is stored as
data, never obeyed. A visit is a normal web request: the visited site sees
it (your IP, a LookalikeLens user agent, the time). DNS is checked before
the browser connects and the browser resolves again on its own, so an
answer that changes in between (rebinding) is not fully closed. Cookies set
mid-redirect are not carried.

Verified vs not: unit tests (47) and the 9 boundary tests pass locally with
a real browser; the fixture screenshot was inspected. The Docker path itself
(services above) is config-checked here and first runs on your machine.
