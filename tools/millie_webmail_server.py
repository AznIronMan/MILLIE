#!/usr/bin/env python3
"""No-auth development webmail view for the current MILLIE mailbox."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
from datetime import date, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from http.cookies import SimpleCookie
from html.parser import HTMLParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from millie.brain.automation import automation_level, automation_level_allows
from millie.brain.llm_taxonomy import LLMProviderError, run_taxonomy_assistant
from millie.settings_loader import load_local_settings
from millie.service.auth import default_service_login
from millie.storage.postgres_store import PostgresMailStore
from millie.sync.live_mail import LiveSyncConfig, start_live_sync_thread


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 22001
DEFAULT_PID_FILE = PROJECT_ROOT / ".private" / "local" / "millie_webmail_server.pid"
DEFAULT_LOG_FILE = PROJECT_ROOT / ".private" / "local" / "millie_webmail_server.log"
AUTODISCOVER_PATHS = {
    "/autodiscover/autodiscover.xml",
    "/autodiscover/autodiscovery.xml",
}
AUTOCONFIG_PATHS = {
    "/mail/config-v1.1.xml",
    "/autoconfig/mail/config-v1.1.xml",
    "/.well-known/autoconfig/mail/config-v1.1.xml",
}
MESSAGE_LIMIT_OPTIONS = {"25", "50", "100", "250", "500", "all"}
DEFAULT_MESSAGE_LIMIT = "50"
SESSION_COOKIE_NAME = "millie_session"
PUBLIC_GET_PATHS = {"/login", "/favicon.ico", *AUTODISCOVER_PATHS, *AUTOCONFIG_PATHS}
PUBLIC_POST_PATHS = {"/api/login", *AUTODISCOVER_PATHS}


class MillieWebmailHandler(BaseHTTPRequestHandler):
    server: "MillieWebmailServer"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if self.auth_required(parsed, "GET") and not self.session_context():
            if parsed.path.startswith("/api/"):
                self.send_json({"error": "Authentication required"}, status=HTTPStatus.UNAUTHORIZED)
            else:
                self.send_html(LOGIN_HTML)
            return
        if parsed.path == "/":
            self.send_html(INDEX_HTML)
            return
        if parsed.path == "/login":
            if self.server.auth_required and self.session_context():
                self.send_redirect("/")
            else:
                self.send_html(LOGIN_HTML)
            return
        if parsed.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        if parsed.path == "/api/session":
            self.send_json(self.session_payload())
            return
        if parsed.path == "/api/bootstrap":
            self.send_json(self.bootstrap_payload(parsed))
            return
        if parsed.path == "/api/messages":
            self.send_json(self.message_payload(parsed))
            return
        if parsed.path == "/api/review":
            self.send_json(self.review_payload(parsed))
            return
        if parsed.path == "/api/review/workbench":
            self.send_json(self.review_workbench_payload(parsed))
            return
        if parsed.path == "/api/unsubscribe":
            self.send_json(self.unsubscribe_payload(parsed))
            return
        if parsed.path == "/api/retention/policies":
            self.send_json(self.retention_policies_payload(parsed))
            return
        if parsed.path == "/api/search":
            self.send_json(self.search_payload(parsed))
            return
        if parsed.path == "/api/rules":
            self.send_json(self.rules_payload(parsed))
            return
        if parsed.path == "/api/learning/metrics":
            self.send_json(self.learning_metrics_payload(parsed))
            return
        if parsed.path == "/api/rules/candidates":
            self.send_json(self.rule_candidates_payload(parsed))
            return
        if parsed.path == "/api/taxonomy/proposals":
            self.send_json(self.taxonomy_proposals_payload(parsed))
            return
        if parsed.path == "/api/proposals":
            self.send_json(self.proposal_review_payload(parsed))
            return
        if parsed.path == "/api/internal-apply":
            self.send_json(self.internal_apply_payload(parsed))
            return
        if parsed.path == "/api/operations":
            self.send_json(self.operations_payload(parsed))
            return
        if parsed.path in AUTODISCOVER_PATHS:
            self.send_xml(autodiscover_xml(self.server.settings, self.server.mailbox_address))
            return
        if parsed.path in AUTOCONFIG_PATHS:
            self.send_xml(autoconfig_xml(self.server.settings, self.server.mailbox_address))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/login":
            self.send_login_response()
            return
        if self.auth_required(parsed, "POST") and not self.session_context():
            self.send_json({"error": "Authentication required"}, status=HTTPStatus.UNAUTHORIZED)
            return
        if parsed.path in AUTODISCOVER_PATHS:
            body = self.read_request_body()
            requested_email = autodiscover_request_email(body) or self.server.mailbox_address
            self.send_xml(autodiscover_xml(self.server.settings, requested_email))
            return
        if parsed.path == "/api/logout":
            self.send_logout_response()
            return
        if parsed.path == "/api/classifications/action":
            self.send_json(self.classification_action_payload())
            return
        if parsed.path == "/api/review/workbench/action":
            self.send_json(self.review_workbench_action_payload())
            return
        if parsed.path == "/api/unsubscribe/action":
            self.send_json(self.unsubscribe_action_payload())
            return
        if parsed.path == "/api/retention/policies/action":
            self.send_json(self.retention_policy_action_payload())
            return
        if parsed.path == "/api/rules/action":
            self.send_json(self.rule_action_payload())
            return
        if parsed.path == "/api/rules/candidates/action":
            self.send_json(self.rule_candidate_action_payload())
            return
        if parsed.path == "/api/taxonomy/proposals/action":
            self.send_json(self.taxonomy_proposal_action_payload())
            return
        if parsed.path == "/api/taxonomy/assistant":
            self.send_json(self.taxonomy_assistant_payload())
            return
        if parsed.path == "/api/proposals/action":
            self.send_json(self.proposal_action_payload())
            return
        if parsed.path == "/api/internal-apply/action":
            self.send_json(self.internal_apply_action_payload())
            return
        if parsed.path == "/api/operations/action":
            self.send_json(self.operations_action_payload())
            return
        if parsed.path == "/api/retention/action":
            self.send_json(self.retention_action_payload())
            return
        self.send_error(HTTPStatus.METHOD_NOT_ALLOWED, "Method not allowed")

    def log_message(self, format: str, *args: object) -> None:
        print(
            f"WEBMAIL client={self.client_address[0]}:{self.client_address[1]} "
            f"request={self.requestline!r} status={args[1] if len(args) > 1 else '-'}",
            flush=True,
        )

    def auth_required(self, parsed: urllib.parse.ParseResult, method: str) -> bool:
        if not self.server.auth_required:
            return False
        public = PUBLIC_GET_PATHS if method == "GET" else PUBLIC_POST_PATHS
        return parsed.path not in public

    def session_context(self) -> dict[str, object] | None:
        if not self.server.auth_required:
            return None
        cached = getattr(self, "_session_context", None)
        if cached is not None:
            return cached
        token = self.session_cookie()
        if not token:
            self._session_context = None
            return None
        with self.store() as store:
            context = store.web_session(token)
            store.connection.commit()
        self._session_context = context
        return context

    def session_cookie(self) -> str:
        raw = self.headers.get("Cookie") or ""
        if not raw:
            return ""
        cookie = SimpleCookie(raw)
        morsel = cookie.get(SESSION_COOKIE_NAME)
        return morsel.value if morsel else ""

    def request_mailbox(self, store: PostgresMailStore) -> dict[str, object]:
        context = self.session_context()
        if self.server.auth_required and context:
            mailbox = context.get("mailbox")
            if isinstance(mailbox, dict) and mailbox.get("id"):
                return mailbox
        mailbox = store.mailbox_by_address(self.server.mailbox_address)
        if mailbox is None:
            raise NotFoundError(f"Mailbox not found: {self.server.mailbox_address}")
        return mailbox

    def session_payload(self) -> dict[str, object]:
        context = self.session_context()
        if not self.server.auth_required:
            return {
                "auth_required": False,
                "authenticated": True,
                "mailbox_address": self.server.mailbox_address,
            }
        return {
            "auth_required": True,
            "authenticated": bool(context),
            "identity": {
                "id": context.get("identity_id"),
                "login_address": context.get("login_address"),
                "display_name": context.get("display_name"),
            }
            if context
            else None,
            "mailbox": context.get("mailbox") if context else None,
            "expires_at": context.get("expires_at") if context else None,
        }

    def send_login_response(self) -> None:
        payload = self.read_json_body()
        login = str(payload.get("login") or "").strip()
        password = str(payload.get("password") or "")
        if not login or not password:
            raise BadRequestError("login and password are required")
        with self.store() as store:
            session = store.create_web_session(
                login=login,
                password=password,
                remote_address=self.client_address[0],
                user_agent=self.headers.get("User-Agent", ""),
            )
            if session is None:
                store.connection.rollback()
                self.send_json({"error": "Invalid login"}, status=HTTPStatus.UNAUTHORIZED)
                return
            store.connection.commit()
        self.send_json(
            {
                "ok": True,
                "mailbox": session["mailbox"],
                "expires_at": session["expires_at"],
            },
            headers=[
                (
                    "Set-Cookie",
                    (
                        f"{SESSION_COOKIE_NAME}={session['token']}; Path=/; "
                        "HttpOnly; SameSite=Lax; Max-Age=43200"
                    ),
                )
            ],
        )

    def send_logout_response(self) -> None:
        token = self.session_cookie()
        if token:
            with self.store() as store:
                store.revoke_web_session(token)
                store.connection.commit()
        self.send_json(
            {"ok": True},
            headers=[
                (
                    "Set-Cookie",
                    f"{SESSION_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0",
                )
            ],
        )

    def bootstrap_payload(self, parsed: urllib.parse.ParseResult) -> dict[str, object]:
        query = urllib.parse.parse_qs(parsed.query)
        folder = query.get("folder", ["INBOX"])[0]
        requested_limit = parse_message_limit(query.get("limit", [DEFAULT_MESSAGE_LIMIT])[0])
        with self.store() as store:
            mailbox = self.request_mailbox(store)
            folders = store.list_folders(str(mailbox["id"]))
            folder_counts = store.webmail_folder_counts(mailbox_id=str(mailbox["id"]))
            selected_folder = folder if folder in folder_counts else "INBOX"
            messages = store.list_webmail_messages(
                mailbox_id=str(mailbox["id"]),
                folder_path=selected_folder,
                limit=None if requested_limit == "all" else int(requested_limit),
            )
        return {
            "mailbox": mailbox,
            "folders": [decorate_folder(folder, folder_counts) for folder in folders],
            "selected_folder": selected_folder,
            "message_limit": requested_limit,
            "folder_count": folder_counts.get(selected_folder, 0),
            "messages": messages,
        }

    def message_payload(self, parsed: urllib.parse.ParseResult) -> dict[str, object]:
        query = urllib.parse.parse_qs(parsed.query)
        folder = query.get("folder", ["INBOX"])[0]
        uid_text = query.get("uid", [""])[0]
        if not uid_text.isdigit():
            raise BadRequestError("uid is required")
        with self.store() as store:
            mailbox = self.request_mailbox(store)
            detail = store.get_webmail_message_by_uid(
                mailbox_id=str(mailbox["id"]),
                folder_path=folder,
                uid=int(uid_text),
            )
        if detail is None:
            raise NotFoundError("Message not found")
        body = display_body(detail)
        return {
            "uid": detail["uid"],
            "folder": folder,
            "message_id": detail["message_id"],
            "internet_message_id": detail["internet_message_id"],
            "subject": detail["subject"] or "(no subject)",
            "message_date": detail["message_date"],
            "body": body,
            "body_preview": detail["body_preview"],
            "addresses": group_addresses(detail["addresses"]),
            "attachments": detail["attachments"],
            "has_attachments": detail["has_attachments"],
            "size": detail["size"],
            "classifications": detail["classifications"],
            "unsubscribe_candidates": detail["unsubscribe_candidates"],
            "retention_status": detail["retention_status"],
        }

    def review_payload(self, parsed: urllib.parse.ParseResult) -> dict[str, object]:
        query = urllib.parse.parse_qs(parsed.query)
        limit_text = query.get("limit", ["50"])[0]
        try:
            limit = min(max(int(limit_text), 1), 250)
        except ValueError:
            limit = 50
        with self.store() as store:
            mailbox = self.request_mailbox(store)
            suggestions = store.list_review_suggestions(limit=limit)
            retention = store.list_retention_review_items(
                mailbox_id=str(mailbox["id"]),
                limit=limit,
            )
        return {"suggestions": suggestions, "retention": retention, "limit": limit}

    def review_workbench_payload(self, parsed: urllib.parse.ParseResult) -> dict[str, object]:
        query = urllib.parse.parse_qs(parsed.query)
        group_limit = parse_int_limit(query.get("group_limit", ["25"])[0], default=25, maximum=100)
        sample_limit = parse_int_limit(query.get("sample_limit", ["5"])[0], default=5, maximum=12)
        candidate_limit = parse_int_limit(
            query.get("candidate_limit", ["1000"])[0],
            default=1000,
            maximum=5000,
        )
        with self.store() as store:
            self.request_mailbox(store)
            groups = store.review_workbench_groups(
                group_limit=group_limit,
                sample_limit=sample_limit,
                candidate_limit=candidate_limit,
            )
        return {
            "groups": groups,
            "group_limit": group_limit,
            "sample_limit": sample_limit,
            "candidate_limit": candidate_limit,
        }

    def unsubscribe_payload(self, parsed: urllib.parse.ParseResult) -> dict[str, object]:
        query = urllib.parse.parse_qs(parsed.query)
        limit_text = query.get("limit", ["100"])[0]
        try:
            limit = min(max(int(limit_text), 1), 250)
        except ValueError:
            limit = 100
        statuses = [
            status
            for value in query.get("status", [])
            for status in value.split(",")
            if status
        ]
        with self.store() as store:
            candidates = store.list_unsubscribe_review_items(
                limit=limit,
                statuses=statuses or None,
            )
        return {"candidates": candidates, "limit": limit, "statuses": statuses}

    def retention_policies_payload(self, parsed: urllib.parse.ParseResult) -> dict[str, object]:
        query = urllib.parse.parse_qs(parsed.query)
        statuses = [
            status
            for value in query.get("status", [])
            for status in value.split(",")
            if status
        ]
        with self.store() as store:
            policies = store.list_retention_policies(statuses=statuses or None)
        return {"policies": policies, "statuses": statuses}

    def search_payload(self, parsed: urllib.parse.ParseResult) -> dict[str, object]:
        query = urllib.parse.parse_qs(parsed.query)
        q = query.get("q", [""])[0].strip()
        folder = query.get("folder", [""])[0].strip()
        source_type = query.get("source_type", [""])[0].strip()
        source = query.get("source", [""])[0].strip()
        sender = query.get("from", [""])[0].strip()
        since = query.get("since", [""])[0].strip()
        until = query.get("until", [""])[0].strip()
        has_attachments = parse_bool_filter(query.get("has_attachments", [""])[0])
        limit = parse_int_limit(query.get("limit", ["100"])[0], default=100, maximum=500)
        if not any([q, folder, source_type, source, sender, since, until, has_attachments is not None]):
            raise BadRequestError("Search text or at least one filter is required")
        with self.store() as store:
            mailbox = self.request_mailbox(store)
            results = store.search_webmail_messages(
                mailbox_id=str(mailbox["id"]),
                query=q,
                folder_path=folder,
                source_type=source_type,
                source=source,
                sender=sender,
                since=since,
                until=until,
                has_attachments=has_attachments,
                limit=limit,
            )
        return {"results": results, "limit": limit}

    def rules_payload(self, parsed: urllib.parse.ParseResult) -> dict[str, object]:
        query = urllib.parse.parse_qs(parsed.query)
        statuses = [
            status
            for value in query.get("status", [])
            for status in value.split(",")
            if status
        ]
        limit = parse_int_limit(query.get("limit", ["100"])[0], default=100, maximum=500)
        with self.store() as store:
            rules = store.list_brain_rules(statuses=statuses or None, limit=limit)
        return {"rules": rules, "limit": limit, "statuses": statuses}

    def learning_metrics_payload(self, parsed: urllib.parse.ParseResult) -> dict[str, object]:
        query = urllib.parse.parse_qs(parsed.query)
        limit = parse_int_limit(query.get("limit", ["12"])[0], default=12, maximum=50)
        with self.store() as store:
            self.request_mailbox(store)
            metrics = store.learning_metrics(limit=limit)
        return metrics

    def rule_candidates_payload(self, parsed: urllib.parse.ParseResult) -> dict[str, object]:
        query = urllib.parse.parse_qs(parsed.query)
        limit = parse_int_limit(query.get("limit", ["12"])[0], default=12, maximum=50)
        sample_limit = parse_int_limit(query.get("sample_limit", ["4"])[0], default=4, maximum=12)
        min_messages = parse_int_limit(query.get("min_messages", ["1"])[0], default=1, maximum=25)
        with self.store() as store:
            self.request_mailbox(store)
            candidates = store.rule_backtest_candidates(
                limit=limit,
                sample_limit=sample_limit,
                min_messages=min_messages,
            )
        return {
            "candidates": candidates,
            "limit": limit,
            "sample_limit": sample_limit,
            "min_messages": min_messages,
        }

    def taxonomy_proposals_payload(self, parsed: urllib.parse.ParseResult) -> dict[str, object]:
        query = urllib.parse.parse_qs(parsed.query)
        limit = parse_int_limit(query.get("limit", ["12"])[0], default=12, maximum=50)
        sample_limit = parse_int_limit(query.get("sample_limit", ["4"])[0], default=4, maximum=12)
        with self.store() as store:
            self.request_mailbox(store)
            proposals = store.taxonomy_proposals(limit=limit, sample_limit=sample_limit)
        return {
            "proposals": proposals,
            "limit": limit,
            "sample_limit": sample_limit,
        }

    def proposal_review_payload(self, parsed: urllib.parse.ParseResult) -> dict[str, object]:
        query = urllib.parse.parse_qs(parsed.query)
        statuses = [
            status
            for value in query.get("status", [])
            for status in value.split(",")
            if status and status.strip().lower() != "all"
        ]
        limit = parse_int_limit(query.get("limit", ["100"])[0], default=100, maximum=500)
        with self.store() as store:
            self.request_mailbox(store)
            review = store.proposal_review(statuses=statuses or None, limit=limit)
        return {
            **review,
            "statuses": statuses,
        }

    def internal_apply_payload(self, parsed: urllib.parse.ParseResult) -> dict[str, object]:
        query = urllib.parse.parse_qs(parsed.query)
        limit = parse_int_limit(query.get("limit", ["100"])[0], default=100, maximum=500)
        with self.store() as store:
            mailbox = self.request_mailbox(store)
            status = store.internal_apply_status(mailbox_id=str(mailbox["id"]), limit=limit)
        return {
            **status,
            "automation_level": automation_level(self.server.settings),
            "auto_internal_allowed": automation_level_allows(self.server.settings, "auto_internal"),
        }

    def operations_payload(self, parsed: urllib.parse.ParseResult) -> dict[str, object]:
        query = urllib.parse.parse_qs(parsed.query)
        run_limit = parse_int_limit(query.get("run_limit", ["10"])[0], default=10, maximum=50)
        with self.store() as store:
            mailbox = self.request_mailbox(store)
            status = store.operations_status(
                mailbox_id=str(mailbox["id"]),
                accounts=self.server.accounts,
                run_limit=run_limit,
            )
            store.connection.commit()
        return {
            **status,
            "automation_level": automation_level(self.server.settings),
            "auto_internal_allowed": automation_level_allows(self.server.settings, "auto_internal"),
        }

    def classification_action_payload(self) -> dict[str, object]:
        payload = self.read_json_body()
        classification_id = str(payload.get("classification_id") or "")
        action = str(payload.get("action") or "")
        if not classification_id or action not in {"approve", "reject", "always", "never"}:
            raise BadRequestError("classification_id and valid action are required")
        with self.store() as store:
            mailbox = self.request_mailbox(store)
            identity_id = str(mailbox["owner_identity_id"]) if mailbox else None
            try:
                result = store.record_classification_feedback(
                    classification_id=classification_id,
                    action=action,
                    identity_id=identity_id,
                )
            except KeyError as exc:
                raise NotFoundError(str(exc)) from exc
            store.connection.commit()
        return {"ok": True, "classification": result}

    def review_workbench_action_payload(self) -> dict[str, object]:
        payload = self.read_json_body()
        classification_ids = payload.get("classification_ids") or []
        action = str(payload.get("action") or "").strip().lower()
        if action not in {"approve", "reject", "always", "never"}:
            raise BadRequestError("valid action is required")
        if not isinstance(classification_ids, list):
            raise BadRequestError("classification_ids must be a list")
        with self.store() as store:
            mailbox = self.request_mailbox(store)
            identity_id = str(mailbox["owner_identity_id"]) if mailbox else None
            try:
                result = store.record_classification_batch_feedback(
                    classification_ids=[str(item) for item in classification_ids],
                    action=action,
                    identity_id=identity_id,
                    feedback_source="webmail_workbench",
                )
            except KeyError as exc:
                raise NotFoundError(str(exc)) from exc
            except ValueError as exc:
                raise BadRequestError(str(exc)) from exc
            store.connection.commit()
        return {"ok": True, "batch": result}

    def unsubscribe_action_payload(self) -> dict[str, object]:
        payload = self.read_json_body()
        candidate_id = str(payload.get("candidate_id") or "")
        action = str(payload.get("action") or "")
        if not candidate_id or action not in {"approve", "reject"}:
            raise BadRequestError("candidate_id and valid action are required")
        with self.store() as store:
            mailbox = self.request_mailbox(store)
            identity_id = str(mailbox["owner_identity_id"]) if mailbox else None
            try:
                result = store.record_unsubscribe_feedback(
                    candidate_id=candidate_id,
                    action=action,
                    identity_id=identity_id,
                )
            except KeyError as exc:
                raise NotFoundError(str(exc)) from exc
            store.connection.commit()
        return {"ok": True, "unsubscribe_candidate": result}

    def retention_action_payload(self) -> dict[str, object]:
        payload = self.read_json_body()
        policy_id = str(payload.get("policy_id") or "")
        mailbox_message_id = str(payload.get("mailbox_message_id") or "")
        action = str(payload.get("action") or "")
        if not policy_id or not mailbox_message_id or action not in {"acknowledge", "defer"}:
            raise BadRequestError("policy_id, mailbox_message_id, and valid action are required")
        with self.store() as store:
            mailbox = self.request_mailbox(store)
            identity_id = str(mailbox["owner_identity_id"])
            try:
                result = store.record_retention_feedback(
                    mailbox_id=str(mailbox["id"]),
                    policy_id=policy_id,
                    mailbox_message_id=mailbox_message_id,
                    action=action,
                    identity_id=identity_id,
                )
            except KeyError as exc:
                raise NotFoundError(str(exc)) from exc
            except ValueError as exc:
                raise BadRequestError(str(exc)) from exc
            store.connection.commit()
        return {"ok": True, "retention": result}

    def retention_policy_action_payload(self) -> dict[str, object]:
        payload = self.read_json_body()
        policy_id = str(payload.get("policy_id") or "")
        action = str(payload.get("action") or "").strip().lower()
        if not policy_id or action not in {"activate", "disable", "update"}:
            raise BadRequestError("policy_id and valid action are required")
        updates: dict[str, object] = {}
        if action == "update":
            if "policy_name" in payload:
                updates["policy_name"] = str(payload.get("policy_name") or "")
            if "status" in payload:
                updates["status"] = str(payload.get("status") or "")
            if "policy_action" in payload:
                updates["policy_action"] = str(payload.get("policy_action") or "")
            if "requires_review" in payload:
                updates["requires_review"] = bool(payload.get("requires_review"))
            if "hold_duration_seconds" in payload:
                try:
                    updates["hold_duration_seconds"] = int(str(payload.get("hold_duration_seconds") or "0"))
                except ValueError as exc:
                    raise BadRequestError("hold_duration_seconds must be a number") from exc
        with self.store() as store:
            mailbox = self.request_mailbox(store)
            identity_id = str(mailbox["owner_identity_id"]) if mailbox else None
            try:
                policy = store.record_retention_policy_action(
                    policy_id=policy_id,
                    action=action,
                    updates=updates,
                    identity_id=identity_id,
                )
            except KeyError as exc:
                raise NotFoundError(str(exc)) from exc
            except ValueError as exc:
                raise BadRequestError(str(exc)) from exc
            store.connection.commit()
        return {"ok": True, "policy": policy}

    def rule_action_payload(self) -> dict[str, object]:
        payload = self.read_json_body()
        rule_id = str(payload.get("rule_id") or "")
        action = str(payload.get("action") or "").strip().lower()
        if not rule_id or action not in {"activate", "disable", "retire", "update"}:
            raise BadRequestError("rule_id and valid action are required")
        updates: dict[str, object] = {}
        if action == "update":
            if "rule_name" in payload:
                updates["rule_name"] = str(payload.get("rule_name") or "")
            if "status" in payload:
                updates["status"] = str(payload.get("status") or "")
            if "priority" in payload:
                try:
                    updates["priority"] = int(str(payload.get("priority") or "0"))
                except ValueError as exc:
                    raise BadRequestError("priority must be a number") from exc
        with self.store() as store:
            mailbox = self.request_mailbox(store)
            identity_id = str(mailbox["owner_identity_id"]) if mailbox else None
            try:
                rule = store.record_brain_rule_action(
                    rule_id=rule_id,
                    action=action,
                    updates=updates,
                    identity_id=identity_id,
                )
            except KeyError as exc:
                raise NotFoundError(str(exc)) from exc
            except ValueError as exc:
                raise BadRequestError(str(exc)) from exc
            store.connection.commit()
        return {"ok": True, "rule": rule}

    def rule_candidate_action_payload(self) -> dict[str, object]:
        payload = self.read_json_body()
        candidate_id = str(payload.get("candidate_id") or "")
        action = str(payload.get("action") or "").strip().lower()
        if not candidate_id or action not in {"seed", "dismiss"}:
            raise BadRequestError("candidate_id and valid action are required")
        with self.store() as store:
            mailbox = self.request_mailbox(store)
            identity_id = str(mailbox["owner_identity_id"]) if mailbox else None
            try:
                result = store.record_rule_candidate_action(
                    candidate_id=candidate_id,
                    action=action,
                    identity_id=identity_id,
                )
            except KeyError as exc:
                raise NotFoundError(str(exc)) from exc
            except ValueError as exc:
                raise BadRequestError(str(exc)) from exc
            store.connection.commit()
        return result

    def taxonomy_proposal_action_payload(self) -> dict[str, object]:
        payload = self.read_json_body()
        proposal_id = str(payload.get("proposal_id") or "")
        action = str(payload.get("action") or "").strip().lower()
        if not proposal_id or action not in {"seed", "dismiss"}:
            raise BadRequestError("proposal_id and valid action are required")
        with self.store() as store:
            mailbox = self.request_mailbox(store)
            identity_id = str(mailbox["owner_identity_id"]) if mailbox else None
            try:
                result = store.record_taxonomy_proposal_action(
                    proposal_id=proposal_id,
                    action=action,
                    identity_id=identity_id,
                )
            except KeyError as exc:
                raise NotFoundError(str(exc)) from exc
            except ValueError as exc:
                raise BadRequestError(str(exc)) from exc
            store.connection.commit()
        return result

    def taxonomy_assistant_payload(self) -> dict[str, object]:
        payload = self.read_json_body()
        tier = str(payload.get("tier") or "main").strip().lower()
        limit = parse_int_limit(str(payload.get("limit") or "12"), default=12, maximum=50)
        if tier not in {"main", "second", "third"}:
            raise BadRequestError("tier must be main, second, or third")
        with self.store() as store:
            self.request_mailbox(store)
            proposals = store.taxonomy_proposals(limit=limit, sample_limit=5)
        if not proposals:
            return {
                "ok": False,
                "tier": tier,
                "proposal_count": 0,
                "assistant": {
                    "summary": "No taxonomy proposals are available for LLM review.",
                    "recommendations": [],
                    "safety_notes": ["No provider call was made."],
                },
            }
        try:
            return run_taxonomy_assistant(
                self.server.settings,
                proposals,
                tier=tier,
                timeout=60,
            )
        except LLMProviderError as exc:
            raise BadRequestError(str(exc)) from exc

    def proposal_action_payload(self) -> dict[str, object]:
        payload = self.read_json_body()
        action = str(payload.get("action") or "").strip().lower()
        if action == "observe_preview":
            limit = parse_int_limit(str(payload.get("limit") or "25"), default=25, maximum=250)
            command = [
                sys.executable,
                str(PROJECT_ROOT / "tools" / "millie_sort_mail.py"),
                "--observe",
                "--include-classified",
                "--limit",
                str(limit),
                "--sample",
                "10",
            ]
            result = run_apply_command("proposal_observe_preview", command)
            return {
                "ok": result["returncode"] == 0,
                "action": action,
                "limit": limit,
                "result": result,
            }
        if action not in {"activate", "disable", "retire"}:
            raise BadRequestError("valid proposal action is required")
        rule_ids = payload.get("rule_ids")
        if rule_ids is None and payload.get("rule_id"):
            rule_ids = [payload.get("rule_id")]
        if not isinstance(rule_ids, list):
            raise BadRequestError("rule_ids must be a list")
        with self.store() as store:
            mailbox = self.request_mailbox(store)
            identity_id = str(mailbox["owner_identity_id"]) if mailbox else None
            try:
                result = store.record_proposal_batch_action(
                    rule_ids=[str(item) for item in rule_ids],
                    action=action,
                    identity_id=identity_id,
                )
            except KeyError as exc:
                raise NotFoundError(str(exc)) from exc
            except ValueError as exc:
                raise BadRequestError(str(exc)) from exc
            store.connection.commit()
        return result

    def internal_apply_action_payload(self) -> dict[str, object]:
        payload = self.read_json_body()
        mode = str(payload.get("mode") or "dry_run").strip().lower()
        target = str(payload.get("target") or "both").strip().lower()
        if mode not in {"dry_run", "execute"}:
            raise BadRequestError("mode must be dry_run or execute")
        if target not in {"both", "suggestions", "retention"}:
            raise BadRequestError("target must be both, suggestions, or retention")
        limit = parse_int_limit(str(payload.get("limit") or "100"), default=100, maximum=500)
        if mode == "execute" and not automation_level_allows(self.server.settings, "auto_internal"):
            raise BadRequestError("automation_level must allow auto_internal before execute")
        commands = []
        if target in {"both", "suggestions"}:
            commands.append(("suggestions", apply_command("millie_apply_suggestions.py", mode=mode, limit=limit)))
        if target in {"both", "retention"}:
            commands.append(("retention", apply_command("millie_apply_retention.py", mode=mode, limit=limit)))
        results = [run_apply_command(name, command) for name, command in commands]
        return {
            "ok": all(item["returncode"] == 0 for item in results),
            "mode": mode,
            "target": target,
            "limit": limit,
            "results": results,
        }

    def operations_action_payload(self) -> dict[str, object]:
        payload = self.read_json_body()
        action = str(payload.get("action") or "").strip().lower()
        limit = parse_int_limit(str(payload.get("limit") or "500"), default=500, maximum=5000)
        batch_size = parse_int_limit(
            str(payload.get("fetch_batch_size") or "10"),
            default=10,
            maximum=100,
        )
        commit_every = parse_int_limit(
            str(payload.get("commit_every") or "50"),
            default=50,
            maximum=1000,
        )
        command: list[str]
        timeout = 600
        if action == "live_sync_once":
            account_filter = str(payload.get("account") or "").strip()
            folder_filter = str(payload.get("folder") or "").strip()
            command = [
                sys.executable,
                str(PROJECT_ROOT / "tools" / "millie_live_sync.py"),
                "--once",
                "--fetch-batch-size",
                str(batch_size),
                "--commit-every",
                str(commit_every),
                "--imap-timeout",
                "120",
            ]
            if account_filter:
                command.extend(["--account", account_filter])
            if folder_filter:
                command.extend(["--folder", folder_filter])
            timeout = 900
        elif action == "live_upkeep_once":
            command = [
                sys.executable,
                str(PROJECT_ROOT / "tools" / "millie_live_upkeep.py"),
                "--once",
                "--dedupe-limit",
                str(limit),
            ]
            timeout = 900
        elif action == "dedupe_report":
            command = [
                sys.executable,
                str(PROJECT_ROOT / "tools" / "millie_dedupe_report.py"),
                "--json",
                "--samples",
                "0",
            ]
        elif action == "dedupe_backfill":
            command = [
                sys.executable,
                str(PROJECT_ROOT / "tools" / "millie_dedupe_report.py"),
                "--backfill",
                "--limit",
                str(limit),
                "--samples",
                "0",
                "--json",
            ]
        else:
            raise BadRequestError("Unsupported operations action")
        result = run_admin_command(action, command, timeout=timeout)
        return {
            "ok": result["returncode"] == 0,
            "action": action,
            "limit": limit,
            "result": result,
        }

    def store(self) -> PostgresMailStore:
        return PostgresMailStore.connect(self.server.settings)

    def send_html(self, value: str, *, status: int = HTTPStatus.OK) -> None:
        payload = value.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_json(
        self,
        value: object,
        *,
        status: int = HTTPStatus.OK,
        headers: list[tuple[str, str]] | None = None,
    ) -> None:
        payload = json.dumps(value, default=json_default, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        for name, header_value in headers or []:
            self.send_header(name, header_value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    def send_xml(self, value: str) -> None:
        payload = value.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def read_request_body(self) -> bytes:
        length_text = self.headers.get("Content-Length") or "0"
        try:
            length = int(length_text)
        except ValueError:
            length = 0
        return self.rfile.read(max(length, 0)) if length else b""

    def read_json_body(self) -> dict[str, object]:
        body = self.read_request_body()
        if not body:
            raise BadRequestError("JSON body is required")
        try:
            value = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise BadRequestError("Invalid JSON body") from exc
        if not isinstance(value, dict):
            raise BadRequestError("JSON object body is required")
        return value

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        if self.path.startswith("/api/"):
            payload = json.dumps({"error": message or HTTPStatus(code).phrase}).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        super().send_error(code, message, explain)

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except BadRequestError as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
        except NotFoundError as exc:
            self.send_error(HTTPStatus.NOT_FOUND, str(exc))
        except Exception as exc:  # noqa: BLE001 - dev server should surface request failures.
            print(f"WEBMAIL error={exc!r}", flush=True)
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Webmail request failed")


class MillieWebmailServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address,
        handler_class,
        *,
        settings: dict[str, str],
        accounts: list[dict[str, Any]],
        mailbox_address: str,
        auth_required: bool,
    ):
        self.settings = settings
        self.accounts = accounts
        self.mailbox_address = mailbox_address
        self.auth_required = auth_required
        super().__init__(server_address, handler_class)


class BadRequestError(Exception):
    pass


class NotFoundError(Exception):
    pass


class BodyTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)

    def get_text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "\n".join(self.parts)).strip()


def decorate_folder(folder: dict[str, object], counts: dict[str, int]) -> dict[str, object]:
    path = str(folder["path"])
    return {
        "path": path,
        "display_name": folder.get("display_name") or path.rsplit("/", 1)[-1],
        "role": folder.get("role"),
        "selectable": bool(folder.get("selectable")),
        "subscribed": bool(folder.get("subscribed")),
        "count": counts.get(path, 0),
    }


def group_addresses(addresses: list[dict[str, object]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for address in addresses:
        grouped.setdefault(str(address["role"]), []).append(str(address["display"]))
    return grouped


def display_body(detail: dict[str, Any]) -> str:
    if detail.get("body_text"):
        return str(detail["body_text"]).strip()
    if detail.get("body_html"):
        return html_to_text(str(detail["body_html"]))
    raw = detail.get("raw_mime")
    if raw:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        text = message_text(message)
        if text:
            return text
    return detail.get("body_preview") or ""


def message_text(message: EmailMessage) -> str:
    if message.is_multipart():
        html_fallback = ""
        for part in message.walk():
            if part.get_content_disposition() == "attachment":
                continue
            content_type = part.get_content_type()
            if content_type == "text/plain":
                return str(part.get_content()).strip()
            if content_type == "text/html" and not html_fallback:
                html_fallback = html_to_text(str(part.get_content()))
        return html_fallback
    if message.get_content_type() == "text/html":
        return html_to_text(str(message.get_content()))
    return str(message.get_content()).strip()


def html_to_text(value: str) -> str:
    extractor = BodyTextExtractor()
    extractor.feed(value)
    return extractor.get_text()


def json_default(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def parse_message_limit(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in MESSAGE_LIMIT_OPTIONS else DEFAULT_MESSAGE_LIMIT


def parse_int_limit(value: str, *, default: int, maximum: int) -> int:
    try:
        limit = int(str(value or "").strip())
    except ValueError:
        return default
    return max(1, min(limit, maximum))


def parse_bool_filter(value: str) -> bool | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on", "with"}:
        return True
    if normalized in {"0", "false", "no", "off", "without"}:
        return False
    return None


def apply_command(script_name: str, *, mode: str, limit: int) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "tools" / script_name),
        "--limit",
        str(limit),
    ]
    if mode == "execute":
        command.extend(["--execute", "--record-blocked"])
    return command


def tail_output(value: object, *, limit: int = 6000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")[-limit:]
    return str(value)[-limit:]


def run_admin_command(name: str, command: list[str], *, timeout: int = 600) -> dict[str, object]:
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "returncode": 124,
            "stdout": tail_output(exc.stdout),
            "stderr": tail_output(exc.stderr) or f"Timed out after {timeout} seconds",
        }
    return {
        "name": name,
        "returncode": completed.returncode,
        "stdout": tail_output(completed.stdout),
        "stderr": tail_output(completed.stderr),
    }


def run_apply_command(name: str, command: list[str]) -> dict[str, object]:
    return run_admin_command(name, command, timeout=300)


def autodiscover_request_email(body: bytes) -> str | None:
    text = body.decode("utf-8", errors="ignore")
    match = re.search(r"<(?:[A-Za-z0-9_]+:)?E?MailAddress>\s*([^<]+?)\s*</", text, re.I)
    if not match:
        return None
    value = match.group(1).strip()
    return value if "@" in value else None


def autodiscover_xml(settings: dict[str, str], login_name: str) -> str:
    domain = settings.get("service_mail_domain") or "localhost"
    login = login_name or default_service_login(settings, "geon")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Autodiscover xmlns="http://schemas.microsoft.com/exchange/autodiscover/responseschema/2006">
  <Response xmlns="http://schemas.microsoft.com/exchange/autodiscover/outlook/responseschema/2006a">
    <Account>
      <AccountType>email</AccountType>
      <Action>settings</Action>
      <Protocol>
        <Type>IMAP</Type>
        <Server>{xml_escape(domain)}</Server>
        <Port>993</Port>
        <LoginName>{xml_escape(login)}</LoginName>
        <SSL>on</SSL>
        <AuthRequired>on</AuthRequired>
      </Protocol>
      <Protocol>
        <Type>SMTP</Type>
        <Server>{xml_escape(domain)}</Server>
        <Port>465</Port>
        <LoginName>{xml_escape(login)}</LoginName>
        <SSL>on</SSL>
        <AuthRequired>on</AuthRequired>
      </Protocol>
    </Account>
  </Response>
</Autodiscover>
"""


