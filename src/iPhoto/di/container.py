from __future__ import annotations

import warnings
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
        # Legacy stores kept for backward-compatible register() path
        self._factories: Dict[Type, Callable] = {}
        self._singletons: Dict[Type, Any] = {}
        self._singleton_flags: Dict[Type, bool] = {}
        self._resolving: Set[Type] = set()

    # --- New registration API ---

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

    def register_factory(self, interface: Type, factory: Callable):
        self._registrations[interface] = Registration(
            interface=interface,
            lifetime=Lifetime.TRANSIENT,
            factory=factory,
        )

    # --- Backward-compatible register() ---

    def register(
        self,
        interface: Type,
        implementation: Optional[Type] = None,
        factory: Optional[Callable] = None,
        singleton: bool = False,
        args: list = None,
        kwargs: dict = None,
    ):
        warnings.warn(
            "register() is deprecated, use register_singleton/register_transient/register_factory instead",
            DeprecationWarning,
            stacklevel=2,
        )
        args = args or []
        kwargs = kwargs or {}

        if factory:
            self._factories[interface] = lambda: factory(*args, **kwargs)
        elif implementation:
            self._factories[interface] = lambda: implementation(*args, **kwargs)
        else:
            self._factories[interface] = lambda: interface(*args, **kwargs)

        self._singleton_flags[interface] = singleton
        if singleton:
            self._singletons[interface] = None

    # --- Resolution ---

    def resolve(self, interface: Type) -> Any:
        # Check new-style registrations first
        if interface in self._registrations:
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

        # Fall back to legacy stores
        if self._singleton_flags.get(interface, False):
            if self._singletons[interface] is None:
                if interface not in self._factories:
                    raise ResolutionError(f"No registration found for {interface}")
                self._singletons[interface] = self._factories[interface]()
            return self._singletons[interface]

        if interface not in self._factories:
            raise ValueError(f"No registration found for {interface}")

        return self._factories[interface]()

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
