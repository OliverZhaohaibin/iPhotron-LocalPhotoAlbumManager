"""Enhanced tests for the DI container (Phase 1/2 refactoring)."""

import pytest
from iPhoto.di.container import Container, DependencyContainer, Registration, Scope
from iPhoto.di.lifetime import Lifetime
from iPhoto.errors import CircularDependencyError, ResolutionError


class _Dummy:
    """Simple class used as a registration target."""
    pass


class _DummyWithKwargs:
    def __init__(self, name="default", value=0):
        self.name = name
        self.value = value


def test_register_singleton_returns_same_instance():
    container = Container()
    container.register_singleton(_Dummy)
    first = container.resolve(_Dummy)
    second = container.resolve(_Dummy)
    assert first is second


def test_register_transient_returns_new_instance():
    container = Container()
    container.register_transient(_Dummy)
    first = container.resolve(_Dummy)
    second = container.resolve(_Dummy)
    assert first is not second


def test_circular_dependency_raises_error():
    container = Container()

    class A:
        def __init__(self, b):
            self.b = b

    class B:
        def __init__(self, a):
            self.a = a

    container.register_factory(A, lambda: A(container.resolve(B)))
    container.register_factory(B, lambda: B(container.resolve(A)))

    with pytest.raises(CircularDependencyError):
        container.resolve(A)


def test_unregistered_raises_resolution_error():
    container = Container()

    class Unregistered:
        pass

    with pytest.raises((ResolutionError, ValueError)):
        container.resolve(Unregistered)


def test_factory_registration():
    container = Container()
    called = []

    def factory():
        called.append(True)
        return _Dummy()

    container.register_factory(_Dummy, factory)
    result = container.resolve(_Dummy)
    assert isinstance(result, _Dummy)
    assert len(called) == 1


def test_scoped_lifetime_same_within_scope():
    container = Container()
    container.register_scoped(_Dummy)
    scope = container.create_scope()
    first = scope.resolve(_Dummy)
    second = scope.resolve(_Dummy)
    assert first is second


def test_scoped_lifetime_different_across_scopes():
    container = Container()
    container.register_scoped(_Dummy)
    scope1 = container.create_scope()
    scope2 = container.create_scope()
    inst1 = scope1.resolve(_Dummy)
    inst2 = scope2.resolve(_Dummy)
    assert inst1 is not inst2


def test_scope_dispose_clears_instances():
    container = Container()
    container.register_scoped(_Dummy)
    scope = container.create_scope()
    scope.resolve(_Dummy)
    assert len(scope._instances) == 1
    scope.dispose()
    assert len(scope._instances) == 0


def test_backward_compat_alias():
    assert DependencyContainer is Container


def test_register_with_kwargs():
    container = Container()
    container.register_singleton(_DummyWithKwargs, name="test", value=42)
    instance = container.resolve(_DummyWithKwargs)
    assert instance.name == "test"
    assert instance.value == 42


def test_register_instance_returns_same_object():
    container = Container()
    obj = _Dummy()
    container.register_instance(_Dummy, obj)
    first = container.resolve(_Dummy)
    second = container.resolve(_Dummy)
    assert first is obj
    assert second is obj
