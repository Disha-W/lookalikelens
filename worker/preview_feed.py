"""Replay SAMPLE certificate events into the database - not a live feed.

Reads made-up, certstream-shaped messages from a JSON file (default:
sample_events.json next to this file) and stores them the way the live
consumer will. Every case created here is marked source="sample" in the
database, so sample findings can never be confused with live ones.

    python preview_feed.py [events.json] [db_path]
"""
import json
import os
import sys

from store import DEFAULT_DB_PATH, open_db, save_event

WATCH_TERMS = [t.strip() for t in os.environ.get("WATCH_TERMS", "butterpaperbakes").split(",") if t.strip()]
OWNED_DOMAINS = {d.strip().lower() for d in os.environ.get("OWNED_DOMAINS", "butterpaperbakes.shop").split(",") if d.strip()}


def main():
    events_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "sample_events.json")
    db_path = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("DB_PATH", str(DEFAULT_DB_PATH))
    with open(events_path) as f:
        doc = json.load(f)
    events = doc["events"] if isinstance(doc, dict) else doc
    db = open_db(db_path)
    new_certs = new_cases = 0
    for event in events:
        is_new, is_case = save_event(db, event, WATCH_TERMS, OWNED_DOMAINS, source="sample")
        new_certs += is_new
        new_cases += is_case
        if is_case:
            print(f"NEW CANDIDATE CASE (sample data, not a live finding): {event['data']['leaf_cert']['all_domains']}")
    total_events = db.execute("SELECT count(*) FROM ct_events").fetchone()[0]
    total_cases = db.execute("SELECT count(*) FROM cases").fetchone()[0]
    print(f"SAMPLE replay: read {len(events)} made-up events -> {new_certs} new certificates, "
          f"{new_cases} new candidate cases (db totals: {total_events} certificates, {total_cases} cases)")


if __name__ == "__main__":
    main()
