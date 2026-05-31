"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const ROOT_DIR = __dirname;
const KEYCHAIN_SERVICE = "MILLIE settings encryption key";
const KEY_FILE = path.join(ROOT_DIR, ".private", "secrets", "millie_settings.key");
const ENCRYPTED_PREFIX = "millie:v1:aes-256-gcm";
const SECRET_SETTING_KEYS = new Set([
  "postgres_password",
  "main_api_key",
  "second_api_key",
  "third_api_key",
  "microsoft_oauth_client_secret",
  "microsoft_oauth_access_token",
  "microsoft_oauth_refresh_token",
]);

let cachedKey;
let cachedKeySource;

function isSecretSetting(key) {
  return SECRET_SETTING_KEYS.has(String(key));
}

function keySource() {
  ensureKey();
  return cachedKeySource;
}

function protectSecret(value, context) {
  const plain = String(value ?? "");
  if (plain === "" || isProtectedSecret(plain)) {
    return plain;
  }

  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv("aes-256-gcm", ensureKey(), iv);
  cipher.setAAD(Buffer.from(String(context), "utf8"));
  const ciphertext = Buffer.concat([
    cipher.update(Buffer.from(plain, "utf8")),
    cipher.final(),
  ]);
  const tag = cipher.getAuthTag();
  return [
    ENCRYPTED_PREFIX,
    encode(iv),
    encode(tag),
    encode(ciphertext),
  ].join(":");
}

function revealSecret(value, context) {
  const stored = String(value ?? "");
  if (stored === "" || !isProtectedSecret(stored)) {
    return stored;
  }

  const parts = stored.split(":");
  if (parts.length !== 6) {
    throw new Error("Invalid protected secret format.");
  }

  const iv = decode(parts[3]);
  const tag = decode(parts[4]);
  const ciphertext = decode(parts[5]);
  const decipher = crypto.createDecipheriv("aes-256-gcm", ensureKey(), iv);
  decipher.setAAD(Buffer.from(String(context), "utf8"));
  decipher.setAuthTag(tag);
  return Buffer.concat([
    decipher.update(ciphertext),
    decipher.final(),
  ]).toString("utf8");
}

function isProtectedSecret(value) {
  return String(value ?? "").startsWith(`${ENCRYPTED_PREFIX}:`);
}

function secretStorageSummary() {
  const source = keySource();
  if (source === "macos-keychain") {
    return "Secret values are encrypted in millie.settings with an AES-256-GCM key stored in macOS Keychain.";
  }
  if (source === "env") {
    return "Secret values are encrypted in millie.settings with an AES-256-GCM key from MILLIE_SETTINGS_KEY.";
  }
  return "Secret values are encrypted in millie.settings with an AES-256-GCM key stored under ignored .private/secrets/.";
}

function ensureKey() {
  if (cachedKey) {
    return cachedKey;
  }

  const envKey = keyFromEnv();
  if (envKey) {
    cachedKey = envKey;
    cachedKeySource = "env";
    return cachedKey;
  }

  const keychainKey = existingMacKeychainKey();
  if (keychainKey) {
    cachedKey = keychainKey;
    cachedKeySource = "macos-keychain";
    return cachedKey;
  }

  const fileKey = existingFileKey();
  if (fileKey) {
    cachedKey = fileKey;
    cachedKeySource = "file";
    return cachedKey;
  }

  const newKeychainKey = createMacKeychainKey();
  if (newKeychainKey) {
    cachedKey = newKeychainKey;
    cachedKeySource = "macos-keychain";
    return cachedKey;
  }

  cachedKey = createFileKey();
  cachedKeySource = "file";
  return cachedKey;
}

function keyFromEnv() {
  const value = process.env.MILLIE_SETTINGS_KEY;
  if (!value) {
    return null;
  }

  const decoded = decodePossibleKey(value);
  if (decoded) {
    return decoded;
  }

  return crypto.scryptSync(value, "MILLIE settings encryption key v1", 32);
}

function existingMacKeychainKey() {
  if (process.platform !== "darwin") {
    return null;
  }

  const account = os.userInfo().username || "default";
  const existing = runSecurity([
    "find-generic-password",
    "-a",
    account,
    "-s",
    KEYCHAIN_SERVICE,
    "-w",
  ]);

  if (existing.status === 0 && existing.stdout.trim()) {
    const decoded = decodePossibleKey(existing.stdout.trim());
    if (decoded) {
      return decoded;
    }
  }

  return null;
}

function createMacKeychainKey() {
  if (process.platform !== "darwin") {
    return null;
  }

  const account = os.userInfo().username || "default";
  const generated = crypto.randomBytes(32).toString("base64url");
  const added = runSecurity([
    "add-generic-password",
    "-a",
    account,
    "-s",
    KEYCHAIN_SERVICE,
    "-w",
    generated,
    "-U",
  ]);

  if (added.status === 0) {
    const decoded = decodePossibleKey(generated);
    if (decoded) {
      return decoded;
    }
  }

  return null;
}

function existingFileKey() {
  if (fs.existsSync(KEY_FILE)) {
    const decoded = decodePossibleKey(fs.readFileSync(KEY_FILE, "utf8").trim());
    if (decoded) {
      return decoded;
    }
  }
  return null;
}

function createFileKey() {
  fs.mkdirSync(path.dirname(KEY_FILE), { recursive: true });
  const generated = crypto.randomBytes(32).toString("base64url");
  fs.writeFileSync(KEY_FILE, `${generated}\n`, { mode: 0o600 });
  try {
    fs.chmodSync(KEY_FILE, 0o600);
  } catch {
    // Best-effort permissions on non-POSIX filesystems.
  }
  return decodePossibleKey(generated);
}

function decodePossibleKey(value) {
  const trimmed = String(value || "").trim();
  if (!trimmed) {
    return null;
  }

  for (const encoding of ["base64url", "base64", "hex"]) {
    try {
      const decoded = Buffer.from(trimmed, encoding);
      if (decoded.length === 32) {
        return decoded;
      }
    } catch {
      // Try the next encoding.
    }
  }

  return null;
}

function runSecurity(args) {
  const result = spawnSync("security", args, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
  if (result.error) {
    return { status: 1, stdout: "", stderr: result.error.message };
  }
  return {
    status: typeof result.status === "number" ? result.status : 1,
    stdout: result.stdout || "",
    stderr: result.stderr || "",
  };
}

function encode(value) {
  return Buffer.from(value).toString("base64url");
}

function decode(value) {
  return Buffer.from(value, "base64url");
}

module.exports = {
  ENCRYPTED_PREFIX,
  isProtectedSecret,
  isSecretSetting,
  keySource,
  protectSecret,
  revealSecret,
  secretStorageSummary,
};
