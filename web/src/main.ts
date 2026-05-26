import "./styles.css";

type Health = {
  ok: boolean;
  version: string;
  auth: AuthStatus;
  profile: Profile;
  settings_path: string;
  db_path: string;
  data_dir: string;
};

type AuthStatus = {
  authenticated: boolean;
  dev_bypass: boolean;
  setup_required: boolean;
  username: string | null;
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
  options_json: string;
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
  options_json: string;
  manifest_ref: string | null;
};

type SyncState = {
  source_id: number;
  source_kind: string;
  source_name: string;
  source_uri: string | null;
  scope: string;
  state_json: string;
  updated_at: string;
  state: Record<string, unknown>;
};

type ExportProfile = {
  id: string;
  display_name: string;
  recommended_format: string;
  formats: string[];
  description: string;
  import_instructions: string[];
  limitations: string[];
};

type BackupResult = {
  output_path: string;
  profile_id: string;
  include_secrets: boolean;
  file_count: number;
  warnings: string[];
};

type RestoreResult = {
  input_path: string;
  profile_id: string;
  profile_name: string;
  file_count: number;
  switched: boolean;
  warnings: string[];
};

type SourceCandidate = {
  id: string;
  source_type: string;
  format: string;
  path: string;
  display_name: string;
  mailbox_path: string;
  size_bytes: number;
  message_estimate: number | null;
  confidence: string;
  notes: string[];
  importable: boolean;
};

type ImapSource = {
  id: string;
  name: string;
  host: string;
  port: number;
  username: string;
  use_tls: boolean;
  folders: string[];
  sync_limit: number;
  auth_method: string;
  provider: string;
  password_configured: boolean;
  secret_backend: string | null;
};

type ImapProvider = {
  id: string;
  display_name: string;
  host: string;
  port: number;
  use_tls: boolean;
  default_folders: string[];
  host_aliases: string[];
};

type ImapFolder = {
  name: string;
  delimiter: string | null;
  flags: string[];
  selectable: boolean;
  role: string | null;
};

type ImapSyncResult = {
  import_job_id: number;
  source_id: number;
  imported: number;
  processed: number;
  duplicates: number;
  errors: number;
  folders: string[];
  format: string;
};

type PopSource = {
  id: string;
  name: string;
  host: string;
  port: number;
  username: string;
  use_ssl: boolean;
  sync_limit: number;
  auth_method: string;
  provider: string;
  password_configured: boolean;
  secret_backend: string | null;
};

type PopProvider = {
  id: string;
  display_name: string;
  host: string;
  port: number;
  use_ssl: boolean;
  host_aliases: string[];
};

type PopProbeResult = {
  message_count: number;
  maildrop_size_bytes: number;
  uidl_available: boolean;
  uidl_sample_count: number;
  capabilities: string[];
  commands_not_used: string[];
};

type PopSyncResult = {
  import_job_id: number;
  source_id: number;
  imported: number;
  processed: number;
  duplicates: number;
  errors: number;
  format: string;
};

type GraphSource = {
  id: string;
  name: string;
  client_id: string;
  tenant_id: string;
  redirect_uri: string;
  scopes: string[];
  mailbox: string;
  folders: GraphFolderSelection[];
  sync_limit: number;
  auth_method: string;
  provider: string;
  token_configured: boolean;
  pending_auth_configured: boolean;
  secret_backend: string | null;
};

type GraphFolderSelection = {
  id: string;
  display_name: string;
  path: string;
};

type GraphFolder = GraphFolderSelection & {
  parent_folder_id: string | null;
  total_item_count: number | null;
  unread_item_count: number | null;
  child_folder_count: number | null;
  role: string | null;
};

type GraphProvider = {
  id: string;
  display_name: string;
  authority_host: string;
  api_base_url: string;
  default_tenant: string;
  default_scopes: string[];
  auth_flow: string;
};

type GraphAuthRequest = {
  authorization_url: string;
  tenant_id: string;
  client_id: string;
  redirect_uri: string;
  scopes: string[];
  state: string;
  code_challenge: string;
  code_challenge_method: string;
};

type GraphProbeResult = {
  source_id: string;
  mailbox: string;
  display_name: string | null;
  user_principal_name: string | null;
  mail: string | null;
  folder_count: number;
  token_refreshed: boolean;
  read_only: boolean;
};

type GraphSyncResult = {
  import_job_id: number;
  source_id: number;
  processed: number;
  imported: number;
  duplicates: number;
  errors: number;
  removed: number;
  folders: string[];
  token_refreshed: boolean;
  sync_limit: number;
  format: string;
  read_only: boolean;
};

type ImportJobError = {
  id: number;
  import_job_id: number;
  source_item_ref: string | null;
  severity: string;
  message: string;
  detail_json: string;
  created_at: string;
};

type ExportJobItem = {
  id: number;
  export_job_id: number;
  message_id: number;
  mailbox_id: number | null;
  output_path: string;
  output_hash: string | null;
  format: string;
  status: string;
  warning_json: string;
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
  internal_date: string | null;
  created_at: string;
  body_text: string | null;
  source_name: string;
  mailbox_path: string | null;
};

type MessageDetail = MessageSummary & {
  internet_message_id: string | null;
  size_bytes: number;
  body_html_ref: string | null;
  body_sanitized_html_ref: string | null;
  addresses: Array<{
    role: string;
    ordinal: number;
    email: string;
    display_name: string | null;
    display_name_snapshot: string | null;
  }>;
  attachments: Array<{
    id: number;
    filename: string | null;
    mime_type: string | null;
    size_bytes: number;
    content_hash: string;
    is_inline: number;
  }>;
  mailboxes: Array<{
    id: number;
    path: string;
    display_name: string;
    source_uid: string | null;
    flags_json: string;
    labels_json: string;
  }>;
  headers: Array<{ name: string; value: string | null; ordinal: number }>;
};

type State = {
  auth: AuthStatus | null;
  health: Health | null;
  profiles: Profile[];
  activeProfileId: string | null;
  migrations: Migration[];
  importJobs: ImportJob[];
  exportJobs: ExportJob[];
  syncStates: SyncState[];
  exportProfiles: ExportProfile[];
  selectedExportProfileId: string;
  selectedExportFormat: string;
  sourceScanPath: string;
  sourceScanType: string;
  sourceScanCandidates: SourceCandidate[];
  imapProviders: ImapProvider[];
  imapSources: ImapSource[];
  imapDiscoveredSourceId: string | null;
  imapDiscoveredFolders: ImapFolder[];
  popProviders: PopProvider[];
  popSources: PopSource[];
  graphProviders: GraphProvider[];
  graphSources: GraphSource[];
  graphDiscoveredSourceId: string | null;
  graphDiscoveredFolders: GraphFolder[];
  mailboxes: Mailbox[];
  messages: MessageSummary[];
  selectedMailboxId: number | null;
  selectedMessageId: number | null;
  selectedMessage: MessageDetail | null;
  selectedJob:
    | { kind: "import"; id: number; errors: ImportJobError[] }
    | { kind: "export"; id: number; items: ExportJobItem[] }
    | null;
  query: string;
  status: string;
};

const state: State = {
  auth: null,
  health: null,
  profiles: [],
  activeProfileId: null,
  migrations: [],
  importJobs: [],
  exportJobs: [],
  syncStates: [],
  exportProfiles: [],
  selectedExportProfileId: "generic-eml",
  selectedExportFormat: "auto",
  sourceScanPath: "",
  sourceScanType: "thunderbird",
  sourceScanCandidates: [],
  imapProviders: [],
  imapSources: [],
  imapDiscoveredSourceId: null,
  imapDiscoveredFolders: [],
  popProviders: [],
  popSources: [],
  graphProviders: [],
  graphSources: [],
  graphDiscoveredSourceId: null,
  graphDiscoveredFolders: [],
  mailboxes: [],
  messages: [],
  selectedMailboxId: null,
  selectedMessageId: null,
  selectedMessage: null,
  selectedJob: null,
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
    credentials: "include",
    ...init,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error ?? `HTTP ${response.status}`);
  }
  return payload as T;
}

