#!/bin/sh
# Applies Alembic migrations. Runs as the Railway pre-deploy command so that a
# failed migration stops the deploy instead of breaking a live service.
set -e

if [ -z "${DATABASE_URL}" ]; then
  echo "DATABASE_URL is not set; skipping migrations."
  exit 0
fi

alembic upgrade head
