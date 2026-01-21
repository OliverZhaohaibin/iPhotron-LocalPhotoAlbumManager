from typing import Any, Callable, Dict, Type, Optional

class DependencyContainer:
    def __init__(self):
        self._factories: Dict[Type, Callable] = {}
        self._singletons: Dict[Type, Any] = {}
        self._singleton_flags: Dict[Type, bool] = {}

    def register(
        self,
        interface: Type,
        implementation: Optional[Type] = None,
        factory: Optional[Callable] = None,
        singleton: bool = False,
        args: list = None,
        kwargs: dict = None
    ):
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

    def resolve(self, interface: Type) -> Any:
        if self._singleton_flags.get(interface, False):
            if self._singletons[interface] is None:
                if interface not in self._factories:
                     raise ValueError(f"No registration found for {interface}")
                self._singletons[interface] = self._factories[interface]()
            return self._singletons[interface]

        if interface not in self._factories:
             raise ValueError(f"No registration found for {interface}")

        return self._factories[interface]()
