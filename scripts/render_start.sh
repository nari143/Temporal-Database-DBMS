#!/bin/sh
# Start command for the Render free plan (see render.yaml).
#
# The free plan has no pre-deploy step and no separate background-worker
# service, so this script does both jobs before starting the web server.
set -e

# Apply pending migrations. Idempotent: a no-op when already up to date.
python -m scripts.migrate

# Generate demo price history, but only on a database with no prices yet.
python -m scripts.seed_demo --if-empty

# Limit-order matching, order expiry and the live price feed. Exactly one
# scheduler process runs next to the web server, so each job fires once per
# interval. The feed defaults to on here even if render.yaml was not synced.
export PRICE_FEED="${PRICE_FEED:-true}"
python -m backend.worker &

# One gunicorn process with threads keeps memory inside the free plan's limit.
exec gunicorn --bind "0.0.0.0:${PORT:-5000}" --workers 1 --threads 4 \
    --access-logfile - --error-logfile - backend.app:app
