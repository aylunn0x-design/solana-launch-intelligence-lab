#!/usr/bin/env bash
#
# Container entrypoint.
#
# Applies database migrations before serving, so a fresh deploy against an
# empty database comes up with a schema rather than 500s on every query.
# Guarded by RUN_MIGRATIONS_ON_START because running migrations at boot is only
# safe while a single instance starts at a time — which is the case on the free
# plan this project targets. Scale past one instance and you should turn this
# off and run `alembic upgrade head` as a separate deploy step instead.
set -euo pipefail

if [[ "${RUN_MIGRATIONS_ON_START:-false}" == "true" ]]; then
  echo "[entrypoint] applying database migrations..."
  # Fail loudly: serving against a half-migrated schema is worse than not
  # starting, because the errors would look like application bugs.
  alembic upgrade head
  echo "[entrypoint] migrations applied."
else
  echo "[entrypoint] RUN_MIGRATIONS_ON_START is not 'true'; skipping migrations."
fi

# Render (and most hosts) inject the port to bind. Default to 8000 locally.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
