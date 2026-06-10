from typing import Any, Iterable, cast

from custom_components.wjg_camera.coordinator import WJGCameraCoordinator


def as_any(value: object) -> Any:
    return cast(Any, value)


def make_coordinator(hass: object, entry: object) -> WJGCameraCoordinator:
    # DataUpdateCoordinator (HA ≥2025) registriert sich am Config-Entry —
    # Dummy-Entries der Tests bekommen die nötige Methode nachgerüstet.
    if not hasattr(entry, "async_on_unload"):
        setattr(entry, "async_on_unload", lambda _func: None)
    return WJGCameraCoordinator(as_any(hass), as_any(entry))


def set_private_attr(obj: object, name: str, value: Any) -> None:
    setattr(obj, name, value)


def mark_port_open(coordinator: Any, port: int) -> None:
    """Port im TCP-Status-Cache als offen markieren.

    Nötig in Tests mit Fake-Sessions: Der Coordinator prüft vor XM-/HTTP-
    Fallbacks per echtem TCP-Connect, ob der Port erreichbar ist.
    """
    import time

    coordinator._port_state_cache[port] = (True, time.time())


def get_private_attr(obj: object, name: str) -> Any:
    return getattr(obj, name)


async def call_private_async(obj: object, name: str) -> Any:
    return await getattr(obj, name)()


def private_name(name: str) -> str:
    return f"_{name}"


def make_add_entities_callback(added: list[Any]):
    def _add_entities(new_entities: Iterable[Any], update_before_add: bool = False) -> None:
        _ = update_before_add
        added.extend(new_entities)

    return _add_entities