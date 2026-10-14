#!/usr/bin/env bash
# Starts a local wrangler dev server, runs both test suites, then stops it.
set -u
cd "$(dirname "$0")/.."
PORT=${PORT:-8787}
export BINGO_URL=${BINGO_URL:-http://localhost:$PORT} HOST_PIN=${HOST_PIN:-ribeye} SHOTS=${SHOTS:-$(mktemp -d)}
npx --yes wrangler@latest dev --port "$PORT" --local --var "HOST_PIN:$HOST_PIN" > /tmp/bingo-wrangler.log 2>&1 &
WR=$!
trap 'kill $WR 2>/dev/null; pkill -P $WR 2>/dev/null' EXIT
for _ in $(seq 1 90); do curl -sf -m 5 "$BINGO_URL/api/state" >/dev/null && break; sleep 2; done
status=0
python3 tests/e2e.py || status=1
python3 tests/test_names.py || status=1
echo "Screenshots: $SHOTS"
exit $status
