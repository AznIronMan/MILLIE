#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${MILLIE_SETTINGS_PORT:-22011}"
HOST="${MILLIE_SETTINGS_HOST:-127.0.0.1}"
URL="http://${HOST}:${PORT}/"

if ! command -v node >/dev/null 2>&1; then
  echo "Node.js is required to run the temporary settings editor." >&2
  exit 1
fi

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "sqlite3 is required to run the temporary settings editor." >&2
  exit 1
fi

(
  sleep 1
  if command -v open >/dev/null 2>&1; then
    open "${URL}"
  else
    echo "Open ${URL} in your browser."
  fi
) &

exec node "${ROOT_DIR}/tmp_settings_server.js" --host "${HOST}" --port "${PORT}"
