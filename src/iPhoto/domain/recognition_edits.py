"""Explicit identity-edit intent and the shared inline selection policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

IdentityKind = Literal["person", "pet"]


@dataclass(frozen=True, slots=True)
class IdentityRef:
    kind: IdentityKind
    entity_id: str

    def __post_init__(self) -> None:
        normalized_id = str(self.entity_id or "").strip()
        if self.kind not in {"person", "pet"} or not normalized_id:
            raise ValueError("identity kind and entity id are required")
        object.__setattr__(self, "entity_id", normalized_id)

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.entity_id}"

    @classmethod
    def parse(cls, value: object) -> IdentityRef | None:
        if isinstance(value, cls):
            return value
        text = str(value or "").strip()
        if ":" not in text:
            return None
        kind, entity_id = (part.strip() for part in text.split(":", 1))
        if kind not in {"person", "pet"} or not entity_id:
            return None
        return cls(kind=kind, entity_id=entity_id)  # type: ignore[arg-type]


class InlineSelectionScope(StrEnum):
    ANNOTATION = "annotation"
    CANDIDATE_IDENTITY = "candidate_identity"


@dataclass(frozen=True, slots=True)
class AnnotationEditContext:
    asset_id: str
    source_kind: IdentityKind
    annotation_id: str
    source_identity: IdentityRef | None
    current_identity: IdentityRef | None
    is_manual: bool = False
    promotion_state: str = "legacy_visible"

    @property
    def selection_scope(self) -> InlineSelectionScope:
        if (
            not self.is_manual
            and self.promotion_state == "candidate"
            and self.source_identity is not None
            and self.source_identity == self.current_identity
            and self.source_identity.kind == self.source_kind
        ):
            return InlineSelectionScope.CANDIDATE_IDENTITY
        return InlineSelectionScope.ANNOTATION


@dataclass(frozen=True, slots=True)
class IdentitySelectionRequest:
    context: AnnotationEditContext
    target: IdentityRef


@dataclass(frozen=True, slots=True)
class IdentityRenameRequest:
    context: AnnotationEditContext
    name: str | None
    expected_name: str | None


class RecognitionEditStatus(StrEnum):
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RecognitionEditOutcome:
    status: RecognitionEditStatus
    failure: str | None = None
