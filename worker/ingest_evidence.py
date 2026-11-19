"""Trusted ingestion of collector captures into the case store.

The collector (an isolated browser that fetches untrusted pages) writes its
artifacts - screenshot file plus meta.json - into an evidence folder and
never touches the database. THIS step, run from the app side, reads those
artifacts, verifies them, and records the evidence row:

  docker compose run --rm app python worker/ingest_evidence.py CASE_SHA256

Provenance is inherited from the case (sample stays sample), never taken
from the collector's output. A failed capture is recorded as an error row,
not silently dropped.
"""
import hashlib
import json
import os
import sys

from store import DEFAULT_DB_PATH, get_case, insert_evidence, open_db

# Where capture artifacts land (the collector's write-only volume, mounted
# read-only here). Override with EVIDENCE_DIR for local runs.
EVIDENCE_DIR = os.environ.get("EVIDENCE_DIR", "/evidence")


def load_capture(case_sha256, evidence_dir=EVIDENCE_DIR):
    """Read and verify one capture folder. Returns (meta, screenshot_rel_path, screenshot_sha256)."""
    capture_dir = os.path.join(evidence_dir, f"case-{case_sha256}")
    meta_path = os.path.join(capture_dir, "meta.json")
    if not os.path.exists(meta_path):
        raise FileNotFoundError(
            f"no capture found at {capture_dir} - run the collector first "
            f"(see README, evidence section)"
        )
    with open(meta_path) as fh:
        meta = json.load(fh)
    if meta.get("case_sha256") != case_sha256:
        raise ValueError(f"capture metadata names a different case: {meta.get('case_sha256')!r}")
    screenshot_rel = None
    screenshot_sha = None
    shot_path = os.path.join(capture_dir, "screenshot.png")
    if os.path.exists(shot_path):
        with open(shot_path, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        if meta.get("screenshot_sha256") and meta["screenshot_sha256"] != digest:
            raise ValueError("screenshot file does not match the collector's recorded hash")
        screenshot_rel = os.path.join(f"case-{case_sha256}", "screenshot.png")
        screenshot_sha = digest
    return meta, screenshot_rel, screenshot_sha


def main(argv):
    if len(argv) != 1:
        print("usage: python worker/ingest_evidence.py CASE_SHA256", file=sys.stderr)
        return 2
    case_sha256 = argv[0]
    db_path = os.environ.get("DB_PATH", str(DEFAULT_DB_PATH))
    db = open_db(db_path)
    try:
        case = get_case(db, case_sha256)
        if case is None:
            print(f"no such case: {case_sha256}", file=sys.stderr)
            return 1
        meta, shot_rel, shot_sha = load_capture(case_sha256)
        evidence_id = insert_evidence(db, case_sha256, meta, shot_rel, shot_sha)
        status = meta.get("status") or "error"
        print(f"evidence #{evidence_id} saved for case {case_sha256[:12]}... "
              f"(status: {status}, source: {case['source']})")
        if status != "ok":
            print(f"capture error recorded: {meta.get('error')}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (FileNotFoundError, ValueError) as exc:
        print(f"cannot ingest: {exc}", file=sys.stderr)
        sys.exit(1)
