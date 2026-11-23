"""Schema validation helpers."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator

from ..config import SCHEMA_DIR
from ..errors import ManifestInvalidError

@lru_cache(maxsize=2)
def _load_validator(name: str) -> Draft202012Validator:
    schema_path = SCHEMA_DIR / name
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def validate_album(document: dict[str, Any]) -> None:
    """Validate an album manifest and raise :class:`ManifestInvalidError` on failure."""

    _validate(document, "album.schema.json")


def validate_links(document: dict[str, Any]) -> None:
    """Validate a Live Photo links snapshot before it is written."""

    _validate(document, "links.schema.json")


def _validate(document: dict[str, Any], schema_name: str) -> None:
    errors = sorted(_load_validator(schema_name).iter_errors(document), key=lambda err: err.path)
    if errors:
        messages = "; ".join(error.message for error in errors)
        raise ManifestInvalidError(messages)
