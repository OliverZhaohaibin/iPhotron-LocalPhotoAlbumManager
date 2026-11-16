"""Process-scoped installation and library-scoped model availability events."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from .extension_install import ExtensionProgress

RecognitionKind = Literal["face", "pet"]
ModelFailureReason = Literal["missing", "download_failed", "incomplete", "download_disabled"]


@dataclass(frozen=True)
class RecognitionModelFailure:
    library_root: Path
    generation: int
    kind: RecognitionKind
    reason: ModelFailureReason


@dataclass(frozen=True)
class RecognitionExtensionRequest:
    local_archive_path: Path | None = None


@dataclass(frozen=True)
class RecognitionExtensionResult:
    model_root: Path


class RecognitionExtensionPort(Protocol):
    @property
    def download_url(self) -> str: ...

    def execute(
        self, request: RecognitionExtensionRequest, progress: ExtensionProgress
    ) -> RecognitionExtensionResult: ...