function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
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
  if (state.auth && !state.auth.authenticated && !state.auth.dev_bypass) {
    renderAuthScreen();
    return;
  }

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
            <div><dt>Auth</dt><dd>${escapeHtml(authLabel())}</dd></div>
            <div><dt>Global</dt><dd>${escapeHtml(state.health?.settings_path ?? "")}</dd></div>
            <div><dt>Profile</dt><dd>${escapeHtml(state.health?.profile.settings_path ?? "")}</dd></div>
            <div><dt>DB</dt><dd>${escapeHtml(state.health?.db_path ?? "No profile loaded")}</dd></div>
          </dl>
          ${
            state.auth?.authenticated && !state.auth.dev_bypass
              ? `<button id="logout-button">Logout</button>`
              : ""
          }
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
          <div class="panel-rule"></div>
          <label>
            Scan path
            <input id="source-scan-path" value="${escapeHtml(state.sourceScanPath)}" placeholder="/path/to/mail client store" />
          </label>
          <div class="inline-controls">
            <select id="source-scan-type">
              <option value="thunderbird" ${state.sourceScanType === "thunderbird" ? "selected" : ""}>Thunderbird</option>
              <option value="evolution" ${state.sourceScanType === "evolution" ? "selected" : ""}>Evolution</option>
              <option value="apple-mail" ${state.sourceScanType === "apple-mail" ? "selected" : ""}>Apple Mail</option>
              <option value="auto" ${state.sourceScanType === "auto" ? "selected" : ""}>Auto</option>
              <option value="generic" ${state.sourceScanType === "generic" ? "selected" : ""}>Generic</option>
            </select>
            <button id="source-scan-button">Scan</button>
          </div>
          ${renderSourceCandidates()}
          <div class="panel-rule"></div>
          <label>
            IMAP provider
            <select id="imap-provider">
              ${renderImapProviderOptions()}
            </select>
          </label>
          <label>
            IMAP source
            <input id="imap-name" placeholder="Work mailbox" />
          </label>
          <label>
            Host
            <input id="imap-host" placeholder="imap.example.com" />
          </label>
          <label>
            Username
            <input id="imap-username" autocomplete="username" />
          </label>
          <label>
            Password
            <input id="imap-password" type="password" autocomplete="current-password" />
          </label>
          <div class="inline-controls">
            <input id="imap-folder" placeholder="INBOX" />
            <button id="imap-save-button">Save</button>
          </div>
          <div class="imap-options">
            <select id="imap-security">
              <option value="tls">TLS</option>
              <option value="plain">Plain</option>
            </select>
            <input id="imap-port" type="number" min="1" placeholder="993" />
            <input id="imap-limit" type="number" min="1" placeholder="100" />
          </div>
          ${renderImapSources()}
          ${renderImapFolderPicker()}
          <div class="panel-rule"></div>
          <label>
            POP provider
            <select id="pop-provider">
              ${renderPopProviderOptions()}
            </select>
          </label>
          <label>
            POP source
            <input id="pop-name" placeholder="Gmail POP" />
          </label>
          <label>
            Host
            <input id="pop-host" placeholder="pop.example.com" />
          </label>
          <label>
            Username
            <input id="pop-username" autocomplete="username" />
          </label>
          <label>
            Password
            <input id="pop-password" type="password" autocomplete="current-password" />
          </label>
          <div class="inline-controls">
            <select id="pop-security">
              <option value="ssl">SSL</option>
              <option value="plain">Plain</option>
            </select>
            <button id="pop-save-button">Save</button>
          </div>
          <div class="imap-options">
            <input id="pop-port" type="number" min="1" placeholder="995" />
            <input id="pop-limit" type="number" min="1" placeholder="100" />
            <span class="muted compact-note">No delete</span>
          </div>
          ${renderPopSources()}
          <div class="panel-rule"></div>
          <label>
            Graph provider
            <select id="graph-provider">
              ${renderGraphProviderOptions()}
            </select>
          </label>
          <label>
            Graph source
            <input id="graph-name" placeholder="CNB Portland Connector" />
          </label>
          <label>
            Client ID
            <input id="graph-client-id" placeholder="Application client ID" />
          </label>
          <label>
            Tenant ID
            <input id="graph-tenant-id" placeholder="Directory tenant ID" />
          </label>
          <label>
            Redirect URI
            <input id="graph-redirect-uri" placeholder="http://localhost" />
          </label>
          <div class="inline-controls">
            <input id="graph-mailbox" placeholder="me" />
            <button id="graph-save-button">Save</button>
          </div>
          <div class="imap-options">
            <input id="graph-scopes" placeholder="openid offline_access User.Read Mail.Read" />
            <input id="graph-limit" type="number" min="1" placeholder="100" />
            <span class="muted compact-note">Read-only</span>
          </div>
          ${renderGraphSources()}
          ${renderGraphFolderPicker()}
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
            Export target
            <select id="export-profile">
              ${state.exportProfiles.map(renderExportProfileOption).join("")}
            </select>
          </label>
          <label>
            Export folder
            <input id="export-path" placeholder="${escapeHtml(defaultExportPath())}" />
          </label>
          <select id="export-format">
            ${renderExportFormatOptions()}
          </select>
          <button id="export-button">Export</button>
          <label>
            Backup folder
            <input id="backup-path" placeholder="${escapeHtml(defaultBackupPath())}" />
          </label>
          <label class="checkbox-label">
            <input id="backup-include-secrets" type="checkbox" />
            Include secrets
          </label>
          <button id="backup-button">Backup</button>
          <label>
            Restore ZIP
            <input id="restore-path" placeholder=".private/local/backups/default/millie-backup-default.zip" />
          </label>
          <label>
            Restore as
            <input id="restore-name" placeholder="Restored profile" />
          </label>
          <button id="restore-button">Restore</button>
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

function renderExportProfileOption(profile: ExportProfile): string {
  return `
    <option value="${escapeHtml(profile.id)}" ${profile.id === state.selectedExportProfileId ? "selected" : ""}>
      ${escapeHtml(profile.display_name)}
    </option>
  `;
}

function renderExportFormatOptions(): string {
  const profile = selectedExportProfile();
  const formats = profile?.formats ?? ["eml", "mbox", "maildir"];
  const recommended = profile?.recommended_format ?? "eml";
  const options = [
    `<option value="auto" ${state.selectedExportFormat === "auto" ? "selected" : ""}>Recommended (${escapeHtml(recommended.toUpperCase())})</option>`,
    ...formats.map(
      (format) =>
        `<option value="${escapeHtml(format)}" ${state.selectedExportFormat === format ? "selected" : ""}>${escapeHtml(format.toUpperCase())}</option>`,
    ),
  ];
  return options.join("");
}

function renderSourceCandidates(): string {
  if (!state.sourceScanCandidates.length) return "";
  return `
    <div class="source-candidates">
      ${state.sourceScanCandidates.map(renderSourceCandidate).join("")}
    </div>
  `;
}

function renderSourceCandidate(candidate: SourceCandidate): string {
  const notes = candidate.notes.length ? `<small>${escapeHtml(candidate.notes.join(" "))}</small>` : "";
  const disabled = candidate.importable ? "" : "disabled";
  const buttonLabel = candidate.importable ? "Import" : "Unavailable";
  return `
    <div class="source-candidate ${candidate.importable ? "" : "not-importable"}">
      <div>
        <strong>${escapeHtml(candidate.mailbox_path || candidate.display_name)}</strong>
        <span>${escapeHtml(candidate.format.toUpperCase())} · ${escapeHtml(candidate.confidence)} · ${escapeHtml(candidateEstimate(candidate))}</span>
        <small>${escapeHtml(candidate.path)}</small>
        ${notes}
      </div>
      <button class="candidate-import-button" data-candidate-id="${escapeHtml(candidate.id)}" ${disabled}>${buttonLabel}</button>
    </div>
  `;
}

function candidateEstimate(candidate: SourceCandidate): string {
  if (candidate.message_estimate === null) return "unknown";
  return `${candidate.message_estimate} message(s)`;
}

function renderImapProviderOptions(): string {
  const providers = state.imapProviders.length
    ? state.imapProviders
    : [
        {
          id: "generic",
          display_name: "Generic IMAP",
          host: "",
          port: 993,
          use_tls: true,
          default_folders: ["INBOX"],
          host_aliases: [],
        },
      ];
  return providers
    .map(
      (provider) => `
        <option value="${escapeHtml(provider.id)}">${escapeHtml(provider.display_name)}</option>
      `,
    )
    .join("");
}

function imapProviderLabel(providerId: string): string {
  return state.imapProviders.find((provider) => provider.id === providerId)?.display_name ?? providerId;
}

function renderImapSources(): string {
  if (!state.imapSources.length) return `<p class="muted compact-note">No saved IMAP sources.</p>`;
  return `
    <div class="imap-source-list">
      ${state.imapSources.map(renderImapSource).join("")}
    </div>
  `;
}

function renderImapSource(source: ImapSource): string {
  const security = source.use_tls ? "TLS" : "plain";
  const folders = source.folders.join(", ");
  const secret = source.secret_backend ?? "no secret";
  const provider = imapProviderLabel(source.provider);
  return `
    <div class="imap-source-row">
      <div>
        <strong>${escapeHtml(source.name)}</strong>
        <span>${escapeHtml(source.username)}@${escapeHtml(source.host)}:${source.port} · ${escapeHtml(security)} · ${escapeHtml(provider)}</span>
        <small>${escapeHtml(folders)} · limit ${source.sync_limit} · ${escapeHtml(secret)}</small>
      </div>
      <div class="imap-source-actions">
        <button class="imap-discover-button" data-imap-source-id="${escapeHtml(source.id)}">Folders</button>
        <button class="imap-sync-button" data-imap-source-id="${escapeHtml(source.id)}">Sync</button>
        <button class="imap-delete-button" data-imap-source-id="${escapeHtml(source.id)}">Delete</button>
      </div>
    </div>
  `;
}

