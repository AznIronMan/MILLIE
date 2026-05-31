"""Mailbox facade definitions for IMAP/webmail clients."""

from __future__ import annotations

from dataclasses import dataclass

from millie.importing.models import stable_id


@dataclass(frozen=True, slots=True)
class MailboxFolder:
    id: str
    mailbox_id: str
    path: str
    display_name: str
    role: str
    special_use: str | None
    sort_order: int
    parent_id: str | None = None


def default_mailbox_folders(mailbox_id: str) -> list[MailboxFolder]:
    """Return folders expected by IMAP clients and webmail frontends."""

    source_root_id = stable_id("millie_folder", mailbox_id, "Sources")
    rows = [
        ("INBOX", "INBOX", "inbox", "\\Inbox", 10, None),
        ("All Mail", "All Mail", "all_mail", "\\All", 20, None),
        ("Archive", "Archive", "archive", "\\Archive", 30, None),
        ("Sent", "Sent", "sent", "\\Sent", 40, None),
        ("Drafts", "Drafts", "drafts", "\\Drafts", 50, None),
        ("Trash", "Trash", "trash", "\\Trash", 60, None),
        ("Junk", "Junk", "junk", "\\Junk", 70, None),
        ("Sources", "Sources", "source_root", None, 80, None),
        ("Sources/IMAP", "IMAP", "source", None, 90, source_root_id),
        ("Sources/PST", "PST", "source", None, 100, source_root_id),
    ]
    return [
        MailboxFolder(
            id=stable_id("millie_folder", mailbox_id, path),
            mailbox_id=mailbox_id,
            path=path,
            display_name=display_name,
            role=role,
            special_use=special_use,
            sort_order=sort_order,
            parent_id=parent_id,
        )
        for path, display_name, role, special_use, sort_order, parent_id in rows
    ]
