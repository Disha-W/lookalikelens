# lookalikelens

LookalikeLens watches Certificate Transparency logs for addresses that look
like a brand's own, and saves them as candidate cases for a human to review.
A candidate is a lead, never a confirmed fake; nothing is reported or acted
on automatically.

Current stage: the live certificate feed connects and saves real events;
detection, evidence capture and the Claude case review are proven on
clearly marked sample data. The report export is not built yet.

## Run it with Docker (recommended - no Python setup needed)

```sh
docker compose build

# run the tests (expect: 98 passed)
docker compose run --rm app python -m pytest tests/ -q

# replay the MADE-UP sample events into the shared database
docker compose run --rm app python worker/preview_feed.py

# start the app, then open http://localhost:8000 (the owner dashboard)
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

## Review a case with Claude (a second opinion, not an accusation)

One command asks Claude to weigh a case BOTH ways - what looks like
deliberate impersonation, what innocent explanations also fit, and what is
still unknown - and saves that review on the case page
(/cases/CASE_SHA256, next to the captured evidence). A review is advice for
the owner's own decision; nothing is reported or acted on automatically.

```sh
# type your own key without echoing it or leaving it in shell history
# (same in macOS zsh and bash):
echo "API key (typing hidden): "; read -rs ANTHROPIC_API_KEY; export ANTHROPIC_API_KEY; echo
sh bin/demo-review.sh          # two staged sample cases: impersonation + innocent near-name
```

The key is passed through to one container run and never stored, printed or
committed.

What the demo actually compares: the protected identity is the DEMO bakery
"butterpaperbakes" with owned domain butterpaperbakes.shop - the domain was
bought for the hackathon, but the business itself is fictional. The
captured pages are internal fixture pages served only inside Docker, never
the real butterpaperbakes.shop or any real site. The review weighs that
fixture text against the configured business profile; there is no verified
baseline capture of any real site to compare against, and a flagged staged
case is never a real discovery.

Page text shown to Claude is treated as untrusted data: the review must
attribute the site's claims as claims, cite only the captured evidence, and
never follow instructions written inside a captured page. These prompt
rules are mitigations, not a guarantee: the pages Claude reads in this demo
are our own fixtures; untrusted real pages would be a stronger test.

The demo runs one API call per case (model from ANTHROPIC_MODEL, default
claude-haiku-4-5). No key, or rehearsing offline:

```sh
sh bin/demo-review.sh --mock
```

`--mock` makes NO network call and stores a deterministic practice review
that is labelled "offline-mock" in the database and shown with a visible
"Practice review - NOT a real Claude assessment" banner on the case page.

Reviewing a single case directly:

```sh
docker compose run --rm -e ANTHROPIC_API_KEY app python worker/claude_review.py CASE_SHA256 [--mock]
```

A failed review (no key, API error, unreadable reply) stores nothing and
says so; a case page without a review simply says none exists yet.

Verified vs not: unit tests (71) and the 9 boundary tests pass locally with
a real browser; the fixture screenshot and both staged case pages (mock and
real-shaped reviews) were inspected. The Docker path itself (services
above) is config-checked here and first runs on your machine. The real
Claude API call is exercised only with your own key on your machine - the
tests here mock the API, so the live call itself is verified on your run,
not here.

## The owner pages (dashboard UI)

Three pages, no sign-in in this demo build:

1. `http://localhost:8000/` asks which domain to watch over and saves it.
2. `http://localhost:8000/dashboard` lists the flagged candidates for it,
   newest first, with each case's verdict and whether the review was a real
   Claude call or an offline practice review.
3. Opening a flagged site shows its details: why it was flagged, what could
   be innocent, what is still unknown, and the captured evidence.

The domain field scopes the dashboard only. The live certificate worker
keeps its own configured watch terms (`WATCH_TERMS` in docker-compose.yml);
multi-domain watching and accounts are not built yet. Sample cases always
belong to the staged demo name: if you enter a different domain, the
dashboard says the samples did not come from your domain.
