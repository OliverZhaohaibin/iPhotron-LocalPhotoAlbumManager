"""Schema helpers for the application settings file."""

from __future__ import annotations

import os
from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator

SETTINGS_SCHEMA: dict[str, Any] = {
    "$id": "iPhoto/settings.schema.json",
    "type": "object",
    "required": ["schema", "ui", "last_open_albums"],
    "properties": {
        "schema": {"const": "iPhoto/settings@1"},
        "basic_library_path": {"type": ["string", "null"]},
        "pinned_items_by_library": {
            "type": "object",
            "additionalProperties": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["kind", "item_id", "label"],
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["album", "person", "group"],
                        },
                        "item_id": {"type": "string"},
                        "label": {"type": "string"},
                        "custom_label": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
            },
        },
        "ui": {
            "type": "object",
            "properties": {
                "theme": {
                    "type": "string",
                    "enum": ["system", "light", "dark"],
                },
                "sidebar_width": {"type": "number", "minimum": 120},
                "volume": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 100,
                },
                "is_muted": {"type": "boolean"},
                "share_action": {
                    "type": "string",
                    "enum": ["copy_file", "copy_path", "reveal_file"],
                },
                "export_destination": {
                    "type": "string",
                    "enum": ["library", "ask"],
                },
                "export_format": {
                    "type": "string",
                    "enum": ["jpg", "png", "tiff"],
                },
                "show_filmstrip": {"type": "boolean"},
                "show_face_names_in_detail": {"type": "boolean"},
                "show_hidden_people": {"type": "boolean"},
                "wheel_action": {
                    "type": "string",
                    "enum": ["navigate", "zoom"],
                },
            },
            "additionalProperties": True,
        },
        "last_open_albums": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "additionalProperties": True,
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "schema": "iPhoto/settings@1",
    "basic_library_path": None,
    "pinned_items_by_library": {},
    "ui": {
        "theme": "system",
        "sidebar_width": 280,
        "volume": 75,
        "is_muted": False,
        "share_action": "reveal_file",
        "export_destination": "library",
        "export_format": "jpg",
        "show_filmstrip": True,
        "show_face_names_in_detail": False,
        "show_hidden_people": False,
        "wheel_action": "navigate",
    },
    "last_open_albums": [],
}

_validator = Draft202012Validator(SETTINGS_SCHEMA)


def _normalise_last_open(entries: list[Any]) -> list[str]:
    normalised: list[str] = []
    for entry in entries:
        try:
            path = os.fspath(entry)
        except TypeError:
            continue
        normalised.append(str(path))
    return normalised


def merge_with_defaults(data: dict[str, Any] | None) -> dict[str, Any]:
    """Merge *data* with :data:`DEFAULT_SETTINGS` and validate the result."""

    merged = deepcopy(DEFAULT_SETTINGS)
    if data:
        for key, value in data.items():
            if key == "ui" and isinstance(value, dict):
                target = merged.setdefault("ui", {})
                for sub_key, sub_value in value.items():
                    target[sub_key] = sub_value
                continue
            if key == "last_open_albums" and isinstance(value, list):
                merged[key] = _normalise_last_open(value)
                continue
            if key == "basic_library_path" and value not in {None, ""}:
                try:
                    merged[key] = os.fspath(value)
                except TypeError:
                    continue
                continue
            if key == "pinned_items_by_library" and isinstance(value, dict):
                normalised: dict[str, Any] = {}
                for library_key, entries in value.items():
                    try:
                        resolved_key = os.fspath(library_key)
                    except TypeError:
                        continue
                    if not isinstance(entries, list):
                        continue
                    normalised_entries: list[dict[str, str]] = []
                    for entry in entries:
                        if not isinstance(entry, dict):
                            continue
                        kind = str(entry.get("kind") or "").strip()
                        item_id = str(entry.get("item_id") or "").strip()
                        label = str(entry.get("label") or "").strip()
                        custom_label = bool(entry.get("custom_label", False))
                        if kind not in {"album", "person", "group"}:
                            continue
                        if not item_id or not label:
                            continue
                        normalised_entries.append(
                            {
                                "kind": kind,
                                "item_id": item_id,
                                "label": label,
                                "custom_label": custom_label,
                            }
                        )
                    normalised[resolved_key] = normalised_entries
                merged[key] = normalised
                continue
            merged[key] = value
    _validator.validate(merged)
    return merged


def validate_settings(data: dict[str, Any]) -> None:
    """Validate *data* against the settings schema."""

    _validator.validate(data)


__all__ = ["DEFAULT_SETTINGS", "SETTINGS_SCHEMA", "merge_with_defaults", "validate_settings"]
