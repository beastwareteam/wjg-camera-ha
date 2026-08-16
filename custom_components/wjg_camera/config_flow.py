"""
WJG Camera Config Flow
======================
Einrichtungsassistent für die HA-Benutzeroberfläche.
Führt durch: IP-Eingabe → Protokoll-Auswahl → Verbindungstest → Speichern
"""
from __future__ import annotations

import logging
import socket
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv

from . import (
    CONF_HTTP_RETRIES, CONF_MOTION_AUTO_RECORD, CONF_MOTION_RECORD_COOLDOWN,
    CONF_MOTION_RTSP_DIFF, CONF_MOTION_RTSP_INTERVAL,
    CONF_ONVIF_DEVICE_PATH, CONF_ONVIF_EVENTS_PATH,
    CONF_ONVIF_IMAGING_PATH, CONF_ONVIF_MEDIA_PATH, CONF_ONVIF_MOTION_ITEM_KEYS,
    CONF_ONVIF_MOTION_TOPIC_KEYWORDS, CONF_ONVIF_PORT, CONF_ONVIF_PROFILE_TOKEN,
    CONF_ONVIF_PTZ_PATH,
    CONF_ONVIF_SIGNAL_ITEM_KEYS, CONF_ONVIF_SIGNAL_TOPIC_KEYWORDS,
    CONF_ONVIF_TAMPER_ITEM_KEYS, CONF_ONVIF_TAMPER_TOPIC_KEYWORDS,
    CONF_ONVIF_VIDEO_SOURCE_TOKEN, CONF_PROTOCOL,
    CONF_RTSP_PATH, CONF_RTSP_PORT, CONF_SNAPSHOT_PATH,
    DEFAULT_HTTP_PORT, DEFAULT_HTTP_RETRIES,
    DEFAULT_MOTION_AUTO_RECORD, DEFAULT_MOTION_RECORD_COOLDOWN,
    DEFAULT_MOTION_RTSP_DIFF, DEFAULT_MOTION_RTSP_INTERVAL,
    DEFAULT_ONVIF_PORT, DEFAULT_PASSWORD,
    DEFAULT_RTSP_PATH, DEFAULT_RTSP_PORT,
    DEFAULT_SNAPSHOT_PATH, DEFAULT_USERNAME, DOMAIN,
    PROTOCOL_HTTP, PROTOCOL_RTSP, PROTOCOL_XM, PROTOCOL_ONVIF,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({
    vol.Required(CONF_HOST, description={"suggested_value": "192.168.4.1"}): str,
    vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): str,
    vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
    vol.Optional(CONF_PORT, default=DEFAULT_HTTP_PORT): cv.port,
    vol.Optional(CONF_RTSP_PORT, default=DEFAULT_RTSP_PORT): cv.port,
    vol.Optional(CONF_ONVIF_PORT, default=DEFAULT_ONVIF_PORT): cv.port,
    vol.Optional(CONF_PROTOCOL, default=PROTOCOL_RTSP): vol.In([
        PROTOCOL_RTSP, PROTOCOL_HTTP, PROTOCOL_XM, PROTOCOL_ONVIF
    ]),
    vol.Optional(CONF_HTTP_RETRIES, default=DEFAULT_HTTP_RETRIES): vol.All(
        vol.Coerce(int), vol.Range(min=0, max=5)
    ),
    vol.Optional(CONF_RTSP_PATH, default=DEFAULT_RTSP_PATH): str,
    vol.Optional(CONF_SNAPSHOT_PATH, default=DEFAULT_SNAPSHOT_PATH): str,
})

def _check_host_reachable(host: str, ports: list[int], timeout: float = 3.0) -> int | None:
    """Prüft ob mindestens einer der Ports erreichbar ist. Gibt offenen Port zurück."""
    for port in ports:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return port
        except OSError:
            continue
    return None


