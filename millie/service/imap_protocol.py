"""Small IMAP protocol formatting helpers used by dev listeners."""

from __future__ import annotations

import re


def imap_capabilities() -> list[str]:
    return ["IMAP4rev1", "UIDPLUS", "LITERAL+", "AUTH=PLAIN", "ID", "NAMESPACE"]


def summarize_fetch_items(value: str) -> str:
    upper = value.upper()
    names = []
    for name in [
        "UID",
        "FLAGS",
        "INTERNALDATE",
        "RFC822.SIZE",
        "ENVELOPE",
        "BODYSTRUCTURE",
        "BODY.PEEK",
        "BODY",
        "RFC822",
    ]:
        if name in upper:
            names.append(name)
    return "+".join(names) or "unknown"


def body_literal_name(item_text: str) -> str:
    match = re.search(r"BODY(?:\.PEEK)?(\[[^\]]*\])(\<\d+(?:\.\d+)?\>)?", item_text, flags=re.I)
    if not match:
        return "BODY[]"
    name = f"BODY{match.group(1)}"
    partial = match.group(2)
    if partial:
        start = partial[1:-1].split(".", 1)[0]
        name += f"<{start}>"
    return name
