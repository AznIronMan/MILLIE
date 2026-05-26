from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ExportProfile:
    id: str
    display_name: str
    recommended_format: str
    formats: tuple[str, ...]
    description: str
    import_instructions: tuple[str, ...]
    limitations: tuple[str, ...]

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "recommended_format": self.recommended_format,
            "formats": list(self.formats),
            "description": self.description,
            "import_instructions": list(self.import_instructions),
            "limitations": list(self.limitations),
        }


EXPORT_PROFILES = {
    "generic-eml": ExportProfile(
        id="generic-eml",
        display_name="Generic EML Bundle",
        recommended_format="eml",
        formats=("eml",),
        description="Portable one-file-per-message export grouped by MILLIE mailbox path.",
        import_instructions=(
            "Import or drag the generated .eml files into a client or migration tool that accepts EML.",
            "Use the manifest to audit message counts, output hashes, and warnings.",
        ),
        limitations=("Folder metadata is represented by output directories, not client-native folder metadata.",),
    ),
    "generic-mbox": ExportProfile(
        id="generic-mbox",
        display_name="Generic MBOX Folder Files",
        recommended_format="mbox",
        formats=("mbox",),
        description="Portable folder-oriented MBOX export for clients and migration tools.",
        import_instructions=(
            "Import each generated .mbox file with the target client's mailbox import workflow.",
            "Use the manifest to map output files back to MILLIE message and mailbox IDs.",
        ),
        limitations=("MBOX does not preserve every per-message flag or label across all clients.",),
    ),
    "generic-maildir": ExportProfile(
        id="generic-maildir",
        display_name="Generic Maildir Folders",
        recommended_format="maildir",
        formats=("maildir",),
        description="Maildir export with tmp/new/cur folders for each MILLIE mailbox path.",
        import_instructions=(
            "Point a Maildir-aware tool or server at the generated folder tree.",
            "Use the manifest to confirm generated file hashes before importing elsewhere.",
        ),
        limitations=("Current export writes messages into new/ and does not yet map read/flag state.",),
    ),
    "thunderbird": ExportProfile(
        id="thunderbird",
        display_name="Thunderbird Import",
        recommended_format="mbox",
        formats=("mbox", "eml"),
        description="Thunderbird-friendly export using MBOX folder files by default.",
        import_instructions=(
            "Install or use Thunderbird's mailbox import workflow, then import the generated .mbox files.",
            "Keep the manifest with the exported files so mailbox-to-file mapping remains auditable.",
        ),
        limitations=("Thunderbird-specific profile metadata is not generated yet.",),
    ),
    "evolution": ExportProfile(
        id="evolution",
        display_name="Evolution Import",
        recommended_format="maildir",
        formats=("maildir", "mbox"),
        description="Evolution-oriented export using Maildir by default, with MBOX available.",
        import_instructions=(
            "Import the generated Maildir folders or use Evolution's import workflow for MBOX output.",
            "Review manifest warnings for unsupported flag or label mappings.",
        ),
        limitations=("Evolution-specific metadata files are not generated yet.",),
    ),
    "apple-mail": ExportProfile(
        id="apple-mail",
        display_name="Apple Mail Import",
        recommended_format="mbox",
        formats=("mbox", "eml"),
        description="Apple Mail-oriented export using MBOX files by default.",
        import_instructions=(
            "Use Apple Mail's import mailbox workflow and select the generated .mbox files.",
            "After import, compare folder/message counts with the MILLIE manifest.",
        ),
        limitations=("Apple Mail .mbox bundle metadata is not generated yet.",),
    ),
    "outlook-workflow": ExportProfile(
        id="outlook-workflow",
        display_name="Outlook Workflow",
        recommended_format="eml",
        formats=("eml",),
        description="Near-term Outlook workflow export using EML bundles until a reliable PST writer is selected.",
        import_instructions=(
            "Use the generated EML files with the Outlook or migration workflow available in your environment.",
            "For highest fidelity Outlook-native export, test the local IMAP facade or a future vetted PST writer path.",
        ),
        limitations=("Direct PST/OLM writing is intentionally not promised by this profile yet.",),
    ),
}


def list_export_profiles() -> list[ExportProfile]:
    return list(EXPORT_PROFILES.values())


def get_export_profile(profile_id: str | None) -> ExportProfile:
    normalized = (profile_id or "generic-eml").strip().lower()
    aliases = {
        "generic": "generic-eml",
        "eml": "generic-eml",
        "mbox": "generic-mbox",
        "maildir": "generic-maildir",
        "apple": "apple-mail",
        "outlook": "outlook-workflow",
    }
    resolved = aliases.get(normalized, normalized)
    if resolved not in EXPORT_PROFILES:
        raise ValueError(f"Unsupported export profile: {profile_id}")
    return EXPORT_PROFILES[resolved]


def resolve_export_format(profile: ExportProfile, export_format: str | None) -> str:
    normalized = (export_format or "auto").strip().lower()
    if normalized in {"", "auto", "recommended"}:
        return profile.recommended_format
    if normalized not in profile.formats:
        raise ValueError(f"Profile {profile.id} does not support export format: {normalized}")
    return normalized