function renderImapFolderPicker(): string {
  if (!state.imapDiscoveredSourceId || !state.imapDiscoveredFolders.length) return "";
  const source = state.imapSources.find((item) => item.id === state.imapDiscoveredSourceId);
  const selected = new Set(source?.folders ?? []);
  const selectable = state.imapDiscoveredFolders.filter((folder) => folder.selectable);
  return `
    <div class="imap-folder-picker imap-folder-picker-panel">
      <div class="imap-folder-picker-header">
        <strong>${escapeHtml(source?.name ?? "IMAP folders")}</strong>
        <div class="inline-actions">
          <button id="imap-sync-selected-folders-button">Sync Selected</button>
          <button id="imap-apply-folders-button">Use Selected</button>
        </div>
      </div>
      <div class="imap-folder-list">
        ${selectable
          .map(
            (folder) => `
              <label class="imap-folder-option">
                <input
                  type="checkbox"
                  value="${escapeHtml(folder.name)}"
                  ${selected.has(folder.name) || (!selected.size && folder.name.toUpperCase() === "INBOX") ? "checked" : ""}
                />
                <span>${escapeHtml(folder.name)}</span>
                <small>${escapeHtml(imapFolderNote(folder))}</small>
              </label>
            `,
          )
          .join("")}
      </div>
    </div>
  `;
}

function imapFolderNote(folder: ImapFolder): string {
  if (folder.name.toLowerCase() === "[gmail]/all mail") {
    return "archive · overlaps labels";
  }
  return folder.role ?? (folder.flags.join(", ") || "folder");
}

function renderPopProviderOptions(): string {
  const providers = state.popProviders.length
    ? state.popProviders
    : [
        {
          id: "generic",
          display_name: "Generic POP3",
          host: "",
          port: 995,
          use_ssl: true,
          host_aliases: [],
        },
      ];
  return providers
    .map(
      (provider) => `
        <option value="${escapeHtml(provider.id)}">${escapeHtml(provider.display_name)}</option>
      `,
    )
    .join("");
}

function popProviderLabel(providerId: string): string {
  return state.popProviders.find((provider) => provider.id === providerId)?.display_name ?? providerId;
}

function renderPopSources(): string {
  if (!state.popSources.length) return `<p class="muted compact-note">No saved POP sources.</p>`;
  return `
    <div class="imap-source-list">
      ${state.popSources.map(renderPopSource).join("")}
    </div>
  `;
}

function renderPopSource(source: PopSource): string {
  const security = source.use_ssl ? "SSL" : "plain";
  const secret = source.secret_backend ?? "no secret";
  const provider = popProviderLabel(source.provider);
  return `
    <div class="imap-source-row">
      <div>
        <strong>${escapeHtml(source.name)}</strong>
        <span>${escapeHtml(source.username)}@${escapeHtml(source.host)}:${source.port} · ${escapeHtml(security)} · ${escapeHtml(provider)}</span>
        <small>limit ${source.sync_limit} · ${escapeHtml(secret)} · delete never</small>
      </div>
      <div class="imap-source-actions">
        <button class="pop-probe-button" data-pop-source-id="${escapeHtml(source.id)}">Probe</button>
        <button class="pop-sync-button" data-pop-source-id="${escapeHtml(source.id)}">Sync</button>
        <button class="pop-delete-button" data-pop-source-id="${escapeHtml(source.id)}">Delete</button>
      </div>
    </div>
  `;
}

function renderGraphProviderOptions(): string {
  const providers = state.graphProviders.length
    ? state.graphProviders
    : [
        {
          id: "microsoft-graph",
          display_name: "Microsoft Graph",
          authority_host: "https://login.microsoftonline.com",
          api_base_url: "https://graph.microsoft.com/v1.0",
          default_tenant: "common",
          default_scopes: ["openid", "offline_access", "User.Read", "Mail.Read"],
          auth_flow: "authorization_code_pkce",
        },
      ];
  return providers
    .map(
      (provider) => `
        <option value="${escapeHtml(provider.id)}">${escapeHtml(provider.display_name)}</option>
      `,
    )
    .join("");
}

function graphProviderLabel(providerId: string): string {
  return state.graphProviders.find((provider) => provider.id === providerId)?.display_name ?? providerId;
}

function renderGraphSources(): string {
  if (!state.graphSources.length) return `<p class="muted compact-note">No saved Graph sources.</p>`;
  return `
    <div class="imap-source-list">
      ${state.graphSources.map(renderGraphSource).join("")}
    </div>
  `;
}

function renderGraphSource(source: GraphSource): string {
  const provider = graphProviderLabel(source.provider);
  const secret = source.secret_backend ?? "no token";
  const token = source.token_configured ? "connected" : source.pending_auth_configured ? "pending" : "not connected";
  const folders = source.folders.length ? `${source.folders.length} folder(s)` : "no folders selected";
  return `
    <div class="imap-source-row">
      <div>
        <strong>${escapeHtml(source.name)}</strong>
        <span>${escapeHtml(provider)} · ${escapeHtml(source.mailbox)} · ${escapeHtml(token)}</span>
        <small>${escapeHtml(folders)} · ${escapeHtml(secret)} · limit ${source.sync_limit}</small>
      </div>
      <div class="imap-source-actions">
        <button class="graph-connect-button" data-graph-source-id="${escapeHtml(source.id)}">Connect</button>
        <button class="graph-discover-button" data-graph-source-id="${escapeHtml(source.id)}" ${source.token_configured ? "" : "disabled"}>Folders</button>
        <button class="graph-probe-button" data-graph-source-id="${escapeHtml(source.id)}" ${source.token_configured ? "" : "disabled"}>Probe</button>
        <button class="graph-sync-button" data-graph-source-id="${escapeHtml(source.id)}" ${source.token_configured && source.folders.length ? "" : "disabled"}>Sync</button>
        <button class="graph-delete-button" data-graph-source-id="${escapeHtml(source.id)}">Delete</button>
      </div>
    </div>
  `;
}

function renderGraphFolderPicker(): string {
  if (!state.graphDiscoveredSourceId || !state.graphDiscoveredFolders.length) return "";
  const source = state.graphSources.find((item) => item.id === state.graphDiscoveredSourceId);
  const selected = new Set(source?.folders.map((folder) => folder.id) ?? []);
  return `
    <div class="imap-folder-picker graph-folder-picker">
      <div class="imap-folder-picker-header">
        <strong>${escapeHtml(source?.name ?? "Graph folders")}</strong>
        <div class="inline-actions">
          <button id="graph-sync-selected-folders-button">Sync Selected</button>
          <button id="graph-apply-folders-button">Use Selected</button>
        </div>
      </div>
      <div class="imap-folder-list">
        ${state.graphDiscoveredFolders
          .map(
            (folder) => `
              <label class="imap-folder-option">
                <input
                  type="checkbox"
                  value="${escapeHtml(folder.id)}"
                  ${selected.has(folder.id) || (!selected.size && graphFolderDefaultChecked(folder)) ? "checked" : ""}
                />
                <span>${escapeHtml(folder.path)}</span>
                <small>${escapeHtml(graphFolderNote(folder))}</small>
              </label>
            `,
          )
          .join("")}
      </div>
    </div>
  `;
}

function graphFolderDefaultChecked(folder: GraphFolder): boolean {
  return folder.path.toLowerCase() === "inbox";
}

function graphFolderNote(folder: GraphFolder): string {
  const total = folder.total_item_count === null ? "unknown" : String(folder.total_item_count);
  const unread = folder.unread_item_count === null ? "unknown" : String(folder.unread_item_count);
  return `${total} total · ${unread} unread`;
}

function renderAuthScreen(): void {
  const setup = state.auth?.setup_required ?? false;
  appRoot.innerHTML = `
    <main class="auth-shell">
      <section class="auth-panel">
        <div class="brand auth-brand">
          <div>
            <h1>MILLIE</h1>
            <p>${setup ? "First-run setup" : "Login"}</p>
          </div>
        </div>
        <form id="${setup ? "setup-form" : "login-form"}" class="auth-form">
          <label>
            Username
            <input id="auth-username" autocomplete="username" />
          </label>
          <label>
            Password
            <input id="auth-password" type="password" autocomplete="${setup ? "new-password" : "current-password"}" />
          </label>
          <button type="submit">${setup ? "Create Admin" : "Login"}</button>
        </form>
        <footer class="status-line">${escapeHtml(state.status)}</footer>
      </section>
    </main>
  `;
  bindAuthEvents();
}

function renderMessageRow(message: MessageSummary): string {
  const active = state.selectedMessageId === message.id ? "active" : "";
  return `
    <button class="message-row ${active}" data-message-id="${message.id}">
      <span class="message-subject">${escapeHtml(message.subject || "(No subject)")}</span>
      <span class="message-date">${escapeHtml(formatDate(message.sent_at || message.received_at || message.internal_date || message.created_at))}</span>
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
        <p>${escapeHtml(formatDate(detail.sent_at || detail.received_at || detail.internal_date || detail.created_at))}</p>
      </header>
      <dl class="metadata">
        <div><dt>From</dt><dd>${escapeHtml(roleLine(detail, "from"))}</dd></div>
        <div><dt>To</dt><dd>${escapeHtml(roleLine(detail, "to"))}</dd></div>
        <div><dt>Source</dt><dd>${escapeHtml(detail.source_name)}</dd></div>
        <div><dt>Internal</dt><dd>${escapeHtml(formatDate(detail.internal_date))}</dd></div>
        <div><dt>Flags</dt><dd>${escapeHtml(messageFlagLine(detail))}</dd></div>
        <div><dt>Message-ID</dt><dd>${escapeHtml(detail.internet_message_id || "")}</dd></div>
      </dl>
      ${
        detail.attachments.length
          ? `<div class="attachments">${detail.attachments
              .map(
                (item) => `
                  <a class="attachment-link" href="${escapeHtml(apiUrl(`/api/v1/attachments/${item.id}`))}" download>
                    <span>${escapeHtml(item.filename || "(inline part)")}</span>
                    <small>${escapeHtml(item.mime_type || "unknown")} · ${item.size_bytes} bytes</small>
                  </a>
                `,
              )
              .join("")}</div>`
          : ""
      }
      ${renderMessageBody(detail)}
    </article>
  `;
}

