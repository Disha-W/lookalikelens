"""Consume the LIVE Certificate Transparency feed via the certstream sidecar.

Connects to the local certstream container's WebSocket and stores events
exactly like the sample replay, but marks every case source="live". Only
reads the feed - it never sends anything anywhere.

Storage is bounded: RETENTION_MAX_EVENTS keeps only the newest N certificate
events (default 10000; 0 disables pruning). Certificates referenced by a
case are never pruned, and cases are never deleted.

    CERTSTREAM_URL=ws://localhost:8080/ WATCH_TERMS=butterpaperbakes \
    OWNED_DOMAINS=butterpaperbakes.shop python consume_live.py

MAX_EVENTS=1 stops after the first new certificate (smoke-test mode).
"""
import asyncio
import json
import os

import websockets

from store import DEFAULT_DB_PATH, open_db, prune_ct_events, save_event


async def run():
    url = os.environ.get("CERTSTREAM_URL", "ws://localhost:8080/")
    db_path = os.environ.get("DB_PATH", str(DEFAULT_DB_PATH))
    max_events = int(os.environ.get("MAX_EVENTS", "0"))
    retention = int(os.environ.get("RETENTION_MAX_EVENTS", "10000"))
    watch_terms = [t.strip() for t in os.environ.get("WATCH_TERMS", "").split(",") if t.strip()]
    owned_domains = {d.strip().lower() for d in os.environ.get("OWNED_DOMAINS", "").split(",") if d.strip()}

    db = open_db(db_path)
    print(f"watching: {', '.join(watch_terms) or '(none - saving only)'}; "
          f"never flagged (owned): {', '.join(sorted(owned_domains)) or '(none)'}; "
          f"keeping newest {retention or 'unlimited'} certificates", flush=True)
    new = 0
    while True:
        try:
            async with websockets.connect(url, max_size=2**22) as ws:
                print(f"connected to {url}", flush=True)
                async for raw in ws:
                    try:
                        message = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        print(f"skipped malformed feed message ({exc}); stream continues", flush=True)
                        continue
                    is_new, is_case = save_event(db, message, watch_terms, owned_domains, source="live")
                    if is_case:
                        print("NEW CANDIDATE CASE (live feed):", flush=True)
                    if is_new:
                        new += 1
                        pruned = prune_ct_events(db, retention)
                        if pruned:
                            print(f"storage bound: pruned {pruned} old certificate events", flush=True)
                        if max_events and new >= max_events:
                            print(f"smoke test done: saved {new} live certificate(s)", flush=True)
                            return
        except (OSError, websockets.WebSocketException) as exc:
            print(f"feed not ready ({exc}); retrying in 3s", flush=True)
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(run())
