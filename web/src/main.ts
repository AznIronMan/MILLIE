import "./styles.css";

type Health = {
  ok: boolean;
  version: string;
  profile: Profile;
  settings_path: string;
  db_path: string;
  data_dir: string;
};

type Profile = {
  id: string;
  name: string;
  db_path: string;
  data_dir: string;
  settings_path: string;
  created_at: string;
  last_opened_at: string;
};

type Migration = {
  version: number;
  name: string;
  applied_at: string;
};

type ImportJob = {
  id: number;
  source_id: number;
  source_name: string;
  source_uri: string | null;
  kind: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  message_count: number;
  new_message_count: number;
  duplicate_count: number;
  error_count: number;
};

type ExportJob = {
  id: number;
  target_profile: string;
  format: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  message_count: number;
  error_count: number;
  warning_count: number;
  output_root: string;
  manifest_ref: string | null;
};

type Mailbox = {
  id: number;
  path: string;
  display_name: string;
  source_name: string;
  message_count: number;
};

type MessageSummary = {
  id: number;
  subject: string | null;
  sent_at: string | null;
  received_at: string | null;
  created_at: string;
  body_text: string | null;
  source_name: string;
  mailbox_path: string | null;
};

type MessageDetail = MessageSummary & {
  internet_message_id: string | null;
  size_bytes: number;
  addresses: Array<{
    role: string;
    ordinal: number;
    email: string;
    display_name: string | null;
    display_name_snapshot: string | null;
  }>;
  attachments: Array<{
    filename: string | null;
    mime_type: string | null;
    size_bytes: number;
    is_inline: number;
  }>;
  mailboxes: Array<{ id: number; path: string }>;
  headers: Array<{ name: string; value: string | null; ordinal: number }>;
};

type State = {
  health: Health | null;
  profiles: Profile[];
  activeProfileId: string | null;
  migrations: Migration[];
  importJobs: ImportJob[];
  exportJobs: ExportJob[];
  mailboxes: Mailbox[];
  messages: MessageSummary[];
  selectedMailboxId: number | null;
  selectedMessageId: number | null;
  selectedMessage: MessageDetail | null;
  query: string;
  status: string;
};

const state: State = {
  health: null,
  profiles: [],
  activeProfileId: null,
  migrations: [],
  importJobs: [],
  exportJobs: [],
  mailboxes: [],
  messages: [],
  selectedMailboxId: null,
  selectedMessageId: null,
  selectedMessage: null,
  query: "",
  status: "Ready",
};

const API_BASE = import.meta.env.VITE_MILLIE_API_BASE ?? "";

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) {
  throw new Error("App root was not found");
}
const appRoot: HTMLDivElement = app;

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error ?? `HTTP ${response.status}`);
  }
  return payload as T;
}

