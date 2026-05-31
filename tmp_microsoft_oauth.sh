#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v node >/dev/null 2>&1; then
  echo "Node.js is required to run the temporary Microsoft OAuth helper." >&2
  exit 1
fi

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "sqlite3 is required to run the temporary Microsoft OAuth helper." >&2
  exit 1
fi

exec node "${ROOT_DIR}/tmp_microsoft_oauth_server.js"
