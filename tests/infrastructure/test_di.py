import pytest
from iPhoto.di.container import DependencyContainer
from abc import ABC, abstractmethod

class IService(ABC):
    @abstractmethod
    def do_something(self):
        pass

class ServiceImpl(IService):
    def do_something(self):
        return "done"

class ServiceWithArgs(IService):
    def __init__(self, value):
        self.value = value

    def do_something(self):
        return self.value

def test_register_resolve_transient():
    container = DependencyContainer()
    container.register(IService, ServiceImpl)

    s1 = container.resolve(IService)
    s2 = container.resolve(IService)

    assert isinstance(s1, ServiceImpl)
    assert isinstance(s2, ServiceImpl)
    assert s1 is not s2

def test_register_resolve_singleton():
    container = DependencyContainer()
    container.register(IService, ServiceImpl, singleton=True)

    s1 = container.resolve(IService)
    s2 = container.resolve(IService)

    assert s1 is s2

def test_register_with_factory():
    container = DependencyContainer()
    container.register(IService, factory=lambda: ServiceImpl())

    s1 = container.resolve(IService)
    assert isinstance(s1, ServiceImpl)

def test_register_with_args():
    container = DependencyContainer()
    container.register(IService, ServiceWithArgs, args=["test_value"])

    s1 = container.resolve(IService)
    assert isinstance(s1, ServiceWithArgs)
    assert s1.do_something() == "test_value"

def test_resolve_unregistered():
    container = DependencyContainer()
    with pytest.raises(ValueError):
        container.resolve(IService)
