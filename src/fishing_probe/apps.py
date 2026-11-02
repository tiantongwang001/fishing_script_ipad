"""Appium driver 12.22 mobile:listApps returns bundle-ID to metadata mapping."""
from __future__ import annotations

from typing import Any


def normalize_app_list(value: Any) -> dict[str, dict]:
    """Accept documented map and older/alternate list-of-maps forms."""
    if isinstance(value, dict):
        if "value" in value and isinstance(value["value"], (dict, list)):
            return normalize_app_list(value["value"])
        return {str(bundle): info if isinstance(info, dict) else {}
                for bundle, info in value.items()}
    if isinstance(value, list):
        merged: dict[str, dict] = {}
        for entry in value:
            if isinstance(entry, dict):
                bundle = entry.get("CFBundleIdentifier")
                if isinstance(bundle, str) and bundle:
                    merged[bundle] = entry
                else:
                    merged.update(normalize_app_list(entry))
        return merged
    raise TypeError(f"Unexpected mobile:listApps result type: {type(value).__name__}")


def select_fishing_game(apps: dict[str, dict], requested_bundle: str = "") -> tuple[str, dict]:
    if requested_bundle:
        if requested_bundle not in apps:
            raise LookupError("Configured game_bundle_id is not installed according to mobile:listApps")
        return requested_bundle, apps[requested_bundle]
    matches = []
    for bundle, metadata in apps.items():
        description = " ".join((bundle, str(metadata.get("CFBundleName", "")),
                                str(metadata.get("CFBundleDisplayName", "")))).lower()
        joined = description.replace(" ", "")
        if "fishing" in joined and "superstar" in joined:
            matches.append((bundle, metadata))
    if len(matches) != 1:
        raise LookupError(f"Expected exactly 1 Fishing Superstars match, found {len(matches)}. Inspect installed_apps.json locally.")
    return matches[0]