# Pyright erkennt den Home-Assistant-Metaclass-Hook fuer `domain=` nicht korrekt.
class WJGCameraConfigFlow(  # pyright: ignore[reportAbstractUsage, reportCallIssue, reportGeneralTypeIssues]
    config_entries.ConfigFlow,
    domain=DOMAIN,
):
    """Config Flow Handler."""

    VERSION = 1

    def __init__(self) -> None:
        """Config-Flow initialisieren."""
        self._host: str | None = None

    def is_matching(self, other_flow: object) -> bool:
        """Flows fuer denselben Host als identisch behandeln."""
        if not isinstance(other_flow, WJGCameraConfigFlow):
            return False
        other_host = getattr(other_flow, "_host", None)
        return self._host is not None and self._host == other_host

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Erstkonfiguration pruefen und Config-Entry anlegen."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = str(user_input[CONF_HOST]).strip()
            self._host = host
            ports: list[int] = [
                int(user_input.get(CONF_PORT, DEFAULT_HTTP_PORT)),
                int(user_input.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT)),
                34567,
            ]

            # Erreichbarkeit prüfen (in executor weil synchron)
            open_port = await self.hass.async_add_executor_job(
                _check_host_reachable,
                host,
                ports,
                3.0,
            )

            if open_port is None:
                errors["base"] = "cannot_connect"
                _LOGGER.error(
                    "Kamera auf %s nicht erreichbar. "
                    "Ports 80, 554, 34567 alle geschlossen.", host
                )
            else:
                _LOGGER.info(
                    "Kamera gefunden auf %s (Port %s offen)", host, open_port
                )
                await self.async_set_unique_id(f"wjg_{host}")
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"WJG XM-3820 ({host})",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
            description_placeholders={
                "docs_url": "https://github.com/your-repo/wjg-ha-bridge",
                "hotspot_hint": (
                    "Kamera-Hotspot: GW_AP_XXXX • "
                    "Standard-IP im Hotspot-Modus: 192.168.4.1"
                ),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Options-Flow fuer einen bestehenden Config-Entry liefern."""
        return WJGOptionsFlow(config_entry)


class WJGOptionsFlow(config_entries.OptionsFlow):
    """Options Flow für nachträgliche Konfigurationsänderungen."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    @staticmethod
    def _csv_default(value: Any) -> str:
        if isinstance(value, (list, tuple, set)):
            return ", ".join(str(item) for item in value if str(item).strip())
        if value is None:
            return ""
        return str(value)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optionen fuer einen bestehenden Config-Entry anzeigen oder speichern."""
        if user_input is not None:
            merged = dict(self._config_entry.options)
            merged.update(user_input)
            return self.async_create_entry(title="", data=merged)

        schema = vol.Schema({
            # Motion-Kanäle / Auto-Aufnahme (Netzlast-Steuerung)
            vol.Optional(
                CONF_MOTION_RTSP_DIFF,
                default=bool(self._config_entry.options.get(
                    CONF_MOTION_RTSP_DIFF,
                    self._config_entry.data.get(
                        CONF_MOTION_RTSP_DIFF, DEFAULT_MOTION_RTSP_DIFF
                    ),
                )),
            ): cv.boolean,
            vol.Optional(
                CONF_MOTION_RTSP_INTERVAL,
                default=self._config_entry.options.get(
                    CONF_MOTION_RTSP_INTERVAL,
                    self._config_entry.data.get(
                        CONF_MOTION_RTSP_INTERVAL, DEFAULT_MOTION_RTSP_INTERVAL
                    ),
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=30)),
            vol.Optional(
                CONF_MOTION_AUTO_RECORD,
                default=bool(self._config_entry.options.get(
                    CONF_MOTION_AUTO_RECORD,
                    self._config_entry.data.get(
                        CONF_MOTION_AUTO_RECORD, DEFAULT_MOTION_AUTO_RECORD
                    ),
                )),
            ): cv.boolean,
            vol.Optional(
                CONF_MOTION_RECORD_COOLDOWN,
                default=self._config_entry.options.get(
                    CONF_MOTION_RECORD_COOLDOWN,
                    self._config_entry.data.get(
                        CONF_MOTION_RECORD_COOLDOWN, DEFAULT_MOTION_RECORD_COOLDOWN
                    ),
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=600)),
            vol.Optional(
                CONF_RTSP_PATH,
                default=self._config_entry.options.get(
                    CONF_RTSP_PATH,
                    self._config_entry.data.get(CONF_RTSP_PATH, DEFAULT_RTSP_PATH),
                )
            ): str,
            vol.Optional(
                CONF_SNAPSHOT_PATH,
                default=self._config_entry.options.get(
                    CONF_SNAPSHOT_PATH,
                    self._config_entry.data.get(CONF_SNAPSHOT_PATH, DEFAULT_SNAPSHOT_PATH),
                )
            ): str,
            vol.Optional(
                CONF_PROTOCOL,
                default=self._config_entry.options.get(
                    CONF_PROTOCOL,
                    self._config_entry.data.get(CONF_PROTOCOL, PROTOCOL_RTSP),
                )
            ): vol.In([PROTOCOL_RTSP, PROTOCOL_HTTP, PROTOCOL_XM, PROTOCOL_ONVIF]),
            vol.Optional(
                CONF_HTTP_RETRIES,
                default=self._config_entry.options.get(
                    CONF_HTTP_RETRIES,
                    self._config_entry.data.get(CONF_HTTP_RETRIES, DEFAULT_HTTP_RETRIES),
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=5)),
            vol.Optional(
                CONF_ONVIF_PORT,
                default=self._config_entry.options.get(
                    CONF_ONVIF_PORT,
                    self._config_entry.data.get(CONF_ONVIF_PORT, DEFAULT_ONVIF_PORT),
                ),
            ): cv.port,
            vol.Optional(
                CONF_ONVIF_DEVICE_PATH,
                default=self._config_entry.options.get(CONF_ONVIF_DEVICE_PATH, ""),
            ): str,
            vol.Optional(
                CONF_ONVIF_MEDIA_PATH,
                default=self._config_entry.options.get(CONF_ONVIF_MEDIA_PATH, ""),
            ): str,
            vol.Optional(
                CONF_ONVIF_PTZ_PATH,
                default=self._config_entry.options.get(CONF_ONVIF_PTZ_PATH, ""),
            ): str,
            vol.Optional(
                CONF_ONVIF_IMAGING_PATH,
                default=self._config_entry.options.get(CONF_ONVIF_IMAGING_PATH, ""),
            ): str,
            vol.Optional(
                CONF_ONVIF_EVENTS_PATH,
                default=self._config_entry.options.get(CONF_ONVIF_EVENTS_PATH, ""),
            ): str,
            vol.Optional(
                CONF_ONVIF_PROFILE_TOKEN,
                default=self._config_entry.options.get(CONF_ONVIF_PROFILE_TOKEN, ""),
            ): str,
            vol.Optional(
                CONF_ONVIF_VIDEO_SOURCE_TOKEN,
                default=self._config_entry.options.get(CONF_ONVIF_VIDEO_SOURCE_TOKEN, ""),
            ): str,
            vol.Optional(
                CONF_ONVIF_MOTION_ITEM_KEYS,
                default=self._csv_default(
                    self._config_entry.options.get(CONF_ONVIF_MOTION_ITEM_KEYS, "")
                ),
            ): str,
            vol.Optional(
                CONF_ONVIF_MOTION_TOPIC_KEYWORDS,
                default=self._csv_default(
                    self._config_entry.options.get(CONF_ONVIF_MOTION_TOPIC_KEYWORDS, "")
                ),
            ): str,
            vol.Optional(
                CONF_ONVIF_TAMPER_ITEM_KEYS,
                default=self._csv_default(
                    self._config_entry.options.get(CONF_ONVIF_TAMPER_ITEM_KEYS, "")
                ),
            ): str,
            vol.Optional(
                CONF_ONVIF_TAMPER_TOPIC_KEYWORDS,
                default=self._csv_default(
                    self._config_entry.options.get(CONF_ONVIF_TAMPER_TOPIC_KEYWORDS, "")
                ),
            ): str,
            vol.Optional(
                CONF_ONVIF_SIGNAL_ITEM_KEYS,
                default=self._csv_default(
                    self._config_entry.options.get(CONF_ONVIF_SIGNAL_ITEM_KEYS, "")
                ),
            ): str,
            vol.Optional(
                CONF_ONVIF_SIGNAL_TOPIC_KEYWORDS,
                default=self._csv_default(
                    self._config_entry.options.get(CONF_ONVIF_SIGNAL_TOPIC_KEYWORDS, "")
                ),
            ): str,
        })

        return self.async_show_form(step_id="init", data_schema=schema)
