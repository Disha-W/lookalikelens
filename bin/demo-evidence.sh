#!/bin/sh
# End-to-end SAMPLE demo of evidence capture, entirely inside Docker.
#
# The collector visits the local fake look-alike fixture page (never a real
# site) and the evidence lands on your newest sample-marked case. Run this
# first; it is the safe proof that capture works. It only ADDS: one evidence
# row in the database plus the capture files in the bw-evidence volume.
# Nothing existing is deleted, reset or overwritten.
#
#   sh bin/demo-evidence.sh
set -eu
cd "$(dirname "$0")/.."

# The app image must be rebuilt too: `docker compose run` reuses whatever
# image exists, and an old one will not have the newest worker scripts.
docker compose build app fixture collector
docker compose up -d fixture

# Always the staged IMPERSONATION case - the first made-up event in
# worker/sample_events.json (run worker/preview_feed.py first). The fixture
# page below pretends to copy that business; newer sample cases pair with
# their own fixture pages in bin/demo-review.sh.
case_sha="SAMPLELOOKALIKE00000000000000000000000000000000000000000000000001"
docker compose run --rm -T app python worker/case_info.py "$case_sha"
echo "capturing the demo fixture page for sample case $case_sha"
docker compose run --rm collector python capture.py \
  --case "$case_sha" --url http://fixture:8000/ \
  --allow-host fixture --out "/evidence/case-$case_sha"
docker compose run --rm app python worker/ingest_evidence.py "$case_sha"
echo "done - the sample case now has screenshot + page details saved as evidence (marked sample)."
