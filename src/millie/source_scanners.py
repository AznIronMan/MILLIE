from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


MBOX_SAMPLE_LIMIT = 16 * 1024 * 1024
THUNDERBIRD_MAIL_ROOTS = {"Mail", "ImapMail"}
APPLE_MAIL_MARKERS = {"MailData", "Envelope Index", "Accounts.plist"}
APPLE_MAIL_METADATA_NAMES = {
    ".mboxcache.plist",
    ".mboxentries",
    "info.plist",
    "table_of_contents",
}
APPLE_MAIL_METADATA_SUFFIXES = {
    ".plist",
    ".sqlite",
    ".sqlite-shm",
    ".sqlite-wal",
}
EVOLUTION_METADATA_NAMES = {
    "folders.db",
    "folders.db-shm",
    "folders.db-wal",
    "journal",
    "uidvalidity",
}
EVOLUTION_METADATA_SUFFIXES = {
    ".cmeta",
    ".dat",
    ".db",
    ".ev-summary",
    ".ibex.index",
    ".ibex.index.data",
    ".index",
    ".json",
    ".log",
    ".sqlite",
    ".sqlite-shm",
    ".sqlite-wal",
}
THUNDERBIRD_METADATA_NAMES = {
    "foldertree.json",
    "global-messages-db.sqlite",
    "history.mab",
    "msgfilterrules.dat",
    "panacea.dat",
    "popstate.dat",
}
THUNDERBIRD_METADATA_SUFFIXES = {
    ".bak",
    ".dat",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".log",
    ".mab",
    ".msf",
    ".rdf",
    ".sqlite",
    ".sqlite-shm",
    ".sqlite-wal",
}


@dataclass(slots=True)
class SourceCandidate:
    id: str
    source_type: str
    format: str
    path: str
    display_name: str
    mailbox_path: str
    size_bytes: int
    message_estimate: int | None
    confidence: str
    notes: list[str]
    importable: bool = True

    def to_api(self) -> dict[str, object]:
        return {
            "id": self.id,
            "source_type": self.source_type,
            "format": self.format,
            "path": self.path,
            "display_name": self.display_name,
            "mailbox_path": self.mailbox_path,
            "size_bytes": self.size_bytes,
            "message_estimate": self.message_estimate,
            "confidence": self.confidence,
            "notes": self.notes,
            "importable": self.importable,
        }


def scan_source(path: Path, source_type: str = "auto") -> list[SourceCandidate]:
    root = path.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(root)

    normalized_type = source_type.lower().strip() or "auto"
    if normalized_type == "thunderbird":
        return scan_thunderbird(root)
    if normalized_type == "evolution":
        return scan_evolution(root)
    if normalized_type in {"apple-mail", "apple_mail", "applemail", "apple"}:
        return scan_apple_mail(root)
    if normalized_type == "generic":
        return scan_generic(root)
    if normalized_type != "auto":
        raise ValueError(f"Unsupported source scan type: {source_type}")

    candidates: list[SourceCandidate] = []
    if root.is_dir():
        thunderbird_profiles = find_thunderbird_profiles(root)
        if thunderbird_profiles:
            candidates.extend(scan_thunderbird(root, thunderbird_profiles))
        if looks_like_evolution_root(root):
            candidates.extend(scan_evolution(root))
        if looks_like_apple_mail_root(root):
            candidates.extend(scan_apple_mail(root))
    if not candidates:
        candidates.extend(scan_generic(root))
    return sort_candidates(unique_candidates(candidates))


def scan_thunderbird(
    root: Path,
    profile_roots: list[Path] | None = None,
) -> list[SourceCandidate]:
    profiles = profile_roots or find_thunderbird_profiles(root)
    candidates: list[SourceCandidate] = []
    for profile_root in profiles:
        for mail_root_name in THUNDERBIRD_MAIL_ROOTS:
            mail_root = profile_root / mail_root_name
            if mail_root.exists() and mail_root.is_dir():
                candidates.extend(scan_thunderbird_mail_root(profile_root, mail_root))
    return sort_candidates(unique_candidates(candidates))


