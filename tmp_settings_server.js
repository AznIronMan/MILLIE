"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const {
  isProtectedSecret,
  keySource,
  protectSecret,
  secretStorageSummary,
} = require("./tmp_settings_crypto");

const ROOT_DIR = __dirname;
const SETTINGS_DB = path.join(ROOT_DIR, "millie.settings");
const DEFAULT_HOST = "127.0.0.1";
const DEFAULT_PORT = 22011;

const PROVIDERS = ["", "openai", "claude", "gemini", "xai", "local"];
const THINKING_LEVELS = ["", "low", "med", "high", "xhigh"];
const ACCOUNT_TYPES = ["imap", "smtp"];
const ACCOUNT_SECURITY = ["", "none", "starttls", "ssl_tls"];
const ACCOUNT_AUTH = ["password", "oauth", "none"];
const AUTOMATION_LEVELS = ["observe", "review", "auto_internal", "provider_write"];
const ICLOUD_DOMAINS = new Set(["icloud.com", "me.com", "mac.com"]);

function parseArgs(argv) {
  const args = {
    host: process.env.MILLIE_SETTINGS_HOST || DEFAULT_HOST,
    port: Number(process.env.MILLIE_SETTINGS_PORT || DEFAULT_PORT),
    initOnly: false,
    dump: false,
  };

  for (let index = 2; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--host") {
      args.host = argv[index + 1];
      index += 1;
    } else if (arg === "--port") {
      args.port = Number(argv[index + 1]);
      index += 1;
    } else if (arg === "--init-only") {
      args.initOnly = true;
    } else if (arg === "--dump") {
      args.dump = true;
    } else if (arg === "--help" || arg === "-h") {
      printHelp();
      process.exit(0);
    }
  }

  if (!Number.isInteger(args.port) || args.port < 1 || args.port > 65535) {
    throw new Error("Invalid port.");
  }

  return args;
}

function printHelp() {
  console.log(`Usage:
  ./tmp_settings.sh
  node tmp_settings_server.js [--host 127.0.0.1] [--port 22011]
  node tmp_settings_server.js --init-only
  node tmp_settings_server.js --dump`);
}

function sqlite(input, options = {}) {
  const args = options.json ? ["-json", SETTINGS_DB] : [SETTINGS_DB];
  const result = spawnSync("sqlite3", args, {
    cwd: ROOT_DIR,
    input,
    encoding: "utf8",
  });

  if (result.error) {
    throw result.error;
  }

  if (result.status !== 0) {
    throw new Error(result.stderr || "sqlite3 command failed.");
  }

  return result.stdout || "";
}

function sqlQuote(value) {
  return `'${String(value ?? "").replaceAll("'", "''")}'`;
}

function optionList(values) {
  return JSON.stringify(values);
}

function randomInstanceId() {
  return crypto.randomBytes(8).toString("hex");
}

