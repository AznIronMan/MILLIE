"""Load local MILLIE settings with decrypted secrets for live tools."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


NODE_SETTINGS_SCRIPT = r"""
const { spawnSync } = require('node:child_process');
const { isProtectedSecret, isSecretSetting, revealSecret } = require('./tmp_settings_crypto');

function sqlite(sql) {
  const result = spawnSync('sqlite3', ['-json', 'millie.settings'], {
    input: sql,
    encoding: 'utf8',
  });
  if (result.status !== 0) {
    throw new Error(result.stderr || 'sqlite3 failed');
  }
  return result.stdout.trim() ? JSON.parse(result.stdout) : [];
}

function settingValue(row) {
  let value = row.setting_value || '';
  if (isSecretSetting(row.setting_key) && isProtectedSecret(value)) {
    value = revealSecret(value, `settings:${row.setting_key}`);
  }
  return value;
}

function accountPassword(row) {
  let value = row.password || '';
  if (isProtectedSecret(value)) {
    value = revealSecret(value, `mail_accounts:${row.id}:password`);
  }
  return value;
}

const settingsRows = sqlite('select setting_key, setting_value from settings;');
const accountRows = sqlite(`
  select id, account_type, display_name, email_address, host, port, username,
         password, security, auth_method, enabled, sort_order
  from mail_accounts
  order by account_type, sort_order, display_name, email_address, id;
`);

const settings = Object.fromEntries(settingsRows.map(row => [row.setting_key, settingValue(row)]));
const accounts = accountRows.map(row => ({
  id: row.id,
  account_type: row.account_type || '',
  display_name: row.display_name || '',
  email_address: row.email_address || '',
  host: row.host || '',
  port: row.port || '',
  username: row.username || '',
  password: accountPassword(row),
  security: row.security || '',
  auth_method: row.auth_method || 'password',
  enabled: Number(row.enabled) === 1,
  sort_order: Number(row.sort_order) || 0,
}));

console.log(JSON.stringify({ settings, accounts }));
"""


def load_local_settings() -> dict[str, Any]:
    """Return decrypted settings and mail accounts without logging secret values."""

    output = subprocess.check_output(
        ["node", "-e", NODE_SETTINGS_SCRIPT],
        cwd=PROJECT_ROOT,
        text=True,
    )
    return json.loads(output)