def scan_thunderbird_mail_root(profile_root: Path, mail_root: Path) -> list[SourceCandidate]:
    candidates: list[SourceCandidate] = []
    stack = [mail_root]
    while stack:
        current = stack.pop()
        if is_hidden_path(current, profile_root):
            continue
        if is_maildir(current):
            candidates.append(build_maildir_candidate("thunderbird", profile_root, current))
            continue
        if has_eml_files(current):
            candidates.append(build_eml_dir_candidate("thunderbird", profile_root, current))
        for entry in safe_iterdir(current):
            if entry.is_dir():
                stack.append(entry)
            elif is_thunderbird_mbox_file(entry):
                candidates.append(build_mbox_candidate("thunderbird", profile_root, entry))
    return candidates


def scan_evolution(root: Path) -> list[SourceCandidate]:
    candidates: list[SourceCandidate] = []
    stack = [root]
    while stack:
        current = stack.pop()
        if is_hidden_path(current, root):
            continue
        if is_maildir(current):
            candidates.append(build_maildir_candidate("evolution", root, current))
            continue
        if has_eml_files(current):
            candidates.append(build_eml_dir_candidate("evolution", root, current))
        for entry in safe_iterdir(current):
            if entry.is_dir():
                stack.append(entry)
            elif is_evolution_mbox_file(entry):
                candidates.append(build_mbox_candidate("evolution", root, entry))
    return sort_candidates(unique_candidates(candidates))


def scan_apple_mail(root: Path) -> list[SourceCandidate]:
    if root.is_file():
        if root.name.lower() == "mbox" or root.suffix.lower() in {".mbox", ".mbx"}:
            return [build_apple_mail_mbox_candidate(root.parent, root)]
        return scan_generic(root)

    candidates: list[SourceCandidate] = []
    stack = [root]
    while stack:
        current = stack.pop()
        if is_hidden_path(current, root):
            continue
        if current.suffix.lower() == ".mbox":
            candidates.extend(scan_apple_mail_package(root, current))
            continue
        if current.name == "Messages" and has_eml_files(current):
            candidates.append(build_apple_mail_messages_candidate(root, current))
            continue
        for entry in safe_iterdir(current):
            if entry.is_dir():
                stack.append(entry)
            elif is_apple_mail_mbox_file(entry):
                candidates.append(build_apple_mail_mbox_candidate(root, entry))
    return sort_candidates(unique_candidates(candidates))


def scan_apple_mail_package(root: Path, package: Path) -> list[SourceCandidate]:
    candidates: list[SourceCandidate] = []
    mbox_file = package / "mbox"
    messages_dir = package / "Messages"
    if mbox_file.exists() and mbox_file.is_file():
        candidates.append(build_apple_mail_mbox_candidate(root, mbox_file))
    if messages_dir.exists() and messages_dir.is_dir() and has_eml_files(messages_dir):
        candidates.append(build_apple_mail_messages_candidate(root, messages_dir))
    return candidates


def scan_generic(path: Path) -> list[SourceCandidate]:
    if path.is_dir():
        if is_maildir(path):
            return [build_maildir_candidate("generic", path.parent, path)]
        if has_eml_files(path):
            return [build_eml_dir_candidate("generic", path.parent, path)]
        return []

    suffix = path.suffix.lower()
    if suffix in {".eml", ".emlx"}:
        return [
            SourceCandidate(
                id=candidate_id("generic", "eml", path),
                source_type="generic",
                format="eml",
                path=str(path),
                display_name=path.name,
                mailbox_path="Imported",
                size_bytes=safe_size(path),
                message_estimate=1,
                confidence="high",
                notes=[],
            )
        ]
    if suffix in {".mbox", ".mbx"} or (not suffix and starts_like_mbox(path)):
        return [build_mbox_candidate("generic", path.parent, path)]
    if suffix == ".pst":
        return [
            SourceCandidate(
                id=candidate_id("generic", "pst", path),
                source_type="generic",
                format="pst",
                path=str(path),
                display_name=path.name,
                mailbox_path=path.stem,
                size_bytes=safe_size(path),
                message_estimate=None,
                confidence="high",
                notes=["PST import requires readpst/libpst."],
            )
        ]
    if suffix in {".olm", ".ost"}:
        return [build_unsupported_outlook_candidate(path, suffix[1:])]
    return []


