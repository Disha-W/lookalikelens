# Automatic Claude review, opt-in only

This adds one step to the existing architecture: a case with a successful
saved evidence capture can receive a Claude assessment automatically.
It does not find domains, capture websites, add separate visitor accounts,
or export abuse reports. It never reports or takes down a site.
The public web app does not receive a Claude key or trigger paid jobs.

## Defaults

The reviewer is behind the Compose `reviewer` profile and also disabled by
`AUTO_REVIEW_ENABLED=false`, with an attempt allowance of zero. Starting the
web app does not start it. No paid test is included in this change.

The two existing real-reviewed sample cases are skipped if transferred.
Offline mock reviews do not count as real reviews. All source labels are
preserved. With no successful evidence in the server database, no review
can run. The newest evidence must be successful and contain captured text.
The model receives metadata and text, not screenshot pixels. The historic
case schema has no verified owner-domain snapshot per case, so automatic
review says that the real owner domain is unknown rather than borrowing
another visitor's mutable global watch setting.

## Duplicate and failure rules

Jobs are keyed by case, evidence ID and pinned model (`claude-haiku-4-5`).
An atomic SQLite transaction claims a job and consumes one attempt BEFORE
any request. Only one job can be in flight globally. Refreshes, repeated
polls, competing runners and restarts do not reset the allowance.
The allowance is stored in the database. A later environment value that
differs is refused; it cannot silently grant more attempts.

Any existing real review conservatively excludes the entire case, including
new captures, because old review rows do not identify the evidence version.
A case is not silently re-reviewed after migration.

Errors, malformed output and timeouts are final, with no automatic retry.
A crash after claiming leaves a running/uncertain job and blocks further
spending. An owner must inspect the provider's usage and the job before
any deliberate recovery. Do not delete job/allowance rows to make retries
happen. A failed request may still have been billed. A failure can consume
an attempt even before a request is sent; safety wins over a guessed refund.

The case page shows queued, started, saved, failed, or allowance-used status.
Refresh to see changes. This is not a push-notification feature.

## Activation requires a separate owner decision

Before activation, get an approved finite number of attempts AND a budget,
verify the current model price and actual selected prompt's cost, and set
appropriate provider-side workspace spending limits. The code enforces an
ATTEMPT cap, not an exact dollar ceiling. The prompt has a 16,000-byte limit
and response is capped at 1,024 tokens; byte length is not a token estimate.
Do not claim these limits alone guarantee a particular dollar total.

Resolve any previously exposed key by revoking/replacing it. Enter the key
privately on the host, never in chat, source, Compose text, command-line
arguments, screenshots or shell history. Store the file outside this repo
and its Docker build context, restrict its permissions, and set
`CLAUDE_REVIEW_KEY_FILE` to its absolute path. Compose mounts it read-only
only into the reviewer at `/run/secrets/claude_review_key`. The key is not
passed to the public app. Host root/Docker administrators can still access
container secrets; a mount is not protection against the host owner.

After permission and cost verification, set `AUTO_REVIEW_ENABLED=true` and
`AUTO_REVIEW_MAX_ATTEMPTS` to the exact approved count in the host's shell,
then explicitly start only the reviewer profile/service. Never enable it
just to test deployment. Keep real-site capture off until its separate
network-egress boundary is fixed and tested. The existing staged fixture
capture is separate work and must remain clearly labelled sample.

## Deployment notes

Publish this feature only after mocked tests and visual inspection pass.
Preserve the VPS's local app `FORWARDED_ALLOW_IPS` setting when updating
Compose: the trusted IP is that host's confirmed Caddy/Docker gateway, not
a portable constant. Keep the public app port on `127.0.0.1:8000`.
The new tables are additive; existing case/review/evidence rows are not reset.
Back up the SQLite database consistently before any deployment migration.

Tests use fake replies and temporary databases only. No real API calls are
needed to test queue selection, atomic claims, deduplication, allowance,
restarts, errors, rendering or disabled mode.
