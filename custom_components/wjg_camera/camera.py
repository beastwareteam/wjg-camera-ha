"""
WJG Camera Entity
=================
Stellt Livestream (RTSP) und Snapshot für HA zur Verfügung.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, MANUFACTURER, MODEL
from .coordinator import WJGCameraCoordinator

_LOGGER = logging.getLogger(__name__)

# 1x1 transparentes PNG als letzter Fallback, damit camera_proxy nicht mit 500 endet.
_FALLBACK_IMAGE_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\x0bIDATx\x9cc``\x00\x00\x00\x02\x00\x01\xe2!\xbc3"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: WJGCameraCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([WJGCamera(coordinator, entry)])

class WJGCamera(  # pyright: ignore[reportArgumentType]
    CoordinatorEntity[WJGCameraCoordinator],
    Camera,
):
    """Kamera-Entity für WJG XM-3820."""

    _attr_has_entity_name = True
    _attr_name = None  # Gerätename als Entity-Name

    def __init__(
        self, coordinator: WJGCameraCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        Camera.__init__(self)
        # RTSP over TCP ist in HA-Container-Umgebungen robuster als UDP.
        self.stream_options["rtsp_transport"] = "tcp"
        self._entry = entry
        self._last_camera_image: bytes | None = None
        self._attr_unique_id = f"{entry.entry_id}_camera"
        self._attr_supported_features = (
            CameraEntityFeature.STREAM | CameraEntityFeature.ON_OFF
        )

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=f"WJG {MODEL}",
            manufacturer=MANUFACTURER,
            model=MODEL,
            configuration_url=(
                f"http://{self.coordinator.host}:{self.coordinator.http_port}"
            ),
        )

    @property
    def available(self) -> bool:
        return bool(
            super().available and self.coordinator.data
            and self.coordinator.data.get("available", False)
        )

    @property
    def is_recording(self) -> bool:
        return self.coordinator.is_recording

    @property
    def motion_detection_enabled(self) -> bool:
        return True

    async def stream_source(self) -> str | None:
        """RTSP-Stream-URL zurückgeben."""
        return self.coordinator.rtsp_url

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Aktuelles Standbild laden."""
        _ = width
        _ = height
        # Bevorzuge gecachten Snapshot aus Coordinator
        cached_snapshot = None
        if self.coordinator.data and "snapshot_bytes" in self.coordinator.data:
            cached_snapshot = self.coordinator.data["snapshot_bytes"]
        if isinstance(cached_snapshot, (bytes, bytearray)) and cached_snapshot:
            self._last_camera_image = bytes(cached_snapshot)
            return self._last_camera_image

        live_snapshot = await self.coordinator.async_snapshot()
        if isinstance(live_snapshot, (bytes, bytearray)) and live_snapshot:
            self._last_camera_image = bytes(live_snapshot)
            return self._last_camera_image

        if self._last_camera_image:
            return self._last_camera_image

        _LOGGER.debug("Snapshot nicht verfügbar, liefere Fallback-Bild")
        return _FALLBACK_IMAGE_BYTES

    async def async_enable_motion_detection(self) -> None:
        """Bewegungserkennung aktivieren."""
        _LOGGER.info("Bewegungserkennung aktiviert")
        return None

    async def async_disable_motion_detection(self) -> None:
        """Bewegungserkennung deaktivieren."""
        _LOGGER.info("Bewegungserkennung deaktiviert")
        return None

    async def async_turn_on(self) -> None:
        """Kamera-Stream einschalten (IR-LEDs etc.)."""
        return None

    async def async_turn_off(self) -> None:
        """Kamera-Stream ausschalten."""
        return None

    def camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        raise NotImplementedError()

    def enable_motion_detection(self) -> None:
        raise NotImplementedError()

    def disable_motion_detection(self) -> None:
        raise NotImplementedError()

    def turn_on(self) -> None:
        raise NotImplementedError()

    def turn_off(self) -> None:
        raise NotImplementedError()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Zusätzliche Attribute für HA."""
        return {
            "rtsp_url": self.coordinator.rtsp_url,
            "snapshot_url": self.coordinator.snapshot_url,
            "host": self.coordinator.host,
            "protocol": self.coordinator.protocol,
        }
