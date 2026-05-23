"""WJG Sensor Entities."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN
from .coordinator import WJGCameraCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Sensor-Entities fuer einen Config-Entry registrieren."""
    coordinator: WJGCameraCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            WJGFileListSensor(coordinator, entry),
            WJGFirmwareSensor(coordinator, entry),
            WJGSerialSensor(coordinator, entry),
            WJGMacSensor(coordinator, entry),
            WJGCameraTimeSensor(coordinator, entry),
            WJGActiveStreamSensor(coordinator, entry),
        ]
    )


class WJGFileListSensor(CoordinatorEntity[WJGCameraCoordinator], SensorEntity):
    """Sensor für die Dateiliste/SD-Karte der Kamera."""

    _attr_has_entity_name = True
    _attr_name = "Dateiliste"
    _attr_icon = "mdi:folder-multiple"

    def __init__(self, coordinator: WJGCameraCoordinator, entry: ConfigEntry) -> None:
        """Dateilisten-Sensor initialisieren."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_filelist"

    @property
    def device_info(self) -> DeviceInfo:
        """Zugehoerige Geraeteinformationen zurueckgeben."""
        return DeviceInfo(identifiers={(DOMAIN, self._entry.entry_id)})

    @property
    def native_value(self) -> int:
        """Anzahl Dateien aus dem Coordinator-State."""
        files = self.coordinator.data.get("files", []) if self.coordinator.data else []
        return len(files)

    @property
    def extra_state_attributes(self) -> dict:
        """Vollstaendige Dateiliste als Attribut."""
        files = self.coordinator.data.get("files", []) if self.coordinator.data else []
        return {"files": files}


class _WJGStaticSensor(CoordinatorEntity[WJGCameraCoordinator], SensorEntity):
    """Basis für statische Text-Sensoren."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WJGCameraCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.entry_id)})


class WJGFirmwareSensor(_WJGStaticSensor):
    def __init__(self, coordinator: WJGCameraCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "firmware", "Firmware", "mdi:chip")

    @property
    def native_value(self) -> str:
        return self.coordinator.firmware_version or "–"


class WJGSerialSensor(_WJGStaticSensor):
    def __init__(self, coordinator: WJGCameraCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator, entry, "serial", "Seriennummer", "mdi:barcode"
        )

    @property
    def native_value(self) -> str:
        return self.coordinator.serial_number or "–"


class WJGMacSensor(_WJGStaticSensor):
    def __init__(self, coordinator: WJGCameraCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator, entry, "mac", "MAC-Adresse", "mdi:ethernet"
        )

    @property
    def native_value(self) -> str:
        return self.coordinator.mac_address or "–"


class WJGCameraTimeSensor(_WJGStaticSensor):
    def __init__(self, coordinator: WJGCameraCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator, entry, "cam_time", "Kamera-Uhrzeit", "mdi:clock-outline"
        )

    @property
    def native_value(self) -> str:
        return self.coordinator.camera_time or "–"


class WJGActiveStreamSensor(_WJGStaticSensor):
    def __init__(self, coordinator: WJGCameraCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            "active_stream",
            "Aktiver Stream",
            "mdi:video-wireless",
        )

    @property
    def native_value(self) -> str:
        profile = self.coordinator.active_stream
        return "Hauptstream (1080p)" if profile == "000" else "Substream (360p)"