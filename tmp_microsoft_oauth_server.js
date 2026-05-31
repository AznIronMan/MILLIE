"use strict";

const crypto = require("node:crypto");
const http = require("node:http");
const { spawnSync } = require("node:child_process");
const {
  isProtectedSecret,
  isSecretSetting,
  protectSecret,
  revealSecret,
} = require("./tmp_settings_crypto");

const SETTINGS_DB = `${__dirname}/millie.settings`;
const CALLBACK_PORT = 22013;
const CALLBACK_HOST = "127.0.0.1";

function sqlite(input, options = {}) {
  const args = options.json ? ["-json", SETTINGS_DB] : [SETTINGS_DB];
  const result = spawnSync("sqlite3", args, {
    cwd: __dirname,
    input,
    encoding: "utf8",
  });

  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(result.stderr || "sqlite3 failed.");
  }
  return result.stdout || "";
}

function quote(value) {
  return `'${String(value ?? "").replaceAll("'", "''")}'`;
}

function settings() {
  const output = sqlite("select setting_key, setting_value from settings;", { json: true }).trim();
  const rows = output ? JSON.parse(output) : [];
  return Object.fromEntries(rows.map((row) => {
    const value = isSecretSetting(row.setting_key) && isProtectedSecret(row.setting_value)
      ? revealSecret(row.setting_value, settingSecretContext(row.setting_key))
      : row.setting_value;
    return [row.setting_key, value];
  }));
}

function saveSetting(key, value, options = {}) {
  const secret = Boolean(options.secret ?? isSecretSetting(key));
  const storedValue = secret && value
    ? protectSecret(value, settingSecretContext(key))
    : value;
  sqlite(`
INSERT INTO settings (setting_key, setting_value, description, options, is_secret, sort_order, updated_at)
VALUES (${quote(key)}, ${quote(storedValue)}, '', '', ${secret ? 1 : 0}, 9990, datetime('now'))
ON CONFLICT(setting_key) DO UPDATE SET
  setting_value = excluded.setting_value,
  is_secret = excluded.is_secret,
  updated_at = excluded.updated_at;
`);
}

function settingSecretContext(key) {
  return `settings:${key}`;
}

function makeAuthUrl(config, state) {
  const tenant = config.microsoft_oauth_tenant || "organizations";
  const url = new URL(`https://login.microsoftonline.com/${encodeURIComponent(tenant)}/oauth2/v2.0/authorize`);
  url.searchParams.set("client_id", config.microsoft_oauth_client_id);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("redirect_uri", config.microsoft_oauth_redirect_uri);
  url.searchParams.set("response_mode", "query");
  url.searchParams.set("scope", config.microsoft_oauth_scopes);
  url.searchParams.set("state", state);
  url.searchParams.set("prompt", "consent");
  return url.toString();
}

async function exchangeCode(code, config) {
  const tenant = config.microsoft_oauth_tenant || "organizations";
  const body = new URLSearchParams({
    client_id: config.microsoft_oauth_client_id,
    scope: config.microsoft_oauth_scopes,
    code,
    redirect_uri: config.microsoft_oauth_redirect_uri,
    grant_type: "authorization_code",
  });

  if (config.microsoft_oauth_client_secret) {
    body.set("client_secret", config.microsoft_oauth_client_secret);
  }

  const response = await fetch(`https://login.microsoftonline.com/${encodeURIComponent(tenant)}/oauth2/v2.0/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = payload.error_description || payload.error || `HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

function storeToken(payload) {
  const expiresAt = new Date(Date.now() + Number(payload.expires_in || 0) * 1000).toISOString();
  saveSetting("microsoft_oauth_access_token", payload.access_token || "", { secret: true });
  saveSetting("microsoft_oauth_refresh_token", payload.refresh_token || "", { secret: true });
  saveSetting("microsoft_oauth_expires_at", expiresAt);
  saveSetting("microsoft_oauth_token_scope", payload.scope || "");
}

function html(title, body) {
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${title}</title>
  <style>
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 40px; color: #1d252d; }
    main { max-width: 760px; }
    code { background: #eef2f5; padding: 2px 5px; border-radius: 4px; }
  </style>
</head>
<body><main>${body}</main></body>
</html>`;
}

function send(response, status, body) {
  response.writeHead(status, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" });
  response.end(body);
}

async function main() {
  const state = `millie-${crypto.randomBytes(12).toString("hex")}`;
  saveSetting("microsoft_oauth_state", state);
  const config = settings();

  const missing = [
    "microsoft_oauth_tenant",
    "microsoft_oauth_client_id",
    "microsoft_oauth_redirect_uri",
    "microsoft_oauth_scopes",
  ].filter((key) => !config[key]);

  if (missing.length > 0) {
    throw new Error(`Missing settings: ${missing.join(", ")}`);
  }

  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, `http://localhost:${CALLBACK_PORT}`);
    if (url.pathname !== "/oauth/microsoft/callback") {
      send(response, 200, html("MILLIE Microsoft OAuth", `<h1>MILLIE Microsoft OAuth</h1><p>Waiting for Microsoft OAuth callback.</p>`));
      return;
    }

    const error = url.searchParams.get("error");
    if (error) {
      const description = url.searchParams.get("error_description") || error;
      send(response, 400, html("MILLIE OAuth Failed", `<h1>Authorization failed</h1><p>${escapeHtml(description)}</p>`));
      return;
    }

    const code = url.searchParams.get("code");
    if (!code) {
      send(response, 400, html("MILLIE OAuth Failed", "<h1>Authorization failed</h1><p>No code was returned.</p>"));
      return;
    }

    try {
      const token = await exchangeCode(code, settings());
      storeToken(token);
      send(response, 200, html("MILLIE OAuth Saved", "<h1>OAuth token saved</h1><p>The Microsoft OAuth token was saved to <code>millie.settings</code>. You can close this tab.</p>"));
      console.log("Microsoft OAuth token saved to millie.settings.");
      server.close();
    } catch (exchangeError) {
      send(response, 500, html("MILLIE OAuth Failed", `<h1>Token exchange failed</h1><p>${escapeHtml(exchangeError.message)}</p>`));
      console.error(`Token exchange failed: ${exchangeError.message}`);
    }
  });

  server.listen(CALLBACK_PORT, CALLBACK_HOST, () => {
    const authUrl = makeAuthUrl(config, state);
    console.log(`Listening at http://localhost:${CALLBACK_PORT}/oauth/microsoft/callback`);
    console.log("Open this URL to authorize Outlook IMAP:");
    console.log(authUrl);
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
