"""WJG Switch Entities."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN
from .coordinator import WJGCameraCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Switch-Entities fuer einen Config-Entry registrieren."""
    coordinator: WJGCameraCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            WJGRecordingSwitch(coordinator, entry),
            WJGWDRSwitch(coordinator, entry),
            WJGIRCutSwitch(coordinator, entry),
            WJGMicrophoneSwitch(coordinator, entry),
        ]
    )

class WJGRecordingSwitch(  # pyright: ignore[reportAbstractUsage]
    CoordinatorEntity[WJGCameraCoordinator],
    SwitchEntity,
):
    """Schalter zum Starten/Stoppen der Aufnahme."""

    _attr_has_entity_name = True
    _attr_name = "Aufnahme"
    _attr_icon = "mdi:record-circle"

    def __init__(self, coordinator: WJGCameraCoordinator, entry: ConfigEntry) -> None:
        """Recording-Switch initialisieren."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_recording"

    @property
    def device_info(self) -> DeviceInfo:
        """Zugehoerige Geraeteinformationen zurueckgeben."""
        return DeviceInfo(identifiers={(DOMAIN, self._entry.entry_id)})

    @property
    def is_on(self) -> bool:
        """Aktuellen Recording-Status liefern."""
        return self.coordinator.is_recording

    async def async_turn_on(self, **_: Any) -> None:
        """Aufnahme starten."""
        ok = await self.coordinator.async_set_recording(True)
        if ok:
            _LOGGER.info("Aufnahme gestartet auf %s", self.coordinator.host)
        else:
            _LOGGER.warning("Aufnahme konnte nicht gestartet werden")
        self.async_write_ha_state()

    def turn_on(self, **kwargs: Any) -> None:
        """Sync-API bewusst nicht unterstuetzen."""
        raise NotImplementedError("Use async_turn_on instead")

    async def async_turn_off(self, **_: Any) -> None:
        """Aufnahme stoppen."""
        ok = await self.coordinator.async_set_recording(False)
        if ok:
            _LOGGER.info("Aufnahme gestoppt auf %s", self.coordinator.host)
        self.async_write_ha_state()

    def turn_off(self, **kwargs: Any) -> None:
        """Sync-API bewusst nicht unterstuetzen."""
        raise NotImplementedError("Use async_turn_off instead")


class WJGWDRSwitch(  # pyright: ignore[reportAbstractUsage]
    CoordinatorEntity[WJGCameraCoordinator],
    SwitchEntity,
):
    """Wide Dynamic Range (WDR) ein-/ausschalten."""

    _attr_has_entity_name = True
    _attr_name = "WDR"
    _attr_icon = "mdi:brightness-7"

    def __init__(self, coordinator: WJGCameraCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_wdr"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.entry_id)})

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.imaging.get("wdr_enabled", False))

    async def async_turn_on(self, **_: Any) -> None:
        await self.coordinator.async_set_imaging_setting("wdr_enabled", True)
        self.async_write_ha_state()

    def turn_on(self, **kwargs: Any) -> None:
        raise NotImplementedError

    async def async_turn_off(self, **_: Any) -> None:
        await self.coordinator.async_set_imaging_setting("wdr_enabled", False)
        self.async_write_ha_state()

    def turn_off(self, **kwargs: Any) -> None:
        raise NotImplementedError


class WJGIRCutSwitch(  # pyright: ignore[reportAbstractUsage]
    CoordinatorEntity[WJGCameraCoordinator],
    SwitchEntity,
):
    """IR-Cut-Filter manuell ein-/ausschalten (Nacht=ON, Tag=OFF)."""

    _attr_has_entity_name = True
    _attr_name = "IR-Cut (Nachtmodus)"
    _attr_icon = "mdi:weather-night"

    def __init__(self, coordinator: WJGCameraCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_ir_cut"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.entry_id)})

    @property
    def is_on(self) -> bool:
        return self.coordinator.imaging.get("ir_cut", "AUTO") == "ON"

    async def async_turn_on(self, **_: Any) -> None:
        await self.coordinator.async_set_imaging_setting("ir_cut", "ON")
        self.async_write_ha_state()

    def turn_on(self, **kwargs: Any) -> None:
        raise NotImplementedError

    async def async_turn_off(self, **_: Any) -> None:
        await self.coordinator.async_set_imaging_setting("ir_cut", "OFF")
        self.async_write_ha_state()

    def turn_off(self, **kwargs: Any) -> None:
        raise NotImplementedError


class WJGMicrophoneSwitch(  # pyright: ignore[reportAbstractUsage]
    CoordinatorEntity[WJGCameraCoordinator],
    SwitchEntity,
):
    """Mikrofon ein-/ausschalten (ONVIF AudioOutput)."""

    _attr_has_entity_name = True
    _attr_name = "Mikrofon"
    _attr_icon = "mdi:microphone"

    def __init__(self, coordinator: WJGCameraCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_microphone"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.entry_id)})

    @property
    def is_on(self) -> bool:
        return self.coordinator.microphone_enabled

    async def async_turn_on(self, **_: Any) -> None:
        await self.coordinator.async_set_microphone_enabled(True)
        self.async_write_ha_state()

    def turn_on(self, **kwargs: Any) -> None:
        raise NotImplementedError

    async def async_turn_off(self, **_: Any) -> None:
        await self.coordinator.async_set_microphone_enabled(False)
        self.async_write_ha_state()

    def turn_off(self, **kwargs: Any) -> None:
        raise NotImplementedError
