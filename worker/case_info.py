"""Tiny read-only helper for the evidence scripts.

  python worker/case_info.py --newest-sample   -> prints the sha256 of the
                                                   newest sample-marked case
  python worker/case_info.py CASE_SHA256       -> prints that case's domain

Used by bin/demo-evidence.sh so the shell never parses JSON or touches
the database itself.
"""
import os
import sys

from store import DEFAULT_DB_PATH, get_case, newest_sample_case, open_db


def main(argv):
    db_path = os.environ.get("DB_PATH", str(DEFAULT_DB_PATH))
    db = open_db(db_path)
    try:
        if argv == ["--newest-sample"]:
            case = newest_sample_case(db)
            if case is None:
                print("no sample case yet - run: docker compose run --rm app python worker/preview_feed.py", file=sys.stderr)
                return 1
            print(case["sha256"])
            return 0
        if len(argv) == 1:
            case = get_case(db, argv[0])
            if case is None:
                print(f"no such case: {argv[0]}", file=sys.stderr)
                return 1
            print(case["domain"])
            return 0
        print(__doc__, file=sys.stderr)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
