#!/bin/sh
# Apply database migrations (retrying while Postgres starts), then exec the server.
set -e

attempt=1
until python -m alembic upgrade head; do
  if [ "$attempt" -ge 20 ]; then
    echo '{"event": "migrations_failed", "level": "error", "hint": "check DATABASE_URL and that the db service is healthy"}'
    exit 1
  fi
  echo "{\"event\": \"migrations_retry\", \"level\": \"warning\", \"attempt\": $attempt}"
  attempt=$((attempt + 1))
  sleep 2
done

exec "$@"
