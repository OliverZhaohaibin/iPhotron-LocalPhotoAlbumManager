from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class UseCaseRequest:
    """Use Case input DTO base."""
    pass

@dataclass(frozen=True)
class UseCaseResponse:
    """Use Case output DTO base."""
    success: bool = True
    error: Optional[str] = None

class UseCase(ABC):
    """Use Case base class."""
    
    @abstractmethod
    def execute(self, request: UseCaseRequest) -> UseCaseResponse:
        ...
