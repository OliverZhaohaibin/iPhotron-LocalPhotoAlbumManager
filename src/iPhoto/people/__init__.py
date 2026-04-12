"""People and face-clustering helpers."""

from .status import (
    FACE_STATUS_DONE,
    FACE_STATUS_FAILED,
    FACE_STATUS_PENDING,
    FACE_STATUS_RETRY,
    FACE_STATUS_SKIPPED,
    FACE_STATUS_VALUES,
    initial_face_status,
    is_face_scan_candidate,
    normalize_face_status,
)

__all__ = [
    "FACE_STATUS_DONE",
    "FACE_STATUS_FAILED",
    "FACE_STATUS_PENDING",
    "FACE_STATUS_RETRY",
    "FACE_STATUS_SKIPPED",
    "FACE_STATUS_VALUES",
    "initial_face_status",
    "is_face_scan_candidate",
    "normalize_face_status",
    "FaceLibraryPaths",
    "PeopleService",
    "PersonSummary",
    "PersonRecord",
    "FaceRecord",
    "FaceRepository",
    "FaceStateRepository",
    "face_library_paths",
]


def __getattr__(name: str):
    if name in {
        "FaceRecord",
        "FaceRepository",
        "FaceStateRepository",
        "PersonRecord",
        "PersonSummary",
    }:
        from .repository import (
            FaceRecord,
            FaceRepository,
            FaceStateRepository,
            PersonRecord,
            PersonSummary,
        )

        return {
            "FaceRecord": FaceRecord,
            "FaceRepository": FaceRepository,
            "FaceStateRepository": FaceStateRepository,
            "PersonRecord": PersonRecord,
            "PersonSummary": PersonSummary,
        }[name]

    if name in {"FaceLibraryPaths", "PeopleService", "face_library_paths"}:
        from .service import FaceLibraryPaths, PeopleService, face_library_paths

        return {
            "FaceLibraryPaths": FaceLibraryPaths,
            "PeopleService": PeopleService,
            "face_library_paths": face_library_paths,
        }[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