function messageFlagLine(detail: MessageDetail): string {
  const flags = new Set<string>();
  for (const mailbox of detail.mailboxes) {
    for (const flag of parseJsonList(mailbox.flags_json)) {
      flags.add(flag);
    }
  }
  return Array.from(flags).join(", ");
}

function parseJsonList(value: string): string[] {
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
}

function renderMessageBody(detail: MessageDetail): string {
  if (detail.body_sanitized_html_ref || detail.body_html_ref) {
    return `
      <iframe
        class="html-frame"
        title="${escapeHtml(detail.subject || "Message HTML")}"
        sandbox
        referrerpolicy="no-referrer"
        src="${escapeHtml(apiUrl(`/api/v1/messages/${detail.id}/html`))}"
      ></iframe>
    `;
  }
  return `<pre class="body-text">${escapeHtml(detail.body_text || "(No text body captured yet.)")}</pre>`;
}

function renderOperations(): string {
  return `
    <section class="operations-panel">
      <div class="operations-header">
        <h3>Operations</h3>
        <span>${state.migrations.length ? `schema v${state.migrations.at(-1)?.version}` : "schema pending"}</span>
      </div>
      ${renderExportProfileSummary()}
      <div class="job-grid">
        <div>
          <h4>Imports</h4>
          ${state.importJobs.slice(0, 5).map(renderImportJob).join("") || `<p class="muted">No import jobs yet.</p>`}
        </div>
        <div>
          <h4>Exports</h4>
          ${state.exportJobs.slice(0, 5).map(renderExportJob).join("") || `<p class="muted">No export jobs yet.</p>`}
        </div>
        <div>
          <h4>Sync State</h4>
          ${state.syncStates.slice(0, 5).map(renderSyncState).join("") || `<p class="muted">No sync state yet.</p>`}
        </div>
      </div>
      ${renderSelectedJob()}
    </section>
  `;
}

function renderExportProfileSummary(): string {
  const profile = selectedExportProfile();
  if (!profile) return "";
  return `
    <div class="profile-summary">
      <strong>${escapeHtml(profile.display_name)}</strong>
      <span>${escapeHtml(profile.description)}</span>
      <small>Recommended: ${escapeHtml(profile.recommended_format.toUpperCase())}</small>
    </div>
  `;
}

function renderImportJob(job: ImportJob): string {
  return `
    <button class="job-row ${state.selectedJob?.kind === "import" && state.selectedJob.id === job.id ? "active" : ""}" data-job-kind="import" data-job-id="${job.id}">
      <strong>${escapeHtml(job.kind)} · ${escapeHtml(job.status)}</strong>
      <span>${escapeHtml(job.source_name)} · ${job.message_count} processed · ${job.new_message_count} new · ${job.duplicate_count} duplicate(s) · ${job.error_count} error(s)</span>
      <small>${escapeHtml(formatDate(job.started_at))}</small>
    </button>
  `;
}

function renderExportJob(job: ExportJob): string {
  return `
    <button class="job-row ${state.selectedJob?.kind === "export" && state.selectedJob.id === job.id ? "active" : ""}" data-job-kind="export" data-job-id="${job.id}">
      <strong>${escapeHtml(job.format)} · ${escapeHtml(job.status)}</strong>
      <span>${job.message_count} message(s) · ${job.warning_count} warning(s) · ${job.error_count} error(s)</span>
      <small>${escapeHtml(job.manifest_ref || job.output_root)}</small>
    </button>
  `;
}

function renderSyncState(sync: SyncState): string {
  const status = String(sync.state.last_status ?? "ok");
  const failures = firstStateList(sync.state.last_failed_uids, sync.state.last_failed_uidls, sync.state.last_failed_message_ids);
  const cursor = syncCursor(sync);
  return `
    <div class="job-row sync-state-row">
      <strong>${escapeHtml(sync.source_kind)} · ${escapeHtml(status)}</strong>
      <span>${escapeHtml(sync.source_name)} · ${escapeHtml(sync.scope)}</span>
      <small>${escapeHtml([cursor, failures ? `failed ${failures}` : "", formatDate(sync.updated_at)].filter(Boolean).join(" · "))}</small>
    </div>
  `;
}

function syncCursor(sync: SyncState): string {
  if (sync.state.last_uid !== undefined) return `uid ${String(sync.state.last_uid)}`;
  if (sync.state.next_link) return "next link saved";
  if (sync.state.delta_link) return "delta link saved";
  if (Array.isArray(sync.state.seen_uidls)) return `${sync.state.seen_uidls.length} UIDLs seen`;
  return "";
}

function firstStateList(...values: unknown[]): string {
  for (const value of values) {
    if (Array.isArray(value) && value.length) {
      return value.slice(0, 3).map(String).join(", ");
    }
  }
  return "";
}

function renderSelectedJob(): string {
  if (!state.selectedJob) return "";
  if (state.selectedJob.kind === "import") {
    const job = state.importJobs.find((item) => item.id === state.selectedJob?.id);
    if (!job) return "";
    return `
      <section class="job-detail">
        <div class="operations-header">
          <h4>Import ${job.id}</h4>
          <span>${escapeHtml(job.status)}</span>
        </div>
        <dl class="metadata compact-metadata">
          <div><dt>Source</dt><dd>${escapeHtml(job.source_name)}</dd></div>
          <div><dt>Kind</dt><dd>${escapeHtml(job.kind)}</dd></div>
          <div><dt>Started</dt><dd>${escapeHtml(formatDate(job.started_at))}</dd></div>
          <div><dt>Counts</dt><dd>${job.message_count} processed, ${job.new_message_count} new, ${job.duplicate_count} duplicate(s), ${job.error_count} error(s)</dd></div>
          <div><dt>Options</dt><dd>${escapeHtml(compactJson(job.options_json))}</dd></div>
        </dl>
        ${
          state.selectedJob.errors.length
            ? `<div class="detail-list">${state.selectedJob.errors.map(renderImportError).join("")}</div>`
            : `<p class="muted">No import errors recorded.</p>`
        }
      </section>
    `;
  }

  const job = state.exportJobs.find((item) => item.id === state.selectedJob?.id);
  if (!job) return "";
  return `
    <section class="job-detail">
      <div class="operations-header">
        <h4>Export ${job.id}</h4>
        <span>${escapeHtml(job.status)}</span>
      </div>
      <dl class="metadata compact-metadata">
        <div><dt>Format</dt><dd>${escapeHtml(job.format)}</dd></div>
        <div><dt>Target</dt><dd>${escapeHtml(job.target_profile)}</dd></div>
        <div><dt>Output</dt><dd>${escapeHtml(job.manifest_ref || job.output_root)}</dd></div>
        <div><dt>Counts</dt><dd>${job.message_count} message(s), ${job.warning_count} warning(s), ${job.error_count} error(s)</dd></div>
        <div><dt>Options</dt><dd>${escapeHtml(compactJson(job.options_json))}</dd></div>
      </dl>
      ${
        state.selectedJob.items.length
          ? `<div class="detail-list">${state.selectedJob.items.slice(0, 20).map(renderExportItem).join("")}</div>`
          : `<p class="muted">No export items recorded.</p>`
      }
    </section>
  `;
}

function renderImportError(error: ImportJobError): string {
  return `
    <div class="detail-row">
      <strong>${escapeHtml(error.severity)} · ${escapeHtml(error.source_item_ref || "source item")}</strong>
      <span>${escapeHtml(error.message)}</span>
      <small>${escapeHtml(compactJson(error.detail_json))}</small>
    </div>
  `;
}

