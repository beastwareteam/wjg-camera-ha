from typing import Any, Iterable, cast

from custom_components.wjg_camera.coordinator import WJGCameraCoordinator


def as_any(value: object) -> Any:
    return cast(Any, value)


def make_coordinator(hass: object, entry: object) -> WJGCameraCoordinator:
    return WJGCameraCoordinator(as_any(hass), as_any(entry))


def set_private_attr(obj: object, name: str, value: Any) -> None:
    setattr(obj, name, value)


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