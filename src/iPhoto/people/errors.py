"""Availability failures for the optional face recognition runtime."""

from iPhoto.application.ports.recognition_extension import ModelFailureReason


class FaceRuntimeUnavailableError(RuntimeError):
    """The optional runtime could not initialize or continue inference."""


class FaceModelUnavailableError(RuntimeError):
    """A required face model could not be acquired or is incomplete."""

    def __init__(self, message: str, reason: ModelFailureReason):
        super().__init__(message)
        self.reason = reason
