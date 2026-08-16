"""
WJG XM-3820 Camera Bridge für Home Assistant
=============================================
Unterstützt:
  - Livestream via RTSP / MJPEG
  - Snapshot (still image)
  - Aufnahme Start/Stop via HTTP API oder XM SDK
  - Dateiliste / SD-Karten-Zugriff
  - Bewegungserkennung (Sensor)
  - PTZ-Steuerung (falls verfügbar)
"""
from __future__ import annotations

import logging
import pathlib
from typing import Final

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.components.persistent_notification import async_create as pn_async_create

from .coordinator import WJGCameraCoordinator

_LOGGER = logging.getLogger(__name__)

DOMAIN: Final = "wjg_camera"
MANUFACTURER: Final = "WJG / Tenganda"
MODEL: Final = "XM-3820"

CONF_RTSP_PORT: Final = "rtsp_port"
CONF_RTSP_PATH: Final = "rtsp_path"
CONF_SNAPSHOT_PATH: Final = "snapshot_path"
CONF_PROTOCOL: Final = "protocol"
CONF_HTTP_RETRIES: Final = "http_retries"
CONF_ONVIF_PORT: Final = "onvif_port"
CONF_ONVIF_DEVICE_PATH: Final = "onvif_device_path"
CONF_ONVIF_MEDIA_PATH: Final = "onvif_media_path"
CONF_ONVIF_PTZ_PATH: Final = "onvif_ptz_path"
CONF_ONVIF_IMAGING_PATH: Final = "onvif_imaging_path"
CONF_ONVIF_EVENTS_PATH: Final = "onvif_events_path"
CONF_ONVIF_PROFILE_TOKEN: Final = "onvif_profile_token"
CONF_ONVIF_VIDEO_SOURCE_TOKEN: Final = "onvif_video_source_token"
CONF_ONVIF_MOTION_ITEM_KEYS: Final = "onvif_motion_item_keys"
CONF_ONVIF_MOTION_TOPIC_KEYWORDS: Final = "onvif_motion_topic_keywords"
CONF_ONVIF_TAMPER_ITEM_KEYS: Final = "onvif_tamper_item_keys"
CONF_ONVIF_TAMPER_TOPIC_KEYWORDS: Final = "onvif_tamper_topic_keywords"
CONF_ONVIF_SIGNAL_ITEM_KEYS: Final = "onvif_signal_item_keys"
CONF_ONVIF_SIGNAL_TOPIC_KEYWORDS: Final = "onvif_signal_topic_keywords"
CONF_MOTION_RTSP_DIFF: Final = "motion_rtsp_diff"
CONF_MOTION_RTSP_INTERVAL: Final = "motion_rtsp_interval"
CONF_MOTION_AUTO_RECORD: Final = "motion_auto_record"
CONF_MOTION_RECORD_COOLDOWN: Final = "motion_record_cooldown"

PROTOCOL_RTSP: Final = "rtsp"
PROTOCOL_HTTP: Final = "http_only"
PROTOCOL_XM: Final = "xm_sdk"
PROTOCOL_ONVIF: Final = "onvif"

DEFAULT_RTSP_PORT: Final = 554
DEFAULT_HTTP_PORT: Final = 80
DEFAULT_XM_PORT: Final = 34567
DEFAULT_ONVIF_PORT: Final = 8899
DEFAULT_USERNAME: Final = "admin"
DEFAULT_PASSWORD: Final = ""
DEFAULT_HTTP_RETRIES: Final = 1
DEFAULT_MOTION_RTSP_DIFF: Final = False
DEFAULT_MOTION_RTSP_INTERVAL: Final = 30
DEFAULT_MOTION_AUTO_RECORD: Final = True
DEFAULT_MOTION_RECORD_COOLDOWN: Final = 30

# Standard RTSP-Pfad für XM-basierte Kameras
DEFAULT_RTSP_PATH: Final = (
    "/user=admin&password=&channel=1&stream=1.sdp?real_stream"
)
DEFAULT_SNAPSHOT_PATH: Final = "/webcapture.jpg?command=snap&channel=1"

PLATFORMS: list[Platform] = [
    Platform.CAMERA,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
]

_SERVICE_SET_ZOOM = "set_digital_zoom"
_SVC_SCHEMA_ZOOM = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("zoom"):      vol.All(vol.Coerce(float), vol.Range(min=1.0, max=10.0)),
    vol.Optional("cx", default=0.5): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
    vol.Optional("cy", default=0.5): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
})