function buildSeedRows(instanceId) {
  const rows = [
    {
      key: "instance_id",
      value: instanceId,
      description: "Unique ID used for generated local data and log database names.",
      options: "",
      secret: false,
    },
    {
      key: "service_mail_domain",
      value: "millie.cnbsk.cloud",
      description: "Primary MILLIE mailbox DNS domain. Hosted identities are canonicalized here, for example geon@millie.cnbsk.cloud.",
      options: "",
      secret: false,
    },
    {
      key: "service_mail_local_domain",
      value: "MILLIE",
      description: "Local development mailbox domain alias. This keeps addresses like geon@MILLIE usable while the primary domain is hosted DNS.",
      options: "",
      secret: false,
    },
    {
      key: "service_mail_domain_aliases",
      value: "MILLIE,millie",
      description: "Comma-separated additional domains accepted as aliases for the primary MILLIE mailbox domain.",
      options: "",
      secret: false,
    },
    {
      key: "database_mode",
      value: "sqlite",
      description: "Primary database engine. Use sqlite for local single-file storage or postgres for an external PostgreSQL database.",
      options: optionList(["sqlite", "postgres"]),
      secret: false,
    },
    {
      key: "sqlite_location",
      value: "",
      description: "SQLite data location. If blank, MILLIE should create data/<instance_id>.millie from the project root.",
      options: "",
      secret: false,
    },
    {
      key: "postgres_host_ip",
      value: "",
      description: "PostgreSQL host name or IP address when database_mode is postgres.",
      options: "",
      secret: false,
    },
    {
      key: "postgres_port",
      value: "5432",
      description: "PostgreSQL port when database_mode is postgres.",
      options: "",
      secret: false,
    },
    {
      key: "postgres_username",
      value: "",
      description: "PostgreSQL username when database_mode is postgres.",
      options: "",
      secret: false,
    },
    {
      key: "postgres_password",
      value: "",
      description: "PostgreSQL password when database_mode is postgres. Encrypted at rest in millie.settings.",
      options: "",
      secret: true,
    },
    {
      key: "postgres_database",
      value: "",
      description: "PostgreSQL database name when database_mode is postgres.",
      options: "",
      secret: false,
    },
    ...apiRows("main", "Primary API provider and model configuration."),
    ...apiRows("second", "Backup API provider and model configuration."),
    ...apiRows("third", "Third tier API provider and model configuration."),
    {
      key: "microsoft_oauth_tenant",
      value: "organizations",
      description: "Microsoft OAuth tenant for Outlook/Exchange accounts. Use organizations for work or school accounts, common for mixed accounts, or a tenant ID/domain.",
      options: "",
      secret: false,
    },
    {
      key: "microsoft_oauth_client_id",
      value: "",
      description: "Microsoft Entra application client ID used for Outlook/Exchange OAuth.",
      options: "",
      secret: false,
    },
    {
      key: "microsoft_oauth_client_secret",
      value: "",
      description: "Optional Microsoft Entra client secret value used for token exchange. This is the secret value string, not the secret ID. Encrypted at rest if used.",
      options: "",
      secret: true,
    },
    {
      key: "microsoft_oauth_client_secret_id",
      value: "",
      description: "Optional Microsoft Entra client secret ID for reference only. OAuth token exchange uses the secret value, not this ID.",
      options: "",
      secret: false,
    },
    {
      key: "microsoft_oauth_redirect_uri",
      value: "http://localhost:22013/oauth/microsoft/callback",
      description: "Redirect URI registered in Entra and used by MILLIE's local OAuth callback handler.",
      options: "",
      secret: false,
    },
    {
      key: "microsoft_oauth_scopes",
      value: "offline_access https://outlook.office.com/IMAP.AccessAsUser.All",
      description: "Space-separated Microsoft OAuth scopes for Outlook/Exchange access.",
      options: "",
      secret: false,
    },
    {
      key: "microsoft_oauth_access_token",
      value: "",
      description: "Microsoft OAuth access token for Outlook/Exchange. Generated by the temporary OAuth helper and encrypted at rest.",
      options: "",
      secret: true,
    },
    {
      key: "microsoft_oauth_refresh_token",
      value: "",
      description: "Microsoft OAuth refresh token for Outlook/Exchange. Generated by the temporary OAuth helper and encrypted at rest.",
      options: "",
      secret: true,
    },
    {
      key: "microsoft_oauth_expires_at",
      value: "",
      description: "UTC ISO timestamp when the current Microsoft OAuth access token expires.",
      options: "",
      secret: false,
    },
    {
      key: "microsoft_oauth_token_scope",
      value: "",
      description: "Scopes returned by Microsoft for the current Outlook/Exchange OAuth token.",
      options: "",
      secret: false,
    },
    {
      key: "logging_type",
      value: "files",
      description: "Logging target. files appends to logs/YYYYMMDD.log. sqlite creates data/<instance_id>.logdb when possible. cnblogger is reserved and disabled for now.",
      options: optionList(["files", "sqlite", "cnblogger"]),
      secret: false,
    },
    {
      key: "automation_level",
      value: "observe",
      description: "Maximum autonomous MILLIE automation level. observe stores suggestions only; review records user feedback; auto_internal may apply MILLIE-only mailbox changes; provider_write is reserved and requires a second switch.",
      options: optionList(AUTOMATION_LEVELS),
      secret: false,
    },
    {
      key: "automation_provider_write_enabled",
      value: "false",
      description: "Second switch for future provider-side automation. Must be true and automation_level must be provider_write. Manifest purge tools remain separate.",
      options: optionList(["false", "true"]),
      secret: false,
    },
    {
      key: "sync_stale_after_hours",
      value: "24",
      description: "Hours after the last successful live folder sync before Ops marks that folder stale.",
      options: "",
      secret: false,
    },
  ];

  return rows.map((row, index) => ({
    ...row,
    order: (index + 1) * 10,
  }));
}

function apiRows(prefix, label) {
  return [
    {
      key: `${prefix}_api_provider`,
      value: prefix === "main" ? "local" : "",
      description: `${label} Valid values are openai, claude, gemini, xai, local, or blank for an unused fallback tier.`,
      options: optionList(PROVIDERS),
      secret: false,
    },
    {
      key: `${prefix}_api_key`,
      value: "",
      description: `${label} API key. Leave empty for local models. Encrypted at rest in millie.settings.`,
      options: "",
      secret: true,
    },
    {
      key: `${prefix}_api_model`,
      value: "",
      description: `${label} Model identifier expected by the provider. For local, use the path to the local LLM.`,
      options: "",
      secret: false,
    },
    {
      key: `${prefix}_api_thinking`,
      value: "",
      description: `${label} Thinking level. Blank means med or the LLM/provider default.`,
      options: optionList(THINKING_LEVELS),
      secret: false,
    },
  ];
}

