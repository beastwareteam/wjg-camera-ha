"""WJG Number Entities – Imaging + PTZ Speed."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN
from .coordinator import WJGCameraCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class WJGNumberDescription(NumberEntityDescription):
    imaging_key: str = ""
    ptz_speed: bool = False


IMAGING_NUMBERS: tuple[WJGNumberDescription, ...] = (
    WJGNumberDescription(
        key="helligkeit",
        name="Helligkeit",
        icon="mdi:brightness-6",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        imaging_key="brightness",
    ),
    WJGNumberDescription(
        key="kontrast",
        name="Kontrast",
        icon="mdi:contrast-circle",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        imaging_key="contrast",
    ),
    WJGNumberDescription(
        key="saettigung",
        name="Sättigung",
        icon="mdi:palette",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        imaging_key="saturation",
    ),
    WJGNumberDescription(
        key="schaerfe",
        name="Schärfe",
        icon="mdi:image-filter-hdr",
        native_min_value=0,
        native_max_value=15,
        native_step=1,
        mode=NumberMode.SLIDER,
        imaging_key="sharpness",
    ),
    WJGNumberDescription(
        key="wdr_level",
        name="WDR-Stärke",
        icon="mdi:brightness-7",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        imaging_key="wdr_level",
    ),
    WJGNumberDescription(
        key="wb_cr_gain",
        name="Weißabgleich CrGain",
        icon="mdi:thermometer-lines",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
        imaging_key="wb_cr",
    ),
    WJGNumberDescription(
        key="wb_cb_gain",
        name="Weißabgleich CbGain",
        icon="mdi:thermometer-lines",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
        imaging_key="wb_cb",
    ),
)

PTZ_SPEED_NUMBER = WJGNumberDescription(
    key="ptz_speed",
    name="PTZ-Geschwindigkeit",
    icon="mdi:speedometer",
    native_min_value=1,
    native_max_value=8,
    native_step=1,
    mode=NumberMode.SLIDER,
    ptz_speed=True,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Number-Entities registrieren."""
    coordinator: WJGCameraCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = [
        WJGImagingNumber(coordinator, entry, desc) for desc in IMAGING_NUMBERS
    ]
    entities.append(WJGPTZSpeedNumber(coordinator, entry))
    async_add_entities(entities)


class WJGImagingNumber(CoordinatorEntity[WJGCameraCoordinator], NumberEntity):
    """Number-Entity für eine Bildeinstellung."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WJGCameraCoordinator,
        entry: ConfigEntry,
        description: WJGNumberDescription,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._desc = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = description.name
        self._attr_icon = description.icon
        self._attr_native_min_value = description.native_min_value
        self._attr_native_max_value = description.native_max_value
        self._attr_native_step = description.native_step
        self._attr_mode = description.mode
        if description.native_unit_of_measurement:
            self._attr_native_unit_of_measurement = (
                description.native_unit_of_measurement
            )

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.entry_id)})

    @property
    def native_value(self) -> float:
        return float(self.coordinator.imaging.get(self._desc.imaging_key, 50))

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_imaging_setting(
            self._desc.imaging_key, value
        )
        self.async_write_ha_state()


class WJGPTZSpeedNumber(CoordinatorEntity[WJGCameraCoordinator], NumberEntity):
    """PTZ-Geschwindigkeit (1–8)."""

    _attr_has_entity_name = True
    _attr_name = "PTZ-Geschwindigkeit"
    _attr_icon = "mdi:speedometer"
    _attr_native_min_value = 1
    _attr_native_max_value = 8
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self, coordinator: WJGCameraCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_ptz_speed"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.entry_id)})

    @property
    def native_value(self) -> float:
        return float(self.coordinator.ptz_speed)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_ptz_speed(int(value))
        self.async_write_ha_state()
