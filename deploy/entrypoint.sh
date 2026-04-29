#!/usr/bin/env bash
set -euo pipefail

if [[ "${AUTO_MIGRATE_ON_START:-true}" == "true" ]]; then
  echo "Running database migrations..."
  alembic upgrade head
fi

exec supervisord -c /app/deploy/supervisord.conf