function initSettings() {
  fs.mkdirSync(ROOT_DIR, { recursive: true });

  sqlite(`
CREATE TABLE IF NOT EXISTS settings (
  setting_key TEXT PRIMARY KEY,
  setting_value TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  options TEXT NOT NULL DEFAULT '',
  is_secret INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings_meta (
  meta_key TEXT PRIMARY KEY,
  meta_value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mail_accounts (
  id TEXT PRIMARY KEY,
  account_type TEXT NOT NULL CHECK(account_type IN ('imap', 'smtp')),
  display_name TEXT NOT NULL DEFAULT '',
  email_address TEXT NOT NULL DEFAULT '',
  host TEXT NOT NULL DEFAULT '',
  port TEXT NOT NULL DEFAULT '',
  username TEXT NOT NULL DEFAULT '',
  password TEXT NOT NULL DEFAULT '',
  security TEXT NOT NULL DEFAULT '',
  auth_method TEXT NOT NULL DEFAULT 'password',
  enabled INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO settings_meta (meta_key, meta_value)
VALUES ('schema_version', '3')
ON CONFLICT(meta_key) DO UPDATE SET meta_value = excluded.meta_value;

INSERT INTO settings_meta (meta_key, meta_value)
VALUES ('created_at', datetime('now'))
ON CONFLICT(meta_key) DO NOTHING;
`);

  const existingInstance = getSettingValue("instance_id");
  const instanceId = existingInstance || randomInstanceId();
  const seedRows = buildSeedRows(instanceId);
  const statements = seedRows.map((row) => `
INSERT INTO settings (
  setting_key,
  setting_value,
  description,
  options,
  is_secret,
  sort_order,
  updated_at
)
VALUES (
  ${sqlQuote(row.key)},
  ${sqlQuote(row.value)},
  ${sqlQuote(row.description)},
  ${sqlQuote(row.options)},
  ${row.secret ? 1 : 0},
  ${row.order},
  datetime('now')
)
ON CONFLICT(setting_key) DO UPDATE SET
  description = excluded.description,
  options = excluded.options,
  is_secret = excluded.is_secret,
  sort_order = excluded.sort_order;
`).join("\n");

  sqlite(statements);
  protectStoredSecrets();
}

function getSettingValue(key) {
  const rows = queryRows(`
SELECT setting_value
FROM settings
WHERE setting_key = ${sqlQuote(key)}
LIMIT 1;
`);
  return rows[0]?.setting_value || "";
}

function queryRows(query) {
  const output = sqlite(query, { json: true }).trim();
  if (!output) {
    return [];
  }
  return JSON.parse(output);
}

function readSettings() {
  return queryRows(`
SELECT
  setting_key,
  setting_value,
  description,
  options,
  is_secret,
  sort_order,
  updated_at
FROM settings
ORDER BY sort_order, setting_key;
`);
}

function settingsForApi() {
  return readSettings().map((row) => {
    const isSecret = Number(row.is_secret) === 1;
    const hasValue = String(row.setting_value || "").length > 0;
    return {
      setting_key: row.setting_key,
      setting_value: isSecret ? "" : row.setting_value,
      description: row.description,
      options: parseOptions(row.options),
      is_secret: isSecret,
      has_value: hasValue,
      is_protected: isSecret && hasValue && isProtectedSecret(row.setting_value),
      display_value: isSecret && hasValue ? "********" : row.setting_value,
      updated_at: row.updated_at,
    };
  });
}

function readMailAccounts() {
  return queryRows(`
SELECT
  id,
  account_type,
  display_name,
  email_address,
  host,
  port,
  username,
  password,
  security,
  auth_method,
  enabled,
  sort_order,
  updated_at
FROM mail_accounts
ORDER BY account_type, sort_order, display_name, email_address, id;
`);
}

function accountsForApi() {
  return readMailAccounts().map((row) => {
    const hasPassword = String(row.password || "").length > 0;
    return {
      id: row.id,
      account_type: row.account_type,
      display_name: row.display_name,
      email_address: row.email_address,
      host: row.host,
      port: row.port,
      username: row.username,
      password: "",
      has_password: hasPassword,
      security: row.security,
      auth_method: row.auth_method,
      enabled: Number(row.enabled) === 1,
      sort_order: Number(row.sort_order) || 0,
      updated_at: row.updated_at,
    };
  });
}

function parseOptions(rawOptions) {
  if (!rawOptions) {
    return [];
  }
  try {
    const parsed = JSON.parse(rawOptions);
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return String(rawOptions)
      .split(",")
      .map((option) => option.trim())
      .filter(Boolean);
  }
}

function saveSettings(payload) {
  const values = payload?.values;
  const clearSecrets = new Set(payload?.clearSecrets || []);
  if (!values || typeof values !== "object" || Array.isArray(values)) {
    throw httpError(400, "Request body must include a values object.");
  }

  const currentRows = readSettings();
  const byKey = new Map(currentRows.map((row) => [row.setting_key, row]));
  const statements = [];

  for (const [key, rawValue] of Object.entries(values)) {
    const row = byKey.get(key);
    if (!row) {
      throw httpError(400, `Unknown setting: ${key}`);
    }

    const isSecret = Number(row.is_secret) === 1;
    const value = String(rawValue ?? "");

    if (isSecret && value === "" && !clearSecrets.has(key)) {
      continue;
    }

    if (isSecret && clearSecrets.has(key) && value !== "") {
      throw httpError(400, `Cannot clear and set ${key} in the same save.`);
    }

    const nextValue = clearSecrets.has(key) ? "" : value;
    validateValue(row, nextValue);
    const storedValue = isSecret && nextValue !== ""
      ? protectSecret(nextValue, settingSecretContext(key))
      : nextValue;
    statements.push(`
UPDATE settings
SET setting_value = ${sqlQuote(storedValue)},
    updated_at = datetime('now')
WHERE setting_key = ${sqlQuote(key)};
`);
  }

  if (Array.isArray(payload?.accounts)) {
    statements.push(...buildMailAccountStatements(payload.accounts));
  }

  if (statements.length > 0) {
    sqlite(`BEGIN;\n${statements.join("\n")}\nCOMMIT;`);
  }
}