# Testhilfe: löst denselben Code-Pfad wie eine echte Bewegung aus (siehe
# WJGCameraCoordinator.async_simulate_motion), ohne auf reale Kamera-Bewegung
# warten zu müssen — nützlich um motion_auto_record/Cooldown/Backoff gezielt
# zu testen.
_SERVICE_SIMULATE_MOTION = "simulate_motion"
_SVC_SCHEMA_SIMULATE_MOTION = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
})


def _get_coordinator(hass: HomeAssistant, entity_id: str) -> WJGCameraCoordinator | None:
    """Coordinator für eine entity_id finden (Multi-Device-fähig).

    Ordnet die entity_id über die Entity-Registry dem zugehörigen Config-Entry
    zu, damit bei mehreren Kameras die richtige angesprochen wird. Fällt nur dann
    auf den ersten Coordinator zurück, wenn keine Zuordnung möglich ist.
    """
    from homeassistant.helpers import entity_registry as er  # pylint: disable=import-outside-toplevel

    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry and entry.config_entry_id:
        coord = hass.data.get(DOMAIN, {}).get(entry.config_entry_id)
        if isinstance(coord, WJGCameraCoordinator):
            return coord

    # Fallback: erster Coordinator (z. B. wenn nur eine Kamera existiert)
    for coord in hass.data.get(DOMAIN, {}).values():
        if isinstance(coord, WJGCameraCoordinator):
            return coord
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Integration einrichten aus Config-Entry."""
    coordinator = WJGCameraCoordinator(hass, entry)

    try:
        await coordinator.async_setup()
    except Exception as err:
        _LOGGER.error(
            "Fehler beim Verbinden mit der Kamera %s: %s",
            entry.data.get(CONF_HOST),
            err,
        )
        raise ConfigEntryNotReady(f"Kamera nicht erreichbar: {err}") from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Statischen Pfad für Lovelace-Karte registrieren (einmalig)
    www_dir = pathlib.Path(__file__).parent / "www"
    if www_dir.is_dir():
        try:
            from homeassistant.components.http import StaticPathConfig  # pylint: disable=import-outside-toplevel
            await hass.http.async_register_static_paths([
                StaticPathConfig("/wjg_camera/wjg-camera-card.js",
                                 str(www_dir / "wjg-camera-card.js"),
                                 cache_headers=False),
            ])
        except Exception:  # noqa: BLE001
            # Bereits registriert oder HTTP noch nicht bereit — ignorieren
            pass

    # HA-Service registrieren: wjg_camera.set_digital_zoom
    if not hass.services.has_service(DOMAIN, _SERVICE_SET_ZOOM):
        async def _handle_set_zoom(call: ServiceCall) -> None:
            coord = _get_coordinator(hass, call.data["entity_id"])
            if coord:
                await coord.async_digital_zoom_set(
                    call.data["zoom"],
                    call.data.get("cx", 0.5),
                    call.data.get("cy", 0.5),
                )
        hass.services.async_register(DOMAIN, _SERVICE_SET_ZOOM, _handle_set_zoom,
                                     schema=_SVC_SCHEMA_ZOOM)

    # HA-Service registrieren: wjg_camera.simulate_motion (Testhilfe)
    if not hass.services.has_service(DOMAIN, _SERVICE_SIMULATE_MOTION):
        async def _handle_simulate_motion(call: ServiceCall) -> None:
            coord = _get_coordinator(hass, call.data["entity_id"])
            if coord:
                await coord.async_simulate_motion()
        hass.services.async_register(DOMAIN, _SERVICE_SIMULATE_MOTION, _handle_simulate_motion,
                                     schema=_SVC_SCHEMA_SIMULATE_MOTION)

    # Einmalige Hinweis-Benachrichtigung für Lovelace-Ressource
    notif_key = f"{DOMAIN}_lovelace_hint"
    if notif_key not in hass.data:
        hass.data[notif_key] = True
        # FIX: hass.components wurde entfernt → direkt importierte Funktion verwenden
        pn_async_create(
            hass,
            "**WJG Zoom-Karte verfügbar!**\n\n"
            "Einstellungen → Dashboards → Ressourcen → **+ Hinzufügen**\n\n"
            "URL: `/wjg_camera/wjg-camera-card.js`  \nTyp: **JavaScript Modul**\n\n"
            "Dann im Dashboard: Karte hinzufügen → `custom:wjg-camera-card`",
            title="WJG Camera: Zoom-Karte",
            notification_id="wjg_camera_lovelace_hint",
        )

    _LOGGER.info(
        "WJG XM-3820 Bridge erfolgreich eingerichtet: %s",
        entry.data.get(CONF_HOST)
    )
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Integration entladen."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: WJGCameraCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unload_ok

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Integration neu laden."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
