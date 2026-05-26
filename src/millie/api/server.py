from __future__ import annotations

import json
import mimetypes
import re
import ssl
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from millie import __version__
from millie.auth import AuthManager, expired_session_cookie, session_cookie
from millie.backup import create_backup
from millie.config import AppConfig
from millie.export_profiles import list_export_profiles
from millie.exporters import export_messages
from millie.importers import import_path
from millie.graph_connector import (
    complete_graph_authorization,
    create_graph_authorization_request,
    delete_graph_source,
    discover_graph_folders,
    get_graph_source,
    list_graph_provider_presets,
    load_graph_sources,
    probe_graph_source,
    save_graph_source,
    sync_graph_source,
)
from millie.imap_connector import (
    delete_imap_source,
    discover_imap_folders,
    get_imap_source,
    list_imap_provider_presets,
    load_imap_sources,
    migrate_imap_source_secrets,
    save_imap_source,
    sync_imap_source,
)
from millie.pop_connector import (
    delete_pop_source,
    get_pop_source,
    list_pop_provider_presets,
    load_pop_sources,
    migrate_pop_source_secrets,
    probe_pop_source,
    save_pop_source,
    sync_pop_source,
)
from millie.profiles import ProfileManager
from millie.secrets import SecretManager
from millie.source_scanners import scan_source


