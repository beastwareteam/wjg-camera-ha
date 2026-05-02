"""WJG Select Entities – PTZ Preset, IR, Belichtung, Stream."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN
from .coordinator import WJGCameraCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Select-Entities registrieren."""
    coordinator: WJGCameraCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            WJGPTZPresetSelect(coordinator, entry),
            WJGIRModeSelect(coordinator, entry),
            WJGExposureModeSelect(coordinator, entry),
            WJGExposurePrioritySelect(coordinator, entry),
            WJGWhiteBalanceModeSelect(coordinator, entry),
            WJGStreamProfileSelect(coordinator, entry),
        ]
    )


class _WJGSelectBase(CoordinatorEntity[WJGCameraCoordinator], SelectEntity):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: WJGCameraCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.entry_id)})


class WJGPTZPresetSelect(_WJGSelectBase):
    """Dropdown: PTZ-Preset anfahren."""

    _attr_name = "PTZ-Preset anfahren"
    _attr_icon = "mdi:map-marker-radius"

    def __init__(
        self, coordinator: WJGCameraCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_ptz_preset_select"
        self._current: str | None = None

    @property
    def options(self) -> list[str]:
        presets = self.coordinator.ptz_presets
        if not presets:
            return ["(keine Presets)"]
        return list(presets.values())

    @property
    def current_option(self) -> str | None:
        return self._current

    async def async_select_option(self, option: str) -> None:
        presets = self.coordinator.ptz_presets
        token = next((k for k, v in presets.items() if v == option), None)
        if token:
            ok = await self.coordinator.async_ptz_goto_preset(token)
            if ok:
                self._current = option
        self.async_write_ha_state()


class WJGIRModeSelect(_WJGSelectBase):
    """IR-Cut-Filter Modus (Auto/Tag/Nacht)."""

    _attr_name = "IR-Modus"
    _attr_icon = "mdi:weather-night"
    _attr_options = ["AUTO", "ON", "OFF"]

    def __init__(
        self, coordinator: WJGCameraCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_ir_modus"

    @property
    def current_option(self) -> str:
        return str(self.coordinator.imaging.get("ir_cut", "AUTO"))

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_imaging_setting("ir_cut", option)
        self.async_write_ha_state()


class WJGExposureModeSelect(_WJGSelectBase):
    """Belichtungs-Modus (AUTO/MANUAL)."""

    _attr_name = "Belichtungs-Modus"
    _attr_icon = "mdi:camera-iris"
    _attr_options = ["AUTO", "MANUAL"]

    def __init__(
        self, coordinator: WJGCameraCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_belichtung_modus"

    @property
    def current_option(self) -> str:
        return str(self.coordinator.imaging.get("exposure_mode", "AUTO"))

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_imaging_setting("exposure_mode", option)
        self.async_write_ha_state()


class WJGExposurePrioritySelect(_WJGSelectBase):
    """Belichtungs-Priorität."""

    _attr_name = "Belichtungs-Priorität"
    _attr_icon = "mdi:brightness-5"
    _attr_options = ["LowNoise", "FrameRate"]

    def __init__(
        self, coordinator: WJGCameraCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_belichtung_prioritaet"

    @property
    def current_option(self) -> str:
        return str(self.coordinator.imaging.get("exposure_priority", "LowNoise"))

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_imaging_setting("exposure_priority", option)
        self.async_write_ha_state()


class WJGWhiteBalanceModeSelect(_WJGSelectBase):
    """Weißabgleich-Modus."""

    _attr_name = "Weißabgleich"
    _attr_icon = "mdi:white-balance-auto"
    _attr_options = ["AUTO", "MANUAL"]

    def __init__(
        self, coordinator: WJGCameraCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_weissabgleich"

    @property
    def current_option(self) -> str:
        return str(self.coordinator.imaging.get("wb_mode", "AUTO"))

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_imaging_setting("wb_mode", option)
        self.async_write_ha_state()


class WJGStreamProfileSelect(_WJGSelectBase):
    """Stream-Qualität: Hauptstream (1080p) oder Substream (360p)."""

    _attr_name = "Stream-Qualität"
    _attr_icon = "mdi:video-high-definition"
    _attr_options = ["Hauptstream (H265 1080p)", "Substream (H265 360p)"]

    _PROFILE_MAP = {
        "Hauptstream (H265 1080p)": "000",
        "Substream (H265 360p)": "001",
    }
    _REVERSE_MAP = {v: k for k, v in _PROFILE_MAP.items()}

    def __init__(
        self, coordinator: WJGCameraCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_stream_qualitaet"

    @property
    def current_option(self) -> str:
        return self._REVERSE_MAP.get(
            self.coordinator.active_stream, "Hauptstream (H265 1080p)"
        )

    async def async_select_option(self, option: str) -> None:
        token = self._PROFILE_MAP.get(option, "000")
        await self.coordinator.async_set_stream_profile(token)
        self.async_write_ha_state()
