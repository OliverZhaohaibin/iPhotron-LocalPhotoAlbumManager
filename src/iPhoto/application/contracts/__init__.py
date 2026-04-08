"""Application-layer contracts.

This package defines the formal API contracts for the application's entry
points and boundaries.  All concrete implementations must satisfy the
interfaces declared here.

Phase 4 addition – stabilises the runtime entry contract so that new
code depends on an abstract interface rather than a concrete class.
"""

from .runtime_entry_contract import RuntimeEntryContract

__all__ = ["RuntimeEntryContract"]
