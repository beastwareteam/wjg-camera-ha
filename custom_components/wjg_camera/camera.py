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
        """Aktuelles Standbild laden, optional mit Digital-Zoom-Crop."""
        _ = width
        _ = height
        # Bevorzuge gecachten Snapshot aus Coordinator
        cached_snapshot = None
        if self.coordinator.data and "snapshot_bytes" in self.coordinator.data:
            cached_snapshot = self.coordinator.data["snapshot_bytes"]
        if isinstance(cached_snapshot, (bytes, bytearray)) and cached_snapshot:
            image_bytes: bytes = bytes(cached_snapshot)
        else:
            live_snapshot = await self.coordinator.async_snapshot()
            if isinstance(live_snapshot, (bytes, bytearray)) and live_snapshot:
                image_bytes = bytes(live_snapshot)
            elif self._last_camera_image:
                return self._last_camera_image
            else:
                _LOGGER.debug("Snapshot nicht verfügbar, liefere Fallback-Bild")
                return _FALLBACK_IMAGE_BYTES

        # Digital Zoom: Pillow-Crop wenn Zoom > 1
        zoom = self.coordinator.digital_zoom
        if zoom > 1.0:
            try:
                image_bytes = await self.hass.async_add_executor_job(
                    self._crop_for_zoom, image_bytes, zoom
                )
            except Exception as exc:
                _LOGGER.warning("Digital-Zoom-Crop fehlgeschlagen: %s", exc)

        self._last_camera_image = image_bytes
        return image_bytes

    def _crop_for_zoom(self, image_bytes: bytes, zoom: float) -> bytes:
        """Schneidet den Bildausschnitt per Pillow zu und skaliert auf Originalgröße."""
        try:
            from PIL import Image  # pylint: disable=import-outside-toplevel
        except ImportError:
            _LOGGER.warning("Pillow nicht verfügbar – kein serverseitiger Zoom")
            return image_bytes
        import io  # pylint: disable=import-outside-toplevel
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        cx, cy = self.coordinator.digital_zoom_center
        cw = max(1, int(w / zoom))
        ch = max(1, int(h / zoom))
        x1 = max(0, min(w - cw, int(cx * w - cw / 2)))
        y1 = max(0, min(h - ch, int(cy * h - ch / 2)))
        cropped = img.crop((x1, y1, x1 + cw, y1 + ch))
        resized = cropped.resize((w, h), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format="JPEG", quality=90)
        return buf.getvalue()

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
        cx, cy = self.coordinator.digital_zoom_center
        return {
            "rtsp_url": self.coordinator.rtsp_url,
            "snapshot_url": self.coordinator.snapshot_url,
            "host": self.coordinator.host,
            "protocol": self.coordinator.protocol,
            "digital_zoom": self.coordinator.digital_zoom,
            "digital_zoom_cx": cx,
            "digital_zoom_cy": cy,
        }