function validateValue(row, value) {
  const options = parseOptions(row.options);
  if (options.length > 0 && !options.includes(value)) {
    throw httpError(400, `${row.setting_key} must be one of: ${options.join(", ") || "(blank)"}`);
  }

  if (row.setting_key === "postgres_port" && value !== "" && !/^[0-9]+$/.test(value)) {
    throw httpError(400, "postgres_port must be numeric.");
  }

  if (row.setting_key === "sync_stale_after_hours" && value !== "" && !/^[0-9]+$/.test(value)) {
    throw httpError(400, "sync_stale_after_hours must be numeric.");
  }

  if (row.setting_key === "service_mail_domain") {
    if (!isValidDomainToken(value)) {
      throw httpError(400, "service_mail_domain must be a domain such as millie.cnbsk.cloud.");
    }
  }

  if (row.setting_key === "service_mail_local_domain" && value !== "" && !isValidDomainToken(value)) {
    throw httpError(400, "service_mail_local_domain must use letters, numbers, dots, or hyphens.");
  }

  if (row.setting_key === "service_mail_domain_aliases") {
    const aliases = value.split(",").map((item) => item.trim()).filter(Boolean);
    if (aliases.some((alias) => !isValidDomainToken(alias))) {
      throw httpError(400, "service_mail_domain_aliases must be comma-separated domain tokens.");
    }
  }
}

function isValidDomainToken(value) {
  return /^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$/.test(String(value || ""));
}

function buildMailAccountStatements(accounts) {
  const existing = new Map(readMailAccounts().map((account) => [account.id, account]));
  const normalized = accounts.map((account, index) => normalizeMailAccount(account, index, existing));
  const seen = new Set();

  for (const account of normalized) {
    if (seen.has(account.id)) {
      throw httpError(400, `Duplicate mail account id: ${account.id}`);
    }
    seen.add(account.id);
  }

  const statements = [];
  if (normalized.length === 0) {
    statements.push("DELETE FROM mail_accounts;");
  } else {
    statements.push(`
DELETE FROM mail_accounts
WHERE id NOT IN (${normalized.map((account) => sqlQuote(account.id)).join(", ")});
`);
  }

  for (const account of normalized) {
    statements.push(`
INSERT INTO mail_accounts (
  id,
  account_type,
  display_name,
  email_address,
  host,
  port,
  username,
  password,
  security,
  auth_method,
  enabled,
  sort_order,
  updated_at
)
VALUES (
  ${sqlQuote(account.id)},
  ${sqlQuote(account.account_type)},
  ${sqlQuote(account.display_name)},
  ${sqlQuote(account.email_address)},
  ${sqlQuote(account.host)},
  ${sqlQuote(account.port)},
  ${sqlQuote(account.username)},
  ${sqlQuote(account.password)},
  ${sqlQuote(account.security)},
  ${sqlQuote(account.auth_method)},
  ${account.enabled ? 1 : 0},
  ${account.sort_order},
  datetime('now')
)
ON CONFLICT(id) DO UPDATE SET
  account_type = excluded.account_type,
  display_name = excluded.display_name,
  email_address = excluded.email_address,
  host = excluded.host,
  port = excluded.port,
  username = excluded.username,
  password = excluded.password,
  security = excluded.security,
  auth_method = excluded.auth_method,
  enabled = excluded.enabled,
  sort_order = excluded.sort_order,
  updated_at = excluded.updated_at;
`);
  }

  return statements;
}

function normalizeMailAccount(account, index, existing) {
  if (!account || typeof account !== "object" || Array.isArray(account)) {
    throw httpError(400, "Mail accounts must be objects.");
  }

  const id = String(account.id || crypto.randomUUID());
  const accountType = String(account.account_type || "");
  const emailAddress = String(account.email_address ?? "").trim();
  let displayName = String(account.display_name ?? "");
  let host = String(account.host ?? "").trim();
  let port = String(account.port ?? "").trim();
  let username = String(account.username ?? "").trim();
  let security = String(account.security ?? "");
  let authMethod = String(account.auth_method || "password");

  const provider = mailProviderForAccount(emailAddress, host);
  if (provider === "icloud") {
    if (!displayName) {
      displayName = "iCloud Mail";
    }
    if (accountType === "imap") {
      host = host || "imap.mail.me.com";
      port = port || "993";
      security = security || "ssl_tls";
      authMethod = authMethod || "password";
      username = username || icloudImapUsername(emailAddress);
    } else if (accountType === "smtp") {
      host = host || "smtp.mail.me.com";
      port = port || "587";
      security = security || "starttls";
      authMethod = authMethod || "password";
      username = username || emailAddress;
    }
  }

  if (!ACCOUNT_TYPES.includes(accountType)) {
    throw httpError(400, "Mail account type must be imap or smtp.");
  }

  if (!ACCOUNT_SECURITY.includes(security)) {
    throw httpError(400, "Mail account security must be blank, none, starttls, or ssl_tls.");
  }

  if (!ACCOUNT_AUTH.includes(authMethod)) {
    throw httpError(400, "Mail account auth method must be password, oauth, or none.");
  }

  if (port !== "" && !/^[0-9]+$/.test(port)) {
    throw httpError(400, "Mail account port must be numeric.");
  }

  const existingPassword = existing.get(id)?.password || "";
  const passwordInput = String(account.password ?? "");
  const clearPassword = Boolean(account.clear_password);
  const password = clearPassword
    ? ""
    : (passwordInput === "" ? existingPassword : protectSecret(passwordInput, accountPasswordContext(id)));

  return {
    id,
    account_type: accountType,
    display_name: displayName,
    email_address: emailAddress,
    host,
    port,
    username,
    password,
    security,
    auth_method: authMethod,
    enabled: Boolean(account.enabled),
    sort_order: (index + 1) * 10,
  };
}