function renderExportItem(item: ExportJobItem): string {
  return `
    <div class="detail-row">
      <strong>${escapeHtml(item.format)} · ${escapeHtml(item.status)} · message ${item.message_id}</strong>
      <span>${escapeHtml(item.output_path)}</span>
      <small>${escapeHtml(item.output_hash || compactJson(item.warning_json))}</small>
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
  document.querySelector<HTMLButtonElement>("#source-scan-button")?.addEventListener("click", scanSourcePath);
  document.querySelector<HTMLInputElement>("#source-scan-path")?.addEventListener("keydown", async (event) => {
    if (event.key === "Enter") {
      await scanSourcePath();
    }
  });
  document.querySelectorAll<HTMLButtonElement>(".candidate-import-button").forEach((button) => {
    button.addEventListener("click", async () => {
      await importSourceCandidate(button.dataset.candidateId ?? "");
    });
  });
  document.querySelector<HTMLButtonElement>("#imap-save-button")?.addEventListener("click", saveImapSource);
  document.querySelector<HTMLSelectElement>("#imap-provider")?.addEventListener("change", applyImapProviderPreset);
  document.querySelector<HTMLButtonElement>("#imap-apply-folders-button")?.addEventListener("click", applyDiscoveredImapFolders);
  document.querySelector<HTMLButtonElement>("#imap-sync-selected-folders-button")?.addEventListener("click", syncSelectedImapFolders);
  document.querySelectorAll<HTMLButtonElement>(".imap-discover-button").forEach((button) => {
    button.addEventListener("click", async () => {
      await discoverImapFolders(button.dataset.imapSourceId ?? "");
    });
  });
  document.querySelectorAll<HTMLButtonElement>(".imap-sync-button").forEach((button) => {
    button.addEventListener("click", async () => {
      await syncImapSource(button.dataset.imapSourceId ?? "");
    });
  });
  document.querySelectorAll<HTMLButtonElement>(".imap-delete-button").forEach((button) => {
    button.addEventListener("click", async () => {
      await deleteImapSource(button.dataset.imapSourceId ?? "");
    });
  });
  document.querySelector<HTMLButtonElement>("#pop-save-button")?.addEventListener("click", savePopSource);
  document.querySelector<HTMLSelectElement>("#pop-provider")?.addEventListener("change", applyPopProviderPreset);
  document.querySelectorAll<HTMLButtonElement>(".pop-probe-button").forEach((button) => {
    button.addEventListener("click", async () => {
      await probePopSource(button.dataset.popSourceId ?? "");
    });
  });
  document.querySelectorAll<HTMLButtonElement>(".pop-sync-button").forEach((button) => {
    button.addEventListener("click", async () => {
      await syncPopSource(button.dataset.popSourceId ?? "");
    });
  });
  document.querySelectorAll<HTMLButtonElement>(".pop-delete-button").forEach((button) => {
    button.addEventListener("click", async () => {
      await deletePopSource(button.dataset.popSourceId ?? "");
    });
  });
  document.querySelector<HTMLButtonElement>("#graph-save-button")?.addEventListener("click", saveGraphSource);
  document.querySelector<HTMLSelectElement>("#graph-provider")?.addEventListener("change", applyGraphProviderPreset);
  document.querySelector<HTMLButtonElement>("#graph-apply-folders-button")?.addEventListener("click", applyDiscoveredGraphFolders);
  document.querySelector<HTMLButtonElement>("#graph-sync-selected-folders-button")?.addEventListener("click", syncSelectedGraphFolders);
  document.querySelectorAll<HTMLButtonElement>(".graph-connect-button").forEach((button) => {
    button.addEventListener("click", async () => {
      await connectGraphSource(button.dataset.graphSourceId ?? "");
    });
  });
  document.querySelectorAll<HTMLButtonElement>(".graph-discover-button").forEach((button) => {
    button.addEventListener("click", async () => {
      await discoverGraphFolders(button.dataset.graphSourceId ?? "");
    });
  });
  document.querySelectorAll<HTMLButtonElement>(".graph-probe-button").forEach((button) => {
    button.addEventListener("click", async () => {
      await probeGraphSource(button.dataset.graphSourceId ?? "");
    });
  });
  document.querySelectorAll<HTMLButtonElement>(".graph-sync-button").forEach((button) => {
    button.addEventListener("click", async () => {
      await syncGraphSource(button.dataset.graphSourceId ?? "");
    });
  });
  document.querySelectorAll<HTMLButtonElement>(".graph-delete-button").forEach((button) => {
    button.addEventListener("click", async () => {
      await deleteGraphSource(button.dataset.graphSourceId ?? "");
    });
  });
  document.querySelector<HTMLButtonElement>("#export-button")?.addEventListener("click", exportMail);
  document.querySelector<HTMLButtonElement>("#backup-button")?.addEventListener("click", backupActiveProfile);
  document.querySelector<HTMLButtonElement>("#restore-button")?.addEventListener("click", restoreBackup);
  document.querySelector<HTMLSelectElement>("#export-profile")?.addEventListener("change", (event) => {
    state.selectedExportProfileId = (event.currentTarget as HTMLSelectElement).value;
    state.selectedExportFormat = "auto";
    render();
  });
  document.querySelector<HTMLSelectElement>("#export-format")?.addEventListener("change", (event) => {
    state.selectedExportFormat = (event.currentTarget as HTMLSelectElement).value;
  });
  document.querySelector<HTMLButtonElement>("#logout-button")?.addEventListener("click", logout);
  document.querySelector<HTMLSelectElement>("#profile-select")?.addEventListener("change", switchProfile);
  document.querySelector<HTMLButtonElement>("#profile-create-button")?.addEventListener("click", createProfile);
  document.querySelector<HTMLInputElement>("#profile-name")?.addEventListener("keydown", async (event) => {
    if (event.key === "Enter") {
      await createProfile();
    }
  });

  document.querySelectorAll<HTMLButtonElement>(".job-row[data-job-kind]").forEach((button) => {
    button.addEventListener("click", async () => {
      const kind = button.dataset.jobKind;
      const id = Number(button.dataset.jobId);
      if (kind === "import") await loadImportJobDetail(id);
      if (kind === "export") await loadExportJobDetail(id);
    });
  });
}

function bindAuthEvents(): void {
  document.querySelector<HTMLFormElement>("#setup-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await setupAdmin();
  });
  document.querySelector<HTMLFormElement>("#login-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await login();
  });
}

async function loadInitial(): Promise<void> {
  try {
    await loadAuth();
    if (!state.auth?.authenticated && !state.auth?.dev_bypass) {
      state.status = state.auth?.setup_required ? "Create the first admin user." : "Login required.";
      render();
      return;
    }
    state.health = await api<Health>("/api/v1/health");
    state.auth = state.health.auth;
    await loadProfiles();
    await loadMigrations();
    await loadExportProfiles();
    await loadImapProviders();
    await loadImapSources();
    await loadPopProviders();
    await loadPopSources();
    await loadGraphProviders();
    await loadGraphSources();
    await loadJobs();
    await loadMailboxes();
    await loadMessages();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function loadAuth(): Promise<void> {
  const payload = await api<{ auth: AuthStatus }>("/api/v1/auth/status");
  state.auth = payload.auth;
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
  const [importPayload, exportPayload, syncPayload] = await Promise.all([
    api<{ import_jobs: ImportJob[] }>("/api/v1/import-jobs"),
    api<{ export_jobs: ExportJob[] }>("/api/v1/export-jobs"),
    api<{ sync_states: SyncState[] }>("/api/v1/sync-states"),
  ]);
  state.importJobs = importPayload.import_jobs;
  state.exportJobs = exportPayload.export_jobs;
  state.syncStates = syncPayload.sync_states;
}

async function loadExportProfiles(): Promise<void> {
  const payload = await api<{ export_profiles: ExportProfile[] }>("/api/v1/export-profiles");
  state.exportProfiles = payload.export_profiles;
  if (!state.exportProfiles.some((profile) => profile.id === state.selectedExportProfileId)) {
    state.selectedExportProfileId = state.exportProfiles[0]?.id ?? "generic-eml";
  }
}

async function loadImapProviders(): Promise<void> {
  const payload = await api<{ providers: ImapProvider[] }>("/api/v1/imap-providers");
  state.imapProviders = payload.providers;
}

async function loadImapSources(): Promise<void> {
  const payload = await api<{ sources: ImapSource[] }>("/api/v1/imap-sources");
  state.imapSources = payload.sources;
}

async function loadPopProviders(): Promise<void> {
  const payload = await api<{ providers: PopProvider[] }>("/api/v1/pop-providers");
  state.popProviders = payload.providers;
}

async function loadPopSources(): Promise<void> {
  const payload = await api<{ sources: PopSource[] }>("/api/v1/pop-sources");
  state.popSources = payload.sources;
}

async function loadGraphProviders(): Promise<void> {
  const payload = await api<{ providers: GraphProvider[] }>("/api/v1/graph-providers");
  state.graphProviders = payload.providers;
}

async function loadGraphSources(): Promise<void> {
  const payload = await api<{ sources: GraphSource[] }>("/api/v1/graph-sources");
  state.graphSources = payload.sources;
}

async function loadImportJobDetail(id: number): Promise<void> {
  const payload = await api<{ errors: ImportJobError[] }>(`/api/v1/import-jobs/${id}/errors`);
  state.selectedJob = { kind: "import", id, errors: payload.errors };
  state.status = `Import job ${id} loaded.`;
  render();
}

async function loadExportJobDetail(id: number): Promise<void> {
  const payload = await api<{ items: ExportJobItem[] }>(`/api/v1/export-jobs/${id}/items`);
  state.selectedJob = { kind: "export", id, items: payload.items };
  state.status = `Export job ${id} loaded.`;
  render();
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

async function scanSourcePath(): Promise<void> {
  const path = document.querySelector<HTMLInputElement>("#source-scan-path")?.value.trim() ?? "";
  const type = document.querySelector<HTMLSelectElement>("#source-scan-type")?.value ?? "auto";
  state.sourceScanPath = path;
  state.sourceScanType = type;
  if (!path) {
    state.status = "Enter a local source path to scan.";
    render();
    return;
  }
  state.status = "Scanning source...";
  render();
  try {
    const params = new URLSearchParams({ path, type });
    const payload = await api<{ candidates: SourceCandidate[] }>(`/api/v1/source-scan?${params.toString()}`);
    state.sourceScanCandidates = payload.candidates;
    state.status = `Found ${payload.candidates.length} candidate(s).`;
    render();
  } catch (error) {
    state.sourceScanCandidates = [];
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function importSourceCandidate(candidateId: string): Promise<void> {
  const candidate = state.sourceScanCandidates.find((item) => item.id === candidateId);
  if (!candidate) {
    state.status = "Candidate is no longer available.";
    render();
    return;
  }
  if (!candidate.importable) {
    state.status = candidate.notes[0] ?? "This source is not importable yet.";
    render();
    return;
  }
  state.status = `Importing ${candidate.mailbox_path}...`;
  render();
  try {
    const result = await api<{ imported: number; processed: number; duplicates: number; errors: number; format: string }>("/api/v1/import", {
      method: "POST",
      body: JSON.stringify({
        path: candidate.path,
        format: candidate.format,
        sourceName: candidate.display_name,
        mailboxPath: candidate.mailbox_path,
      }),
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

function applyImapProviderPreset(event: Event): void {
  const providerId = (event.currentTarget as HTMLSelectElement).value;
  const provider = state.imapProviders.find((item) => item.id === providerId);
  if (!provider || provider.id === "generic") return;

  const hostInput = document.querySelector<HTMLInputElement>("#imap-host");
  const portInput = document.querySelector<HTMLInputElement>("#imap-port");
  const folderInput = document.querySelector<HTMLInputElement>("#imap-folder");
  const securitySelect = document.querySelector<HTMLSelectElement>("#imap-security");
  const nameInput = document.querySelector<HTMLInputElement>("#imap-name");
  const usernameInput = document.querySelector<HTMLInputElement>("#imap-username");

  if (hostInput && !hostInput.value.trim()) hostInput.value = provider.host;
  if (portInput && !portInput.value.trim()) portInput.value = String(provider.port);
  if (folderInput && !folderInput.value.trim()) folderInput.value = provider.default_folders.join(", ");
  if (securitySelect) securitySelect.value = provider.use_tls ? "tls" : "plain";
  if (nameInput && !nameInput.value.trim() && usernameInput?.value.trim()) {
    nameInput.value = usernameInput.value.trim();
  }
}

async function saveImapSource(): Promise<void> {
  const name = document.querySelector<HTMLInputElement>("#imap-name")?.value.trim() ?? "";
  const provider = document.querySelector<HTMLSelectElement>("#imap-provider")?.value ?? "generic";
  const host = document.querySelector<HTMLInputElement>("#imap-host")?.value.trim() ?? "";
  const username = document.querySelector<HTMLInputElement>("#imap-username")?.value.trim() ?? "";
  const password = document.querySelector<HTMLInputElement>("#imap-password")?.value ?? "";
  const folderText = document.querySelector<HTMLInputElement>("#imap-folder")?.value.trim() || "INBOX";
  const useTls = (document.querySelector<HTMLSelectElement>("#imap-security")?.value ?? "tls") === "tls";
  const portText = document.querySelector<HTMLInputElement>("#imap-port")?.value.trim();
  const limitText = document.querySelector<HTMLInputElement>("#imap-limit")?.value.trim();
  const folders = folderText.split(",").map((item) => item.trim()).filter(Boolean);
  if (!name || !host || !username) {
    state.status = "IMAP source, host, and username are required.";
    render();
    return;
  }
  state.status = "Saving IMAP source...";
  render();
  try {
    const port = portText ? Number(portText) : useTls ? 993 : 143;
    const syncLimit = limitText ? Number(limitText) : 100;
    const payload = await api<{ source: ImapSource; sources: ImapSource[] }>("/api/v1/imap-sources", {
      method: "POST",
      body: JSON.stringify({
        name,
        provider,
        host,
        username,
        password,
        folders,
        use_tls: useTls,
        port,
        sync_limit: syncLimit,
      }),
    });
    state.imapSources = payload.sources;
    state.imapDiscoveredSourceId = null;
    state.imapDiscoveredFolders = [];
    state.status = `Saved IMAP source ${payload.source.name}.`;
    render();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function discoverImapFolders(sourceId: string): Promise<void> {
  const source = state.imapSources.find((item) => item.id === sourceId);
  if (!source) {
    state.status = "IMAP source is no longer available.";
    render();
    return;
  }
  state.status = `Discovering folders for ${source.name}...`;
  render();
  try {
    const payload = await api<{ folders: ImapFolder[] }>(`/api/v1/imap-sources/${encodeURIComponent(sourceId)}/folders`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.imapDiscoveredSourceId = sourceId;
    state.imapDiscoveredFolders = payload.folders;
    state.status = `Found ${payload.folders.filter((folder) => folder.selectable).length} selectable folder(s).`;
    render();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function applyDiscoveredImapFolders(): Promise<void> {
  const sourceId = state.imapDiscoveredSourceId;
  const source = state.imapSources.find((item) => item.id === sourceId);
  if (!sourceId || !source) {
    state.status = "Discover folders before applying a folder list.";
    render();
    return;
  }
  const folders = selectedDiscoveredImapFolders();
  if (!folders.length) {
    state.status = "Select at least one IMAP folder.";
    render();
    return;
  }
  state.status = `Saving folders for ${source.name}...`;
  render();
  try {
    const payload = await api<{ source: ImapSource; sources: ImapSource[] }>("/api/v1/imap-sources", {
      method: "POST",
      body: JSON.stringify({
        id: source.id,
        name: source.name,
        host: source.host,
        port: source.port,
        username: source.username,
        use_tls: source.use_tls,
        folders,
        sync_limit: source.sync_limit,
        auth_method: source.auth_method,
        provider: source.provider,
      }),
    });
    state.imapSources = payload.sources;
    state.status = `Saved ${folders.length} folder(s) for ${payload.source.name}.`;
    render();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function syncSelectedImapFolders(): Promise<void> {
  const sourceId = state.imapDiscoveredSourceId;
  if (!sourceId) {
    state.status = "Discover folders before syncing a selected folder list.";
    render();
    return;
  }
  const folders = selectedDiscoveredImapFolders();
  if (!folders.length) {
    state.status = "Select at least one IMAP folder.";
    render();
    return;
  }
  await syncImapSource(sourceId, folders);
}

function selectedDiscoveredImapFolders(): string[] {
  return Array.from(document.querySelectorAll<HTMLInputElement>(".imap-folder-picker-panel .imap-folder-option input:checked"))
    .map((input) => input.value)
    .filter(Boolean);
}

async function deleteImapSource(sourceId: string): Promise<void> {
  const source = state.imapSources.find((item) => item.id === sourceId);
  if (!source) {
    state.status = "IMAP source is no longer available.";
    render();
    return;
  }
  state.status = `Deleting ${source.name}...`;
  render();
  try {
    const payload = await api<{ deleted: boolean; sources: ImapSource[] }>(`/api/v1/imap-sources/${encodeURIComponent(sourceId)}/delete`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.imapSources = payload.sources;
    if (state.imapDiscoveredSourceId === sourceId) {
      state.imapDiscoveredSourceId = null;
      state.imapDiscoveredFolders = [];
    }
    state.status = `Deleted ${source.name}.`;
    render();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function syncImapSource(sourceId: string, folders?: string[]): Promise<void> {
  const source = state.imapSources.find((item) => item.id === sourceId);
  if (!source) {
    state.status = "IMAP source is no longer available.";
    render();
    return;
  }
  const folderList = folders && folders.length ? folders : source.folders;
  state.status = `Syncing ${source.name} (${folderList.join(", ")})...`;
  render();
  try {
    const payload = await api<{ sync: ImapSyncResult }>(`/api/v1/imap-sources/${encodeURIComponent(sourceId)}/sync`, {
      method: "POST",
      body: JSON.stringify({ folders }),
    });
    await loadMailboxes();
    await loadJobs();
    await loadMessages();
    state.status = `IMAP sync processed ${payload.sync.processed} message(s); new=${payload.sync.imported}, duplicates=${payload.sync.duplicates}, errors=${payload.sync.errors}.`;
    render();
  } catch (error) {
    await loadJobs();
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

function applyPopProviderPreset(event: Event): void {
  const providerId = (event.currentTarget as HTMLSelectElement).value;
  const provider = state.popProviders.find((item) => item.id === providerId);
  if (!provider || provider.id === "generic") return;

  const hostInput = document.querySelector<HTMLInputElement>("#pop-host");
  const portInput = document.querySelector<HTMLInputElement>("#pop-port");
  const securitySelect = document.querySelector<HTMLSelectElement>("#pop-security");
  const nameInput = document.querySelector<HTMLInputElement>("#pop-name");
  const usernameInput = document.querySelector<HTMLInputElement>("#pop-username");

  if (hostInput && !hostInput.value.trim()) hostInput.value = provider.host;
  if (portInput && !portInput.value.trim()) portInput.value = String(provider.port);
  if (securitySelect) securitySelect.value = provider.use_ssl ? "ssl" : "plain";
  if (nameInput && !nameInput.value.trim() && usernameInput?.value.trim()) {
    nameInput.value = usernameInput.value.trim();
  }
}

async function savePopSource(): Promise<void> {
  const name = document.querySelector<HTMLInputElement>("#pop-name")?.value.trim() ?? "";
  const provider = document.querySelector<HTMLSelectElement>("#pop-provider")?.value ?? "generic";
  const host = document.querySelector<HTMLInputElement>("#pop-host")?.value.trim() ?? "";
  const username = document.querySelector<HTMLInputElement>("#pop-username")?.value.trim() ?? "";
  const password = document.querySelector<HTMLInputElement>("#pop-password")?.value ?? "";
  const useSsl = (document.querySelector<HTMLSelectElement>("#pop-security")?.value ?? "ssl") === "ssl";
  const portText = document.querySelector<HTMLInputElement>("#pop-port")?.value.trim();
  const limitText = document.querySelector<HTMLInputElement>("#pop-limit")?.value.trim();
  if (!name || !host || !username) {
    state.status = "POP source, host, and username are required.";
    render();
    return;
  }
  state.status = "Saving POP source...";
  render();
  try {
    const port = portText ? Number(portText) : useSsl ? 995 : 110;
    const syncLimit = limitText ? Number(limitText) : 100;
    const payload = await api<{ source: PopSource; sources: PopSource[] }>("/api/v1/pop-sources", {
      method: "POST",
      body: JSON.stringify({
        name,
        provider,
        host,
        username,
        password,
        use_ssl: useSsl,
        port,
        sync_limit: syncLimit,
      }),
    });
    state.popSources = payload.sources;
    state.status = `Saved POP source ${payload.source.name}.`;
    render();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function probePopSource(sourceId: string): Promise<void> {
  const source = state.popSources.find((item) => item.id === sourceId);
  if (!source) {
    state.status = "POP source is no longer available.";
    render();
    return;
  }
  state.status = `Probing ${source.name} without retrieving or deleting mail...`;
  render();
  try {
    const payload = await api<{ probe: PopProbeResult }>(`/api/v1/pop-sources/${encodeURIComponent(sourceId)}/probe`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.status = `POP probe found ${payload.probe.message_count} message(s), UIDL=${payload.probe.uidl_available ? "yes" : "no"}; no RETR/DELE used.`;
    render();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function syncPopSource(sourceId: string): Promise<void> {
  const source = state.popSources.find((item) => item.id === sourceId);
  if (!source) {
    state.status = "POP source is no longer available.";
    render();
    return;
  }
  state.status = `Syncing ${source.name} by POP without delete...`;
  render();
  try {
    const payload = await api<{ sync: PopSyncResult }>(`/api/v1/pop-sources/${encodeURIComponent(sourceId)}/sync`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await loadMailboxes();
    await loadJobs();
    await loadMessages();
    state.status = `POP sync processed ${payload.sync.processed} message(s); new=${payload.sync.imported}, duplicates=${payload.sync.duplicates}, errors=${payload.sync.errors}; delete never.`;
    render();
  } catch (error) {
    await loadJobs();
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function deletePopSource(sourceId: string): Promise<void> {
  const source = state.popSources.find((item) => item.id === sourceId);
  if (!source) {
    state.status = "POP source is no longer available.";
    render();
    return;
  }
  state.status = `Deleting ${source.name}...`;
  render();
  try {
    const payload = await api<{ deleted: boolean; sources: PopSource[] }>(`/api/v1/pop-sources/${encodeURIComponent(sourceId)}/delete`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.popSources = payload.sources;
    state.status = `Deleted ${source.name}.`;
    render();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

function applyGraphProviderPreset(event: Event): void {
  const providerId = (event.currentTarget as HTMLSelectElement).value;
  const provider = state.graphProviders.find((item) => item.id === providerId);
  if (!provider) return;

  const tenantInput = document.querySelector<HTMLInputElement>("#graph-tenant-id");
  const scopesInput = document.querySelector<HTMLInputElement>("#graph-scopes");
  const redirectInput = document.querySelector<HTMLInputElement>("#graph-redirect-uri");

  if (tenantInput && !tenantInput.value.trim()) tenantInput.value = provider.default_tenant;
  if (scopesInput && !scopesInput.value.trim()) scopesInput.value = provider.default_scopes.join(" ");
  if (redirectInput && !redirectInput.value.trim()) redirectInput.value = "http://localhost";
}

async function saveGraphSource(): Promise<void> {
  const name = document.querySelector<HTMLInputElement>("#graph-name")?.value.trim() ?? "";
  const provider = document.querySelector<HTMLSelectElement>("#graph-provider")?.value ?? "microsoft-graph";
  const clientId = document.querySelector<HTMLInputElement>("#graph-client-id")?.value.trim() ?? "";
  const tenantId = document.querySelector<HTMLInputElement>("#graph-tenant-id")?.value.trim() || "common";
  const redirectUri = document.querySelector<HTMLInputElement>("#graph-redirect-uri")?.value.trim() || "http://localhost";
  const mailbox = document.querySelector<HTMLInputElement>("#graph-mailbox")?.value.trim() || "me";
  const scopeText =
    document.querySelector<HTMLInputElement>("#graph-scopes")?.value.trim() ||
    "openid offline_access User.Read Mail.Read";
  const limitText = document.querySelector<HTMLInputElement>("#graph-limit")?.value.trim();
  if (!name || !clientId) {
    state.status = "Graph source and client ID are required.";
    render();
    return;
  }
  state.status = "Saving Graph source...";
  render();
  try {
    const payload = await api<{ source: GraphSource; sources: GraphSource[] }>("/api/v1/graph-sources", {
      method: "POST",
      body: JSON.stringify({
        name,
        provider,
        client_id: clientId,
        tenant_id: tenantId,
        redirect_uri: redirectUri,
        scopes: scopeText.split(/[\s,]+/).filter(Boolean),
        mailbox,
        sync_limit: limitText ? Number(limitText) : 100,
      }),
    });
    state.graphSources = payload.sources;
    state.status = `Saved Graph source ${payload.source.name}.`;
    render();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function connectGraphSource(sourceId: string): Promise<void> {
  const source = state.graphSources.find((item) => item.id === sourceId);
  if (!source) {
    state.status = "Graph source is no longer available.";
    render();
    return;
  }
  state.status = `Opening Microsoft sign-in for ${source.name}...`;
  render();
  try {
    const payload = await api<{ auth: GraphAuthRequest }>(`/api/v1/graph-sources/${encodeURIComponent(sourceId)}/auth-url`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    const opened = window.open(payload.auth.authorization_url, "_blank", "noopener,noreferrer");
    if (!opened) {
      window.location.href = payload.auth.authorization_url;
      return;
    }
    state.status = `Microsoft sign-in opened for ${source.name}.`;
    await loadGraphSources();
    render();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function probeGraphSource(sourceId: string): Promise<void> {
  const source = state.graphSources.find((item) => item.id === sourceId);
  if (!source) {
    state.status = "Graph source is no longer available.";
    render();
    return;
  }
  state.status = `Probing ${source.name} by Microsoft Graph...`;
  render();
  try {
    const payload = await api<{ probe: GraphProbeResult }>(`/api/v1/graph-sources/${encodeURIComponent(sourceId)}/probe`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await loadGraphSources();
    const account = payload.probe.user_principal_name || payload.probe.mail || payload.probe.display_name || payload.probe.mailbox;
    state.status = `Graph probe found ${payload.probe.folder_count} folder(s) for ${account}; token refreshed=${payload.probe.token_refreshed ? "yes" : "no"}.`;
    render();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function discoverGraphFolders(sourceId: string): Promise<void> {
  const source = state.graphSources.find((item) => item.id === sourceId);
  if (!source) {
    state.status = "Graph source is no longer available.";
    render();
    return;
  }
  state.status = `Discovering Graph folders for ${source.name}...`;
  render();
  try {
    const payload = await api<{ folders: GraphFolder[] }>(`/api/v1/graph-sources/${encodeURIComponent(sourceId)}/folders`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.graphDiscoveredSourceId = sourceId;
    state.graphDiscoveredFolders = payload.folders;
    state.status = `Found ${payload.folders.length} Graph folder(s).`;
    render();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function applyDiscoveredGraphFolders(): Promise<void> {
  const sourceId = state.graphDiscoveredSourceId;
  const source = state.graphSources.find((item) => item.id === sourceId);
  if (!sourceId || !source) {
    state.status = "Discover Graph folders before applying a folder list.";
    render();
    return;
  }
  const folders = selectedDiscoveredGraphFolders();
  if (!folders.length) {
    state.status = "Select at least one Graph folder.";
    render();
    return;
  }
  state.status = `Saving Graph folders for ${source.name}...`;
  render();
  try {
    const payload = await api<{ source: GraphSource; sources: GraphSource[] }>("/api/v1/graph-sources", {
      method: "POST",
      body: JSON.stringify({
        id: source.id,
        name: source.name,
        client_id: source.client_id,
        tenant_id: source.tenant_id,
        redirect_uri: source.redirect_uri,
        scopes: source.scopes,
        mailbox: source.mailbox,
        folders,
        sync_limit: source.sync_limit,
        auth_method: source.auth_method,
        provider: source.provider,
      }),
    });
    state.graphSources = payload.sources;
    state.status = `Saved ${folders.length} Graph folder(s) for ${payload.source.name}.`;
    render();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function syncSelectedGraphFolders(): Promise<void> {
  const sourceId = state.graphDiscoveredSourceId;
  if (!sourceId) {
    state.status = "Discover Graph folders before syncing a selected folder list.";
    render();
    return;
  }
  const folders = selectedDiscoveredGraphFolders();
  if (!folders.length) {
    state.status = "Select at least one Graph folder.";
    render();
    return;
  }
  await saveGraphFoldersForSync(sourceId, folders);
  await syncGraphSource(sourceId);
}

function selectedDiscoveredGraphFolders(): GraphFolderSelection[] {
  const selectedIds = new Set(
    Array.from(document.querySelectorAll<HTMLInputElement>(".graph-folder-picker .imap-folder-option input:checked"))
      .map((input) => input.value)
      .filter(Boolean),
  );
  return state.graphDiscoveredFolders
    .filter((folder) => selectedIds.has(folder.id))
    .map((folder) => ({ id: folder.id, display_name: folder.display_name, path: folder.path }));
}

async function saveGraphFoldersForSync(sourceId: string, folders: GraphFolderSelection[]): Promise<void> {
  const source = state.graphSources.find((item) => item.id === sourceId);
  if (!source) return;
  const payload = await api<{ source: GraphSource; sources: GraphSource[] }>("/api/v1/graph-sources", {
    method: "POST",
    body: JSON.stringify({
      id: source.id,
      name: source.name,
      client_id: source.client_id,
      tenant_id: source.tenant_id,
      redirect_uri: source.redirect_uri,
      scopes: source.scopes,
      mailbox: source.mailbox,
      folders,
      sync_limit: source.sync_limit,
      auth_method: source.auth_method,
      provider: source.provider,
    }),
  });
  state.graphSources = payload.sources;
}

async function syncGraphSource(sourceId: string): Promise<void> {
  const source = state.graphSources.find((item) => item.id === sourceId);
  if (!source) {
    state.status = "Graph source is no longer available.";
    render();
    return;
  }
  if (!source.folders.length) {
    state.status = "Select Graph folders before syncing.";
    render();
    return;
  }
  state.status = `Syncing ${source.name} by Microsoft Graph...`;
  render();
  try {
    const payload = await api<{ sync: GraphSyncResult }>(`/api/v1/graph-sources/${encodeURIComponent(sourceId)}/sync`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await loadMailboxes();
    await loadJobs();
    await loadMessages();
    await loadGraphSources();
    state.status = `Graph sync processed ${payload.sync.processed}/${payload.sync.sync_limit} message(s); new=${payload.sync.imported}, duplicates=${payload.sync.duplicates}, removed=${payload.sync.removed}, errors=${payload.sync.errors}.`;
    render();
  } catch (error) {
    await loadJobs();
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function deleteGraphSource(sourceId: string): Promise<void> {
  const source = state.graphSources.find((item) => item.id === sourceId);
  if (!source) {
    state.status = "Graph source is no longer available.";
    render();
    return;
  }
  state.status = `Deleting ${source.name}...`;
  render();
  try {
    const payload = await api<{ deleted: boolean; sources: GraphSource[] }>(`/api/v1/graph-sources/${encodeURIComponent(sourceId)}/delete`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.graphSources = payload.sources;
    if (state.graphDiscoveredSourceId === sourceId) {
      state.graphDiscoveredSourceId = null;
      state.graphDiscoveredFolders = [];
    }
    state.status = `Deleted ${source.name}.`;
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
    state.selectedJob = null;
    state.sourceScanCandidates = [];
    state.imapDiscoveredSourceId = null;
    state.imapDiscoveredFolders = [];
    state.graphDiscoveredSourceId = null;
    state.graphDiscoveredFolders = [];
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
    state.selectedJob = null;
    state.sourceScanCandidates = [];
    state.imapDiscoveredSourceId = null;
    state.imapDiscoveredFolders = [];
    state.graphDiscoveredSourceId = null;
    state.graphDiscoveredFolders = [];
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
  state.auth = state.health.auth;
  await loadProfiles();
  await loadMigrations();
  await loadExportProfiles();
  await loadImapProviders();
  await loadImapSources();
  await loadPopProviders();
  await loadPopSources();
  await loadGraphProviders();
  await loadGraphSources();
  await loadJobs();
  await loadMailboxes();
  await loadMessages();
}

async function setupAdmin(): Promise<void> {
  const username = document.querySelector<HTMLInputElement>("#auth-username")?.value.trim() ?? "";
  const password = document.querySelector<HTMLInputElement>("#auth-password")?.value ?? "";
  try {
    const payload = await api<{ auth: AuthStatus }>("/api/v1/auth/setup", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    state.auth = payload.auth;
    state.status = "Admin created.";
    await loadInitial();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function login(): Promise<void> {
  const username = document.querySelector<HTMLInputElement>("#auth-username")?.value.trim() ?? "";
  const password = document.querySelector<HTMLInputElement>("#auth-password")?.value ?? "";
  try {
    const payload = await api<{ auth: AuthStatus }>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    state.auth = payload.auth;
    state.status = "Logged in.";
    await loadInitial();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function logout(): Promise<void> {
  try {
    const payload = await api<{ auth: AuthStatus }>("/api/v1/auth/logout", { method: "POST" });
    state.auth = payload.auth;
    state.status = "Logged out.";
    render();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function exportMail(): Promise<void> {
  const outputPath = document.querySelector<HTMLInputElement>("#export-path")?.value.trim() || defaultExportPath();
  const format = document.querySelector<HTMLSelectElement>("#export-format")?.value ?? state.selectedExportFormat;
  const targetProfile = document.querySelector<HTMLSelectElement>("#export-profile")?.value ?? state.selectedExportProfileId;
  state.selectedExportProfileId = targetProfile;
  state.selectedExportFormat = format;
  state.status = "Exporting...";
  render();
  try {
    const result = await api<{ export_job_id: number; exported: number; warnings: number; manifest_path: string }>("/api/v1/export", {
      method: "POST",
      body: JSON.stringify({
        outputPath,
        format,
        mailboxId: state.selectedMailboxId,
        targetProfile,
      }),
    });
    state.status = `Exported ${result.exported} message(s), warnings=${result.warnings}. Manifest: ${result.manifest_path}`;
    await loadJobs();
    await loadExportJobDetail(result.export_job_id);
    render();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function backupActiveProfile(): Promise<void> {
  const outputPath = document.querySelector<HTMLInputElement>("#backup-path")?.value.trim() || defaultBackupPath();
  const includeSecrets = document.querySelector<HTMLInputElement>("#backup-include-secrets")?.checked ?? false;
  state.status = "Creating backup...";
  render();
  try {
    const payload = await api<{ backup: BackupResult }>("/api/v1/backup", {
      method: "POST",
      body: JSON.stringify({
        outputPath,
        includeSecrets,
      }),
    });
    const warningText = payload.backup.warnings.length ? ` warnings=${payload.backup.warnings.length}` : "";
    state.status = `Backup created: ${payload.backup.output_path} (${payload.backup.file_count} file(s), secrets=${payload.backup.include_secrets ? "included" : "redacted"}${warningText}).`;
    render();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

async function restoreBackup(): Promise<void> {
  const path = document.querySelector<HTMLInputElement>("#restore-path")?.value.trim() ?? "";
  const name = document.querySelector<HTMLInputElement>("#restore-name")?.value.trim() ?? "";
  if (!path) {
    state.status = "Enter a backup ZIP path to restore.";
    render();
    return;
  }
  state.status = "Restoring backup...";
  render();
  try {
    const payload = await api<{
      restore: RestoreResult;
      active_profile_id: string;
      profiles: Profile[];
    }>("/api/v1/restore-backup", {
      method: "POST",
      body: JSON.stringify({
        path,
        name,
        switch: true,
      }),
    });
    state.profiles = payload.profiles;
    state.activeProfileId = payload.active_profile_id;
    state.status = `Restored ${payload.restore.profile_name} (${payload.restore.file_count} file(s)) and switched profiles.`;
    await refreshActiveProfileData();
    render();
  } catch (error) {
    state.status = error instanceof Error ? error.message : String(error);
    render();
  }
}

function defaultExportPath(): string {
  return `.private/local/exports/${state.activeProfileId ?? "default"}/${state.selectedExportProfileId}`;
}

function defaultBackupPath(): string {
  return `.private/local/backups/${state.activeProfileId ?? "default"}`;
}

function selectedExportProfile(): ExportProfile | null {
  return state.exportProfiles.find((profile) => profile.id === state.selectedExportProfileId) ?? null;
}

function authLabel(): string {
  if (!state.auth) return "Unknown";
  if (state.auth.dev_bypass) return "Dev bypass";
  return state.auth.authenticated ? state.auth.username || "Session" : "Required";
}

function compactJson(value: string | null): string {
  if (!value) return "";
  try {
    return JSON.stringify(JSON.parse(value));
  } catch {
    return value;
  }
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
