# worker

The certificate watcher: it reads Certificate Transparency events, saves
them to SQLite, and flags addresses that look like the watched brand as
candidate cases for a human to review. A candidate is a lead, never a
confirmed fake, and nothing is reported or acted on automatically.

## What is here now (sample-data stage)

- `match.py` - decides whether a certificate's domains target the watched
  brand. Never flags the brand's own domain or its subdomains.
- `store.py` - SQLite storage. Certificates dedupe by their SHA-256, so
  replays never duplicate rows. Every case records its `source`:
  `"live"` or `"sample"`.
- `preview_feed.py` - replays MADE-UP sample events into the database to
  try the logic without any live feed. Everything it saves is marked
  `source="sample"`.
- `sample_events.json` - the made-up sample events. Not real certificates,
  not live findings; uses reserved `.test` domains.

Try it:

    cd worker
    python preview_feed.py            # writes brand_watch.db, prints a summary
    python -m pytest ../tests/        # runs the tests

The live Certificate Transparency connection (Rust certstream sidecar) is a
later step and is not here yet.