function mailProviderForAccount(emailAddress, host) {
  const normalizedHost = String(host || "").trim().toLowerCase();
  if (normalizedHost === "imap.mail.me.com" || normalizedHost === "smtp.mail.me.com") {
    return "icloud";
  }
  const domain = String(emailAddress || "").split("@").pop().trim().toLowerCase();
  return ICLOUD_DOMAINS.has(domain) ? "icloud" : "";
}

function icloudImapUsername(emailAddress) {
  const local = String(emailAddress || "").split("@", 1)[0].trim();
  return local || String(emailAddress || "").trim();
}

function settingSecretContext(key) {
  return `settings:${key}`;
}

function accountPasswordContext(id) {
  return `mail_accounts:${id}:password`;
}

function protectStoredSecrets() {
  const statements = [];
  for (const row of queryRows("SELECT setting_key, setting_value FROM settings WHERE is_secret = 1;")) {
    const value = String(row.setting_value || "");
    if (value !== "" && !isProtectedSecret(value)) {
      statements.push(`
UPDATE settings
SET setting_value = ${sqlQuote(protectSecret(value, settingSecretContext(row.setting_key)))},
    updated_at = datetime('now')
WHERE setting_key = ${sqlQuote(row.setting_key)};
`);
    }
  }

  for (const row of queryRows("SELECT id, password FROM mail_accounts WHERE password <> '';")) {
    const value = String(row.password || "");
    if (value !== "" && !isProtectedSecret(value)) {
      statements.push(`
UPDATE mail_accounts
SET password = ${sqlQuote(protectSecret(value, accountPasswordContext(row.id)))},
    updated_at = datetime('now')
WHERE id = ${sqlQuote(row.id)};
`);
    }
  }

  if (statements.length > 0) {
    sqlite(`BEGIN;\n${statements.join("\n")}\nCOMMIT;`);
  }

  sqlite(`
INSERT INTO settings_meta (meta_key, meta_value)
VALUES ('secret_storage', ${sqlQuote(keySource())})
ON CONFLICT(meta_key) DO UPDATE SET meta_value = excluded.meta_value;
`);
}

function httpError(status, message) {
  const error = new Error(message);
  error.status = status;
  return error;
}

function sendJson(response, status, value) {
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  response.end(JSON.stringify(value, null, 2));
}

function sendText(response, status, value, type = "text/plain; charset=utf-8") {
  response.writeHead(status, {
    "content-type": type,
    "cache-control": "no-store",
  });
  response.end(value);
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    request.on("data", (chunk) => {
      chunks.push(chunk);
      if (Buffer.concat(chunks).length > 1024 * 128) {
        reject(httpError(413, "Request body is too large."));
        request.destroy();
      }
    });
    request.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    request.on("error", reject);
  });
}

async function handleRequest(request, response) {
  const requestUrl = new URL(request.url, "http://127.0.0.1");

  if (request.method === "GET" && requestUrl.pathname === "/") {
    sendText(response, 200, renderPage(), "text/html; charset=utf-8");
    return;
  }

  if (request.method === "GET" && requestUrl.pathname === "/api/settings") {
    sendJson(response, 200, {
      database: path.relative(ROOT_DIR, SETTINGS_DB),
      secret_storage: secretStorageSummary(),
      settings: settingsForApi(),
      accounts: accountsForApi(),
    });
    return;
  }

  if (request.method === "PUT" && requestUrl.pathname === "/api/settings") {
    const body = await readBody(request);
    saveSettings(JSON.parse(body || "{}"));
    sendJson(response, 200, {
      ok: true,
      secret_storage: secretStorageSummary(),
      settings: settingsForApi(),
      accounts: accountsForApi(),
    });
    return;
  }

  sendJson(response, 404, { error: "Not found." });
}

