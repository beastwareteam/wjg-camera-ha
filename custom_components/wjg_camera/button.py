"""WJG Button Entities."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN
from .coordinator import WJGCameraCoordinator

_LOGGER = logging.getLogger(__name__)


def _raise_action_failed(action: str, detail: str | None = None) -> None:
    """Eine einheitliche Fehlermeldung fuer fehlgeschlagene Button-Aktionen werfen."""
    if detail:
        raise HomeAssistantError(f"Aktion fehlgeschlagen: {action} ({detail})")
    raise HomeAssistantError(f"Aktion fehlgeschlagen: {action}")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """PTZ-Buttons fuer einen Config-Entry registrieren."""
    coordinator: WJGCameraCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = [
        WJGPTZButton(coordinator, entry, "up"),
        WJGPTZButton(coordinator, entry, "down"),
        WJGPTZButton(coordinator, entry, "left"),
        WJGPTZButton(coordinator, entry, "right"),
        WJGPTZButton(coordinator, entry, "zoom_in"),
        WJGPTZButton(coordinator, entry, "zoom_out"),
        # PTZ: Home, Stop
        WJGPTZHomeButton(coordinator, entry),
        WJGPTZSetHomeButton(coordinator, entry),
        WJGPTZStopButton(coordinator, entry),
        # PTZ: Preset Set (4 Slots)
        WJGPTZPresetSetButton(coordinator, entry, "1"),
        WJGPTZPresetSetButton(coordinator, entry, "2"),
        WJGPTZPresetSetButton(coordinator, entry, "3"),
        WJGPTZPresetSetButton(coordinator, entry, "4"),
        # PTZ: Preset Goto (4 Slots)
        WJGPTZPresetGotoButton(coordinator, entry, "1"),
        WJGPTZPresetGotoButton(coordinator, entry, "2"),
        WJGPTZPresetGotoButton(coordinator, entry, "3"),
        WJGPTZPresetGotoButton(coordinator, entry, "4"),
        # System
        WJGSnapshotButton(coordinator, entry),
        WJGRebootButton(coordinator, entry),
        WJGNTPSyncButton(coordinator, entry),
    ]
    async_add_entities(entities)


class WJGPTZButton(CoordinatorEntity[WJGCameraCoordinator], ButtonEntity):
    """Button-Entity fuer PTZ-Steuerung."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WJGCameraCoordinator,
        entry: ConfigEntry,
        direction: str,
    ) -> None:
        """PTZ-Button fuer eine Richtung initialisieren."""
        super().__init__(coordinator)
        self._entry = entry
        self._direction = direction
        self._attr_unique_id = f"{entry.entry_id}_ptz_{direction}"
        self._attr_name = f"PTZ {self._direction.replace('_', ' ').title()}"
        self._attr_icon = self._icon_for_direction(direction)

    @property
    def device_info(self) -> DeviceInfo:
        """Zugehoerige Geraeteinformationen zurueckgeben."""
        return DeviceInfo(identifiers={(DOMAIN, self._entry.entry_id)})

    async def async_press(self) -> None:
        """PTZ-Befehl an den Coordinator weiterreichen."""
        ok = await self.coordinator.async_ptz_command(
            self._direction,
            speed=self.coordinator.ptz_speed,
            )
        if ok:
            _LOGGER.info(
                "PTZ-Befehl '%s' gesendet an %s",
                self._direction,
                self.coordinator.host,
            )
        else:
            _LOGGER.warning("PTZ-Befehl '%s' fehlgeschlagen", self._direction)
            detail = str(getattr(self.coordinator, "last_ptz_fault", "") or "").strip()
            wsse_state = getattr(self.coordinator, "onvif_wsse_enabled", None)
            suffix = "build=2.2.5"
            if wsse_state is not None:
                suffix = f"build=2.2.5 wsse={'on' if wsse_state else 'off'}"
            combined_detail = f"{detail}; {suffix}" if detail else suffix
            _raise_action_failed(f"PTZ {self._direction}", combined_detail)

    def press(self) -> None:
        """Sync-API bewusst nicht unterstuetzen."""
        raise NotImplementedError("Use async_press instead")

    @staticmethod
    def _icon_for_direction(direction: str) -> str:
        """Passendes Icon fuer die PTZ-Richtung liefern."""
        icons = {
            "up": "mdi:arrow-up-bold-circle",
            "down": "mdi:arrow-down-bold-circle",
            "left": "mdi:arrow-left-bold-circle",
            "right": "mdi:arrow-right-bold-circle",
            "zoom_in": "mdi:magnify-plus",
            "zoom_out": "mdi:magnify-minus",
        }
        return icons.get(direction, "mdi:arrow-all")


