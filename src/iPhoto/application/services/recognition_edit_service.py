"""Session-bound edits with explicit annotation or identity scope."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

from iPhoto.domain.recognition_edits import (
    AnnotationEditContext,
    IdentityRef,
    IdentityRenameRequest,
    IdentitySelectionRequest,
    InlineSelectionScope,
    RecognitionEditOutcome,
    RecognitionEditStatus,
)

LOGGER = logging.getLogger(__name__)


def annotation_edit_context(asset_id: str, annotation: object) -> AnnotationEditContext:
    """Adapt native or presentation annotation records without importing Qt."""

    kind = getattr(annotation, "source_detection_kind", None)
    if kind is None:
        kind = "pet" if hasattr(annotation, "pet_id") else "person"
    annotation_id = getattr(annotation, "source_annotation_id", None) or getattr(
        annotation, "detection_id" if kind == "pet" else "face_id", ""
    )
    native_identity = getattr(annotation, "pet_id" if kind == "pet" else "person_id", None)

    def ref(ref_kind: str, value: object) -> IdentityRef | None:
        if not value:
            return None
        return IdentityRef.parse(value) or IdentityRef(ref_kind, str(value))

    source_id = getattr(annotation, "source_identity_id", None)
    if not hasattr(annotation, "source_detection_kind"):
        source_id = source_id or native_identity
    source = ref(
        getattr(annotation, "source_identity_kind", kind),
        source_id,
    )
    current = ref(
        getattr(annotation, "canonical_identity_kind", kind),
        getattr(annotation, "canonical_identity_id", None) or native_identity,
    )
    return AnnotationEditContext(
        asset_id=asset_id,
        source_kind=kind,
        annotation_id=str(annotation_id),
        source_identity=source,
        current_identity=current,
        is_manual=bool(getattr(annotation, "is_manual", False)),
        promotion_state=getattr(annotation, "promotion_state", "legacy_visible"),
    )


class RecognitionEditService:
    def __init__(self, *, people_service, pet_service, merge_service, mutation_coordinator):
        self._people = people_service
        self._pets = pet_service
        self._merges = merge_service
        self._mutations = mutation_coordinator

    def reassign_annotation(self, request: IdentitySelectionRequest) -> RecognitionEditOutcome:
        return self._execute(request.context, lambda _: self._reassign(request))

    def merge_candidate_identity(self, request: IdentitySelectionRequest) -> RecognitionEditOutcome:
        return self._execute(request.context, lambda _: self._merge_candidate(request))

    def rename_identity(self, request: IdentityRenameRequest) -> RecognitionEditOutcome:
        return self._execute(request.context, lambda annotation: self._rename(request, annotation))

    def _execute(
        self,
        context: AnnotationEditContext,
        operation: Callable[[object], RecognitionEditOutcome],
    ) -> RecognitionEditOutcome:
        if not context.asset_id or not context.annotation_id:
            return self._rejected("not_found")
        try:
            with self._mutations.mutation_scope():
                if not self._mutations.recover_pending():
                    return self._rejected("recovery_pending")
                # Bypass the presentation cache while holding the same lease as writes.
                annotations = (
                    self._people.list_asset_face_annotations(context.asset_id)
                    if context.source_kind == "person"
                    else self._pets.list_asset_pet_annotations(context.asset_id)
                )
                for annotation in annotations:
                    current = annotation_edit_context(context.asset_id, annotation)
                    if current.annotation_id != context.annotation_id:
                        continue
                    if current != context:
                        return self._rejected("context_changed")
                    return operation(annotation)
                return self._rejected("not_found")
        except (sqlite3.Error, OSError):
            LOGGER.exception("Recognition edit failed for %s", context.annotation_id)
            return self._rejected("io_error")

    def _exists(self, identity: IdentityRef) -> bool:
        return bool(
            self._people.has_cluster(identity.entity_id)
            if identity.kind == "person"
            else self._pets.has_pet(identity.entity_id)
        )

    def _selection_check(self, request: IdentitySelectionRequest) -> RecognitionEditOutcome | None:
        if request.target == request.context.current_identity:
            return RecognitionEditOutcome(RecognitionEditStatus.UNCHANGED)
        if not self._exists(request.target):
            return self._rejected("not_found")
        return None

    def _reassign(self, request: IdentitySelectionRequest) -> RecognitionEditOutcome:
        checked = self._selection_check(request)
        if checked is not None:
            return checked
        context, target = request.context, request.target
        if context.source_kind != target.kind:
            changed = self._people.reassign_detection_identity(
                source_kind=context.source_kind,
                source_annotation_id=context.annotation_id,
                target_identity=target.key,
            )
        elif context.source_kind == "person":
            changed = self._people.move_face_to_person(context.annotation_id, target.entity_id)
        else:
            outcome = self._pets.move_detection_to_pet_with_outcome(
                context.annotation_id, target.entity_id
            )
            if not outcome.succeeded:
                return self._rejected(str(outcome.failure or "rejected"))
            changed = True
        return self._changed(changed)

    def _merge_candidate(self, request: IdentitySelectionRequest) -> RecognitionEditOutcome:
        checked = self._selection_check(request)
        if checked is not None:
            return checked
        if request.context.selection_scope != InlineSelectionScope.CANDIDATE_IDENTITY:
            return self._rejected("context_changed")
        outcome = self._merges.merge(request.context.current_identity, request.target)
        if not outcome.merged:
            return self._rejected(str(outcome.failure or "rejected"))
        return self._changed(True)

    def _rename(self, request: IdentityRenameRequest, annotation: object) -> RecognitionEditOutcome:
        identity = request.context.current_identity
        if identity is None or not self._exists(identity):
            return self._rejected("not_found")
        name = (request.name or "").strip() or None
        current_name = getattr(annotation, "canonical_display_name", None) or getattr(
            annotation, "display_name", None
        )
        if name == current_name:
            return RecognitionEditOutcome(RecognitionEditStatus.UNCHANGED)
        changed = (
            self._people.rename_cluster(identity.entity_id, name)
            if identity.kind == "person"
            else self._pets.rename_pet(identity.entity_id, name)
        )
        return self._changed(changed)

    @staticmethod
    def _changed(changed: bool) -> RecognitionEditOutcome:
        return RecognitionEditOutcome(
            RecognitionEditStatus.CHANGED if changed else RecognitionEditStatus.REJECTED,
            None if changed else "rejected",
        )

    @staticmethod
    def _rejected(failure: str) -> RecognitionEditOutcome:
        return RecognitionEditOutcome(RecognitionEditStatus.REJECTED, failure)
