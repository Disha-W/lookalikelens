# lookalikelens

LookalikeLens watches Certificate Transparency logs for addresses that look
like a brand's own, and saves them as candidate cases for a human to review.
A candidate is a lead, never a confirmed fake; nothing is reported or acted
on automatically.

Current stage: detection works on made-up sample data. The live certificate
feed is not connected yet.

## Run it with Docker (recommended - no Python setup needed)

```sh
docker compose build

# run the tests (expect: 17 passed)
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