def autoconfig_xml(settings: dict[str, str], login_name: str) -> str:
    domain = settings.get("service_mail_domain") or "localhost"
    login = login_name or default_service_login(settings, "geon")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<clientConfig version="1.1">
  <emailProvider id="{xml_escape(domain)}">
    <domain>{xml_escape(domain)}</domain>
    <displayName>MILLIE Mail</displayName>
    <displayShortName>MILLIE</displayShortName>
    <incomingServer type="imap">
      <hostname>{xml_escape(domain)}</hostname>
      <port>993</port>
      <socketType>SSL</socketType>
      <authentication>password-cleartext</authentication>
      <username>{xml_escape(login)}</username>
    </incomingServer>
    <outgoingServer type="smtp">
      <hostname>{xml_escape(domain)}</hostname>
      <port>465</port>
      <socketType>SSL</socketType>
      <authentication>password-cleartext</authentication>
      <username>{xml_escape(login)}</username>
    </outgoingServer>
  </emailProvider>
</clientConfig>
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start MILLIE's development webmail/admin UI.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help="Disable web login for local-only development testing.",
    )
    parser.add_argument(
        "--mailbox",
        default="",
        help="Mailbox address to open. Defaults to geon@<service_mail_domain> from millie.settings.",
    )
    parser.add_argument("--daemon", action="store_true", help="Detach into the background.")
    parser.add_argument("--pid-file", type=Path, default=DEFAULT_PID_FILE)
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG_FILE)
    parser.add_argument(
        "--live-sync",
        action="store_true",
        help="Sync enabled IMAP/OAuth accounts while this webmail process is running.",
    )
    parser.add_argument(
        "--sync-account",
        action="append",
        default=[],
        help="Account email/id/display name to sync. May be repeated. Defaults to all enabled IMAP accounts.",
    )
    parser.add_argument("--sync-interval", type=int, default=900, help="Seconds between live sync passes.")
    parser.add_argument("--sync-fetch-batch-size", type=int, default=10)
    parser.add_argument("--sync-commit-every", type=int, default=50)
    parser.add_argument("--sync-imap-timeout", type=int, default=120)
    parser.add_argument(
        "--no-sync-on-start",
        action="store_true",
        help="Wait one interval before the first live sync pass.",
    )
    return parser


