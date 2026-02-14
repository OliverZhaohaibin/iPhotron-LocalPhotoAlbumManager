from .container import Container
from iPhoto.events.bus import EventBus

def bootstrap(container: Container) -> None:
    """Register all application services in the DI container."""
    container.register_singleton(EventBus, EventBus)