def looks_like_evolution_root(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    names = {item.name for item in safe_iterdir(path)}
    lower_names = {name.lower() for name in names}
    if {"cur", "new"}.issubset(names) or EVOLUTION_METADATA_NAMES.intersection(lower_names):
        return True
    return any(
        item.is_dir() and (item.name in {"mail", "local"} or item.name.startswith("account_"))
        for item in safe_iterdir(path)
    )


def looks_like_apple_mail_root(path: Path) -> bool:
    if not path.exists():
        return False
    if path.is_file():
        return path.name.lower() == "mbox" or path.suffix.lower() in {".emlx", ".mbox", ".mbx"}
    if path.suffix.lower() == ".mbox":
        return True
    names = {item.name for item in safe_iterdir(path)}
    if APPLE_MAIL_MARKERS.intersection(names):
        return True
    for item in safe_iterdir(path):
        if not item.is_dir():
            continue
        if item.suffix.lower() == ".mbox":
            return True
        child_names = {child.name for child in safe_iterdir(item)}
        if APPLE_MAIL_MARKERS.intersection(child_names):
            return True
    return False


def find_thunderbird_profiles(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for profile_root in possible_profile_roots(root):
        if looks_like_thunderbird_profile(profile_root):
            candidates.append(profile_root)
    return sorted(unique_paths(candidates), key=lambda item: str(item).lower())


def possible_profile_roots(root: Path) -> list[Path]:
    roots = [root]
    for container_name in ("Profiles", "profiles"):
        container = root / container_name
        if container.exists() and container.is_dir():
            roots.extend(child for child in safe_iterdir(container) if child.is_dir())
    roots.extend(child for child in safe_iterdir(root) if child.is_dir())
    if not any(looks_like_thunderbird_profile(item) for item in roots):
        roots.extend(find_profiles_by_prefs(root, max_depth=3))
    return roots


def find_profiles_by_prefs(root: Path, max_depth: int) -> list[Path]:
    found: list[Path] = []
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > max_depth or is_hidden_path(current, root):
            continue
        if (current / "prefs.js").exists():
            found.append(current)
            continue
        for entry in safe_iterdir(current):
            if entry.is_dir():
                stack.append((entry, depth + 1))
    return found


def looks_like_thunderbird_profile(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    return (
        (path / "prefs.js").exists()
        or (path / "Mail").exists()
        or (path / "ImapMail").exists()
        or (path / "global-messages-db.sqlite").exists()
    )


def build_mbox_candidate(source_type: str, profile_root: Path, path: Path) -> SourceCandidate:
    account, mailbox_path = mailbox_identity(profile_root, path)
    size = safe_size(path)
    estimate, sampled = estimate_mbox_messages(path)
    starts = starts_like_mbox(path)
    notes = account_notes(account)
    if size == 0:
        notes.append("Empty mailbox file.")
    if sampled:
        notes.append("Message estimate sampled from first 16 MiB.")
    confidence = "high" if starts or estimate > 0 else "medium"
    if path.suffix.lower() in {".mbox", ".mbx"} and not starts and size > 0:
        confidence = "low"
        notes.append("Mailbox extension found without an MBOX signature.")
    return SourceCandidate(
        id=candidate_id(source_type, "mbox", path),
        source_type=source_type,
        format="mbox",
        path=str(path),
        display_name=display_name(source_type, profile_root, account, mailbox_path, path),
        mailbox_path=mailbox_path,
        size_bytes=size,
        message_estimate=estimate,
        confidence=confidence,
        notes=notes,
    )


def build_apple_mail_mbox_candidate(root: Path, path: Path) -> SourceCandidate:
    _, mailbox_path = apple_mail_identity(root, path)
    size = safe_size(path)
    estimate, sampled = estimate_mbox_messages(path)
    notes = ["Apple Mail export package MBOX file."] if path.name.lower() == "mbox" else []
    if sampled:
        notes.append("Message estimate sampled from first 16 MiB.")
    return SourceCandidate(
        id=candidate_id("apple-mail", "mbox", path),
        source_type="apple-mail",
        format="mbox",
        path=str(path),
        display_name=f"Apple Mail - {mailbox_path}",
        mailbox_path=mailbox_path,
        size_bytes=size,
        message_estimate=estimate,
        confidence="high" if starts_like_mbox(path) or estimate > 0 else "medium",
        notes=notes,
    )


def build_apple_mail_messages_candidate(root: Path, path: Path) -> SourceCandidate:
    _, mailbox_path = apple_mail_identity(root, path)
    return SourceCandidate(
        id=candidate_id("apple-mail", "eml-dir", path),
        source_type="apple-mail",
        format="eml-dir",
        path=str(path),
        display_name=f"Apple Mail - {mailbox_path}",
        mailbox_path=mailbox_path,
        size_bytes=directory_size(path),
        message_estimate=count_eml_files(path),
        confidence="high",
        notes=["Apple Mail .emlx message folder."],
    )


def build_unsupported_outlook_candidate(path: Path, import_format: str) -> SourceCandidate:
    if import_format == "olm":
        notes = [
            "OLM import is not implemented yet.",
            "Recommended today: export from Outlook for Mac to MBOX/EML if available, or keep this file for the future OLM adapter.",
        ]
    elif import_format == "ost":
        notes = [
            "OST import is not implemented and may be encrypted or tied to the original Outlook/Exchange profile.",
            "Recommended today: sync from Exchange/Microsoft Graph/IMAP when available, or export a PST from Outlook.",
        ]
    else:
        notes = ["This vendor format is not importable yet."]
    return SourceCandidate(
        id=candidate_id("outlook", import_format, path),
        source_type="outlook",
        format=import_format,
        path=str(path),
        display_name=path.name,
        mailbox_path=path.stem,
        size_bytes=safe_size(path),
        message_estimate=None,
        confidence="high",
        notes=notes,
        importable=False,
    )


def build_maildir_candidate(source_type: str, profile_root: Path, path: Path) -> SourceCandidate:
    account, mailbox_path = mailbox_identity(profile_root, path)
    notes = account_notes(account)
    return SourceCandidate(
        id=candidate_id(source_type, "maildir", path),
        source_type=source_type,
        format="maildir",
        path=str(path),
        display_name=display_name(source_type, profile_root, account, mailbox_path, path),
        mailbox_path=mailbox_path,
        size_bytes=directory_size(path),
        message_estimate=count_maildir_messages(path),
        confidence="high",
        notes=notes,
    )


def build_eml_dir_candidate(source_type: str, profile_root: Path, path: Path) -> SourceCandidate:
    account, mailbox_path = mailbox_identity(profile_root, path)
    notes = account_notes(account)
    return SourceCandidate(
        id=candidate_id(source_type, "eml-dir", path),
        source_type=source_type,
        format="eml-dir",
        path=str(path),
        display_name=display_name(source_type, profile_root, account, mailbox_path, path),
        mailbox_path=mailbox_path,
        size_bytes=directory_size(path),
        message_estimate=count_eml_files(path),
        confidence="high",
        notes=notes,
    )


def is_thunderbird_mbox_file(path: Path) -> bool:
    name = path.name.lower()
    if path.name.startswith(".") or name in THUNDERBIRD_METADATA_NAMES:
        return False
    suffix = path.suffix.lower()
    if suffix in THUNDERBIRD_METADATA_SUFFIXES:
        return False
    if suffix in {".mbox", ".mbx"}:
        return True
    if suffix:
        return False
    size = safe_size(path)
    return size == 0 or starts_like_mbox(path)


def is_evolution_mbox_file(path: Path) -> bool:
    name = path.name.lower()
    if path.name.startswith(".") or name in EVOLUTION_METADATA_NAMES:
        return False
    if suffix_in(name, EVOLUTION_METADATA_SUFFIXES):
        return False
    suffix = path.suffix.lower()
    if suffix in {".mbox", ".mbx"}:
        return True
    if suffix:
        return False
    size = safe_size(path)
    return size == 0 or starts_like_mbox(path)


def is_apple_mail_mbox_file(path: Path) -> bool:
    name = path.name.lower()
    if path.name.startswith(".") or name in APPLE_MAIL_METADATA_NAMES:
        return False
    if suffix_in(name, APPLE_MAIL_METADATA_SUFFIXES):
        return False
    return name == "mbox" or path.suffix.lower() in {".mbox", ".mbx"}


def mailbox_identity(profile_root: Path, path: Path) -> tuple[str | None, str]:
    try:
        relative = path.relative_to(profile_root)
        parts = list(relative.parts)
    except ValueError:
        parts = [path.name]

    account = None
    if parts and parts[0] in THUNDERBIRD_MAIL_ROOTS:
        parts = parts[1:]
        if parts:
            account = parts[0]
            parts = parts[1:]
    elif parts and parts[0] in {"mail", "local", "accounts"}:
        parts = parts[1:]
        if parts and parts[0].startswith("account_"):
            account = parts[0]
            parts = parts[1:]

    clean_parts: list[str] = []
    for index, part in enumerate(parts):
        cleaned = part[:-4] if part.endswith(".sbd") else part
        if index == len(parts) - 1 and path.is_file() and Path(cleaned).suffix.lower() in {".mbox", ".mbx"}:
            cleaned = Path(cleaned).stem
        if cleaned not in {"cur", "new", "tmp"}:
            clean_parts.append(cleaned)
    mailbox_path = "/".join(item for item in clean_parts if item) or path.stem or path.name
    return account, mailbox_path


def apple_mail_identity(root: Path, path: Path) -> tuple[Path, str]:
    try:
        relative = path.relative_to(root)
        parts = list(relative.parts)
    except ValueError:
        parts = [path.name]

    clean_parts: list[str] = []
    for part in parts:
        if part in {"Messages", "Data", "MailData"} or part.isdigit() or is_apple_version_folder(part):
            continue
        lowered = part.lower()
        if lowered == "mbox":
            continue
        if lowered.endswith(".mbox"):
            clean_parts.append(part[:-5])
        elif lowered.endswith(".emlx"):
            clean_parts.append(Path(part).stem)
        elif lowered not in APPLE_MAIL_METADATA_NAMES:
            clean_parts.append(part)
    mailbox_path = "/".join(item for item in clean_parts if item)
    if not mailbox_path and root.suffix.lower() == ".mbox":
        mailbox_path = root.stem
    mailbox_path = mailbox_path or path.stem or path.name
    return root, mailbox_path


def display_name(
    source_type: str,
    profile_root: Path,
    account: str | None,
    mailbox_path: str,
    path: Path,
) -> str:
    if source_type == "thunderbird":
        parts = [profile_root.name]
        if account:
            parts.append(account)
        parts.append(mailbox_path)
        return "Thunderbird - " + " / ".join(parts)
    if source_type == "evolution":
        parts = [profile_root.name]
        if account:
            parts.append(account)
        parts.append(mailbox_path)
        return "Evolution - " + " / ".join(parts)
    return path.name


def account_notes(account: str | None) -> list[str]:
    return [f"Account: {account}."] if account else []


def estimate_mbox_messages(path: Path) -> tuple[int, bool]:
    count = 0
    read_bytes = 0
    try:
        with path.open("rb") as handle:
            for line in handle:
                read_bytes += len(line)
                if line.startswith(b"From "):
                    count += 1
                if read_bytes >= MBOX_SAMPLE_LIMIT:
                    return count, safe_size(path) > read_bytes
    except OSError:
        return 0, False
    return count, False


def starts_like_mbox(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(5) == b"From "
    except OSError:
        return False


def is_maildir(path: Path) -> bool:
    return path.is_dir() and (path / "cur").is_dir() and (path / "new").is_dir()


def has_eml_files(path: Path) -> bool:
    return any(item.is_file() and item.suffix.lower() in {".eml", ".emlx"} for item in safe_iterdir(path))


def count_eml_files(path: Path) -> int:
    return sum(1 for item in safe_iterdir(path) if item.is_file() and item.suffix.lower() in {".eml", ".emlx"})


def count_maildir_messages(path: Path) -> int:
    return sum(1 for folder in ("cur", "new") for item in safe_iterdir(path / folder) if item.is_file())


def directory_size(path: Path) -> int:
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        for entry in safe_iterdir(current):
            if entry.is_dir():
                stack.append(entry)
            elif entry.is_file():
                total += safe_size(entry)
    return total


def safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def safe_iterdir(path: Path) -> list[Path]:
    try:
        return sorted(path.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return []


def is_hidden_path(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return any(part.startswith(".") for part in parts if part not in {".", ".."})


def unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def unique_candidates(candidates: list[SourceCandidate]) -> list[SourceCandidate]:
    seen: set[tuple[str, str]] = set()
    unique: list[SourceCandidate] = []
    for candidate in candidates:
        key = (candidate.format, str(Path(candidate.path).resolve()).lower())
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def sort_candidates(candidates: list[SourceCandidate]) -> list[SourceCandidate]:
    return sorted(
        candidates,
        key=lambda item: (item.source_type.lower(), item.mailbox_path.lower(), item.path.lower()),
    )


def suffix_in(name: str, suffixes: set[str]) -> bool:
    return any(name.endswith(suffix) for suffix in suffixes)


def is_apple_version_folder(name: str) -> bool:
    return len(name) > 1 and name[0] == "V" and name[1:].isdigit()


def candidate_id(source_type: str, import_format: str, path: Path) -> str:
    digest = hashlib.sha1(str(path.resolve()).lower().encode("utf-8")).hexdigest()[:16]
    return f"{source_type}:{import_format}:{digest}"