function renderPage() {
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MILLIE Settings</title>
  <style>
    :root {
      color-scheme: light;
      --page: #f6f7f9;
      --panel: #ffffff;
      --ink: #1d252d;
      --muted: #5e6a75;
      --line: #d7dde3;
      --accent: #176b5d;
      --accent-strong: #0e5146;
      --danger: #9d2f3a;
      --focus: #f2c14e;
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      background: var(--page);
      color: var(--ink);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 15px;
      line-height: 1.45;
    }

    header {
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      padding: 18px 24px;
    }

    main {
      padding: 20px 24px 96px;
    }

    h1 {
      margin: 0 0 4px;
      font-size: 22px;
      font-weight: 700;
      letter-spacing: 0;
    }

    .subhead {
      margin: 0;
      color: var(--muted);
      max-width: 960px;
    }

    .status {
      min-height: 24px;
      margin: 0 0 16px;
      color: var(--muted);
      font-weight: 600;
    }

    .status.error {
      color: var(--danger);
    }

    .status.saved {
      color: var(--accent-strong);
    }

    .table-wrap {
      overflow-x: auto;
      border: 1px solid var(--line);
      background: var(--panel);
    }

    .section-heading {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin: 28px 0 12px;
    }

    .button-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: flex-end;
    }

    h2 {
      margin: 0;
      font-size: 18px;
      letter-spacing: 0;
    }

    .accounts {
      display: grid;
      gap: 12px;
    }

    .account-row {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 14px;
    }

    .account-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }

    .account-title {
      display: flex;
      align-items: center;
      gap: 10px;
      font-weight: 700;
    }

    .account-title input {
      width: auto;
      min-height: auto;
      margin: 0;
    }

    .account-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(150px, 1fr));
      gap: 12px;
    }

    .field label {
      display: block;
      margin-bottom: 5px;
      color: #4d5965;
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }

    .empty {
      border: 1px dashed #b5bec8;
      background: #ffffff;
      color: var(--muted);
      padding: 14px;
    }

    table {
      width: 100%;
      min-width: 980px;
      border-collapse: collapse;
      table-layout: fixed;
    }

    th,
    td {
      border-bottom: 1px solid var(--line);
      padding: 12px;
      vertical-align: top;
      text-align: left;
    }

    th {
      background: #edf1f4;
      color: #32404c;
      font-size: 12px;
      text-transform: uppercase;
      font-weight: 700;
    }

    tbody tr:last-child td {
      border-bottom: 0;
    }

    .key {
      width: 200px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 13px;
      overflow-wrap: anywhere;
    }

    .value {
      width: 260px;
    }

    .description {
      width: 380px;
      color: #33424f;
    }

    .options {
      width: 180px;
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 12px;
      overflow-wrap: anywhere;
    }

    input,
    select {
      width: 100%;
      min-height: 38px;
      border: 1px solid #aeb8c2;
      border-radius: 6px;
      background: #ffffff;
      color: var(--ink);
      padding: 8px 10px;
      font: inherit;
    }

    input:focus,
    select:focus,
    button:focus {
      outline: 3px solid var(--focus);
      outline-offset: 1px;
    }

    .secret-tools {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
    }

    .secret-tools input {
      width: auto;
      min-height: auto;
      margin: 0;
    }

    .actions {
      position: fixed;
      right: 0;
      bottom: 0;
      left: 0;
      display: flex;
      justify-content: flex-end;
      gap: 10px;
      padding: 14px 24px;
      background: rgba(255, 255, 255, 0.96);
      border-top: 1px solid var(--line);
      box-shadow: 0 -6px 18px rgba(29, 37, 45, 0.08);
    }

    button {
      min-height: 40px;
      border: 1px solid #9aa6b2;
      border-radius: 6px;
      background: #ffffff;
      color: var(--ink);
      padding: 8px 14px;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }

    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #ffffff;
    }

    button.primary:hover {
      background: var(--accent-strong);
    }

    button:hover {
      border-color: #6c7884;
    }

    @media (max-width: 720px) {
      header,
      main,
      .actions {
        padding-right: 14px;
        padding-left: 14px;
      }

      .actions {
        justify-content: stretch;
      }

      .section-heading {
        align-items: stretch;
        flex-direction: column;
      }

      .button-row {
        justify-content: stretch;
      }

      .account-grid {
        grid-template-columns: 1fr;
      }

      button {
        flex: 1;
      }
    }
  </style>
