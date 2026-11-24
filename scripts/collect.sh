#!/usr/bin/env bash
#
# One collection cycle. Run this on a schedule to build the inception cohort.
#
# WHY THIS EXISTS
# ---------------
# The new-pools feed only covers the most recent few minutes of launches and
# cannot be paged back further (page 11 returns HTTP 401). So the unbiased
# inception cohort cannot be backfilled — it has to be *accumulated* by polling
# often enough that no launch window is missed. One sweep of all 10 pages covers
# roughly the last 6 minutes, so a 5-minute schedule keeps the cohort complete:
#
#   */5 * * * * /path/to/scripts/collect.sh >> /var/log/solana-lab.log 2>&1
#
# Repeated polling is also what turns single snapshots into a longitudinal
# panel, which is what liquidity-variability features need.
#
# Usage:
#   scripts/collect.sh              # one cycle
#   BACKFILL_POOLS=40 scripts/collect.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)/backend"

# Use the project's virtualenv if present, otherwise whatever python is active.
if [[ -x "$BACKEND_DIR/.venv/bin/python" ]]; then
  PY="$BACKEND_DIR/.venv/bin/python"
else
  PY="$(command -v python3)"
fi

: "${BACKFILL_POOLS:=25}"
: "${NEW_POOL_PAGES:=10}"

cd "$BACKEND_DIR"
export PYTHONPATH=.

echo "=== collection cycle $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

# 1. Inception cohort: sweep every page of the new-pools feed. This is the only
#    unbiased sample, and it is lost forever if we miss the window.
echo "--- discovering new launches"
"$PY" -m app.cli ingest --feed new --pages "$NEW_POOL_PAGES"

# 2. Historical candles for pools old enough to have them, skipping pools whose
#    history we already hold so the rate-limit budget extends coverage instead
#    of re-fetching.
echo "--- backfilling history"
"$PY" -m app.cli backfill \
  --timeframe hour \
  --limit-pools "$BACKFILL_POOLS" \
  --min-age-minutes 90 \
  --max-age-days 30 \
  --order oldest \
  --only-missing

echo "--- current state"
"$PY" -m app.cli status

echo "=== cycle complete ==="