function formatDate(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return date.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function snippet(value: string | null): string {
  return (value ?? "").replace(/\s+/g, " ").trim().slice(0, 180);
}

function roleLine(detail: MessageDetail, role: string): string {
  return detail.addresses
    .filter((item) => item.role === role)
    .sort((a, b) => a.ordinal - b.ordinal)
    .map((item) => item.display_name_snapshot || item.display_name || item.email)
    .join(", ");
}

function render(): void {
  const selectedMailbox = state.mailboxes.find((box) => box.id === state.selectedMailboxId);
  appRoot.innerHTML = `
    <div class="shell">
      <aside class="sidebar">
        <div class="brand">
          <div>
            <h1>MILLIE</h1>
            <p>${state.health ? `v${state.health.version}` : "Starting"}</p>
          </div>
        </div>
        <section class="profile-panel">
          <label>
            Profile
            <select id="profile-select">
              ${state.profiles
                .map(
                  (profile) => `
                    <option value="${escapeHtml(profile.id)}" ${profile.id === state.activeProfileId ? "selected" : ""}>
                      ${escapeHtml(profile.name)}
                    </option>
                  `,
                )
                .join("")}
            </select>
          </label>
          <div class="inline-controls">
            <input id="profile-name" placeholder="New test profile" />
            <button id="profile-create-button">New</button>
          </div>
          <dl class="profile-facts">
            <div><dt>Global</dt><dd>${escapeHtml(state.health?.settings_path ?? "")}</dd></div>
            <div><dt>Profile</dt><dd>${escapeHtml(state.health?.profile.settings_path ?? "")}</dd></div>
            <div><dt>DB</dt><dd>${escapeHtml(state.health?.db_path ?? "No profile loaded")}</dd></div>
          </dl>
        </section>
        <section class="tool-panel">
          <label>
            Import path
            <input id="import-path" placeholder="/path/to/mailbox.eml" />
          </label>
          <div class="inline-controls">
            <select id="import-format">
              <option value="auto">Auto</option>
              <option value="eml">EML</option>
              <option value="eml-dir">EML folder</option>
              <option value="mbox">MBOX</option>
              <option value="maildir">Maildir</option>
              <option value="pst">PST</option>
            </select>
            <button id="import-button">Import</button>
          </div>
        </section>
        <nav class="folder-list" aria-label="Mailboxes">
          <button class="folder-row ${state.selectedMailboxId === null ? "active" : ""}" data-mailbox-id="">
            <span>All mail</span>
            <strong>${state.messages.length}</strong>
          </button>
          ${state.mailboxes
            .map(
              (box) => `
                <button class="folder-row ${state.selectedMailboxId === box.id ? "active" : ""}" data-mailbox-id="${box.id}">
                  <span>${box.source_name} / ${box.path}</span>
                  <strong>${box.message_count}</strong>
                </button>
              `,
            )
            .join("")}
        </nav>
      </aside>

      <main class="message-list-pane">
        <div class="topbar">
          <input id="search-input" value="${escapeHtml(state.query)}" placeholder="Search subject, people, body" />
          <button id="search-button">Search</button>
          <button id="clear-search-button">Clear</button>
        </div>
        <div class="list-context">
          <strong>${selectedMailbox ? selectedMailbox.path : "All mail"}</strong>
          <span>${state.messages.length} shown</span>
        </div>
        <div class="message-list">
          ${state.messages.map(renderMessageRow).join("") || `<div class="empty">No messages imported yet.</div>`}
        </div>
      </main>

      <section class="detail-pane">
        <div class="detail-toolbar">
          <label>
            Export folder
            <input id="export-path" placeholder="${escapeHtml(defaultExportPath())}" />
          </label>
          <select id="export-format">
            <option value="eml">EML</option>
            <option value="mbox">MBOX</option>
            <option value="maildir">Maildir</option>
          </select>
          <button id="export-button">Export</button>
        </div>
        <div class="detail-content">
          ${state.selectedMessage ? renderDetail(state.selectedMessage) : `<div class="empty detail-empty">Select a message to inspect it.</div>`}
          ${renderOperations()}
        </div>
        <footer class="status-line">${escapeHtml(state.status)}</footer>
      </section>
    </div>
  `;
  bindEvents();
}

function renderMessageRow(message: MessageSummary): string {
  const active = state.selectedMessageId === message.id ? "active" : "";
  return `
    <button class="message-row ${active}" data-message-id="${message.id}">
      <span class="message-subject">${escapeHtml(message.subject || "(No subject)")}</span>
      <span class="message-date">${escapeHtml(formatDate(message.sent_at || message.received_at || message.created_at))}</span>
      <span class="message-meta">${escapeHtml(message.source_name)} / ${escapeHtml(message.mailbox_path || "Imported")}</span>
      <span class="message-snippet">${escapeHtml(snippet(message.body_text))}</span>
    </button>
  `;
}

function renderDetail(detail: MessageDetail): string {
  return `
    <article class="message-detail">
      <header>
        <h2>${escapeHtml(detail.subject || "(No subject)")}</h2>
        <p>${escapeHtml(formatDate(detail.sent_at || detail.received_at || detail.created_at))}</p>
      </header>
      <dl class="metadata">
        <div><dt>From</dt><dd>${escapeHtml(roleLine(detail, "from"))}</dd></div>
        <div><dt>To</dt><dd>${escapeHtml(roleLine(detail, "to"))}</dd></div>
        <div><dt>Source</dt><dd>${escapeHtml(detail.source_name)}</dd></div>
        <div><dt>Message-ID</dt><dd>${escapeHtml(detail.internet_message_id || "")}</dd></div>
      </dl>
      ${
        detail.attachments.length
          ? `<div class="attachments">${detail.attachments
              .map(
                (item) => `
                  <span>${escapeHtml(item.filename || "(inline part)")} · ${escapeHtml(item.mime_type || "unknown")} · ${item.size_bytes} bytes</span>
                `,
              )
              .join("")}</div>`
          : ""
      }
      <pre class="body-text">${escapeHtml(detail.body_text || "(No text body captured yet.)")}</pre>
    </article>
  `;
}

function renderOperations(): string {
  return `
    <section class="operations-panel">
      <div class="operations-header">
        <h3>Operations</h3>
        <span>${state.migrations.length ? `schema v${state.migrations.at(-1)?.version}` : "schema pending"}</span>
      </div>
      <div class="job-grid">
        <div>
          <h4>Imports</h4>
          ${state.importJobs.slice(0, 5).map(renderImportJob).join("") || `<p class="muted">No import jobs yet.</p>`}
        </div>
        <div>
          <h4>Exports</h4>
          ${state.exportJobs.slice(0, 5).map(renderExportJob).join("") || `<p class="muted">No export jobs yet.</p>`}
        </div>
      </div>
    </section>
  `;
}

function renderImportJob(job: ImportJob): string {
  return `
    <div class="job-row">
      <strong>${escapeHtml(job.kind)} · ${escapeHtml(job.status)}</strong>
      <span>${escapeHtml(job.source_name)} · ${job.message_count} processed · ${job.new_message_count} new · ${job.duplicate_count} duplicate(s) · ${job.error_count} error(s)</span>
      <small>${escapeHtml(formatDate(job.started_at))}</small>
    </div>
  `;
}

function renderExportJob(job: ExportJob): string {
  return `
    <div class="job-row">
      <strong>${escapeHtml(job.format)} · ${escapeHtml(job.status)}</strong>
      <span>${job.message_count} message(s) · ${job.warning_count} warning(s) · ${job.error_count} error(s)</span>
      <small>${escapeHtml(job.manifest_ref || job.output_root)}</small>
    </div>
  `;
}

function bindEvents(): void {
  document.querySelectorAll<HTMLButtonElement>(".folder-row").forEach((button) => {
    button.addEventListener("click", async () => {
      const rawId = button.dataset.mailboxId;
      state.selectedMailboxId = rawId ? Number(rawId) : null;
      state.selectedMessageId = null;
      state.selectedMessage = null;
      await loadMessages();
    });
  });

  document.querySelectorAll<HTMLButtonElement>(".message-row").forEach((button) => {
    button.addEventListener("click", async () => {
      state.selectedMessageId = Number(button.dataset.messageId);
      await loadMessageDetail(state.selectedMessageId);
    });
  });

  document.querySelector<HTMLButtonElement>("#search-button")?.addEventListener("click", async () => {
    state.query = document.querySelector<HTMLInputElement>("#search-input")?.value ?? "";
    await loadMessages();
  });

  document.querySelector<HTMLButtonElement>("#clear-search-button")?.addEventListener("click", async () => {
    state.query = "";
    await loadMessages();
  });

  document.querySelector<HTMLInputElement>("#search-input")?.addEventListener("keydown", async (event) => {
    if (event.key === "Enter") {
      state.query = (event.currentTarget as HTMLInputElement).value;
      await loadMessages();
    }
  });

  document.querySelector<HTMLButtonElement>("#import-button")?.addEventListener("click", importMail);
  document.querySelector<HTMLButtonElement>("#export-button")?.addEventListener("click", exportMail);
  document.querySelector<HTMLSelectElement>("#profile-select")?.addEventListener("change", switchProfile);
  document.querySelector<HTMLButtonElement>("#profile-create-button")?.addEventListener("click", createProfile);
  document.querySelector<HTMLInputElement>("#profile-name")?.addEventListener("keydown", async (event) => {
    if (event.key === "Enter") {
      await createProfile();
    }
  });
}

async function loadInitial(): Promise<void> {
  try {
    state.health = await api<Health>("/api/v1/health");
    await loadProfiles();
    await loadMigrations();
    await loadJobs();
    await loadMailboxes();
    await loadMessages();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function loadProfiles(): Promise<void> {
  const payload = await api<{ active_profile_id: string; profiles: Profile[] }>("/api/v1/profiles");
  state.activeProfileId = payload.active_profile_id;
  state.profiles = payload.profiles;
}

async function loadMigrations(): Promise<void> {
  const payload = await api<{ migrations: Migration[] }>("/api/v1/migrations");
  state.migrations = payload.migrations;
}

async function loadJobs(): Promise<void> {
  const [importPayload, exportPayload] = await Promise.all([
    api<{ import_jobs: ImportJob[] }>("/api/v1/import-jobs"),
    api<{ export_jobs: ExportJob[] }>("/api/v1/export-jobs"),
  ]);
  state.importJobs = importPayload.import_jobs;
  state.exportJobs = exportPayload.export_jobs;
}

async function loadMailboxes(): Promise<void> {
  const payload = await api<{ mailboxes: Mailbox[] }>("/api/v1/mailboxes");
  state.mailboxes = payload.mailboxes;
}

async function loadMessages(): Promise<void> {
  const params = new URLSearchParams();
  if (state.selectedMailboxId !== null) params.set("mailbox_id", String(state.selectedMailboxId));
  if (state.query.trim()) params.set("q", state.query.trim());
  params.set("limit", "200");
  const payload = await api<{ messages: MessageSummary[] }>(`/api/v1/messages?${params.toString()}`);
  state.messages = payload.messages;
  state.status = "Ready";
  render();
}

async function loadMessageDetail(id: number): Promise<void> {
  const payload = await api<{ message: MessageDetail }>(`/api/v1/messages/${id}`);
  state.selectedMessage = payload.message;
  state.status = "Ready";
  render();
}

async function importMail(): Promise<void> {
  const path = document.querySelector<HTMLInputElement>("#import-path")?.value.trim();
  const format = document.querySelector<HTMLSelectElement>("#import-format")?.value ?? "auto";
  if (!path) {
    state.status = "Enter a local import path.";
    render();
    return;
  }
  state.status = "Importing...";
  render();
  try {
    const result = await api<{ imported: number; processed: number; duplicates: number; errors: number; format: string }>("/api/v1/import", {
      method: "POST",
      body: JSON.stringify({ path, format }),
    });
    await loadMailboxes();
    await loadJobs();
    await loadMessages();
    state.status = `Processed ${result.processed} message(s) as ${result.format}; new=${result.imported}, duplicates=${result.duplicates}, errors=${result.errors}.`;
    render();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function switchProfile(event: Event): Promise<void> {
  const profileId = (event.currentTarget as HTMLSelectElement).value;
  if (!profileId || profileId === state.activeProfileId) return;
  state.status = "Switching profile...";
  render();
  try {
    await api<{ active_profile_id: string; profile: Profile; profiles: Profile[] }>("/api/v1/profiles/active", {
      method: "POST",
      body: JSON.stringify({ profileId }),
    });
    state.selectedMailboxId = null;
    state.selectedMessageId = null;
    state.selectedMessage = null;
    state.query = "";
    await refreshActiveProfileData();
    state.status = `Profile switched to ${state.health?.profile.name ?? profileId}.`;
    render();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    await loadProfiles();
    render();
  }
}

async function createProfile(): Promise<void> {
  const input = document.querySelector<HTMLInputElement>("#profile-name");
  const name = input?.value.trim() ?? "";
  if (!name) {
    state.status = "Enter a profile name.";
    render();
    return;
  }
  state.status = "Creating profile...";
  render();
  try {
    await api<{ active_profile_id: string; profile: Profile; profiles: Profile[] }>("/api/v1/profiles", {
      method: "POST",
      body: JSON.stringify({ name, switch: true }),
    });
    state.selectedMailboxId = null;
    state.selectedMessageId = null;
    state.selectedMessage = null;
    state.query = "";
    await refreshActiveProfileData();
    state.status = `Profile created: ${state.health?.profile.name ?? name}.`;
    render();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function refreshActiveProfileData(): Promise<void> {
  state.health = await api<Health>("/api/v1/health");
  await loadProfiles();
  await loadMigrations();
  await loadJobs();
  await loadMailboxes();
  await loadMessages();
}

async function exportMail(): Promise<void> {
  const outputPath = document.querySelector<HTMLInputElement>("#export-path")?.value.trim() || defaultExportPath();
  const format = document.querySelector<HTMLSelectElement>("#export-format")?.value ?? "eml";
  state.status = "Exporting...";
  render();
  try {
    const result = await api<{ exported: number; warnings: number; manifest_path: string }>("/api/v1/export", {
      method: "POST",
      body: JSON.stringify({
        outputPath,
        format,
        mailboxId: state.selectedMailboxId,
        profile: "generic",
      }),
    });
    state.status = `Exported ${result.exported} message(s), warnings=${result.warnings}. Manifest: ${result.manifest_path}`;
    await loadJobs();
    render();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

function defaultExportPath(): string {
  return `.private/local/exports/${state.activeProfileId ?? "default"}`;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => {
    const map: Record<string, string> = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    };
    return map[char] ?? char;
  });
}

render();
void loadInitial();
