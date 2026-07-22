"""Typed orchestration for People/Pets identity merges."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

IdentityKind = Literal["person", "pet"]


@dataclass(frozen=True, slots=True)
class IdentityRef:
    """A collision-free reference to one recognition identity."""

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


class IdentityMergeFailure(StrEnum):
    INVALID_IDENTITY = "invalid_identity"
    SAME_IDENTITY = "same_identity"
    NOT_FOUND = "not_found"
    HIDDEN_STATE_MISMATCH = "hidden_state_mismatch"
    REDIRECT_CONFLICT = "redirect_conflict"
    REJECTED = "rejected"


class IdentityMergeRefreshPolicy(StrEnum):
    NONE = "none"
    SNAPSHOT = "snapshot"
    IMMEDIATE = "immediate"


@dataclass(frozen=True, slots=True)
class IdentityMergeOutcome:
    merged: bool
    source: IdentityRef | None
    target: IdentityRef | None
    failure: IdentityMergeFailure | None = None
    refresh_policy: IdentityMergeRefreshPolicy = IdentityMergeRefreshPolicy.NONE
    changed_asset_ids: tuple[str, ...] = ()
    person_redirects: dict[str, str] = field(default_factory=dict)
    pet_redirects: dict[str, str] = field(default_factory=dict)
    group_redirects: dict[str, str | None] = field(default_factory=dict)


class RecognitionMergeService:
    """Route every directional identity merge through one typed boundary."""

    def __init__(self, people_service, pet_service) -> None:
        self._people_service = people_service
        self._pet_service = pet_service

    def merge(self, source: IdentityRef | str, target: IdentityRef | str) -> IdentityMergeOutcome:
        source_ref = IdentityRef.parse(source)
        target_ref = IdentityRef.parse(target)
        if source_ref is None or target_ref is None:
            return IdentityMergeOutcome(
                False,
                source_ref,
                target_ref,
                IdentityMergeFailure.INVALID_IDENTITY,
            )
        if source_ref == target_ref:
            return IdentityMergeOutcome(
                False,
                source_ref,
                target_ref,
                IdentityMergeFailure.SAME_IDENTITY,
            )

        source_hidden = self._hidden_state(source_ref)
        target_hidden = self._hidden_state(target_ref)
        if source_hidden is None or target_hidden is None:
            return IdentityMergeOutcome(
                False,
                source_ref,
                target_ref,
                IdentityMergeFailure.NOT_FOUND,
            )
        if source_hidden != target_hidden:
            return IdentityMergeOutcome(
                False,
                source_ref,
                target_ref,
                IdentityMergeFailure.HIDDEN_STATE_MISMATCH,
            )

        if source_ref.kind == target_ref.kind == "person":
            changed_asset_ids = tuple(
                dict.fromkeys(
                    self._people_service.cluster_asset_ids(source_ref.entity_id)
                    + self._people_service.cluster_asset_ids(target_ref.entity_id)
                )
            )
            merged = self._people_service.merge_clusters(
                source_ref.entity_id,
                target_ref.entity_id,
            )
            return IdentityMergeOutcome(
                merged,
                source_ref,
                target_ref,
                None if merged else IdentityMergeFailure.REJECTED,
                refresh_policy=(
                    IdentityMergeRefreshPolicy.SNAPSHOT
                    if merged
                    else IdentityMergeRefreshPolicy.NONE
                ),
                changed_asset_ids=changed_asset_ids if merged else (),
                person_redirects=(
                    {source_ref.entity_id: target_ref.entity_id} if merged else {}
                ),
            )

        if source_ref.kind == target_ref.kind == "pet":
            changed_asset_ids = tuple(
                dict.fromkeys(
                    self._pet_service.pet_asset_ids(source_ref.entity_id)
                    + self._pet_service.pet_asset_ids(target_ref.entity_id)
                )
            )
            merged = self._pet_service.merge_pets(
                source_ref.entity_id,
                target_ref.entity_id,
            )
            return IdentityMergeOutcome(
                merged,
                source_ref,
                target_ref,
                None if merged else IdentityMergeFailure.REJECTED,
                refresh_policy=(
                    IdentityMergeRefreshPolicy.SNAPSHOT
                    if merged
                    else IdentityMergeRefreshPolicy.NONE
                ),
                changed_asset_ids=changed_asset_ids if merged else (),
                pet_redirects=(
                    {source_ref.entity_id: target_ref.entity_id} if merged else {}
                ),
            )

        changed_asset_ids = tuple(
            dict.fromkeys(self._asset_ids(source_ref) + self._asset_ids(target_ref))
        )
        result = self._people_service.merge_identities(source_ref.key, target_ref.key)
        if result is None or not result.merged:
            return IdentityMergeOutcome(
                False,
                source_ref,
                target_ref,
                IdentityMergeFailure.REDIRECT_CONFLICT,
            )
        return IdentityMergeOutcome(
            True,
            source_ref,
            target_ref,
            refresh_policy=IdentityMergeRefreshPolicy.IMMEDIATE,
            changed_asset_ids=changed_asset_ids,
            group_redirects=dict(result.group_redirects),
        )

    def _hidden_state(self, identity: IdentityRef) -> bool | None:
        if identity.kind == "person":
            summaries = self._people_service.list_clusters(include_hidden=True)
            summary = next(
                (item for item in summaries if item.person_id == identity.entity_id),
                None,
            )
        else:
            summaries = self._pet_service.list_pets(include_hidden=True)
            summary = next(
                (item for item in summaries if item.pet_id == identity.entity_id),
                None,
            )
        return bool(summary.is_hidden) if summary is not None else None

    def _asset_ids(self, identity: IdentityRef) -> list[str]:
        if identity.kind == "person":
            return list(self._people_service.cluster_asset_ids(identity.entity_id))
        return list(self._pet_service.pet_asset_ids(identity.entity_id))


__all__ = [
    "IdentityKind",
    "IdentityMergeFailure",
    "IdentityMergeOutcome",
    "IdentityMergeRefreshPolicy",
    "IdentityRef",
    "RecognitionMergeService",
]
