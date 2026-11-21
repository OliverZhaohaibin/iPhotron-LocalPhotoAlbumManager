"""Typed failures for the optional Pets recognition runtime."""

from __future__ import annotations

from iPhoto.application.ports.recognition_extension import ModelFailureReason


class PetError(RuntimeError):
    """Base class for Pets processing failures."""


class PetRuntimeUnavailableError(PetError):
    """The optional Python/native runtime required by Pets is unavailable."""


class PetModelUnavailableError(PetError):
    """A required, verified Pets model artifact is unavailable."""

    def __init__(self, message: str, *, reason: ModelFailureReason | None = None):
        super().__init__(message)
        self.reason = reason


class PetInferenceError(PetError):
    """Inference failed for an individual asset."""


class PetStateCommitError(PetError):
    """Runtime data committed but durable bookkeeping needs recovery."""


class PetPipelineInvariantError(PetError):
    """A model or pipeline contract was violated."""


__all__ = [
    "PetError",
    "PetInferenceError",
    "PetModelUnavailableError",
    "PetPipelineInvariantError",
    "PetRuntimeUnavailableError",
    "PetStateCommitError",
]
