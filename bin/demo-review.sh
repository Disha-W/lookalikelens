#!/bin/sh
# Full STAGED review demo, entirely inside Docker, with two clearly-labelled
# sample cases:
#   - a staged IMPERSONATION (fake look-alike fixture page)
#   - an INNOCENT near-name (a different family bakery's fixture page)
# Each case is photographed against its own fixture page (never a real site),
# the evidence is filed, then Claude reviews the case - weighing what looks
# suspicious, what might be innocent, and what is still unknown.
# It only ADDS rows and capture files; nothing existing is deleted or reset.
#
# Real review (one Claude API call per case; the key is passed through, never
# stored or printed). Type it WITHOUT echoing or saving it in shell history:
#   read -s ANTHROPIC_API_KEY; export ANTHROPIC_API_KEY
#   sh bin/demo-review.sh
#
# Offline practice (no key, no network call, unmistakably labelled mock):
#   sh bin/demo-review.sh --mock
set -eu
cd "$(dirname "$0")/.."

MOCK=""
if [ "${1:-}" = "--mock" ]; then
  MOCK="--mock"
elif [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "no ANTHROPIC_API_KEY in the environment - export it first," >&2
  echo "or practice offline: sh bin/demo-review.sh --mock" >&2
  exit 1
fi

# The two staged sample cases from worker/sample_events.json.
IMPERSONATION="SAMPLELOOKALIKE00000000000000000000000000000000000000000000000001"
INNOCENT="SAMPLEINNOCENTLOOKALIKE0000000000000000000000000000000000000000004"

docker compose build app fixture collector
docker compose up -d fixture
# Idempotent replay of the MADE-UP sample events: adds any missing sample
# cases, never touches existing rows (certificates dedupe by hash).
docker compose run --rm -T app python worker/preview_feed.py

capture_and_review() {
  case_sha="$1"
  page_path="$2"
  echo "--- sample case $case_sha (fixture page $page_path)"
  docker compose run --rm collector python capture.py \
    --case "$case_sha" --url "http://fixture:8000$page_path" \
    --allow-host fixture --out "/evidence/case-$case_sha"
  docker compose run --rm -T app python worker/ingest_evidence.py "$case_sha"
  docker compose run --rm -T -e ANTHROPIC_API_KEY -e ANTHROPIC_MODEL \
    app python worker/claude_review.py "$case_sha" $MOCK
}

capture_and_review "$IMPERSONATION" "/"
capture_and_review "$INNOCENT" "/innocent.html"

echo "done - two sample cases reviewed."
echo "start the app with: docker compose up"
echo "then open: http://localhost:8000/cases/$IMPERSONATION"
echo "and:       http://localhost:8000/cases/$INNOCENT"
