from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from millie import __version__
from millie.config import AppConfig
from millie.exporters import export_messages
from millie.importers import import_path
from millie.profiles import ProfileManager


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
            if path == "/api/v1/health":
                self.write_json(
                    {
                        "ok": True,
                        "version": __version__,
                        "profile": self.app.profile_manager.active_profile().to_api(),
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
            elif path == "/api/v1/sources":
                self.write_json({"sources": self.app.db.list_sources()})
            elif path == "/api/v1/mailboxes":
                self.write_json({"mailboxes": self.app.db.list_mailboxes()})
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
            elif path.startswith("/api/v1/messages/"):
                message_id = int(path.rsplit("/", 1)[-1])
                message = self.app.db.get_message(message_id)
                if message is None:
                    self.write_error(HTTPStatus.NOT_FOUND, "Message not found")
                    return
                self.write_json({"message": message})
            elif path == "/api/v1/export-jobs":
                self.write_json({"export_jobs": self.app.db.list_export_jobs()})
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
            if path == "/api/v1/profiles":
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
                )
                self.write_json(
                    {
                        "import_job_id": result.import_job_id,
                        "source_id": result.source_id,
                        "imported": result.imported,
                        "errors": result.errors,
                        "format": result.format,
                    },
                    HTTPStatus.CREATED,
                )
            elif path == "/api/v1/export":
                output_path = Path(str(payload.get("outputPath") or payload.get("output_path") or "exports"))
                message_ids = payload.get("messageIds") or payload.get("message_ids")
                result = export_messages(
                    self.app.db,
                    output_path,
                    str(payload.get("format") or "eml"),
                    target_profile=str(payload.get("profile") or "generic"),
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
            else:
                self.write_error(HTTPStatus.NOT_FOUND, "Unknown API endpoint")
        except Exception as exc:  # noqa: BLE001
            self.write_error(HTTPStatus.BAD_REQUEST, str(exc))

    def read_json(self) -> dict[str, object]:
        length = int(self.headers.get("content-length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def write_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def write_error(self, status: HTTPStatus, message: str) -> None:
        self.write_json({"error": message, "status": status.value}, status)

    def send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
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
    def __init__(self, server_address: tuple[str, int], config: AppConfig, profile_manager: ProfileManager):
        super().__init__(server_address, MillieRequestHandler)
        self.config = config
        self.profile_manager = profile_manager
        self.db = profile_manager.active_database()

    def set_active_profile(self, profile_id: str):
        profile = self.profile_manager.set_active(profile_id)
        self.db = self.profile_manager.active_database()
        return profile

    def create_profile(self, name: str, switch: bool = True):
        profile = self.profile_manager.create_profile(name, switch=switch)
        if switch:
            self.db = self.profile_manager.active_database()
        return profile


def run_server(config: AppConfig) -> None:
    resolved = config.resolved()
    profile_manager = ProfileManager(
        resolved.settings_path,
        resolved.profiles_dir,
        resolved.db_path,
        resolved.data_dir,
    )
    server = MillieHTTPServer((resolved.host, resolved.port), resolved, profile_manager)
    print(f"MILLIE listening on http://{resolved.host}:{resolved.port}")
    print(f"Settings: {server.profile_manager.settings_path}")
    print(f"Profile: {server.profile_manager.active_profile().name}")
    print(f"Database: {server.db.db_path}")
    print(f"Data dir: {server.db.data_dir}")
    server.serve_forever()