def daemonize(*, pid_file: Path, log_file: Path) -> None:
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    first_pid = os.fork()
    if first_pid > 0:
        raise SystemExit(0)
    os.setsid()
    second_pid = os.fork()
    if second_pid > 0:
        pid_file.write_text(f"{second_pid}\n")
        raise SystemExit(0)
    os.chdir(PROJECT_ROOT)
    os.umask(0o077)
    log_fd = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(log_fd)
    null_fd = os.open("/dev/null", os.O_RDONLY)
    os.dup2(null_fd, 0)
    os.close(null_fd)


def serve(args: argparse.Namespace) -> None:
    config = load_local_settings()
    settings = config["settings"]
    accounts = config.get("accounts", [])
    mailbox_address = args.mailbox or default_service_login(settings, "geon")
    sync_thread = None
    sync_stop = None
    if args.live_sync:
        sync_config = LiveSyncConfig(
            accounts=tuple(args.sync_account),
            interval_seconds=args.sync_interval,
            fetch_batch_size=args.sync_fetch_batch_size,
            commit_every=args.sync_commit_every,
            imap_timeout_seconds=args.sync_imap_timeout,
        )
        sync_thread, sync_stop = start_live_sync_thread(
            sync_config,
            run_immediately=not args.no_sync_on_start,
            log=lambda value: print(value, flush=True),
        )
    server = MillieWebmailServer(
        (args.host, args.port),
        MillieWebmailHandler,
        settings=settings,
        accounts=accounts,
        mailbox_address=mailbox_address,
        auth_required=not args.no_auth,
    )
    auth_mode = "auth" if server.auth_required else "no-auth"
    print(f"MILLIE webmail listening on http://{args.host}:{args.port} mode={auth_mode}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    finally:
        if sync_stop is not None:
            sync_stop.set()
        if sync_thread is not None:
            sync_thread.join(timeout=5)


LOGIN_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MILLIE Login</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #f6f8fb;
      color: #17202f;
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    form {
      width: min(360px, calc(100vw - 32px));
      display: grid;
      gap: 12px;
      padding: 24px;
      border: 1px solid #d7dde7;
      border-radius: 8px;
      background: #fff;
      box-shadow: 0 1px 2px rgba(18, 28, 45, .08);
    }
    h1 {
      margin: 0 0 6px;
      font-size: 22px;
      letter-spacing: 0;
    }
    label {
      display: grid;
      gap: 5px;
      color: #657084;
      font-size: 12px;
      font-weight: 650;
    }
    input, button {
      height: 38px;
      border-radius: 7px;
      font: inherit;
    }
    input {
      border: 1px solid #d7dde7;
      padding: 0 10px;
    }
    button {
      border: 0;
      background: #17202f;
      color: #fff;
      font-weight: 700;
      cursor: pointer;
    }
    .error {
      min-height: 18px;
      color: #c5221f;
      font-size: 12px;
    }
  </style>
</head>
<body>
  <form id="loginForm">
    <h1>MILLIE Mail</h1>
    <label>Login
      <input id="login" name="login" type="email" autocomplete="username" required>
    </label>
    <label>Password
      <input id="password" name="password" type="password" autocomplete="current-password" required>
    </label>
    <button type="submit">Sign in</button>
    <div id="error" class="error"></div>
  </form>
  <script>
    const form = document.getElementById("loginForm");
    const error = document.getElementById("error");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      error.textContent = "";
      const response = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          login: document.getElementById("login").value,
          password: document.getElementById("password").value,
        }),
      });
      if (response.ok) {
        window.location.href = "/";
        return;
      }
      const data = await response.json().catch(() => ({}));
      error.textContent = data.error || "Sign in failed";
    });
  </script>
