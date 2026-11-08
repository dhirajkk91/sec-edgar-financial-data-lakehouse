#!/bin/sh
# One weeknight run: sync new filings and build Gold, then publish to the lake.
#
# Publishing runs even when some filings failed (sync exit 2): what did succeed
# is still worth publishing, and the failures retry tomorrow. If the Gold build
# failed, publish-lake sends Bronze and Silver but keeps the lake's last good Gold. It doesn't run when
# the sync couldn't start (exit 1), so a bad setup never overwrites the lake.
set -u

DATA_ROOT="${SEC_EDGAR_DATA_ROOT:-/data/lake}"
UNIVERSE="${SEC_EDGAR_UNIVERSE:-config/universe-sp500.json}"
# During a long run, rebuild Gold and publish after this many companies with new filings.
PUBLISH_EVERY="${SEC_EDGAR_PUBLISH_EVERY:-25}"

if [ -z "${SEC_EDGAR_LAKE:-}" ]; then
    echo "FAILED: SEC_EDGAR_LAKE is not set (s3://bucket/prefix)" >&2
    exit 1
fi

echo "$(date -u +%FT%TZ) sync start: $UNIVERSE -> $DATA_ROOT"
sec-edgar sync --universe "$UNIVERSE" --data-root "$DATA_ROOT" \
    --publish-every "$PUBLISH_EVERY" --lake "$SEC_EDGAR_LAKE"
sync_status=$?
if [ "$sync_status" -eq 1 ]; then
    echo "$(date -u +%FT%TZ) sync could not start; not publishing" >&2
    exit 1
fi

echo "$(date -u +%FT%TZ) publish start: $SEC_EDGAR_LAKE"
if ! sec-edgar publish-lake --data-root "$DATA_ROOT" --lake "$SEC_EDGAR_LAKE"; then
    exit 1
fi
echo "$(date -u +%FT%TZ) done (sync exit $sync_status)"
exit "$sync_status"