</head>
<body>
  <header>
    <h1>MILLIE Settings</h1>
    <p class="subhead">Editing <code>millie.settings</code>. Secret fields are encrypted at rest and existing saved values stay hidden unless replaced or cleared.</p>
  </header>
  <main>
    <p id="status" class="status">Loading settings...</p>
    <div class="table-wrap">
      <table aria-label="MILLIE settings">
        <thead>
          <tr>
            <th class="key">Setting</th>
            <th class="value">Value</th>
            <th class="description">Description</th>
            <th class="options">Options</th>
          </tr>
        </thead>
        <tbody id="settings-body"></tbody>
      </table>
    </div>
    <section aria-labelledby="imap-heading">
      <div class="section-heading">
        <h2 id="imap-heading">IMAP Retrieval Accounts</h2>
        <div class="button-row">
          <button type="button" data-add-account="imap">Add IMAP</button>
          <button type="button" data-add-account="imap" data-provider="icloud">Add iCloud IMAP</button>
        </div>
      </div>
      <div id="imap-accounts" class="accounts"></div>
    </section>
    <section aria-labelledby="smtp-heading">
      <div class="section-heading">
        <h2 id="smtp-heading">SMTP Sending Accounts</h2>
        <div class="button-row">
          <button type="button" data-add-account="smtp">Add SMTP</button>
          <button type="button" data-add-account="smtp" data-provider="icloud">Add iCloud SMTP</button>
        </div>
      </div>
      <div id="smtp-accounts" class="accounts"></div>
    </section>
  </main>
  <div class="actions">
    <button id="cancel" type="button">Cancel</button>
    <button id="save" class="primary" type="button">Save changes</button>
  </div>
  <script>
    const state = {
      settings: [],
      accounts: [],
      dirty: false,
    };

    const body = document.querySelector("#settings-body");
    const imapAccounts = document.querySelector("#imap-accounts");
    const smtpAccounts = document.querySelector("#smtp-accounts");
    const status = document.querySelector("#status");
    const saveButton = document.querySelector("#save");
    const cancelButton = document.querySelector("#cancel");
    const accountSecurityOptions = ["", "none", "starttls", "ssl_tls"];
    const accountAuthOptions = ["password", "oauth", "none"];

    function setStatus(message, kind = "") {
      status.textContent = message;
      status.className = "status" + (kind ? " " + kind : "");
    }

    function optionLabel(value) {
      return value === "" ? "(blank/default)" : value;
    }

    function renderControl(setting) {
      const name = setting.setting_key;
      const value = setting.setting_value || "";

      if (setting.is_secret) {
        const wrapper = document.createElement("div");
        const input = document.createElement("input");
        input.type = "password";
        input.name = name;
        input.dataset.setting = name;
        input.dataset.secret = "true";
        input.placeholder = setting.has_value ? "saved value hidden" : "";
        input.autocomplete = "off";
        input.addEventListener("input", markDirty);
        wrapper.append(input);

        const label = document.createElement("label");
        label.className = "secret-tools";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.dataset.clearSecret = name;
        checkbox.addEventListener("change", () => {
          input.disabled = checkbox.checked;
          if (checkbox.checked) {
            input.value = "";
          }
          markDirty();
        });
        label.append(checkbox, document.createTextNode("Clear saved value"));
        wrapper.append(label);
        return wrapper;
      }

      if (setting.options.length > 0) {
        const select = document.createElement("select");
        select.name = name;
        select.dataset.setting = name;
        for (const option of setting.options) {
          const optionNode = document.createElement("option");
          optionNode.value = option;
          optionNode.textContent = optionLabel(option);
          select.append(optionNode);
        }
        select.value = value;
        select.addEventListener("change", markDirty);
        return select;
      }

      const input = document.createElement("input");
      input.type = "text";
      input.name = name;
      input.dataset.setting = name;
      input.value = value;
      input.addEventListener("input", markDirty);
      return input;
    }

    function renderSettings(settings) {
      body.replaceChildren();

      for (const setting of settings) {
        const row = document.createElement("tr");

        const keyCell = document.createElement("td");
        keyCell.className = "key";
        keyCell.textContent = setting.setting_key;

        const valueCell = document.createElement("td");
        valueCell.className = "value";
        valueCell.append(renderControl(setting));

        const descriptionCell = document.createElement("td");
        descriptionCell.className = "description";
        descriptionCell.textContent = setting.description;

        const optionsCell = document.createElement("td");
        optionsCell.className = "options";
        optionsCell.textContent = setting.options.length ? setting.options.map(optionLabel).join(", ") : "";

        row.append(keyCell, valueCell, descriptionCell, optionsCell);
        body.append(row);
      }
    }

    function newAccountId() {
      if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
        return globalThis.crypto.randomUUID();
      }
      return "mail-" + Date.now() + "-" + Math.random().toString(16).slice(2);
    }

    function defaultAccount(type, provider = "") {
      const account = {
        id: newAccountId(),
        account_type: type,
        display_name: "",
        email_address: "",
        host: "",
        port: type === "imap" ? "993" : "587",
        username: "",
        password: "",
        has_password: false,
        security: type === "imap" ? "ssl_tls" : "starttls",
        auth_method: "password",
        enabled: true,
      };
      return applyProviderDefaults(account, provider);
    }

    function applyProviderDefaults(account, provider) {
      if (provider !== "icloud") {
        return account;
      }
      const next = { ...account };
      next.display_name = next.display_name || "iCloud Mail";
      if (next.account_type === "imap") {
        next.host = "imap.mail.me.com";
        next.port = "993";
        next.security = "ssl_tls";
        next.auth_method = "password";
      } else if (next.account_type === "smtp") {
        next.host = "smtp.mail.me.com";
        next.port = "587";
        next.security = "starttls";
        next.auth_method = "password";
      }
      return next;
    }

    function selectControl(options, value) {
      const select = document.createElement("select");
      for (const option of options) {
        const optionNode = document.createElement("option");
        optionNode.value = option;
        optionNode.textContent = optionLabel(option);
        select.append(optionNode);
      }
      select.value = value || "";
      return select;
    }

    function accountField(account, field, label, options = {}) {
      const wrapper = document.createElement("div");
      wrapper.className = "field";

      const labelNode = document.createElement("label");
      labelNode.textContent = label;
      wrapper.append(labelNode);

      let control;
      if (options.select) {
        control = selectControl(options.select, account[field]);
      } else {
        control = document.createElement("input");
        control.type = options.type || "text";
        control.value = account[field] || "";
        if (options.placeholder) {
          control.placeholder = options.placeholder;
        }
        if (options.autocomplete) {
          control.autocomplete = options.autocomplete;
        }
      }

      control.dataset.accountField = field;
      control.addEventListener("input", markDirty);
      control.addEventListener("change", markDirty);
      wrapper.append(control);

      if (field === "password") {
        const secretTools = document.createElement("label");
        secretTools.className = "secret-tools";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.dataset.accountClearPassword = "true";
        checkbox.addEventListener("change", () => {
          control.disabled = checkbox.checked;
          if (checkbox.checked) {
            control.value = "";
          }
          markDirty();
        });
        secretTools.append(checkbox, document.createTextNode("Clear saved value"));
        wrapper.append(secretTools);
      }

      return wrapper;
    }

    function renderAccount(account) {
      const row = document.createElement("div");
      row.className = "account-row";
      row.dataset.accountId = account.id;
      row.dataset.accountType = account.account_type;

      const top = document.createElement("div");
      top.className = "account-top";

      const enabledLabel = document.createElement("label");
      enabledLabel.className = "account-title";
      const enabled = document.createElement("input");
      enabled.type = "checkbox";
      enabled.dataset.accountField = "enabled";
      enabled.checked = Boolean(account.enabled);
      enabled.addEventListener("change", markDirty);
      enabledLabel.append(enabled, document.createTextNode(account.account_type.toUpperCase()));

      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "Remove";
      remove.addEventListener("click", () => {
        state.accounts = collectAccounts().filter((item) => item.id !== account.id);
        renderAccounts(state.accounts);
        markDirty();
      });

      top.append(enabledLabel, remove);

      const grid = document.createElement("div");
      grid.className = "account-grid";
      grid.append(
        accountField(account, "display_name", "Name"),
        accountField(account, "email_address", "Email", { type: "email", autocomplete: "email" }),
        accountField(account, "host", "Host"),
        accountField(account, "port", "Port", { type: "text" }),
        accountField(account, "username", "Username", { autocomplete: "username" }),
        accountField(account, "password", "Password", {
          type: "password",
          placeholder: account.has_password ? "saved value hidden" : "",
          autocomplete: "current-password",
        }),
        accountField(account, "security", "Security", { select: accountSecurityOptions }),
        accountField(account, "auth_method", "Auth", { select: accountAuthOptions })
      );

      row.append(top, grid);
      return row;
    }

    function renderAccounts(accounts) {
      imapAccounts.replaceChildren();
      smtpAccounts.replaceChildren();

      const imap = accounts.filter((account) => account.account_type === "imap");
      const smtp = accounts.filter((account) => account.account_type === "smtp");

      renderAccountGroup(imapAccounts, imap, "No IMAP accounts configured.");
      renderAccountGroup(smtpAccounts, smtp, "No SMTP accounts configured.");
    }

    function renderAccountGroup(container, accounts, emptyText) {
      if (accounts.length === 0) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = emptyText;
        container.append(empty);
        return;
      }

      for (const account of accounts) {
        container.append(renderAccount(account));
      }
    }

    function markDirty() {
      state.dirty = true;
      setStatus("Unsaved changes.");
    }

    async function loadSettings(message = "Settings loaded.") {
      const response = await fetch("/api/settings", { cache: "no-store" });
      if (!response.ok) {
        throw new Error("Failed to load settings.");
      }
      const data = await response.json();
      state.settings = data.settings;
      state.accounts = data.accounts || [];
      state.dirty = false;
      renderSettings(state.settings);
      renderAccounts(state.accounts);
      setStatus(message);
    }

    function collectPayload() {
      const values = {};
      const clearSecrets = [];
      for (const control of document.querySelectorAll("[data-setting]")) {
        values[control.dataset.setting] = control.value;
      }
      for (const checkbox of document.querySelectorAll("[data-clear-secret]")) {
        if (checkbox.checked) {
          clearSecrets.push(checkbox.dataset.clearSecret);
        }
      }

      return { values, clearSecrets, accounts: collectAccounts() };
    }

    function collectAccounts() {
      const accounts = [];
      for (const row of document.querySelectorAll("[data-account-id]")) {
        const account = {
          id: row.dataset.accountId,
          account_type: row.dataset.accountType,
          clear_password: Boolean(row.querySelector("[data-account-clear-password]")?.checked),
        };
        for (const control of row.querySelectorAll("[data-account-field]")) {
          const field = control.dataset.accountField;
          account[field] = control.type === "checkbox" ? control.checked : control.value;
        }
        accounts.push(account);
      }

      return accounts;
    }

    async function saveSettings() {
      setStatus("Saving changes...");
      const response = await fetch("/api/settings", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(collectPayload()),
      });

      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || "Failed to save settings.");
      }

      state.settings = data.settings;
      state.accounts = data.accounts || [];
      state.dirty = false;
      renderSettings(state.settings);
      renderAccounts(state.accounts);
      setStatus("Changes saved.", "saved");
    }

    saveButton.addEventListener("click", () => {
      saveSettings().catch((error) => setStatus(error.message, "error"));
    });

    cancelButton.addEventListener("click", () => {
      loadSettings("Changes canceled. Settings reloaded.").catch((error) => setStatus(error.message, "error"));
    });

    for (const button of document.querySelectorAll("[data-add-account]")) {
      button.addEventListener("click", () => {
        state.accounts = [
          ...collectAccounts(),
          defaultAccount(button.dataset.addAccount, button.dataset.provider || ""),
        ];
        renderAccounts(state.accounts);
        markDirty();
      });
    }

    loadSettings().catch((error) => setStatus(error.message, "error"));
  </script>
</body>
</html>`;
}

function startServer(args) {
  initSettings();
  const server = http.createServer((request, response) => {
    handleRequest(request, response).catch((error) => {
      const status = error.status || 500;
      sendJson(response, status, { error: error.message || "Server error." });
    });
  });

  server.listen(args.port, args.host, () => {
    console.log(`MILLIE temporary settings editor: http://${args.host}:${args.port}/`);
    console.log("Press Ctrl-C to stop.");
  });
}

function dumpSettings() {
  initSettings();
  console.log(JSON.stringify({
    secret_storage: secretStorageSummary(),
    settings: settingsForApi(),
    accounts: accountsForApi(),
  }, null, 2));
}

try {
  const args = parseArgs(process.argv);
  if (args.initOnly) {
    initSettings();
    console.log(`Initialized ${path.relative(ROOT_DIR, SETTINGS_DB)}`);
  } else if (args.dump) {
    dumpSettings();
  } else {
    startServer(args);
  }
} catch (error) {
  console.error(error.message);
  process.exit(1);
}
