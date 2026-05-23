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

    file_sensor = WJGFileListSensor(coordinator, entry)

    # Referenz in hass.data ablegen damit button.py darauf zugreifen kann,
    # ohne einen fragilen Registry-Lookup machen zu muessen.
    hass.data.setdefault(DOMAIN, {})[f"{entry.entry_id}_file_sensor"] = file_sensor

    async_add_entities(
        [
            file_sensor,
            WJGFirmwareSensor(coordinator, entry),
            WJGSerialSensor(coordinator, entry),
            WJGMacSensor(coordinator, entry),
            WJGCameraTimeSensor(coordinator, entry),
            WJGActiveStreamSensor(coordinator, entry),
        ]
    )


class WJGFileListSensor(CoordinatorEntity[WJGCameraCoordinator], SensorEntity):
    """Sensor fuer die Dateiliste/SD-Karte der Kamera.

    Wird NICHT automatisch bei jedem Poll-Zyklus aktualisiert – das wuerde
    bei jedem 30s-Intervall einen teuren XM-SDK- oder HTTP-Request ausloesen.
    Stattdessen aktualisiert der WJGFetchFileListButton (button.py) den Cache
    manuell auf Anforderung. Der Sensor liest dann nur aus dem internen Cache.
    """

    _attr_has_entity_name = True
    _attr_name = "Dateiliste"
    _attr_icon = "mdi:folder-multiple"
    # Kein shouldpoll = False nötig, da wir async_update NICHT überschreiben.
    # CoordinatorEntity aktualisiert den State wenn der Coordinator seinen Zyklus
    # abschliesst – wir zeigen dabei nur den bereits gecachten Wert.

    def __init__(self, coordinator: WJGCameraCoordinator, entry: ConfigEntry) -> None:
        """Dateilisten-Sensor initialisieren."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_filelist"
        # Interner Cache – wird nur durch async_refresh_file_list() befuellt
        self._cached_files: list[dict] = []

    @property
    def device_info(self) -> DeviceInfo:
        """Zugehoerige Geraeteinformationen zurueckgeben."""
        return DeviceInfo(identifiers={(DOMAIN, self._entry.entry_id)})

    @property
    def native_value(self) -> int:
        """Anzahl gecachter Dateien."""
        return len(self._cached_files)

    @property
    def extra_state_attributes(self) -> dict:
        """Gecachte Dateiliste als Attribut exponieren."""
        return {"files": self._cached_files}

    async def async_refresh_file_list(self) -> None:
        """Dateiliste aktiv von der Kamera laden und Cache aktualisieren.

        Wird ausschliesslich vom WJGFetchFileListButton aufgerufen.
        Kein automatischer Aufruf im Poll-Zyklus.
        """
        files = await self.coordinator.async_get_file_list()
        self._cached_files = files if isinstance(files, list) else []
        self.async_write_ha_state()


class _WJGStaticSensor(CoordinatorEntity[WJGCameraCoordinator], SensorEntity):
    """Basis fuer statische Text-Sensoren."""

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