class MillieRequestHandler(BaseHTTPRequestHandler):
    server_version = "MILLIE/0.1"

    @property
    def app(self) -> "MillieHTTPServer":
        return self.server  # type: ignore[return-value]

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[millie] {self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if is_graph_oauth_callback(path, query):
                self.handle_graph_oauth_callback(query)
            elif path == "/api/v1/auth/status":
                self.write_json({"auth": self.app.auth.status(self.headers.get("Cookie")).to_api()})
            elif path.startswith("/api/v1/") and not self.app.is_authorized(self.headers.get("Cookie")):
                self.write_error(HTTPStatus.UNAUTHORIZED, "Authentication required")
            elif path == "/api/v1/health":
                self.write_json(
                    {
                        "ok": True,
                        "version": __version__,
                        "auth": self.app.auth.status(self.headers.get("Cookie")).to_api(),
                        "profile": self.app.profile_manager.active_profile().to_api(),
                        "secrets": self.app.secret_manager.status(),
                        "settings_path": str(self.app.profile_manager.settings_path),
                        "db_path": str(self.app.db.db_path),
                        "data_dir": str(self.app.db.data_dir),
                    }
                )
            elif path == "/api/v1/profiles":
                self.write_json(
                    {
                        "active_profile_id": self.app.profile_manager.active_profile_id,
                        "profiles": self.app.profile_manager.list_profiles(),
                    }
                )
            elif path == "/api/v1/source-scan":
                source_path = query.get("path", [""])[0].strip()
                source_type = query.get("type", ["auto"])[0].strip() or "auto"
                if not source_path:
                    self.write_error(HTTPStatus.BAD_REQUEST, "path is required")
                    return
                try:
                    candidates = scan_source(Path(source_path), source_type)
                except FileNotFoundError as exc:
                    self.write_error(HTTPStatus.BAD_REQUEST, f"Path not found: {exc}")
                    return
                except ValueError as exc:
                    self.write_error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self.write_json(
                    {
                        "path": str(Path(source_path).expanduser().resolve()),
                        "source_type": source_type,
                        "candidates": [candidate.to_api() for candidate in candidates],
                    }
                )
            elif path == "/api/v1/sources":
                self.write_json({"sources": self.app.db.list_sources()})
            elif path == "/api/v1/imap-sources":
                self.write_json(
                    {"sources": [source.to_api() for source in load_imap_sources(self.app.profile_manager)]}
                )
            elif path == "/api/v1/imap-providers":
                self.write_json({"providers": [provider.to_api() for provider in list_imap_provider_presets()]})
            elif path == "/api/v1/pop-sources":
                self.write_json(
                    {"sources": [source.to_api() for source in load_pop_sources(self.app.profile_manager)]}
                )
            elif path == "/api/v1/pop-providers":
                self.write_json({"providers": [provider.to_api() for provider in list_pop_provider_presets()]})
            elif path == "/api/v1/graph-sources":
                self.write_json(
                    {"sources": [source.to_api() for source in load_graph_sources(self.app.profile_manager)]}
                )
            elif path == "/api/v1/graph-providers":
                self.write_json({"providers": [provider.to_api() for provider in list_graph_provider_presets()]})
            elif path == "/api/v1/mailboxes":
                self.write_json({"mailboxes": self.app.db.list_mailboxes()})
            elif path == "/api/v1/migrations":
                self.write_json({"migrations": self.app.db.list_migrations()})
            elif path == "/api/v1/messages":
                mailbox_id = int(query["mailbox_id"][0]) if query.get("mailbox_id") else None
                search = query.get("q", [None])[0]
                limit = int(query.get("limit", ["100"])[0])
                offset = int(query.get("offset", ["0"])[0])
                self.write_json(
                    {
                        "messages": self.app.db.list_messages(
                            mailbox_id=mailbox_id,
                            query=search,
                            limit=limit,
                            offset=offset,
                        )
                    }
                )
            elif path == "/api/v1/search":
                search = query.get("q", [""])[0]
                mailbox_id = int(query["mailbox_id"][0]) if query.get("mailbox_id") else None
                limit = int(query.get("limit", ["100"])[0])
                offset = int(query.get("offset", ["0"])[0])
                self.write_json(
                    {
                        "query": search,
                        "messages": self.app.db.list_messages(
                            mailbox_id=mailbox_id,
                            query=search,
                            limit=limit,
                            offset=offset,
                        ),
                    }
                )
            elif path.startswith("/api/v1/messages/") and path.endswith("/raw"):
                message_id = int(path.split("/")[-2])
                raw = self.app.db.get_raw_message(message_id)
                if raw is None:
                    self.write_error(HTTPStatus.NOT_FOUND, "Message raw content not found")
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "message/rfc822")
                self.send_header("Content-Length", str(len(raw)))
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(raw)
            elif path.startswith("/api/v1/messages/") and path.endswith("/html"):
                message_id = int(path.split("/")[-2])
                html = self.app.db.get_sanitized_message_html(message_id)
                if html is None:
                    self.write_error(HTTPStatus.NOT_FOUND, "Message HTML content not found")
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; base-uri 'none'; form-action 'none'",
                )
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(html)
            elif path.startswith("/api/v1/messages/"):
                message_id = int(path.rsplit("/", 1)[-1])
                message = self.app.db.get_message(message_id)
                if message is None:
                    self.write_error(HTTPStatus.NOT_FOUND, "Message not found")
                    return
                self.write_json({"message": message})
            elif path.startswith("/api/v1/attachments/"):
                attachment_id = int(path.rsplit("/", 1)[-1])
                attachment = self.app.db.get_attachment(attachment_id)
                if attachment is None:
                    self.write_error(HTTPStatus.NOT_FOUND, "Attachment not found")
                    return
                body = attachment.pop("content")
                filename = safe_download_filename(attachment.get("filename"), attachment_id)
                mime_type = str(attachment.get("mime_type") or "application/octet-stream")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mime_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Disposition", content_disposition(filename))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(body)
            elif path.startswith("/api/v1/import-jobs/") and path.endswith("/errors"):
                import_job_id = int(path.split("/")[-2])
                self.write_json({"errors": self.app.db.get_import_job_errors(import_job_id)})
            elif path == "/api/v1/import-jobs":
                self.write_json({"import_jobs": self.app.db.list_import_jobs()})
            elif path.startswith("/api/v1/export-jobs/") and path.endswith("/items"):
                export_job_id = int(path.split("/")[-2])
                self.write_json({"items": self.app.db.list_export_job_items(export_job_id)})
            elif path == "/api/v1/export-jobs":
                self.write_json({"export_jobs": self.app.db.list_export_jobs()})
            elif path == "/api/v1/export-profiles":
                self.write_json({"export_profiles": [profile.to_api() for profile in list_export_profiles()]})
            else:
                self.serve_static(path)
        except Exception as exc:  # noqa: BLE001
            self.write_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_cors_headers()
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            payload = self.read_json()
            if path == "/api/v1/auth/setup":
                token = self.app.auth.setup_admin(
                    str(payload.get("username") or ""),
                    str(payload.get("password") or ""),
                )
                self.write_json(
                    {"auth": self.app.auth.status(f"millie_session={token}").to_api()},
                    HTTPStatus.CREATED,
                    headers={"Set-Cookie": session_cookie(token)},
                )
            elif path == "/api/v1/auth/login":
                token = self.app.auth.login(
                    str(payload.get("username") or ""),
                    str(payload.get("password") or ""),
                )
                self.write_json(
                    {"auth": self.app.auth.status(f"millie_session={token}").to_api()},
                    headers={"Set-Cookie": session_cookie(token)},
                )
            elif path == "/api/v1/auth/logout":
                self.write_json(
                    {"auth": self.app.auth.status(None).to_api()},
                    headers={"Set-Cookie": expired_session_cookie()},
                )
            elif path.startswith("/api/v1/") and not self.app.is_authorized(self.headers.get("Cookie")):
                self.write_error(HTTPStatus.UNAUTHORIZED, "Authentication required")
            elif path == "/api/v1/profiles":
                name = str(payload.get("name") or "").strip()
                if not name:
                    self.write_error(HTTPStatus.BAD_REQUEST, "Profile name is required")
                    return
                profile = self.app.create_profile(name, bool(payload.get("switch", True)))
                self.write_json(
                    {
                        "active_profile_id": self.app.profile_manager.active_profile_id,
                        "profile": profile.to_api(),
                        "profiles": self.app.profile_manager.list_profiles(),
                    },
                    HTTPStatus.CREATED,
                )
            elif path == "/api/v1/profiles/active":
                profile_id = str(payload.get("profileId") or payload.get("profile_id") or "").strip()
                if not profile_id:
                    self.write_error(HTTPStatus.BAD_REQUEST, "profileId is required")
                    return
                profile = self.app.set_active_profile(profile_id)
                self.write_json(
                    {
                        "active_profile_id": self.app.profile_manager.active_profile_id,
                        "profile": profile.to_api(),
                        "profiles": self.app.profile_manager.list_profiles(),
                    }
                )
            elif path == "/api/v1/import":
                source_path = Path(str(payload.get("path", ""))).expanduser()
                result = import_path(
                    self.app.db,
                    source_path,
                    str(payload.get("format") or "auto"),
                    payload.get("sourceName") or payload.get("source_name"),
                    payload.get("mailboxPath") or payload.get("mailbox_path"),
                )
                self.write_json(
                    {
                        "import_job_id": result.import_job_id,
                        "source_id": result.source_id,
                        "imported": result.imported,
                        "processed": result.processed,
                        "duplicates": result.duplicates,
                        "errors": result.errors,
                        "format": result.format,
                    },
                    HTTPStatus.CREATED,
                )
            elif path == "/api/v1/imap-sources":
                source = save_imap_source(self.app.profile_manager, payload, self.app.secret_manager)
                self.write_json(
                    {
                        "source": source.to_api(),
                        "sources": [item.to_api() for item in load_imap_sources(self.app.profile_manager)],
                    },
                    HTTPStatus.CREATED,
                )
            elif path == "/api/v1/imap-sources/migrate-secrets":
                migrated = migrate_imap_source_secrets(self.app.profile_manager, self.app.secret_manager)
                self.write_json({"migrated": migrated})
            elif path.startswith("/api/v1/imap-sources/") and path.endswith("/folders"):
                source_id = unquote(path.split("/")[-2])
                source = get_imap_source(self.app.profile_manager, source_id, self.app.secret_manager)
                folders = discover_imap_folders(source)
                self.write_json({"folders": [folder.to_api() for folder in folders]})
            elif path.startswith("/api/v1/imap-sources/") and path.endswith("/delete"):
                source_id = unquote(path.split("/")[-2])
                deleted = delete_imap_source(self.app.profile_manager, source_id, self.app.secret_manager)
                if not deleted:
                    self.write_error(HTTPStatus.NOT_FOUND, "IMAP source not found")
                    return
                self.write_json(
                    {
                        "deleted": True,
                        "sources": [item.to_api() for item in load_imap_sources(self.app.profile_manager)],
                    }
                )
            elif path.startswith("/api/v1/imap-sources/") and path.endswith("/sync"):
                source_id = unquote(path.split("/")[-2])
                source = get_imap_source(self.app.profile_manager, source_id, self.app.secret_manager)
                folders = parse_string_list(payload.get("folders"))
                sync_limit = int(payload.get("sync_limit") or source.sync_limit)
                result = sync_imap_source(self.app.db, source, folders=folders, sync_limit=sync_limit)
                self.write_json({"sync": result.to_api()}, HTTPStatus.CREATED)
            elif path == "/api/v1/pop-sources":
                source = save_pop_source(self.app.profile_manager, payload, self.app.secret_manager)
                self.write_json(
                    {
                        "source": source.to_api(),
                        "sources": [item.to_api() for item in load_pop_sources(self.app.profile_manager)],
                    },
                    HTTPStatus.CREATED,
                )
            elif path == "/api/v1/pop-sources/migrate-secrets":
                migrated = migrate_pop_source_secrets(self.app.profile_manager, self.app.secret_manager)
                self.write_json({"migrated": migrated})
            elif path.startswith("/api/v1/pop-sources/") and path.endswith("/probe"):
                source_id = unquote(path.split("/")[-2])
                source = get_pop_source(self.app.profile_manager, source_id, self.app.secret_manager)
                probe = probe_pop_source(source)
                self.write_json({"probe": probe.to_api()})
            elif path.startswith("/api/v1/pop-sources/") and path.endswith("/delete"):
                source_id = unquote(path.split("/")[-2])
                deleted = delete_pop_source(self.app.profile_manager, source_id, self.app.secret_manager)
                if not deleted:
                    self.write_error(HTTPStatus.NOT_FOUND, "POP source not found")
                    return
                self.write_json(
                    {
                        "deleted": True,
                        "sources": [item.to_api() for item in load_pop_sources(self.app.profile_manager)],
                    }
                )
            elif path.startswith("/api/v1/pop-sources/") and path.endswith("/sync"):
                source_id = unquote(path.split("/")[-2])
                source = get_pop_source(self.app.profile_manager, source_id, self.app.secret_manager)
                sync_limit = int(payload.get("sync_limit") or source.sync_limit)
                result = sync_pop_source(self.app.db, source, sync_limit=sync_limit)
                self.write_json({"sync": result.to_api()}, HTTPStatus.CREATED)
            elif path == "/api/v1/graph-sources":
                source = save_graph_source(self.app.profile_manager, payload)
                self.write_json(
                    {
                        "source": source.to_api(),
                        "sources": [item.to_api() for item in load_graph_sources(self.app.profile_manager)],
                    },
                    HTTPStatus.CREATED,
                )
            elif path.startswith("/api/v1/graph-sources/") and path.endswith("/auth-url"):
                source_id = unquote(path.split("/")[-2])
                source = get_graph_source(self.app.profile_manager, source_id)
                redirect_uri = str(payload.get("redirect_uri") or payload.get("redirectUri") or "").strip()
                if not redirect_uri:
                    redirect_uri = self.local_graph_redirect_uri(source.redirect_uri)
                auth_request = create_graph_authorization_request(
                    self.app.profile_manager,
                    source_id,
                    self.app.secret_manager,
                    redirect_uri=redirect_uri,
                )
                self.write_json({"auth": auth_request.to_api()})
            elif path.startswith("/api/v1/graph-sources/") and path.endswith("/probe"):
                source_id = unquote(path.split("/")[-2])
                probe = probe_graph_source(
                    self.app.profile_manager,
                    source_id,
                    self.app.secret_manager,
                )
                self.write_json({"probe": probe.to_api()})
            elif path.startswith("/api/v1/graph-sources/") and path.endswith("/folders"):
                source_id = unquote(path.split("/")[-2])
                folders = discover_graph_folders(
                    self.app.profile_manager,
                    source_id,
                    self.app.secret_manager,
                )
                self.write_json({"folders": [folder.to_api() for folder in folders]})
            elif path.startswith("/api/v1/graph-sources/") and path.endswith("/sync"):
                source_id = unquote(path.split("/")[-2])
                sync_limit = int(payload.get("sync_limit") or payload.get("limit") or 0) or None
                result = sync_graph_source(
                    self.app.db,
                    self.app.profile_manager,
                    source_id,
                    self.app.secret_manager,
                    sync_limit=sync_limit,
                )
                self.write_json({"sync": result.to_api()}, HTTPStatus.CREATED)
            elif path.startswith("/api/v1/graph-sources/") and path.endswith("/delete"):
                source_id = unquote(path.split("/")[-2])
                deleted = delete_graph_source(self.app.profile_manager, source_id, self.app.secret_manager)
                if not deleted:
                    self.write_error(HTTPStatus.NOT_FOUND, "Microsoft Graph source not found")
                    return
                self.write_json(
                    {
                        "deleted": True,
                        "sources": [item.to_api() for item in load_graph_sources(self.app.profile_manager)],
                    }
                )
            elif path == "/api/v1/export":
                output_path = Path(str(payload.get("outputPath") or payload.get("output_path") or "exports"))
                message_ids = payload.get("messageIds") or payload.get("message_ids")
                result = export_messages(
                    self.app.db,
                    output_path,
                    str(payload.get("format") or "eml"),
                    target_profile=str(
                        payload.get("targetProfile")
                        or payload.get("target_profile")
                        or payload.get("profile")
                        or "generic-eml"
                    ),
                    mailbox_id=payload.get("mailboxId") or payload.get("mailbox_id"),
                    message_ids=message_ids,
                )
                self.write_json(
                    {
                        "export_job_id": result.export_job_id,
                        "exported": result.exported,
                        "errors": result.errors,
                        "warnings": result.warnings,
                        "manifest_path": str(result.manifest_path),
                    },
                    HTTPStatus.CREATED,
                )
            elif path == "/api/v1/backup":
                output_path = Path(
                    str(payload.get("outputPath") or payload.get("output_path") or ".private/local/backups")
                )
                result = create_backup(
                    self.app.profile_manager,
                    output_path,
                    include_secrets=bool(payload.get("includeSecrets") or payload.get("include_secrets")),
                )
                self.write_json({"backup": result.to_api()}, HTTPStatus.CREATED)
            else:
                self.write_error(HTTPStatus.NOT_FOUND, "Unknown API endpoint")
        except Exception as exc:  # noqa: BLE001
            self.write_error(HTTPStatus.BAD_REQUEST, str(exc))

    def handle_graph_oauth_callback(self, query: dict[str, list[str]]) -> None:
        error = (query.get("error") or [""])[0]
        if error:
            description = (query.get("error_description") or [error])[0]
            self.write_html(
                "Microsoft Graph Sign-In Failed",
                f"<p>{escape(description)}</p>",
                HTTPStatus.BAD_REQUEST,
            )
            return
        code = (query.get("code") or [""])[0]
        state = (query.get("state") or [""])[0]
        try:
            completion = complete_graph_authorization(
                self.app.profile_manager,
                code,
                state,
                self.app.secret_manager,
            )
        except Exception as exc:  # noqa: BLE001
            self.write_html(
                "Microsoft Graph Sign-In Failed",
                f"<p>{escape(str(exc))}</p>",
                HTTPStatus.BAD_REQUEST,
            )
            return
        self.write_html(
            "Microsoft Graph Connected",
            (
                f"<p>{escape(completion.source.name)} is connected to MILLIE.</p>"
                "<p>You can close this tab and return to the MILLIE app.</p>"
            ),
        )

    def write_html(
        self,
        title: str,
        content: str,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(title)}</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f5f7f8;
      color: #1f2a30;
    }}
    main {{
      width: min(520px, calc(100vw - 32px));
      background: #fff;
      border: 1px solid #d7e0e5;
      border-radius: 8px;
      padding: 24px;
      box-shadow: 0 12px 40px rgba(31, 42, 48, 0.12);
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 1.4rem;
    }}
    p {{
      line-height: 1.5;
    }}
    a {{
      color: #1c5d72;
    }}
  </style>
