"""Custom exception hierarchy for iPhoto."""

from __future__ import annotations


class IPhotoError(Exception):
    """Base class for all custom errors raised by iPhoto."""


# --- 3-layer hierarchy ---

class DomainError(IPhotoError):
    """Base class for domain-level errors."""


class InfrastructureError(IPhotoError):
    """Base class for infrastructure-level errors."""


class ApplicationError(IPhotoError):
    """Base class for application-level errors."""


# --- Domain errors ---

class AlbumNotFoundError(DomainError):
    """Raised when the requested album cannot be located."""


class AssetNotFoundError(DomainError):
    """Raised when the requested asset cannot be located."""


# --- Infrastructure errors ---

class DatabaseError(InfrastructureError):
    """Raised when a database operation fails."""


class ConnectionPoolExhausted(InfrastructureError):
    """Raised when no connections are available in the pool."""


# --- Application errors ---

class ScanError(ApplicationError):
    """Raised when an album scan fails."""


class ImportError_(ApplicationError):
    """Raised when an asset import fails (underscore avoids shadowing builtin)."""


# --- DI-specific errors ---

class CircularDependencyError(IPhotoError):
    """Raised when a circular dependency is detected during resolution."""


class ResolutionError(IPhotoError):
    """Raised when a dependency cannot be resolved."""


# --- Existing errors (kept for backward compatibility) ---

class ManifestInvalidError(IPhotoError):
    """Raised when a manifest fails validation against the schema."""


class ExternalToolError(IPhotoError):
    """Raised when an external tool such as exiftool or ffmpeg fails."""


class IndexCorruptedError(IPhotoError):
    """Raised when the cached index cannot be parsed."""


class PairingConflictError(IPhotoError):
    """Raised when mutually exclusive Live Photo pairings are detected."""


class LockTimeoutError(IPhotoError):
    """Raised when a file-level lock cannot be acquired in time."""


class SettingsError(IPhotoError):
    """Base class for settings related failures."""


class SettingsLoadError(SettingsError):
    """Raised when the settings file cannot be parsed or loaded."""


class SettingsValidationError(SettingsError):
    """Raised when settings data fails schema validation."""


class LibraryError(IPhotoError):
    """Base class for errors occurring while managing the basic library."""


class LibraryUnavailableError(LibraryError):
    """Raised when the configured basic library cannot be accessed."""


class AlbumNameConflictError(LibraryError):
    """Raised when trying to create or rename an album to an existing name."""


class AlbumDepthError(LibraryError):
    """Raised when attempting to create albums deeper than the supported nesting level."""


class AlbumOperationError(LibraryError):
    """Raised when album file-system operations fail."""