</body>
</html>
"""


INDEX_HTML = r"""<!doctype html>
<html lang="en" data-theme="gmail">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MILLIE Mail</title>
  <style>
    :root {
      --bg: #f6f8fb;
      --surface: #ffffff;
      --surface-2: #eef2f7;
      --line: #d7dde7;
      --text: #17202f;
      --muted: #657084;
      --accent: #c5221f;
      --accent-soft: #fce8e6;
      --selected: #eaf1fb;
      --shadow: 0 1px 2px rgba(18, 28, 45, .08);
    }
    html[data-theme="outlook"] {
      --bg: #f3f7fb;
      --surface: #ffffff;
      --surface-2: #e8f1fb;
      --line: #d1dcea;
      --text: #102033;
      --muted: #52647a;
      --accent: #0078d4;
      --accent-soft: #dff0ff;
      --selected: #deecf9;
    }
    html[data-theme="m365"] {
      --bg: #f6f7fb;
      --surface: #ffffff;
      --surface-2: #eef1f6;
      --line: #d8deea;
      --text: #1c2434;
      --muted: #626f82;
      --accent: #6264a7;
      --accent-soft: #ecebff;
      --selected: #e8f5f3;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    button, input, select { font: inherit; }
    .app {
      display: grid;
      grid-template-rows: 56px minmax(0, 1fr);
      min-height: 100vh;
    }
    .topbar {
      display: grid;
      grid-template-columns: 210px minmax(150px, 1fr) repeat(11, auto) minmax(80px, 180px) auto;
      align-items: center;
      gap: 10px;
      padding: 0 18px;
      background: var(--surface);
      border-bottom: 1px solid var(--line);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      font-weight: 700;
      font-size: 16px;
      letter-spacing: 0;
      min-width: 0;
    }
    .brand-mark {
      display: grid;
      place-items: center;
      width: 30px;
      height: 30px;
      border-radius: 7px;
      color: #fff;
      background: var(--accent);
      box-shadow: var(--shadow);
    }
    .search {
      min-width: 0;
      height: 36px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-2);
      color: var(--text);
      padding: 0 12px;
      outline: none;
    }
    .search:focus { border-color: var(--accent); background: var(--surface); }
    .themes {
      display: grid;
      grid-template-columns: repeat(3, minmax(70px, 1fr));
      gap: 4px;
      padding: 3px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-2);
    }
    .themes button {
      height: 28px;
      border: 0;
      border-radius: 6px;
      background: transparent;
      color: var(--muted);
      cursor: pointer;
    }
    .themes button.active {
      color: var(--text);
      background: var(--surface);
      box-shadow: var(--shadow);
    }
    .account {
      color: var(--muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 180px;
    }
    .main {
      min-height: 0;
      display: grid;
      grid-template-columns: 228px minmax(300px, 390px) minmax(360px, 1fr);
    }
    .folders, .messages, .reader {
      min-height: 0;
      overflow: auto;
      border-right: 1px solid var(--line);
      background: var(--surface);
    }
    .folders {
      padding: 12px 10px;
      background: color-mix(in srgb, var(--surface) 84%, var(--surface-2));
    }
    .folder {
      width: 100%;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 8px;
      height: 34px;
      margin: 2px 0;
      padding: 0 10px;
      border: 0;
      border-radius: 7px;
      background: transparent;
      color: var(--text);
      text-align: left;
      cursor: pointer;
    }
    .folder .name {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .folder .count { color: var(--muted); font-size: 12px; }
    .folder.active {
      background: var(--accent-soft);
      color: var(--accent);
      font-weight: 650;
    }
    .messages {
      background: var(--surface);
    }
    .list-head {
      position: sticky;
      top: 0;
      z-index: 1;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 10px;
      height: 44px;
      padding: 0 14px;
      background: var(--surface);
      border-bottom: 1px solid var(--line);
      font-weight: 650;
    }
    .list-title {
      display: flex;
      align-items: baseline;
      gap: 8px;
      min-width: 0;
    }
    #folderTitle {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .list-count { color: var(--muted); font-weight: 500; }
    .list-controls {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .limit-label {
      display: flex;
      align-items: center;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
      white-space: nowrap;
    }
    .limit-select {
      width: 66px;
      height: 30px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      font: inherit;
      font-size: 12px;
    }
    .refresh-button {
      height: 30px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      padding: 0 9px;
      font: inherit;
      font-size: 12px;
      font-weight: 650;
      cursor: pointer;
    }
    .refresh-button:hover { border-color: var(--accent); color: var(--accent); }
    .review-button {
      height: 32px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: var(--surface);
      color: var(--text);
      padding: 0 11px;
      font: inherit;
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
      white-space: nowrap;
    }
    .review-button:hover { border-color: var(--accent); color: var(--accent); }
    .message-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px 10px;
      width: 100%;
      min-height: 94px;
      padding: 12px 14px;
      border: 0;
      border-bottom: 1px solid var(--line);
      background: var(--surface);
      color: var(--text);
      text-align: left;
      cursor: pointer;
    }
    .message-row:hover { background: color-mix(in srgb, var(--selected) 55%, var(--surface)); }
    .message-row.active {
      background: var(--selected);
      border-left: 3px solid var(--accent);
      padding-left: 11px;
    }
    .sender, .subject, .preview {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .sender { font-weight: 650; }
    .date { color: var(--muted); font-size: 12px; white-space: nowrap; }
    .subject-line {
      grid-column: 1 / -1;
      display: flex;
      align-items: center;
      gap: 7px;
      min-width: 0;
    }
    .subject { font-weight: 600; }
    .suggestion-badge {
      flex: 0 0 auto;
      display: inline-flex;
      align-items: center;
      height: 19px;
      padding: 0 6px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 11px;
      font-weight: 700;
    }
    .health-ok { background: #e7f4ea; color: #137333; }
    .health-stale { background: #fef7e0; color: #8a5a00; }
    .health-failed { background: #fce8e6; color: #c5221f; }
    .health-running { background: #e8f0fe; color: #185abc; }
    .health-unknown, .health-skipped { background: var(--surface-2); color: var(--muted); }
    .preview { grid-column: 1 / -1; color: var(--muted); }
    .reader {
      border-right: 0;
      background: var(--surface);
    }
    .reader-inner {
      max-width: 980px;
      margin: 0 auto;
      padding: 24px 30px 48px;
    }
    .reader-subject {
      margin: 0 0 14px;
      font-size: 24px;
      line-height: 1.2;
      letter-spacing: 0;
      overflow-wrap: anywhere;
    }
    .meta {
      display: grid;
      gap: 5px;
      padding-bottom: 18px;
      border-bottom: 1px solid var(--line);
      color: var(--muted);
    }
    .meta-row {
      display: grid;
      grid-template-columns: 54px minmax(0, 1fr);
      gap: 10px;
      align-items: baseline;
    }
    .meta-label { color: var(--muted); }
    .meta-value {
      color: var(--text);
      min-width: 0;
      overflow-wrap: anywhere;
    }
    .body {
      margin-top: 24px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 15px;
      line-height: 1.58;
    }
    .attachments {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 22px;
    }
    .attachment {
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 8px 10px;
      background: var(--surface-2);
      color: var(--text);
      max-width: 260px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .review-panel {
      display: grid;
      gap: 10px;
      margin-top: 18px;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-2);
    }
    .review-panel h2 {
      margin: 0;
      font-size: 15px;
      letter-spacing: 0;
    }
    .suggestion-card {
      display: grid;
      gap: 8px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
    }
    .suggestion-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    .suggestion-title { font-weight: 700; overflow-wrap: anywhere; }
    .suggestion-meta { color: var(--muted); font-size: 12px; }
    .suggestion-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .suggestion-actions button {
      height: 28px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      padding: 0 9px;
      font: inherit;
      font-size: 12px;
      font-weight: 650;
      cursor: pointer;
    }
    .suggestion-actions button:hover { border-color: var(--accent); color: var(--accent); }
    .policy-edit {
      display: grid;
      grid-template-columns: minmax(180px, 1fr) repeat(4, auto);
      gap: 8px;
      align-items: end;
    }
    .policy-edit label {
      display: grid;
      gap: 4px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .policy-edit input,
    .policy-edit select {
      height: 30px;
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      padding: 0 8px;
      font-size: 12px;
    }
    .policy-edit .check-label {
      display: flex;
      align-items: center;
      gap: 6px;
      height: 30px;
    }
    .policy-edit .check-label input {
      width: 16px;
      height: 16px;
      padding: 0;
    }
    .policy-edit .duration-group {
      display: grid;
      grid-template-columns: 70px 82px;
      gap: 6px;
    }
    .proposal-controls {
      display: grid;
      grid-template-columns: minmax(140px, 180px) minmax(100px, 130px) repeat(6, auto);
      gap: 8px;
      align-items: end;
    }
    .proposal-controls label {
      display: grid;
      gap: 4px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .proposal-controls select {
      height: 30px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      padding: 0 8px;
      font-size: 12px;
      min-width: 0;
    }
    .proposal-head {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      gap: 10px;
      align-items: start;
    }
    .proposal-check {
      width: 18px;
      height: 18px;
      margin-top: 2px;
    }
    .filter-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(120px, 1fr)) auto;
      gap: 8px;
      align-items: end;
    }
    .filter-grid label {
      display: grid;
      gap: 4px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .filter-grid input,
    .filter-grid select {
      height: 30px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      padding: 0 8px;
      font-size: 12px;
      min-width: 0;
    }
    .metric-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(120px, 1fr));
      gap: 10px;
    }
    .metric-card {
      display: grid;
      gap: 3px;
      min-height: 74px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
    }
    .metric-label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .metric-value {
      font-size: 24px;
      font-weight: 750;
      line-height: 1.1;
    }
    .metric-meta {
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .source-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
    }
    .source-row + .source-row { margin-top: 6px; }
    .source-row .suggestion-meta { overflow-wrap: anywhere; }
    .json-box, .output-box {
      max-height: 170px;
      overflow: auto;
      margin: 0;
      padding: 8px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--surface-2);
      color: var(--text);
      font: 12px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .review-list {
      display: grid;
      gap: 10px;
      padding: 24px 30px 48px;
    }
    .review-list h1 {
      margin: 0 0 4px;
      font-size: 24px;
      letter-spacing: 0;
    }
    .empty, .error {
      padding: 30px;
      color: var(--muted);
    }
    @media (max-width: 980px) {
      .topbar { grid-template-columns: 160px minmax(120px, 1fr); grid-auto-flow: row; height: auto; padding: 10px 12px; }
      .themes, .account { grid-column: span 1; }
      .policy-edit { grid-template-columns: 1fr 1fr; align-items: stretch; }
      .proposal-controls { grid-template-columns: 1fr 1fr; align-items: stretch; }
      .filter-grid { grid-template-columns: 1fr 1fr; }
      .metric-grid { grid-template-columns: 1fr 1fr; }
      .main { grid-template-columns: 76px minmax(260px, 38vw) minmax(320px, 1fr); }
      .folder { grid-template-columns: 1fr; justify-items: center; padding: 0 6px; }
      .folder .name { max-width: 54px; }
      .folder .count { display: none; }
      .list-head { height: auto; min-height: 44px; align-items: stretch; padding: 8px 10px; }
      .list-title { flex-direction: column; gap: 0; }
      .list-controls { align-self: center; }
    }
  </style>
</head>
<body>
  <div class="app">
    <header class="topbar">
      <div class="brand"><span class="brand-mark">M</span><span>MILLIE Mail</span></div>
      <input id="search" class="search" type="search" placeholder="Search mail" autocomplete="off">
      <button id="searchButton" class="review-button" type="button">Search</button>
      <button id="reviewButton" class="review-button" type="button">Review</button>
      <button id="workbenchButton" class="review-button" type="button">Workbench</button>
      <button id="metricsButton" class="review-button" type="button">Metrics</button>
      <button id="unsubscribeButton" class="review-button" type="button">Unsub</button>
      <button id="policiesButton" class="review-button" type="button">Policies</button>
      <button id="proposalsButton" class="review-button" type="button">Proposals</button>
      <button id="rulesButton" class="review-button" type="button">Rules</button>
      <button id="applyButton" class="review-button" type="button">Apply</button>
      <button id="opsButton" class="review-button" type="button">Ops</button>
      <div class="themes" id="themes">
        <button type="button" data-theme="gmail">Gmail</button>
        <button type="button" data-theme="outlook">Outlook</button>
        <button type="button" data-theme="m365">365</button>
      </div>
      <div class="account" id="account"></div>
      <button id="logoutButton" class="review-button" type="button">Logout</button>
    </header>
    <main class="main">
      <nav class="folders" id="folders"></nav>
      <section class="messages">
        <div class="list-head">
          <div class="list-title">
            <span id="folderTitle">INBOX</span>
            <span class="list-count" id="messageCount">0</span>
          </div>
          <div class="list-controls">
            <label class="limit-label">Show
              <select id="messageLimit" class="limit-select">
                <option value="25">25</option>
                <option value="50">50</option>
                <option value="100">100</option>
                <option value="250">250</option>
                <option value="500">500</option>
                <option value="all">All</option>
              </select>
            </label>
            <button id="refreshFolder" class="refresh-button" type="button">Refresh</button>
          </div>
        </div>
        <div id="messageList"></div>
      </section>
      <section class="reader" id="reader"><div class="empty">Loading</div></section>
    </main>
  </div>
  <script>
    const limitStorageKey = "millie.webmail.messageLimit";
    const validMessageLimits = new Set(["25", "50", "100", "250", "500", "all"]);
    const savedLimit = localStorage.getItem(limitStorageKey) || "50";
    const state = {
      mailbox: null,
      folders: [],
      folder: "INBOX",
      folderCount: 0,
      messages: [],
      selectedUid: null,
      query: "",
      limit: validMessageLimits.has(savedLimit) ? savedLimit : "50",
      cache: new Map(),
    };
    const $ = (id) => document.getElementById(id);

    function setTheme(theme) {
      document.documentElement.dataset.theme = theme;
      localStorage.setItem("millie.webmail.theme", theme);
      document.querySelectorAll("#themes button").forEach((button) => {
        button.classList.toggle("active", button.dataset.theme === theme);
      });
    }

    function text(value, fallback = "") {
      return value === null || value === undefined || value === "" ? fallback : String(value);
    }

    function formatDate(value) {
      if (!value) return "";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return "";
      return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(date);
    }

    function formatSize(value) {
      const size = Number(value || 0);
      if (size >= 1048576) return `${(size / 1048576).toFixed(1)} MB`;
      if (size >= 1024) return `${Math.round(size / 1024)} KB`;
      return `${size} B`;
    }

    function formatCount(value) {
      return new Intl.NumberFormat().format(Number(value || 0));
    }

    function statusCounts(counts) {
      const entries = Object.entries(counts || {}).filter(([, count]) => Number(count || 0) > 0);
      if (!entries.length) return "none";
      return entries.map(([status, count]) => `${status} ${formatCount(count)}`).join(" · ");
    }

    async function api(path) {
      const response = await fetch(path, { cache: "no-store" });
      if (response.status === 401) {
        window.location.href = "/login";
        throw new Error("Authentication required");
      }
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.error || response.statusText);
      }
      return response.json();
    }

    async function postJson(path, payload) {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (response.status === 401) {
        window.location.href = "/login";
        throw new Error("Authentication required");
      }
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || response.statusText);
      }
      return response.json();
    }

    function cacheKey(folder, limit = state.limit) {
      return `${limit}::${folder}`;
    }

    function bootstrapUrl(folder) {
      return `/api/bootstrap?folder=${encodeURIComponent(folder)}&limit=${encodeURIComponent(state.limit)}`;
    }

    function applyBootstrap(data) {
      state.mailbox = data.mailbox;
      state.folders = data.folders;
      state.folder = data.selected_folder;
      state.folderCount = Number(data.folder_count || 0);
      state.limit = data.message_limit || state.limit;
      state.messages = data.messages;
      state.selectedUid = state.messages[0]?.uid || null;
    }

    async function loadBootstrap(folder = "INBOX", options = {}) {
      const data = await loadFolderData(folder, options);
      applyBootstrap(data);
      render();
      if (state.selectedUid) await openMessage(state.selectedUid);
    }

    async function loadFolderData(folder, options = {}) {
      const key = cacheKey(folder);
      if (!options.force && state.cache.has(key)) {
        return state.cache.get(key);
      }
      const data = await api(bootstrapUrl(folder));
      state.cache.set(key, data);
      return data;
    }

    async function loadFolder(folder, options = {}) {
      if (options.force) {
        state.cache.delete(cacheKey(folder));
      }
      const data = await loadFolderData(folder, options);
      applyBootstrap(data);
      render();
      if (state.selectedUid) {
        await openMessage(state.selectedUid);
      } else {
        $("reader").innerHTML = `<div class="empty">No messages</div>`;
      }
    }

    async function openMessage(uid) {
      state.selectedUid = uid;
      renderMessages();
      const detail = await api(`/api/messages?folder=${encodeURIComponent(state.folder)}&uid=${encodeURIComponent(uid)}`);
      renderReader(detail);
    }

    function render() {
      $("account").textContent = state.mailbox?.mailbox_address || "";
      $("messageLimit").value = state.limit;
      renderFolders();
      renderMessages();
    }

    function renderFolders() {
      const nav = $("folders");
      nav.innerHTML = "";
      state.folders.forEach((folder) => {
        if (!folder.selectable) return;
        const button = document.createElement("button");
        button.className = `folder${folder.path === state.folder ? " active" : ""}`;
        button.type = "button";
        button.innerHTML = `<span class="name"></span><span class="count"></span>`;
        button.querySelector(".name").textContent = folder.display_name;
        button.querySelector(".count").textContent = folder.count;
        button.addEventListener("click", () => loadFolder(folder.path).catch(showError));
        nav.appendChild(button);
      });
    }

    function filteredMessages() {
      const q = state.query.trim().toLowerCase();
      if (!q) return state.messages;
      return state.messages.filter((message) => [message.from, message.to, message.subject, message.body_preview]
        .some((value) => text(value).toLowerCase().includes(q)));
    }

    function renderMessages() {
      const messages = filteredMessages();
      $("folderTitle").textContent = state.folder;
      $("messageCount").textContent = messageCountText(messages.length);
      const list = $("messageList");
      list.innerHTML = "";
      if (!messages.length) {
        list.innerHTML = `<div class="empty">No messages</div>`;
        return;
      }
      messages.forEach((message) => {
        const row = document.createElement("button");
        row.className = `message-row${message.uid === state.selectedUid ? " active" : ""}`;
        row.type = "button";
        row.innerHTML = `
          <div class="sender"></div>
          <div class="date"></div>
          <div class="subject-line"><span class="subject"></span><span class="suggestion-badge"></span></div>
          <div class="preview"></div>
        `;
        row.querySelector(".sender").textContent = text(message.from, "(unknown)");
        row.querySelector(".date").textContent = formatDate(message.message_date);
        row.querySelector(".subject").textContent = text(message.subject, "(no subject)");
        const badge = row.querySelector(".suggestion-badge");
        const suggestionCount = Number(message.proposed_classifications || 0);
        if (suggestionCount > 0) {
          badge.textContent = `${suggestionCount} suggested`;
        } else {
          badge.remove();
        }
        row.querySelector(".preview").textContent = text(message.body_preview, formatSize(message.size));
        row.addEventListener("click", () => openMessage(message.uid).catch(showError));
        list.appendChild(row);
      });
    }

    function messageCountText(filteredCount) {
      const loaded = state.messages.length;
      const total = state.folderCount || loaded;
      if (state.query.trim()) {
        return `${filteredCount} / ${loaded} loaded`;
      }
      if (loaded < total) {
        return `${loaded} / ${total}`;
      }
      return String(total);
    }

    function renderReader(message) {
      const from = (message.addresses.from || []).join(", ");
      const to = (message.addresses.to || []).join(", ");
      const cc = (message.addresses.cc || []).join(", ");
      const attachments = message.attachments || [];
      $("reader").innerHTML = `
        <div class="reader-inner">
          <h1 class="reader-subject"></h1>
          <div class="meta">
            <div class="meta-row"><span class="meta-label">From</span><span class="meta-value" data-field="from"></span></div>
            <div class="meta-row"><span class="meta-label">To</span><span class="meta-value" data-field="to"></span></div>
            ${cc ? `<div class="meta-row"><span class="meta-label">Cc</span><span class="meta-value" data-field="cc"></span></div>` : ""}
            <div class="meta-row"><span class="meta-label">Date</span><span class="meta-value" data-field="date"></span></div>
          </div>
          <div class="review-panel" data-panel="classifications" hidden>
            <h2>MILLIE Suggestions</h2>
            <div data-list="classifications"></div>
          </div>
          <div class="review-panel" data-panel="unsubscribe" hidden>
            <h2>Unsubscribe Candidates</h2>
            <div data-list="unsubscribe"></div>
          </div>
          <div class="review-panel" data-panel="retention" hidden>
            <h2>Retention</h2>
            <div data-list="retention"></div>
          </div>
          <div class="body"></div>
          <div class="attachments"></div>
        </div>
      `;
      $("reader").querySelector(".reader-subject").textContent = text(message.subject, "(no subject)");
      $("reader").querySelector('[data-field="from"]').textContent = text(from, "(unknown)");
      $("reader").querySelector('[data-field="to"]').textContent = text(to, "(none)");
      const ccNode = $("reader").querySelector('[data-field="cc"]');
      if (ccNode) ccNode.textContent = cc;
      $("reader").querySelector('[data-field="date"]').textContent = formatDate(message.message_date);
      $("reader").querySelector(".body").textContent = text(message.body, "");
      const container = $("reader").querySelector(".attachments");
      attachments.forEach((attachment) => {
        const item = document.createElement("div");
        item.className = "attachment";
        item.textContent = `${attachment.filename} · ${formatSize(attachment.size)}`;
        container.appendChild(item);
      });
      renderClassificationPanel(message.classifications || []);
      renderUnsubscribePanel(message.unsubscribe_candidates || []);
      renderRetentionPanel(message.retention_status || []);
    }

    function renderClassificationPanel(classifications) {
      const panel = $("reader").querySelector('[data-panel="classifications"]');
      const list = $("reader").querySelector('[data-list="classifications"]');
      if (!panel || !list || !classifications.length) return;
      panel.hidden = false;
      list.innerHTML = "";
      classifications.forEach((item) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        const target = item.target_folder_path || (item.target_tags || []).join(", ");
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div class="suggestion-meta" data-field="reason"></div>
          <div class="suggestion-actions"></div>
        `;
        card.querySelector(".suggestion-title").textContent = `${item.kind}:${item.value} -> ${target}`;
        card.querySelector(".suggestion-meta").textContent = `confidence ${Number(item.confidence || 0).toFixed(2)}`;
        card.querySelector(".suggestion-badge").textContent = item.status;
        card.querySelector('[data-field="reason"]').textContent = item.reason || "";
        const actions = card.querySelector(".suggestion-actions");
        if (item.status === "proposed") {
          [
            ["approve", "Approve"],
            ["reject", "Reject"],
            ["always", "Always"],
            ["never", "Never"],
          ].forEach(([action, label]) => {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = label;
            button.addEventListener("click", () => applyClassificationAction(item.id, action).catch(showError));
            actions.appendChild(button);
          });
        }
        list.appendChild(card);
      });
    }

    function renderUnsubscribePanel(candidates) {
      const panel = $("reader").querySelector('[data-panel="unsubscribe"]');
      const list = $("reader").querySelector('[data-list="unsubscribe"]');
      if (!panel || !list || !candidates.length) return;
      panel.hidden = false;
      list.innerHTML = "";
      candidates.forEach((item) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        const target = item.unsubscribe_mailto || item.unsubscribe_url || item.candidate_type;
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div class="suggestion-actions"></div>
        `;
        card.querySelector(".suggestion-title").textContent = target;
        card.querySelector(".suggestion-meta").textContent = `${item.candidate_type} · confidence ${Number(item.confidence || 0).toFixed(2)}`;
        card.querySelector(".suggestion-badge").textContent = item.status;
        const actions = card.querySelector(".suggestion-actions");
        if (["detected", "review_required"].includes(item.status)) {
          [
            ["approve", "Approve"],
            ["reject", "Ignore"],
          ].forEach(([action, label]) => {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = label;
            button.addEventListener("click", () => applyUnsubscribeAction(item.id, action).catch(showError));
            actions.appendChild(button);
          });
        }
        list.appendChild(card);
      });
    }

    function renderRetentionPanel(policies) {
      const panel = $("reader").querySelector('[data-panel="retention"]');
      const list = $("reader").querySelector('[data-list="retention"]');
      if (!panel || !list || !policies.length) return;
      panel.hidden = false;
      list.innerHTML = "";
      policies.forEach((item) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        const due = item.eligible_at ? `eligible ${formatDate(item.eligible_at)}` : "no eligibility date";
        const review = item.requires_review ? "review required" : "review not required";
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div class="suggestion-meta" data-field="timing"></div>
        `;
        card.querySelector(".suggestion-title").textContent = `${item.policy_name} · ${item.target_value}`;
        card.querySelector(".suggestion-meta").textContent =
          `${item.hold_duration_text} hold · ${item.action} · ${review}`;
        card.querySelector(".suggestion-badge").textContent = item.is_eligible ? "eligible" : item.status;
        card.querySelector('[data-field="timing"]').textContent =
          `${due} · copied ${formatDate(item.copied_at)}`;
        list.appendChild(card);
      });
    }

    async function applyClassificationAction(classificationId, action) {
      await postJson("/api/classifications/action", { classification_id: classificationId, action });
      state.cache.delete(cacheKey(state.folder));
      if (state.selectedUid) await openMessage(state.selectedUid);
    }

    async function applyWorkbenchAction(classificationIds, action) {
      await postJson("/api/review/workbench/action", {
        classification_ids: classificationIds,
        action,
      });
      state.cache.clear();
    }

    async function applyUnsubscribeAction(candidateId, action) {
      await postJson("/api/unsubscribe/action", { candidate_id: candidateId, action });
      if (state.selectedUid) await openMessage(state.selectedUid);
    }

    async function applyRetentionAction(policyId, mailboxMessageId, action) {
      await postJson("/api/retention/action", {
        policy_id: policyId,
        mailbox_message_id: mailboxMessageId,
        action,
      });
    }

    async function openReview() {
      const data = await api("/api/review?limit=50");
      renderReviewList(data.suggestions || [], data.retention || []);
    }

    async function openWorkbench() {
      const data = await api("/api/review/workbench?group_limit=25&sample_limit=5&candidate_limit=1000");
      renderWorkbench(data.groups || []);
    }

    async function openUnsubscribeQueue() {
      const data = await api("/api/unsubscribe?limit=100");
      renderUnsubscribeQueue(data.candidates || []);
    }

    async function openPolicies() {
      const data = await api("/api/retention/policies");
      renderPolicyList(data.policies || []);
    }

    async function runGlobalSearch(extra = {}) {
      const params = new URLSearchParams();
      const q = extra.q ?? $("search").value;
      if (q) params.set("q", q);
      ["folder", "from", "source_type", "source", "since", "until", "has_attachments"].forEach((key) => {
        if (extra[key]) params.set(key, extra[key]);
      });
      params.set("limit", extra.limit || "100");
      const data = await api(`/api/search?${params.toString()}`);
      renderSearchList(data.results || [], Object.fromEntries(params.entries()));
    }

    async function openRules() {
      const data = await api("/api/rules?limit=100");
      renderRuleList(data.rules || []);
    }

    async function openLearningMetrics() {
      const [data, ruleCandidates, taxonomy] = await Promise.all([
        api("/api/learning/metrics?limit=12"),
        api("/api/rules/candidates?limit=12&sample_limit=4&min_messages=1"),
        api("/api/taxonomy/proposals?limit=12&sample_limit=4"),
      ]);
      renderLearningMetrics(data, ruleCandidates.candidates || [], taxonomy.proposals || []);
    }

    async function openProposals(statusValue = null) {
      const currentSelect = $("proposalStatus");
      const selectedStatus = statusValue || (currentSelect ? currentSelect.value : "proposed,disabled");
      const params = new URLSearchParams({ limit: "100" });
      if (selectedStatus && selectedStatus !== "all") params.set("status", selectedStatus);
      const data = await api(`/api/proposals?${params.toString()}`);
      renderProposalReview(data, selectedStatus);
    }

    async function openInternalApply() {
      const data = await api("/api/internal-apply?limit=100");
      renderInternalApply(data);
    }

    async function openOperations() {
      const data = await api("/api/operations?run_limit=10");
      renderOperations(data);
    }

    async function loadSessionState() {
      const data = await api("/api/session");
      $("logoutButton").hidden = !data.auth_required;
    }

    function durationParts(seconds) {
      const normalized = Math.max(1, Number(seconds || 86400));
      if (normalized % 604800 === 0) return { value: normalized / 604800, unit: "weeks" };
      if (normalized % 86400 === 0) return { value: normalized / 86400, unit: "days" };
      if (normalized % 3600 === 0) return { value: normalized / 3600, unit: "hours" };
      return { value: normalized, unit: "seconds" };
    }

    function durationSeconds(value, unit) {
      const amount = Math.max(1, Number(value || 1));
      const multipliers = { seconds: 1, hours: 3600, days: 86400, weeks: 604800 };
      return Math.round(amount * (multipliers[unit] || 86400));
    }

    function renderReviewList(suggestions, retentionItems) {
      $("reader").innerHTML = `
        <div class="review-list">
          <h1>Review Queue</h1>
          <div class="suggestion-meta"></div>
          <div data-list="review-classifications"></div>
          <div data-list="review-retention"></div>
        </div>
      `;
      $("reader").querySelector(".suggestion-meta").textContent =
        `${suggestions.length} proposed classifications · ${retentionItems.length} retention items`;
      const list = $("reader").querySelector('[data-list="review-classifications"]');
      const retentionList = $("reader").querySelector('[data-list="review-retention"]');
      suggestions.forEach((item) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        const target = item.target_folder_path || (item.target_tags || []).join(", ");
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div class="suggestion-meta" data-field="reason"></div>
          <div class="suggestion-actions"></div>
        `;
        card.querySelector(".suggestion-title").textContent = `${item.subject} · ${item.kind}:${item.value} -> ${target}`;
        card.querySelector(".suggestion-meta").textContent = `${item.from || "(unknown)"} · ${formatDate(item.message_date)}`;
        card.querySelector(".suggestion-badge").textContent = Number(item.confidence || 0).toFixed(2);
        card.querySelector('[data-field="reason"]').textContent = item.reason || "";
        const actions = card.querySelector(".suggestion-actions");
        [
          ["approve", "Approve"],
          ["reject", "Reject"],
          ["always", "Always"],
          ["never", "Never"],
        ].forEach(([action, label]) => {
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = label;
          button.addEventListener("click", async () => {
            await postJson("/api/classifications/action", { classification_id: item.classification_id, action });
            await openReview();
          });
          actions.appendChild(button);
        });
        if (item.folder_path && item.uid) {
          const openButton = document.createElement("button");
          openButton.type = "button";
          openButton.textContent = "Open";
          openButton.addEventListener("click", async () => {
            await loadFolder(item.folder_path);
            await openMessage(item.uid);
          });
          actions.appendChild(openButton);
        }
        list.appendChild(card);
      });
      retentionItems.forEach((item) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        const review = item.requires_review ? "review required" : "review optional";
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div class="suggestion-meta" data-field="retention"></div>
          <div class="suggestion-actions"></div>
        `;
        card.querySelector(".suggestion-title").textContent = `${item.subject} · ${item.policy_name}`;
        card.querySelector(".suggestion-meta").textContent =
          `${item.from || "(unknown)"} · ${formatDate(item.message_date)}`;
        card.querySelector(".suggestion-badge").textContent = "retention";
        card.querySelector('[data-field="retention"]').textContent =
          `${item.folder_path} · ${item.hold_duration_text} hold · ${item.policy_action} · ${review} · eligible ${formatDate(item.eligible_at)}`;
        const actions = card.querySelector(".suggestion-actions");
        [
          ["acknowledge", "Acknowledge"],
          ["defer", "Snooze 7d"],
        ].forEach(([action, label]) => {
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = label;
          button.addEventListener("click", async () => {
            await applyRetentionAction(item.policy_id, item.mailbox_message_id, action);
            await openReview();
          });
          actions.appendChild(button);
        });
        if (item.folder_path && item.uid) {
          const openButton = document.createElement("button");
          openButton.type = "button";
          openButton.textContent = "Open";
          openButton.addEventListener("click", async () => {
            await loadFolder(item.folder_path);
            await openMessage(item.uid);
          });
          actions.appendChild(openButton);
        }
        retentionList.appendChild(card);
      });
      if (!suggestions.length && !retentionItems.length) {
        list.innerHTML = `<div class="empty">No items waiting for review</div>`;
      }
    }

    function renderWorkbench(groups) {
      $("reader").innerHTML = `
        <div class="review-list">
          <h1>Sort Workbench</h1>
          <div class="suggestion-meta"></div>
          <div data-list="workbench-groups"></div>
        </div>
      `;
      $("reader").querySelector(".suggestion-meta").textContent =
        `${groups.length} grouped suggestion sets · batch actions remain internal to MILLIE`;
      const list = $("reader").querySelector('[data-list="workbench-groups"]');
      if (!groups.length) {
        list.innerHTML = `<div class="empty">No proposed sorting groups are waiting</div>`;
        return;
      }
      groups.forEach((group) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta" data-field="group-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div data-list="workbench-samples"></div>
          <div class="suggestion-actions"></div>
        `;
        card.querySelector(".suggestion-title").textContent =
          `${group.target} · ${group.sender_domain} · ${group.message_year}`;
        card.querySelector('[data-field="group-meta"]').textContent =
          `${group.current_folder} · avg confidence ${Number(group.avg_confidence || 0).toFixed(2)} · ${group.count} messages`;
        card.querySelector(".suggestion-badge").textContent = String(group.count);
        const samples = card.querySelector('[data-list="workbench-samples"]');
        (group.samples || []).forEach((item) => {
          const row = document.createElement("div");
          row.className = "source-row";
          row.innerHTML = `
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta"></div>
            </div>
            <button class="review-button" type="button">Open</button>
          `;
          row.querySelector(".suggestion-title").textContent = item.subject || "(no subject)";
          row.querySelector(".suggestion-meta").textContent =
            `${item.from || "(unknown)"} · ${formatDate(item.message_date)} · ${item.folder_path || ""}`;
          const openButton = row.querySelector("button");
          if (item.folder_path && item.uid) {
            openButton.addEventListener("click", async () => {
              await loadFolder(item.folder_path);
              await openMessage(item.uid);
            });
          } else {
            openButton.disabled = true;
          }
          samples.appendChild(row);
        });
        const actions = card.querySelector(".suggestion-actions");
        [
          ["approve", "Approve group"],
          ["reject", "Reject group"],
          ["always", "Always"],
          ["never", "Never"],
        ].forEach(([action, label]) => {
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = label;
          button.addEventListener("click", async () => {
            await applyWorkbenchAction(group.classification_ids || [], action);
            await openWorkbench();
          });
          actions.appendChild(button);
        });
        list.appendChild(card);
      });
    }

    function renderSearchList(results, criteria = {}) {
      $("reader").innerHTML = `
        <div class="review-list">
          <h1>Search</h1>
          <form class="filter-grid" id="searchFilters">
            <label>Text
              <input name="q" type="search">
            </label>
            <label>Folder
              <input name="folder" type="text">
            </label>
            <label>From
              <input name="from" type="text">
            </label>
            <label>Source
              <input name="source" type="text">
            </label>
            <label>Attachments
              <select name="has_attachments">
                <option value="">Any</option>
                <option value="true">With</option>
                <option value="false">Without</option>
              </select>
            </label>
            <label>Since
              <input name="since" type="date">
            </label>
            <label>Until
              <input name="until" type="date">
            </label>
            <label>Limit
              <select name="limit">
                <option value="25">25</option>
                <option value="50">50</option>
                <option value="100">100</option>
                <option value="250">250</option>
                <option value="500">500</option>
              </select>
            </label>
            <button class="review-button" type="submit">Run</button>
          </form>
          <div class="suggestion-meta">${results.length} results</div>
          <div data-list="search-results"></div>
        </div>
      `;
      const form = $("reader").querySelector("#searchFilters");
      ["q", "folder", "from", "source", "has_attachments", "since", "until", "limit"].forEach((name) => {
        if (criteria[name]) form.elements[name].value = criteria[name];
      });
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        await runGlobalSearch(Object.fromEntries(new FormData(form).entries()));
      });
      const list = $("reader").querySelector('[data-list="search-results"]');
      if (!results.length) {
        list.innerHTML = `<div class="empty">No matching messages</div>`;
        return;
      }
      results.forEach((item) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div class="suggestion-meta" data-field="preview"></div>
          <div class="suggestion-actions"></div>
        `;
        card.querySelector(".suggestion-title").textContent = text(item.subject, "(no subject)");
        card.querySelector(".suggestion-meta").textContent =
          `${item.from || "(unknown)"} · ${formatDate(item.message_date)} · ${item.folder_path}`;
        card.querySelector(".suggestion-badge").textContent = item.source_type || "source";
        card.querySelector('[data-field="preview"]').textContent =
          `${item.source || ""} · ${item.body_preview || formatSize(item.size)}`;
        const actions = card.querySelector(".suggestion-actions");
        const openButton = document.createElement("button");
        openButton.type = "button";
        openButton.textContent = "Open";
        openButton.addEventListener("click", async () => {
          await loadFolder(item.folder_path);
          await openMessage(item.uid);
        });
        actions.appendChild(openButton);
        list.appendChild(card);
      });
    }

    function renderPolicyList(policies) {
      $("reader").innerHTML = `
        <div class="review-list">
          <h1>Retention Policies</h1>
          <div class="suggestion-meta"></div>
          <div data-list="retention-policies"></div>
        </div>
      `;
      $("reader").querySelector(".suggestion-meta").textContent =
        `${policies.length} policies · provider mail is not changed by these controls`;
      const list = $("reader").querySelector('[data-list="retention-policies"]');
      if (!policies.length) {
        list.innerHTML = `<div class="empty">No retention policies</div>`;
        return;
      }
      policies.forEach((item) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        const parts = durationParts(item.hold_duration_seconds);
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta" data-field="policy-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div class="policy-edit">
            <label>Name
              <input data-field="policy-name" type="text">
            </label>
            <label>Hold
              <span class="duration-group">
                <input data-field="policy-duration" type="number" min="1" step="1">
                <select data-field="policy-duration-unit">
                  <option value="hours">hours</option>
                  <option value="days">days</option>
                  <option value="weeks">weeks</option>
                  <option value="seconds">seconds</option>
                </select>
              </span>
            </label>
            <label>Action
              <select data-field="policy-action">
                <option value="no_action">no action</option>
                <option value="hide_from_default_views">hide from defaults</option>
                <option value="expire_internal_copy">expire internal copy</option>
                <option value="delete_internal_copy">delete internal copy</option>
              </select>
            </label>
            <label>Status
              <select data-field="policy-status">
                <option value="proposed">proposed</option>
                <option value="active">active</option>
                <option value="disabled">disabled</option>
                <option value="retired">retired</option>
              </select>
            </label>
            <label class="check-label">
              <input data-field="policy-review" type="checkbox">
              review
            </label>
          </div>
          <div class="suggestion-actions"></div>
        `;
        card.querySelector(".suggestion-title").textContent = item.policy_name || item.id;
        card.querySelector('[data-field="policy-meta"]').textContent =
          `${item.target_kind}:${item.target_value} · ${item.hold_duration_text} · ${item.policy_action}`;
        card.querySelector(".suggestion-badge").textContent = item.status;
        card.querySelector('[data-field="policy-name"]').value = item.policy_name || "";
        card.querySelector('[data-field="policy-duration"]').value = parts.value;
        card.querySelector('[data-field="policy-duration-unit"]').value = parts.unit;
        card.querySelector('[data-field="policy-action"]').value = item.policy_action || "no_action";
        card.querySelector('[data-field="policy-status"]').value = item.status || "proposed";
        card.querySelector('[data-field="policy-review"]').checked = Boolean(item.requires_review);
        const actions = card.querySelector(".suggestion-actions");
        const saveButton = document.createElement("button");
        saveButton.type = "button";
        saveButton.textContent = "Save";
        saveButton.addEventListener("click", async () => {
          await savePolicy(card, item.id);
          await openPolicies();
        });
        actions.appendChild(saveButton);
        if (item.status !== "active") {
          const activateButton = document.createElement("button");
          activateButton.type = "button";
          activateButton.textContent = "Activate";
          activateButton.addEventListener("click", async () => {
            await applyPolicyAction(item.id, "activate");
            await openPolicies();
          });
          actions.appendChild(activateButton);
        }
        if (item.status !== "disabled") {
          const disableButton = document.createElement("button");
          disableButton.type = "button";
          disableButton.textContent = "Disable";
          disableButton.addEventListener("click", async () => {
            await applyPolicyAction(item.id, "disable");
            await openPolicies();
          });
          actions.appendChild(disableButton);
        }
        list.appendChild(card);
      });
    }

    function renderRuleList(rules) {
      $("reader").innerHTML = `
        <div class="review-list">
          <h1>Brain Rules</h1>
          <div class="suggestion-meta">${rules.length} rules</div>
          <div data-list="brain-rules"></div>
        </div>
      `;
      const list = $("reader").querySelector('[data-list="brain-rules"]');
      if (!rules.length) {
        list.innerHTML = `<div class="empty">No brain rules</div>`;
        return;
      }
      rules.forEach((item) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta" data-field="rule-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div class="policy-edit">
            <label>Name
              <input data-field="rule-name" type="text">
            </label>
            <label>Priority
              <input data-field="rule-priority" type="number" step="1">
            </label>
            <label>Status
              <select data-field="rule-status">
                <option value="proposed">proposed</option>
                <option value="active">active</option>
                <option value="disabled">disabled</option>
                <option value="superseded">superseded</option>
                <option value="retired">retired</option>
              </select>
            </label>
          </div>
          <pre class="json-box" data-field="rule-json"></pre>
          <div class="suggestion-actions"></div>
        `;
        card.querySelector(".suggestion-title").textContent = item.rule_name || item.id;
        card.querySelector('[data-field="rule-meta"]').textContent =
          `${item.rule_type} · ${item.rule_source} · evidence ${item.evidence_count} · confidence ${Number(item.confidence || 0).toFixed(2)}`;
        card.querySelector(".suggestion-badge").textContent = item.status;
        card.querySelector('[data-field="rule-name"]').value = item.rule_name || "";
        card.querySelector('[data-field="rule-priority"]').value = item.priority || 0;
        card.querySelector('[data-field="rule-status"]').value = item.status || "proposed";
        card.querySelector('[data-field="rule-json"]').textContent =
          JSON.stringify({ condition: item.condition, action: item.rule_action }, null, 2);
        const actions = card.querySelector(".suggestion-actions");
        const saveButton = document.createElement("button");
        saveButton.type = "button";
        saveButton.textContent = "Save";
        saveButton.addEventListener("click", async () => {
          await saveRule(card, item.id);
          await openRules();
        });
        actions.appendChild(saveButton);
        [
          ["activate", "Activate"],
          ["disable", "Disable"],
          ["retire", "Retire"],
        ].forEach(([action, label]) => {
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = label;
          button.addEventListener("click", async () => {
            await applyRuleAction(item.id, action);
            await openRules();
          });
          actions.appendChild(button);
        });
        list.appendChild(card);
      });
    }

    function renderProposalReview(data, selectedStatus = "proposed,disabled") {
      const proposals = data.proposals || [];
      const counts = data.status_counts || {};
      $("reader").innerHTML = `
        <div class="review-list">
          <h1>Proposal Review</h1>
          <div class="suggestion-meta"></div>
          <div class="metric-grid" data-list="proposal-metrics"></div>
          <div class="proposal-controls">
            <label>Status
              <select id="proposalStatus">
                <option value="proposed,disabled">Open</option>
                <option value="proposed">Proposed</option>
                <option value="active">Active</option>
                <option value="disabled">Disabled</option>
                <option value="retired">Retired</option>
                <option value="all">All</option>
              </select>
            </label>
            <label>Preview
              <select id="proposalPreviewLimit">
                <option value="25">25</option>
                <option value="50">50</option>
                <option value="100">100</option>
                <option value="250">250</option>
              </select>
            </label>
            <button id="proposalRefresh" class="review-button" type="button">Refresh</button>
            <button id="proposalSelectAll" class="review-button" type="button">Select all</button>
            <button id="proposalActivate" class="review-button" type="button">Activate</button>
            <button id="proposalDisable" class="review-button" type="button">Disable</button>
            <button id="proposalRetire" class="review-button" type="button">Retire</button>
            <button id="proposalObserve" class="review-button" type="button">Observe</button>
          </div>
          <pre id="proposalPreviewOutput" class="output-box" hidden></pre>
          <div data-list="proposal-review"></div>
        </div>
      `;
      $("reader").querySelector(".suggestion-meta").textContent =
        `${proposals.length} visible proposals · ${formatCount(data.total)} saved total · ${statusCounts(counts)}`;
      $("proposalStatus").value = selectedStatus;
      const metrics = $("reader").querySelector('[data-list="proposal-metrics"]');
      addMetric(metrics, "Proposed", counts.proposed || 0, "waiting for activation");
      addMetric(metrics, "Active", counts.active || 0, "available to brain workflows");
      addMetric(metrics, "Disabled", counts.disabled || 0, "kept for later");
      addMetric(metrics, "Retired", counts.retired || 0, "suppressed proposals");
      $("proposalStatus").addEventListener("change", () => openProposals($("proposalStatus").value).catch(showError));
      $("proposalRefresh").addEventListener("click", () => openProposals().catch(showError));
      $("proposalSelectAll").addEventListener("click", () => {
        $("reader").querySelectorAll("[data-proposal-select]").forEach((input) => {
          input.checked = true;
        });
      });
      $("proposalActivate").addEventListener("click", () => applyProposalBatchAction("activate").catch(showError));
      $("proposalDisable").addEventListener("click", () => applyProposalBatchAction("disable").catch(showError));
      $("proposalRetire").addEventListener("click", () => applyProposalBatchAction("retire").catch(showError));
      $("proposalObserve").addEventListener("click", () => runProposalObservePreview().catch(showError));
      const list = $("reader").querySelector('[data-list="proposal-review"]');
      if (!proposals.length) {
        list.innerHTML = `<div class="empty">No saved proposals match this filter</div>`;
        return;
      }
      proposals.forEach((item) => {
        const proposal = item.proposal || {};
        const context = item.proposal_context || {};
        const card = document.createElement("div");
        card.className = "suggestion-card";
        card.innerHTML = `
          <div class="proposal-head">
            <input class="proposal-check" data-proposal-select type="checkbox">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta" data-field="proposal-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div class="suggestion-meta" data-field="proposal-context"></div>
          <div data-list="proposal-samples"></div>
          <div class="suggestion-actions"></div>
          <pre class="json-box"></pre>
        `;
        const checkbox = card.querySelector("[data-proposal-select]");
        checkbox.value = item.id;
        checkbox.setAttribute("aria-label", `Select ${item.rule_name || item.id}`);
        card.querySelector(".suggestion-title").textContent = item.rule_name || item.id;
        card.querySelector('[data-field="proposal-meta"]').textContent =
          `${item.proposal_type || item.rule_type} · ${item.rule_source} · evidence ${formatCount(item.evidence_count)} · confidence ${Number(item.confidence || 0).toFixed(2)} · target ${text(item.proposal_target, "n/a")}`;
        card.querySelector(".suggestion-badge").textContent = item.status;
        card.querySelector('[data-field="proposal-context"]').textContent =
          `senders ${proposalListText(context.sender_domains)} · sources ${proposalListText(context.source_folders)} · years ${proposalListText(context.message_years)}`;
        const samples = card.querySelector('[data-list="proposal-samples"]');
        (item.proposal_samples || []).slice(0, 4).forEach((sample) => {
          const row = document.createElement("div");
          row.className = "source-row";
          row.innerHTML = `
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta"></div>
            </div>
            <button class="review-button" type="button">Open</button>
          `;
          row.querySelector(".suggestion-title").textContent = sample.subject || "(no subject)";
          row.querySelector(".suggestion-meta").textContent =
            `${sample.from || "(unknown)"} · ${formatDate(sample.message_date)} · ${sample.folder_path || ""}`;
          const openButton = row.querySelector("button");
          if (sample.folder_path && sample.uid) {
            openButton.addEventListener("click", async () => {
              await loadFolder(sample.folder_path);
              await openMessage(sample.uid);
            });
          } else {
            openButton.disabled = true;
          }
          samples.appendChild(row);
        });
        const actions = card.querySelector(".suggestion-actions");
        [
          ["activate", "Activate"],
          ["disable", "Disable"],
          ["retire", "Retire"],
        ].forEach(([action, label]) => {
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = label;
          button.disabled =
            (action === "activate" && item.status === "active") ||
            (action === "disable" && item.status === "disabled") ||
            (action === "retire" && item.status === "retired");
          button.addEventListener("click", () => applyProposalBatchAction(action, [item.id]).catch(showError));
          actions.appendChild(button);
        });
        card.querySelector(".json-box").textContent = JSON.stringify(
          {
            condition: item.condition,
            action: item.rule_action,
            proposal_context: proposal.llm_context || item.proposal_context,
          },
          null,
          2,
        );
        list.appendChild(card);
      });
    }

    function proposalListText(value) {
      if (Array.isArray(value)) return value.filter(Boolean).join(", ") || "none";
      return text(value, "none");
    }

    function renderLearningMetrics(data, ruleCandidates = [], taxonomyProposals = []) {
      const summary = data.summary || {};
      $("reader").innerHTML = `
        <div class="review-list">
          <h1>Learning Metrics</h1>
          <div class="suggestion-meta"></div>
          <div class="metric-grid" data-list="learning-metrics"></div>
          <div data-list="rule-candidates"></div>
          <div data-list="taxonomy-proposals"></div>
          <div data-list="learning-targets"></div>
          <div data-list="learning-attention"></div>
          <div data-list="learning-top-rules"></div>
        </div>
      `;
      $("reader").querySelector(".suggestion-meta").textContent =
        "Read-only learning health from classifications, feedback, and brain rules";
      const metrics = $("reader").querySelector('[data-list="learning-metrics"]');
      addMetric(metrics, "Proposed", summary.proposed || 0, `approved ${formatCount(summary.approved)} · rejected ${formatCount(summary.rejected)}`);
      addMetric(metrics, "Active rules", summary.active_rules || 0, `${formatCount(summary.rule_total)} total rules`);
      addMetric(metrics, "Rule attention", summary.attention_rules || 0, "negative, weak, or never matched");
      addMetric(metrics, "Feedback", summary.feedback_total || 0, "review events recorded");
      renderRuleCandidates(ruleCandidates);
      renderTaxonomyProposals(taxonomyProposals);
      renderLearningTargets(data.target_breakdown || []);
      renderLearningRules("learning-attention", "Rules Needing Attention", data.attention_rules || []);
      renderLearningRules("learning-top-rules", "Top Active Rules", data.top_rules || []);
    }

    async function applyRuleCandidateAction(candidateId, action) {
      await postJson("/api/rules/candidates/action", { candidate_id: candidateId, action });
      await openLearningMetrics();
    }

    async function applyTaxonomyProposalAction(proposalId, action) {
      await postJson("/api/taxonomy/proposals/action", { proposal_id: proposalId, action });
      await openLearningMetrics();
    }

    async function runTaxonomyAssistant() {
      const box = $("taxonomyAssistantOutput");
      box.hidden = false;
      box.textContent = "Requesting aggregate-only taxonomy review...";
      const data = await postJson("/api/taxonomy/assistant", {
        tier: $("taxonomyAssistantTier").value,
        limit: $("taxonomyAssistantLimit").value,
      });
      const assistant = data.assistant || {};
      const recommendations = assistant.recommendations || [];
      const lines = [
        `provider ${data.provider || "none"} · tier ${data.tier || "main"} · model ${data.model || "n/a"} · proposals ${formatCount(data.proposal_count)}`,
        "",
        assistant.summary || "(no summary)",
        "",
        ...recommendations.map((item) => {
          const risks = (item.risks || []).length ? ` · risks ${(item.risks || []).join("; ")}` : "";
          return `${item.recommendation}: ${item.target} -> ${item.suggested_target} · confidence ${Number(item.confidence || 0).toFixed(2)} · ${item.rationale}${risks}`;
        }),
        "",
        ...((assistant.safety_notes || []).map((note) => `safety: ${note}`)),
      ];
      box.textContent = lines.filter((line) => line !== undefined).join("\\n");
    }

    function renderRuleCandidates(candidates) {
      const list = $("reader").querySelector('[data-list="rule-candidates"]');
      const header = document.createElement("div");
      header.className = "suggestion-meta";
      header.textContent = `Rule candidates: ${candidates.length}`;
      list.appendChild(header);
      if (!candidates.length) {
        list.innerHTML += `<div class="empty">No rule candidates found from current classification evidence</div>`;
        return;
      }
      candidates.forEach((item) => {
        const backtest = item.backtest || {};
        const card = document.createElement("div");
        card.className = "suggestion-card";
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta" data-field="candidate-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div data-list="candidate-samples"></div>
          <div class="suggestion-actions"></div>
          <pre class="json-box"></pre>
        `;
        card.querySelector(".suggestion-title").textContent = item.rule_name || item.id;
        card.querySelector('[data-field="candidate-meta"]').textContent =
          `evidence ${formatCount(item.evidence_count)} · matches ${formatCount(backtest.matched_messages)} · existing ${formatCount(backtest.existing_suggestions)} · conflicts ${formatCount(backtest.conflicting_suggestions)}`;
        card.querySelector(".suggestion-badge").textContent = Number(item.confidence || 0).toFixed(2);
        const samples = card.querySelector('[data-list="candidate-samples"]');
        (backtest.samples || item.samples || []).forEach((sample) => {
          const row = document.createElement("div");
          row.className = "source-row";
          row.innerHTML = `
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta"></div>
            </div>
            <button class="review-button" type="button">Open</button>
          `;
          row.querySelector(".suggestion-title").textContent = sample.subject || "(no subject)";
          row.querySelector(".suggestion-meta").textContent =
            `${sample.from || "(unknown)"} · ${formatDate(sample.message_date)} · ${sample.folder_path || ""}`;
          const openButton = row.querySelector("button");
          if (sample.folder_path && sample.uid) {
            openButton.addEventListener("click", async () => {
              await loadFolder(sample.folder_path);
              await openMessage(sample.uid);
            });
          } else {
            openButton.disabled = true;
          }
          samples.appendChild(row);
        });
        const actions = card.querySelector(".suggestion-actions");
        const seedButton = document.createElement("button");
        seedButton.type = "button";
        seedButton.textContent = item.existing_rule_status ? "Refresh proposal" : "Seed proposal";
        seedButton.addEventListener("click", () => applyRuleCandidateAction(item.id, "seed").catch(showError));
        actions.appendChild(seedButton);
        const dismissButton = document.createElement("button");
        dismissButton.type = "button";
        dismissButton.textContent = "Dismiss";
        dismissButton.addEventListener("click", () => applyRuleCandidateAction(item.id, "dismiss").catch(showError));
        actions.appendChild(dismissButton);
        if (item.existing_rule_status && item.existing_rule_status !== "active") {
          const activateButton = document.createElement("button");
          activateButton.type = "button";
          activateButton.textContent = "Activate rule";
          activateButton.addEventListener("click", async () => {
            await applyRuleAction(item.rule_id, "activate");
            await openLearningMetrics();
          });
          actions.appendChild(activateButton);
        }
        card.querySelector(".json-box").textContent =
          JSON.stringify({ condition: item.condition, action: item.rule_action }, null, 2);
        list.appendChild(card);
      });
    }

    function renderTaxonomyProposals(proposals) {
      const list = $("reader").querySelector('[data-list="taxonomy-proposals"]');
      const header = document.createElement("div");
      header.className = "suggestion-meta";
      header.textContent = `Taxonomy proposals: ${proposals.length}`;
      list.appendChild(header);
      const controls = document.createElement("div");
      controls.className = "proposal-controls";
      controls.innerHTML = `
        <label>Provider
          <select id="taxonomyAssistantTier">
            <option value="main">main</option>
            <option value="second">second</option>
            <option value="third">third</option>
          </select>
        </label>
        <label>Limit
          <select id="taxonomyAssistantLimit">
            <option value="12">12</option>
            <option value="25">25</option>
            <option value="50">50</option>
          </select>
        </label>
        <button id="taxonomyAssistantRun" class="review-button" type="button">Ask LLM</button>
      `;
      list.appendChild(controls);
      const output = document.createElement("pre");
      output.id = "taxonomyAssistantOutput";
      output.className = "output-box";
      output.hidden = true;
      list.appendChild(output);
      $("taxonomyAssistantRun").addEventListener("click", () => {
        runTaxonomyAssistant().catch((error) => {
          const box = $("taxonomyAssistantOutput");
          box.hidden = false;
          box.textContent = error.message || String(error);
        });
      });
      if (!proposals.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "No taxonomy proposals found from current evidence";
        list.appendChild(empty);
        return;
      }
      proposals.forEach((item) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta" data-field="taxonomy-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div class="suggestion-meta" data-field="taxonomy-context"></div>
          <div class="suggestion-actions"></div>
          <pre class="json-box"></pre>
        `;
        card.querySelector(".suggestion-title").textContent = item.rule_name || item.target;
        card.querySelector('[data-field="taxonomy-meta"]').textContent =
          `evidence ${formatCount(item.evidence_count)} · senders ${(item.sender_domains || []).join(", ") || "unknown"}`;
        card.querySelector('[data-field="taxonomy-context"]').textContent =
          `sources ${(item.source_folders || []).join(", ") || "unknown"} · years ${(item.message_years || []).join(", ") || "unknown"}`;
        card.querySelector(".suggestion-badge").textContent = Number(item.confidence || 0).toFixed(2);
        const actions = card.querySelector(".suggestion-actions");
        const seedButton = document.createElement("button");
        seedButton.type = "button";
        seedButton.textContent = item.existing_rule_status ? "Refresh proposal" : "Seed proposal";
        seedButton.addEventListener("click", () => applyTaxonomyProposalAction(item.id, "seed").catch(showError));
        actions.appendChild(seedButton);
        const dismissButton = document.createElement("button");
        dismissButton.type = "button";
        dismissButton.textContent = "Dismiss";
        dismissButton.addEventListener("click", () => applyTaxonomyProposalAction(item.id, "dismiss").catch(showError));
        actions.appendChild(dismissButton);
        if (item.existing_rule_status && item.existing_rule_status !== "active") {
          const activateButton = document.createElement("button");
          activateButton.type = "button";
          activateButton.textContent = "Activate rule";
          activateButton.addEventListener("click", async () => {
            await applyRuleAction(item.rule_id, "activate");
            await openLearningMetrics();
          });
          actions.appendChild(activateButton);
        }
        card.querySelector(".json-box").textContent =
          JSON.stringify({ llm_context: item.llm_context, action: item.rule_action }, null, 2);
        list.appendChild(card);
      });
    }

    function renderLearningTargets(targets) {
      const list = $("reader").querySelector('[data-list="learning-targets"]');
      const header = document.createElement("div");
      header.className = "suggestion-meta";
      header.textContent = `${targets.length} target/status buckets`;
      list.appendChild(header);
      if (!targets.length) {
        list.innerHTML += `<div class="empty">No classification targets recorded yet</div>`;
        return;
      }
      targets.forEach((item) => {
        const card = document.createElement("div");
        card.className = "source-row";
        const target = item.target_folder_path || (item.target_tags || []).join(", ") || `${item.kind}:${item.value}`;
        card.innerHTML = `
          <div>
            <div class="suggestion-title"></div>
            <div class="suggestion-meta"></div>
          </div>
          <span class="suggestion-badge"></span>
        `;
        card.querySelector(".suggestion-title").textContent = `${item.kind}:${item.value} -> ${target}`;
        card.querySelector(".suggestion-meta").textContent =
          `${item.status} · avg confidence ${Number(item.avg_confidence || 0).toFixed(2)}`;
        card.querySelector(".suggestion-badge").textContent = formatCount(item.count);
        list.appendChild(card);
      });
    }

    function renderLearningRules(containerName, title, rules) {
      const list = $("reader").querySelector(`[data-list="${containerName}"]`);
      const header = document.createElement("div");
      header.className = "suggestion-meta";
      header.textContent = `${title}: ${rules.length}`;
      list.appendChild(header);
      if (!rules.length) return;
      rules.forEach((item) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta" data-field="rule-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <pre class="json-box"></pre>
        `;
        card.querySelector(".suggestion-title").textContent = item.rule_name || item.id;
        card.querySelector('[data-field="rule-meta"]').textContent =
          `${item.rule_type} · evidence ${formatCount(item.evidence_count)} · +${formatCount(item.positive_feedback_count)} / -${formatCount(item.negative_feedback_count)} · last matched ${formatDate(item.last_matched_at)}`;
        card.querySelector(".suggestion-badge").textContent = item.status;
        card.querySelector(".json-box").textContent =
          JSON.stringify({ condition: item.condition, action: item.rule_action }, null, 2);
        list.appendChild(card);
      });
    }

    function renderInternalApply(status) {
      $("reader").innerHTML = `
        <div class="review-list">
          <h1>Internal Apply</h1>
          <div class="suggestion-meta"></div>
          <div class="suggestion-card">
            <div class="suggestion-top">
              <div>
                <div class="suggestion-title">Pending internal actions</div>
                <div class="suggestion-meta" data-field="apply-meta"></div>
              </div>
              <span class="suggestion-badge"></span>
            </div>
            <div class="filter-grid">
              <label>Target
                <select id="applyTarget">
                  <option value="both">both</option>
                  <option value="suggestions">suggestions</option>
                  <option value="retention">retention</option>
                </select>
              </label>
              <label>Limit
                <select id="applyLimit">
                  <option value="25">25</option>
                  <option value="50">50</option>
                  <option value="100">100</option>
                  <option value="250">250</option>
                  <option value="500">500</option>
                </select>
              </label>
            </div>
            <div class="suggestion-actions">
              <button id="applyDryRun" type="button">Dry run</button>
              <button id="applyExecute" type="button">Execute</button>
            </div>
          </div>
          <div data-list="apply-output"></div>
        </div>
      `;
      $("reader").querySelector(".suggestion-meta").textContent =
        `level ${status.automation_level} · suggestions ${status.approved_suggestions_pending} · retention ${status.retention_pending}`;
      $("reader").querySelector(".suggestion-badge").textContent =
        status.auto_internal_allowed ? "allowed" : "guarded";
      $("reader").querySelector('[data-field="apply-meta"]').textContent =
        `${status.approved_suggestions_pending} approved suggestions · ${status.retention_pending} retention decisions`;
      $("applyLimit").value = String(status.limit || 100);
      $("applyDryRun").addEventListener("click", () => runInternalApply("dry_run").catch(showError));
      $("applyExecute").addEventListener("click", () => runInternalApply("execute").catch(showError));
    }

    function renderOperations(data) {
      const summary = data.summary || {};
      const queues = data.queues || {};
      const internalApply = queues.internal_apply || {};
      $("reader").innerHTML = `
        <div class="review-list">
          <h1>Ops Dashboard</h1>
          <div class="suggestion-meta"></div>
          <div class="metric-grid" data-list="ops-metrics"></div>
          <div class="suggestion-card">
            <div class="suggestion-top">
              <div>
                <div class="suggestion-title">One-off maintenance</div>
                <div class="suggestion-meta" data-field="ops-actions-meta"></div>
              </div>
              <span class="suggestion-badge"></span>
            </div>
            <div class="filter-grid">
              <label>Limit
                <select id="opsLimit">
                  <option value="100">100</option>
                  <option value="500">500</option>
                  <option value="1000">1000</option>
                  <option value="2500">2500</option>
                  <option value="5000">5000</option>
                </select>
              </label>
              <label>Fetch batch
                <select id="opsBatch">
                  <option value="10">10</option>
                  <option value="25">25</option>
                  <option value="50">50</option>
                  <option value="100">100</option>
                </select>
              </label>
              <label>Commit every
                <select id="opsCommitEvery">
                  <option value="50">50</option>
                  <option value="100">100</option>
                  <option value="250">250</option>
                  <option value="500">500</option>
                </select>
              </label>
              <button class="review-button" type="button" data-operation="refresh">Refresh</button>
            </div>
            <div class="suggestion-actions">
              <button type="button" data-operation="live_sync_once">Sync once</button>
              <button type="button" data-operation="live_upkeep_once">Upkeep once</button>
              <button type="button" data-operation="dedupe_report">Dedupe report</button>
              <button type="button" data-operation="dedupe_backfill">Dedupe backfill</button>
            </div>
          </div>
          <div data-list="operation-output"></div>
          <div data-list="ops-accounts"></div>
          <div data-list="ops-runs"></div>
          <div data-list="ops-unmatched"></div>
        </div>
      `;
      $("reader").querySelector(".suggestion-meta").textContent =
        `level ${data.automation_level} · live sources ${formatCount(summary.live_source_count)} · service mailbox ${formatCount(summary.mailbox_message_count)} messages`;
      $("reader").querySelector(".suggestion-badge").textContent =
        data.auto_internal_allowed ? "auto internal" : "guarded";
      $("reader").querySelector('[data-field="ops-actions-meta"]').textContent =
        "Local sync/import and internal maintenance only; no remote provider purge runs from here";
      const metrics = $("reader").querySelector('[data-list="ops-metrics"]');
      addMetric(metrics, "Archive messages", summary.message_count, `${formatCount(summary.folder_count)} source folders`);
      addMetric(metrics, "Service mailbox", summary.mailbox_message_count, `${formatCount(summary.mailbox_folder_count)} visible folders`);
      addMetric(metrics, "Review queue", queues.classifications?.proposed || 0, `approved ${formatCount(queues.classifications?.approved)}`);
      addMetric(metrics, "Internal apply", Number(internalApply.approved_suggestions_pending || 0) + Number(internalApply.retention_pending || 0), "pending internal changes");
      addMetric(metrics, "Sync health", data.sync_health?.counts?.failed || 0, `${formatCount(data.sync_health?.counts?.stale)} stale · ${formatCount(data.sync_health?.counts?.ok)} ok`);
      renderOperationsAccounts(data.accounts || []);
      renderOperationsRuns(data.automation_runs || []);
      renderOperationsUnmatched(data.unmatched_sources || [], queues);
      $("reader").querySelectorAll("[data-operation]").forEach((button) => {
        button.addEventListener("click", () => {
          const action = button.dataset.operation;
          if (action === "refresh") {
            openOperations().catch(showError);
            return;
          }
          runOperation(action).catch(showError);
        });
      });
    }

    function addMetric(container, label, value, meta) {
      const card = document.createElement("div");
      card.className = "metric-card";
      card.innerHTML = `
        <div class="metric-label"></div>
        <div class="metric-value"></div>
        <div class="metric-meta"></div>
      `;
      card.querySelector(".metric-label").textContent = label;
      card.querySelector(".metric-value").textContent = formatCount(value);
      card.querySelector(".metric-meta").textContent = meta || "";
      container.appendChild(card);
    }

    function renderOperationsAccounts(accounts) {
      const list = $("reader").querySelector('[data-list="ops-accounts"]');
      const header = document.createElement("div");
      header.className = "suggestion-meta";
      header.textContent = `${accounts.length} configured mail accounts`;
      list.appendChild(header);
      if (!accounts.length) {
        list.innerHTML += `<div class="empty">No mail accounts are configured in settings</div>`;
        return;
      }
      accounts.forEach((account) => {
        const sources = account.sources || [];
        const card = document.createElement("div");
        card.className = "suggestion-card";
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta" data-field="account-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div class="suggestion-actions">
            <button type="button" data-operation="sync-account">Sync account</button>
          </div>
          <div data-list="account-sources"></div>
        `;
        card.querySelector(".suggestion-title").textContent =
          `${text(account.display_name, account.email_address || account.id)} · ${text(account.account_type, "mail")}`;
        card.querySelector('[data-field="account-meta"]').textContent =
          `${account.host || "(no host)"}:${account.port || ""} · ${account.auth_method || "auth"} · credential ${account.credential_status}`;
        const badge = card.querySelector(".suggestion-badge");
        badge.textContent = account.health_state || (account.enabled ? "enabled" : "disabled");
        badge.className = `suggestion-badge ${healthClass(account.health_state)}`;
        card.querySelector('[data-operation="sync-account"]').addEventListener("click", () => {
          runOperation("live_sync_once", { account: account.email_address || account.username || account.id }).catch(showError);
        });
        const sourceList = card.querySelector('[data-list="account-sources"]');
        if (sources.length) {
          sources.slice(0, 12).forEach((source) => {
            sourceList.appendChild(sourceRow(source, account));
          });
          if (sources.length > 12) {
            const extra = document.createElement("div");
            extra.className = "suggestion-meta";
            extra.textContent = `${sources.length - 12} more source folders not shown`;
            sourceList.appendChild(extra);
          }
        } else {
          sourceList.innerHTML = `<div class="suggestion-meta">No imported source matched this account yet</div>`;
        }
        list.appendChild(card);
      });
    }

    function renderOperationsRuns(runs) {
      const list = $("reader").querySelector('[data-list="ops-runs"]');
      const header = document.createElement("div");
      header.className = "suggestion-meta";
      header.textContent = `${runs.length} recent automation runs`;
      list.appendChild(header);
      if (!runs.length) {
        list.innerHTML += `<div class="empty">No automation runs recorded yet</div>`;
        return;
      }
      runs.forEach((run) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta" data-field="run-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <pre class="json-box"></pre>
        `;
        card.querySelector(".suggestion-title").textContent = `${run.run_type} · ${formatDate(run.started_at || run.created_at)}`;
        card.querySelector('[data-field="run-meta"]').textContent =
          `${run.trigger_source} · scanned ${formatCount(run.messages_scanned)} · suggestions ${formatCount(run.suggestions_created)} · actions ${formatCount(run.actions_applied)}${run.error_message ? " · " + run.error_message : ""}`;
        card.querySelector(".suggestion-badge").textContent = run.status;
        card.querySelector(".suggestion-badge").className = `suggestion-badge ${healthClass(run.status === "failed" ? "failed" : run.status === "running" ? "running" : "ok")}`;
        card.querySelector(".json-box").textContent = JSON.stringify(run.metadata || {}, null, 2);
        list.appendChild(card);
      });
    }

    function renderOperationsUnmatched(sources, queues) {
      const list = $("reader").querySelector('[data-list="ops-unmatched"]');
      const header = document.createElement("div");
      header.className = "suggestion-meta";
      header.textContent = `Queues: classifications ${statusCounts(queues.classifications)} · unsubscribes ${statusCounts(queues.unsubscribes)} · retention ${statusCounts(queues.retention_policies)}`;
      list.appendChild(header);
      if (!sources.length) return;
      const card = document.createElement("div");
      card.className = "suggestion-card";
      card.innerHTML = `
        <div class="suggestion-top">
          <div class="suggestion-title">Unmatched or archive-only sources</div>
          <span class="suggestion-badge"></span>
        </div>
        <pre class="output-box"></pre>
      `;
      card.querySelector(".suggestion-badge").textContent = String(sources.length);
      card.querySelector(".output-box").textContent = sources.map(sourceLine).join("\n");
      list.appendChild(card);
    }

    function sourceLine(source) {
      const health = source.sync_health || {};
      const cursor = source.last_cursor_at ? ` · cursor ${formatDate(source.last_cursor_at)}` : "";
      const newest = source.newest_message_at ? ` · newest ${formatDate(source.newest_message_at)}` : "";
      const sync = health.health_state ? ` · sync ${health.health_state}` : "";
      return `${source.source_type} ${source.display_name || source.source_uri} · ${formatCount(source.message_count)} messages · ${formatCount(source.folder_count)} folders${newest}${cursor}${sync}`;
    }

    function sourceRow(source, account) {
      const health = source.sync_health || {};
      const row = document.createElement("div");
      row.className = "source-row";
      row.innerHTML = `
        <div>
          <div class="suggestion-title"></div>
          <div class="suggestion-meta"></div>
        </div>
        <button class="review-button" type="button">Sync folder</button>
      `;
      row.querySelector(".suggestion-title").textContent = source.display_name || source.source_uri;
      row.querySelector(".suggestion-meta").textContent =
        `${source.source_type} · ${formatCount(source.message_count)} messages · ${formatCount(source.folder_count)} folders · ${healthText(health)}`;
      const button = row.querySelector("button");
      if (health.folder_path) {
        button.addEventListener("click", () => {
          runOperation("live_sync_once", {
            account: account.email_address || account.username || account.id,
            folder: health.folder_path,
          }).catch(showError);
        });
      } else {
        button.disabled = true;
      }
      return row;
    }

    function healthText(health) {
      if (!health || !health.health_state) return "sync unknown";
      const last = health.last_success_at || health.last_error_at || health.updated_at;
      const suffix = last ? ` ${formatDate(last)}` : "";
      const counts = health.scanned || health.imported || health.skipped_existing || health.deduped_existing
        ? ` · scanned ${formatCount(health.scanned)} · imported ${formatCount(health.imported)} · skipped ${formatCount(health.skipped_existing)} · deduped ${formatCount(health.deduped_existing)}`
        : "";
      return `${health.health_state}${suffix}${health.last_error_message ? " · " + health.last_error_message : ""}${counts}`;
    }

    function healthClass(value) {
      const state = String(value || "unknown").toLowerCase();
      if (["ok", "completed"].includes(state)) return "health-ok";
      if (state === "stale") return "health-stale";
      if (state === "failed") return "health-failed";
      if (state === "running") return "health-running";
      if (state === "skipped") return "health-skipped";
      return "health-unknown";
    }

    async function runOperation(action, extra = {}) {
      const list = $("reader").querySelector('[data-list="operation-output"]');
      list.innerHTML = `<div class="suggestion-card"><div class="suggestion-title">Running ${action}</div><div class="suggestion-meta">Waiting for command output</div></div>`;
      const result = await postJson("/api/operations/action", {
        action,
        limit: $("opsLimit").value,
        fetch_batch_size: $("opsBatch").value,
        commit_every: $("opsCommitEvery").value,
        ...extra,
      });
      await openOperations();
      renderOperationResult(result);
    }

    function renderOperationResult(data) {
      const list = $("reader").querySelector('[data-list="operation-output"]');
      if (!list) return;
      const result = data.result || {};
      const card = document.createElement("div");
      card.className = "suggestion-card";
      card.innerHTML = `
        <div class="suggestion-top">
          <div>
            <div class="suggestion-title"></div>
            <div class="suggestion-meta"></div>
          </div>
          <span class="suggestion-badge"></span>
        </div>
        <pre class="output-box"></pre>
      `;
      card.querySelector(".suggestion-title").textContent = result.name || data.action || "operation";
      card.querySelector(".suggestion-meta").textContent = data.ok ? "completed" : "failed or timed out";
      card.querySelector(".suggestion-badge").textContent = `exit ${result.returncode}`;
      card.querySelector(".output-box").textContent =
        [result.stdout || "", result.stderr || ""].filter(Boolean).join("\n") || "(no output)";
      list.innerHTML = "";
      list.appendChild(card);
    }

    function renderUnsubscribeQueue(candidates) {
      $("reader").innerHTML = `
        <div class="review-list">
          <h1>Unsubscribe Queue</h1>
          <div class="suggestion-meta"></div>
          <div data-list="unsubscribe-queue"></div>
        </div>
      `;
      $("reader").querySelector(".suggestion-meta").textContent = `${candidates.length} candidates`;
      const list = $("reader").querySelector('[data-list="unsubscribe-queue"]');
      if (!candidates.length) {
        list.innerHTML = `<div class="empty">No unsubscribe candidates</div>`;
        return;
      }
      candidates.forEach((item) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        const target = item.unsubscribe_mailto || item.unsubscribe_url || item.candidate_type;
        const browser = item.requires_browser ? "browser/manual assist" : "manual assist";
        card.innerHTML = `
          <div class="suggestion-top">
            <div>
              <div class="suggestion-title"></div>
              <div class="suggestion-meta"></div>
            </div>
            <span class="suggestion-badge"></span>
          </div>
          <div class="suggestion-meta" data-field="target"></div>
          <div class="suggestion-actions"></div>
        `;
        card.querySelector(".suggestion-title").textContent = `${item.subject} · ${item.candidate_type}`;
        card.querySelector(".suggestion-meta").textContent =
          `${item.from || "(unknown)"} · ${formatDate(item.message_date)} · confidence ${Number(item.confidence || 0).toFixed(2)}`;
        card.querySelector(".suggestion-badge").textContent = item.status;
        card.querySelector('[data-field="target"]').textContent = `${browser} · ${target}`;
        const actions = card.querySelector(".suggestion-actions");
        if (["detected", "review_required"].includes(item.status)) {
          [
            ["approve", "Approve"],
            ["reject", "Ignore"],
          ].forEach(([action, label]) => {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = label;
            button.addEventListener("click", async () => {
              await applyUnsubscribeAction(item.id, action);
              await openUnsubscribeQueue();
            });
            actions.appendChild(button);
          });
        }
        if (item.folder_path && item.uid) {
          const openButton = document.createElement("button");
          openButton.type = "button";
          openButton.textContent = "Open";
          openButton.addEventListener("click", async () => {
            await loadFolder(item.folder_path);
            await openMessage(item.uid);
          });
          actions.appendChild(openButton);
        }
        list.appendChild(card);
      });
    }

    function selectedProposalRuleIds() {
      return Array.from($("reader").querySelectorAll("[data-proposal-select]:checked"))
        .map((input) => input.value)
        .filter(Boolean);
    }

    async function applyProposalBatchAction(action, explicitRuleIds = null) {
      const ruleIds = explicitRuleIds || selectedProposalRuleIds();
      const output = $("proposalPreviewOutput");
      if (!ruleIds.length) {
        if (output) {
          output.hidden = false;
          output.textContent = "Select at least one proposal.";
        }
        return;
      }
      await postJson("/api/proposals/action", { action, rule_ids: ruleIds });
      await openProposals();
    }

    async function runProposalObservePreview() {
      const output = $("proposalPreviewOutput");
      output.hidden = false;
      output.textContent = "Running observe preview...";
      const data = await postJson("/api/proposals/action", {
        action: "observe_preview",
        limit: $("proposalPreviewLimit").value,
      });
      const result = data.result || {};
      output.textContent = [
        `proposal observe preview · exit ${result.returncode}`,
        result.stdout || "",
        result.stderr || "",
      ].filter(Boolean).join("\\n");
    }

    async function applyRuleAction(ruleId, action) {
      await postJson("/api/rules/action", { rule_id: ruleId, action });
    }

    async function saveRule(card, ruleId) {
      await postJson("/api/rules/action", {
        rule_id: ruleId,
        action: "update",
        rule_name: card.querySelector('[data-field="rule-name"]').value,
        priority: card.querySelector('[data-field="rule-priority"]').value,
        status: card.querySelector('[data-field="rule-status"]').value,
      });
    }

    async function applyPolicyAction(policyId, action) {
      await postJson("/api/retention/policies/action", { policy_id: policyId, action });
    }

    async function savePolicy(card, policyId) {
      const durationValue = card.querySelector('[data-field="policy-duration"]').value;
      const durationUnit = card.querySelector('[data-field="policy-duration-unit"]').value;
      await postJson("/api/retention/policies/action", {
        policy_id: policyId,
        action: "update",
        policy_name: card.querySelector('[data-field="policy-name"]').value,
        hold_duration_seconds: durationSeconds(durationValue, durationUnit),
        policy_action: card.querySelector('[data-field="policy-action"]').value,
        status: card.querySelector('[data-field="policy-status"]').value,
        requires_review: card.querySelector('[data-field="policy-review"]').checked,
      });
    }

    async function runInternalApply(mode) {
      const data = await postJson("/api/internal-apply/action", {
        mode,
        target: $("applyTarget").value,
        limit: $("applyLimit").value,
      });
      const list = $("reader").querySelector('[data-list="apply-output"]');
      list.innerHTML = "";
      data.results.forEach((result) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        card.innerHTML = `
          <div class="suggestion-top">
            <div class="suggestion-title"></div>
            <span class="suggestion-badge"></span>
          </div>
          <pre class="output-box"></pre>
        `;
        card.querySelector(".suggestion-title").textContent = result.name;
        card.querySelector(".suggestion-badge").textContent = `exit ${result.returncode}`;
        card.querySelector(".output-box").textContent =
          [result.stdout || "", result.stderr || ""].filter(Boolean).join("\\n");
        list.appendChild(card);
      });
    }

    async function logout() {
      await postJson("/api/logout", {});
      window.location.href = "/login";
    }

    function showError(error) {
      $("reader").innerHTML = `<div class="error"></div>`;
      $("reader").querySelector(".error").textContent = error.message || String(error);
    }

    $("themes").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-theme]");
      if (button) setTheme(button.dataset.theme);
    });
    $("search").addEventListener("input", (event) => {
      state.query = event.target.value;
      renderMessages();
    });
    $("search").addEventListener("keydown", (event) => {
      if (event.key === "Enter") runGlobalSearch().catch(showError);
    });
    $("messageLimit").addEventListener("change", (event) => {
      state.limit = validMessageLimits.has(event.target.value) ? event.target.value : "50";
      localStorage.setItem(limitStorageKey, state.limit);
      loadFolder(state.folder).catch(showError);
    });
    $("refreshFolder").addEventListener("click", () => {
      loadFolder(state.folder, { force: true }).catch(showError);
    });
    $("reviewButton").addEventListener("click", () => {
      openReview().catch(showError);
    });
    $("workbenchButton").addEventListener("click", () => {
      openWorkbench().catch(showError);
    });
    $("metricsButton").addEventListener("click", () => {
      openLearningMetrics().catch(showError);
    });
    $("unsubscribeButton").addEventListener("click", () => {
      openUnsubscribeQueue().catch(showError);
    });
    $("policiesButton").addEventListener("click", () => {
      openPolicies().catch(showError);
    });
    $("proposalsButton").addEventListener("click", () => {
      openProposals().catch(showError);
    });
    $("searchButton").addEventListener("click", () => {
      runGlobalSearch().catch(showError);
    });
    $("rulesButton").addEventListener("click", () => {
      openRules().catch(showError);
    });
    $("applyButton").addEventListener("click", () => {
      openInternalApply().catch(showError);
    });
    $("opsButton").addEventListener("click", () => {
      openOperations().catch(showError);
    });
    $("logoutButton").addEventListener("click", () => {
      logout().catch(showError);
    });

    setTheme(localStorage.getItem("millie.webmail.theme") || "gmail");
    loadSessionState().catch(() => {});
    loadBootstrap().catch(showError);
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    parsed_args = build_parser().parse_args()
    if parsed_args.daemon:
        daemonize(pid_file=parsed_args.pid_file, log_file=parsed_args.log_file)
    serve(parsed_args)