</head>
<body>
  <main>
    <h1>{escape(title)}</h1>
    {content}
    <p><a href="/">Return to MILLIE</a></p>
  </main>
</body>
</html>""".encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def local_graph_redirect_uri(self, registered_redirect_uri: str) -> str:
        parsed = urlparse(registered_redirect_uri)
        if parsed.scheme != "http" or (parsed.hostname or "").lower() not in {"localhost", "127.0.0.1", "::1"}:
            return registered_redirect_uri
        host_header = self.headers.get("Host") or f"localhost:{self.app.config.port}"
        port = self.app.config.port
        if ":" in host_header and not host_header.startswith("["):
            maybe_port = host_header.rsplit(":", 1)[-1]
            if maybe_port.isdigit():
                port = int(maybe_port)
        path = parsed.path or ""
        return f"http://localhost:{port}{path}"

    def read_json(self) -> dict[str, object]:
        length = int(self.headers.get("content-length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def write_json(
        self,
        payload: dict[str, object],
        status: HTTPStatus = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def write_error(self, status: HTTPStatus, message: str) -> None:
        self.write_json({"error": message, "status": status.value}, status)

    def send_cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        self.send_header("Access-Control-Allow-Origin", origin or "*")
        if origin:
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def serve_static(self, request_path: str) -> None:
        web_dir = self.app.config.web_dir
        path = request_path.lstrip("/") or "index.html"
        candidate = (web_dir / path).resolve()
        if not str(candidate).startswith(str(web_dir)) or not candidate.exists() or candidate.is_dir():
            candidate = web_dir / "index.html"
        if not candidate.exists():
            self.write_json(
                {
                    "message": "MILLIE API is running. Build the web app with `npm run build` to serve the UI.",
                    "health": "/api/v1/health",
                }
            )
            return
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)


class MillieHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        config: AppConfig,
        profile_manager: ProfileManager,
        secret_backend: str | None = None,
    ):
        super().__init__(server_address, MillieRequestHandler)
        self.config = config
        self.profile_manager = profile_manager
        self.auth = AuthManager(profile_manager)
        self.secret_manager = SecretManager(profile_manager, secret_backend)
        self.db = profile_manager.active_database()

    def is_authorized(self, cookie_header: str | None) -> bool:
        return self.auth.status(cookie_header).authenticated

    def set_active_profile(self, profile_id: str):
        profile = self.profile_manager.set_active(profile_id)
        self.db = self.profile_manager.active_database()
        return profile

    def create_profile(self, name: str, switch: bool = True):
        profile = self.profile_manager.create_profile(name, switch=switch)
        if switch:
            self.db = self.profile_manager.active_database()
        return profile


def run_server(config: AppConfig, secret_backend: str | None = None) -> None:
    resolved = config.resolved()
    profile_manager = ProfileManager(
        resolved.settings_path,
        resolved.profiles_dir,
        resolved.db_path,
        resolved.data_dir,
    )
    server = MillieHTTPServer((resolved.host, resolved.port), resolved, profile_manager, secret_backend)
    scheme = "http"
    if resolved.tls_cert or resolved.tls_key:
        if not resolved.tls_cert or not resolved.tls_key:
            raise ValueError("Both tls_cert and tls_key are required to enable HTTPS")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(resolved.tls_cert), str(resolved.tls_key))
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    print(f"MILLIE listening on {scheme}://{resolved.host}:{resolved.port}")
    print(f"Settings: {server.profile_manager.settings_path}")
    print(f"Profile: {server.profile_manager.active_profile().name}")
    print(f"Database: {server.db.db_path}")
    print(f"Data dir: {server.db.data_dir}")
    server.serve_forever()


def safe_download_filename(value: object, attachment_id: int) -> str:
    raw = str(value or f"attachment-{attachment_id}")
    name = Path(raw.replace("\\", "/")).name
    cleaned = re.sub(r"[\r\n\"]+", "_", name).strip(" .")
    return cleaned or f"attachment-{attachment_id}"


def content_disposition(filename: str) -> str:
    ascii_name = filename.encode("ascii", errors="ignore").decode("ascii") or "attachment"
    quoted_name = ascii_name.replace("\\", "_").replace('"', "_")
    return f'attachment; filename="{quoted_name}"; filename*=UTF-8\'\'{quote(filename)}'


def parse_string_list(value: object) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value]
    else:
        return None
    return [item for item in items if item]


def is_graph_oauth_callback(path: str, query: dict[str, list[str]]) -> bool:
    if path not in {"/", "/api/v1/graph/oauth/callback"}:
        return False
    return bool(query.get("state")) and bool(query.get("code") or query.get("error"))