class WJGPTZHomeButton(CoordinatorEntity[WJGCameraCoordinator], ButtonEntity):
    """PTZ-Heimposition anfahren."""

    _attr_has_entity_name = True
    _attr_name = "PTZ Home"
    _attr_icon = "mdi:home-map-marker"

    def __init__(self, coordinator: WJGCameraCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_ptz_home"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.entry_id)})

    async def async_press(self) -> None:
        ok = await self.coordinator.async_ptz_home()
        if not ok:
            _raise_action_failed("PTZ Home")

    def press(self) -> None:
        raise NotImplementedError


class WJGPTZSetHomeButton(CoordinatorEntity[WJGCameraCoordinator], ButtonEntity):
    """Aktuelle Position als PTZ-Heimposition speichern."""

    _attr_has_entity_name = True
    _attr_name = "PTZ Home setzen"
    _attr_icon = "mdi:home-edit"

    def __init__(self, coordinator: WJGCameraCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_ptz_set_home"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.entry_id)})

    async def async_press(self) -> None:
        ok = await self.coordinator.async_ptz_set_home()
        if not ok:
            _raise_action_failed("PTZ Home setzen")

    def press(self) -> None:
        raise NotImplementedError


class WJGPTZStopButton(CoordinatorEntity[WJGCameraCoordinator], ButtonEntity):
    """Laufende PTZ-Bewegung stoppen."""

    _attr_has_entity_name = True
    _attr_name = "PTZ Stopp"
    _attr_icon = "mdi:stop-circle"

    def __init__(self, coordinator: WJGCameraCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_ptz_stop"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.entry_id)})

    async def async_press(self) -> None:
        ok = await self.coordinator.async_ptz_stop()
        if not ok:
            _raise_action_failed("PTZ Stop")

    def press(self) -> None:
        raise NotImplementedError


class WJGPTZPresetSetButton(CoordinatorEntity[WJGCameraCoordinator], ButtonEntity):
    """Aktuelle Position als Preset N speichern."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: WJGCameraCoordinator, entry: ConfigEntry, slot: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._slot = slot
        self._attr_unique_id = f"{entry.entry_id}_ptz_preset_set_{slot}"
        self._attr_name = f"PTZ Preset {slot} speichern"
        self._attr_icon = "mdi:map-marker-plus"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.entry_id)})

    async def async_press(self) -> None:
        name = f"Preset {self._slot}"
        token = await self.coordinator.async_ptz_set_preset(name, token=self._slot)
        if not token:
            _raise_action_failed(f"PTZ Preset {self._slot} speichern")

    def press(self) -> None:
        raise NotImplementedError


class WJGPTZPresetGotoButton(CoordinatorEntity[WJGCameraCoordinator], ButtonEntity):
    """Preset N anfahren."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: WJGCameraCoordinator, entry: ConfigEntry, slot: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._slot = slot
        self._attr_unique_id = f"{entry.entry_id}_ptz_preset_goto_{slot}"
        self._attr_name = f"PTZ Preset {slot} anfahren"
        self._attr_icon = "mdi:map-marker-radius"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.entry_id)})

    async def async_press(self) -> None:
        ok = await self.coordinator.async_ptz_goto_preset(self._slot)
        if not ok:
            _raise_action_failed(f"PTZ Preset {self._slot} anfahren")

    def press(self) -> None:
        raise NotImplementedError


class WJGSnapshotButton(CoordinatorEntity[WJGCameraCoordinator], ButtonEntity):
    """Snapshot auslösen."""

    _attr_has_entity_name = True
    _attr_name = "Snapshot"
    _attr_icon = "mdi:camera-iris"

    def __init__(self, coordinator: WJGCameraCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_snapshot"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.entry_id)})

    async def async_press(self) -> None:
        image_bytes = await self.coordinator.async_snapshot()
        if not image_bytes:
            _raise_action_failed("Snapshot")

    def press(self) -> None:
        raise NotImplementedError


class WJGRebootButton(CoordinatorEntity[WJGCameraCoordinator], ButtonEntity):
    """Kamera neu starten."""

    _attr_has_entity_name = True
    _attr_name = "Kamera neu starten"
    _attr_icon = "mdi:restart"

    def __init__(self, coordinator: WJGCameraCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_reboot"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.entry_id)})

    async def async_press(self) -> None:
        ok = await self.coordinator.async_reboot()
        if not ok:
            _raise_action_failed("Kamera neu starten")

    def press(self) -> None:
        raise NotImplementedError


class WJGNTPSyncButton(CoordinatorEntity[WJGCameraCoordinator], ButtonEntity):
    """NTP-Zeitserver synchronisieren."""

    _attr_has_entity_name = True
    _attr_name = "NTP synchronisieren"
    _attr_icon = "mdi:clock-check"

    def __init__(self, coordinator: WJGCameraCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_ntp_sync"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.entry_id)})

    async def async_press(self) -> None:
        ok = await self.coordinator.async_ntp_sync()
        if not ok:
            _raise_action_failed("NTP synchronisieren")

    def press(self) -> None:
        raise NotImplementedError

