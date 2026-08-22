"""Pets bounded context."""

from .records import (
    PetDetectionRecord,
    PetProfile,
    PetRecord,
    PetSummary,
)
from .status import (
    PET_STATUS_DONE,
    PET_STATUS_FAILED,
    PET_STATUS_PENDING,
    PET_STATUS_RETRY,
    PET_STATUS_SKIPPED,
    initial_pet_status,
    is_pet_scan_candidate,
    normalize_pet_status,
)

# Keep the public pipeline API unchanged while migrating first-use DINOv2
# acquisition from a prebuilt project release to Meta's official checkpoint.
# Importing here guarantees cached-model validation uses the same contract on
# subsequent application launches, not only during the first bootstrap call.
from . import pipeline as _pipeline  # noqa: E402
from .model_runtime import install_pet_model_runtime as _install_pet_model_runtime  # noqa: E402

_install_pet_model_runtime(_pipeline)

__all__ = [
    "PET_STATUS_DONE",
    "PET_STATUS_FAILED",
    "PET_STATUS_PENDING",
    "PET_STATUS_RETRY",
    "PET_STATUS_SKIPPED",
    "PetDetectionRecord",
    "PetProfile",
    "PetRecord",
    "PetSummary",
    "initial_pet_status",
    "is_pet_scan_candidate",
    "normalize_pet_status",
]
