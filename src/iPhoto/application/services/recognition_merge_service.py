"""Typed orchestration for People/Pets identity merges."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

from iPhoto.pets.records import PetMergeOutcome, PetMutationFailure
from iPhoto.recognition.mutation_coordinator import (
    RecognitionMutationCoordinator,
    get_recognition_mutation_coordinator,
)

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
    RECOVERY_PENDING = "recovery_pending"
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


@dataclass(frozen=True, slots=True)
class _MergeRecoveryResult:
    blocked: bool = False
    matching_outcome: IdentityMergeOutcome | None = None


class RecognitionMergeService:
    """Route every directional identity merge through one typed boundary."""

    def __init__(
        self,
        people_service,
        pet_service,
        *,
        mutation_coordinator: RecognitionMutationCoordinator | None = None,
    ) -> None:
        self._people_service = people_service
        self._pet_service = pet_service
        self._journal = None
        self._recovered_outcomes: dict[tuple[str, str], IdentityMergeOutcome] = {}
        root_getter = getattr(pet_service, "library_root", None)
        root = root_getter() if callable(root_getter) else None
        if isinstance(root, (str, Path)):
            self._journal = mutation_coordinator or get_recognition_mutation_coordinator(
                Path(root)
            )
            self._journal.register_recovery_handler(
                {"recognition_merge"},
                self._recover_registered_merge,
            )
            self._recover_merges()

    def _recover_registered_merge(self, operation) -> bool:
        if operation.kind != "recognition_merge":
            return False
        source = IdentityRef.parse(operation.payload.get("source"))
        target = IdentityRef.parse(operation.payload.get("target"))
        if source is None or target is None:
            self._finish_merge(operation.operation_id, False)
            return True
        if source.kind == target.kind:
            return False
        result = self._people_service.merge_identities(source.key, target.key)
        succeeded = bool(result is not None and result.merged)
        self._finish_merge(operation.operation_id, succeeded)
        self._recovered_outcomes[(source.key, target.key)] = IdentityMergeOutcome(
            succeeded,
            source,
            target,
            None if succeeded else IdentityMergeFailure.REDIRECT_CONFLICT,
            refresh_policy=(
                IdentityMergeRefreshPolicy.IMMEDIATE
                if succeeded
                else IdentityMergeRefreshPolicy.NONE
            ),
            changed_asset_ids=tuple(
                str(value)
                for value in operation.payload.get("changed_asset_ids", ())
                if value
            ),
            group_redirects=(
                dict(result.group_redirects)
                if succeeded and result is not None
                else {}
            ),
        )
        return True

    def merge(self, source: IdentityRef | str, target: IdentityRef | str) -> IdentityMergeOutcome:
        if self._journal is None:
            return self._merge_locked(source, target)
        with self._journal.mutation_scope():
            return self._merge_locked(source, target)

    def _merge_locked(
        self,
        source: IdentityRef | str,
        target: IdentityRef | str,
    ) -> IdentityMergeOutcome:
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

        recovery = self._recover_merges(source_ref, target_ref)
        if recovery.matching_outcome is not None:
            return recovery.matching_outcome
        if recovery.blocked:
            return IdentityMergeOutcome(
                False,
                source_ref,
                target_ref,
                IdentityMergeFailure.RECOVERY_PENDING,
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
            pending = bool(
                not merged
                and callable(
                    pending_getter := getattr(
                        self._people_service,
                        "recognition_mutation_pending",
                        None,
                    )
                )
                and pending_getter()
            )
            return IdentityMergeOutcome(
                merged,
                source_ref,
                target_ref,
                None
                if merged
                else IdentityMergeFailure.RECOVERY_PENDING
                if pending
                else IdentityMergeFailure.REJECTED,
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
            pet_outcome = self._pet_service.merge_pets(
                source_ref.entity_id,
                target_ref.entity_id,
            )
            if isinstance(pet_outcome, PetMergeOutcome):
                merged = pet_outcome.merged
                temporary_failure = pet_outcome.failure in {
                    PetMutationFailure.RECOVERY_PENDING,
                    PetMutationFailure.SHUTTING_DOWN,
                }
            else:
                merged = bool(pet_outcome)
                temporary_failure = False
            return IdentityMergeOutcome(
                merged,
                source_ref,
                target_ref,
                None
                if merged
                else IdentityMergeFailure.RECOVERY_PENDING
                if temporary_failure
                else IdentityMergeFailure.REJECTED,
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
        operation_id = self._prepare_merge(
            source_ref,
            target_ref,
            changed_asset_ids=changed_asset_ids,
        )
        if self._journal is not None and operation_id is None:
            return IdentityMergeOutcome(
                False,
                source_ref,
                target_ref,
                IdentityMergeFailure.RECOVERY_PENDING,
            )

        result = self._people_service.merge_identities(source_ref.key, target_ref.key)
        if result is None or not result.merged:
            self._finish_merge(operation_id, False)
            return IdentityMergeOutcome(
                False,
                source_ref,
                target_ref,
                IdentityMergeFailure.REDIRECT_CONFLICT,
            )
        self._finish_merge(operation_id, True)
        return IdentityMergeOutcome(
            True,
            source_ref,
            target_ref,
            refresh_policy=IdentityMergeRefreshPolicy.IMMEDIATE,
            changed_asset_ids=changed_asset_ids,
            group_redirects=dict(result.group_redirects),
        )

    def _prepare_merge(
        self,
        source: IdentityRef,
        target: IdentityRef,
        *,
        changed_asset_ids: tuple[str, ...],
    ) -> str | None:
        if self._journal is None:
            return None
        operation_id = self._journal.try_prepare(
            "recognition_merge",
            {
                "source": source.key,
                "target": target.key,
                "changed_asset_ids": list(changed_asset_ids),
            },
        )
        if operation_id is None:
            return None
        return operation_id

    def _finish_merge(self, operation_id: str | None, succeeded: bool) -> None:
        if self._journal is None or operation_id is None:
            return
        if not succeeded:
            self._journal.transition(
                operation_id,
                "finalized",
                error="merge_rejected",
            )
            return
        self._journal.commit_and_dispatch(
            operation_id,
            {"kind": "recognition_merge"},
            lambda: None,
        )

    def _recover_merges(
        self,
        requested_source: IdentityRef | None = None,
        requested_target: IdentityRef | None = None,
    ) -> _MergeRecoveryResult:
        if self._journal is None:
            return _MergeRecoveryResult()
        if not self._journal.recover_pending():
            return _MergeRecoveryResult(blocked=True)
        if requested_source is None or requested_target is None:
            return _MergeRecoveryResult()
        return _MergeRecoveryResult(
            matching_outcome=self._recovered_outcomes.pop(
                (requested_source.key, requested_target.key),
                None,
            )
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
