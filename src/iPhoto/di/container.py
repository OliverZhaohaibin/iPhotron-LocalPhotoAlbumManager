from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Set, Type

from .lifetime import Lifetime
from ..errors import CircularDependencyError, ResolutionError


@dataclass
class Registration:
    interface: Type
    implementation: Optional[Type] = None
    lifetime: Lifetime = Lifetime.TRANSIENT
    factory: Optional[Callable] = None
    kwargs: Dict[str, Any] = field(default_factory=dict)


class Scope:
    """Scoped container that caches SCOPED registrations for its lifetime."""

    def __init__(self, container: Container):
        self._container = container
        self._instances: Dict[Type, Any] = {}

    def resolve(self, interface: Type) -> Any:
        reg = self._container._get_registration(interface)
        if reg.lifetime == Lifetime.SCOPED:
            if interface not in self._instances:
                self._instances[interface] = self._container._create(reg)
            return self._instances[interface]
        return self._container.resolve(interface)

    def dispose(self):
        self._instances.clear()


class Container:
    def __init__(self):
        self._registrations: Dict[Type, Registration] = {}
        self._singleton_instances: Dict[Type, Any] = {}
        self._resolving: Set[Type] = set()

    # --- Registration API ---

    def register_singleton(self, interface: Type, implementation: Optional[Type] = None, **kwargs):
        self._registrations[interface] = Registration(
            interface=interface,
            implementation=implementation or interface,
            lifetime=Lifetime.SINGLETON,
            kwargs=kwargs,
        )

    def register_transient(self, interface: Type, implementation: Optional[Type] = None, **kwargs):
        self._registrations[interface] = Registration(
            interface=interface,
            implementation=implementation or interface,
            lifetime=Lifetime.TRANSIENT,
            kwargs=kwargs,
        )

    def register_scoped(self, interface: Type, implementation: Optional[Type] = None, **kwargs):
        self._registrations[interface] = Registration(
            interface=interface,
            implementation=implementation or interface,
            lifetime=Lifetime.SCOPED,
            kwargs=kwargs,
        )

    def register_factory(self, interface: Type, factory: Callable, singleton: bool = False):
        self._registrations[interface] = Registration(
            interface=interface,
            lifetime=Lifetime.SINGLETON if singleton else Lifetime.TRANSIENT,
            factory=factory,
        )

    def register_instance(self, interface: Type, instance: Any):
        """Register a pre-existing instance as a singleton."""
        self._registrations[interface] = Registration(
            interface=interface,
            lifetime=Lifetime.SINGLETON,
        )
        self._singleton_instances[interface] = instance

    # --- Resolution ---

    def resolve(self, interface: Type) -> Any:
        if interface not in self._registrations:
            raise ResolutionError(f"No registration found for {interface}")

        if interface in self._resolving:
            raise CircularDependencyError(
                f"Circular dependency detected for {interface}"
            )
        self._resolving.add(interface)
        try:
            reg = self._registrations[interface]
            if reg.lifetime == Lifetime.SINGLETON:
                if interface not in self._singleton_instances:
                    self._singleton_instances[interface] = self._create(reg)
                return self._singleton_instances[interface]
            return self._create(reg)
        finally:
            self._resolving.discard(interface)

    def create_scope(self) -> Scope:
        return Scope(self)

    # --- Helpers ---

    def _get_registration(self, interface: Type) -> Registration:
        if interface not in self._registrations:
            raise ResolutionError(f"No registration found for {interface}")
        return self._registrations[interface]

    def _create(self, reg: Registration) -> Any:
        if reg.factory:
            return reg.factory()
        impl = reg.implementation or reg.interface
        return impl(**reg.kwargs)


# Backward-compatible alias
DependencyContainer = Container
