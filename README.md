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

# run the tests (expect: 11 passed)
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
