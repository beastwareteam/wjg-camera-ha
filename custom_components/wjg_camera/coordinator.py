"""
WJG Camera Coordinator
======================
Verwaltet Verbindung, State und Datenabruf zur Kamera.
Unterstützt: RTSP, HTTP Snapshot, XM SDK (Port 34567), ONVIF.
"""
# pylint: disable=broad-exception-caught

from __future__ import annotations

import asyncio
import base64
import datetime
import hashlib
import inspect
import json
import logging
import os
import re
import socket
import struct
import time
from datetime import timedelta
from typing import Any
from urllib.error import HTTPError as _UrllibHTTPError
from urllib.parse import quote
from urllib.parse import urlparse
from urllib.parse import urlunparse
from urllib.request import Request as _UrllibRequest
from urllib.request import urlopen as _urlopen

import aiohttp
import async_timeout

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .xm_soap import XMSoapClient as _XMSoapClient

# Konstanten lokal definieren, um zirkulären Import zu vermeiden
CONF_PROTOCOL = "protocol"
CONF_HTTP_RETRIES = "http_retries"
CONF_RTSP_PATH = "rtsp_path"
CONF_RTSP_PORT = "rtsp_port"
CONF_SNAPSHOT_PATH = "snapshot_path"
CONF_ONVIF_DEVICE_PATH = "onvif_device_path"
CONF_ONVIF_MEDIA_PATH = "onvif_media_path"
CONF_ONVIF_PTZ_PATH = "onvif_ptz_path"
CONF_ONVIF_IMAGING_PATH = "onvif_imaging_path"
CONF_ONVIF_EVENTS_PATH = "onvif_events_path"
CONF_ONVIF_PROFILE_TOKEN = "onvif_profile_token"
CONF_ONVIF_VIDEO_SOURCE_TOKEN = "onvif_video_source_token"
CONF_ONVIF_MOTION_ITEM_KEYS = "onvif_motion_item_keys"
CONF_ONVIF_MOTION_TOPIC_KEYWORDS = "onvif_motion_topic_keywords"
CONF_ONVIF_TAMPER_ITEM_KEYS = "onvif_tamper_item_keys"
CONF_ONVIF_TAMPER_TOPIC_KEYWORDS = "onvif_tamper_topic_keywords"
CONF_ONVIF_SIGNAL_ITEM_KEYS = "onvif_signal_item_keys"
CONF_ONVIF_SIGNAL_TOPIC_KEYWORDS = "onvif_signal_topic_keywords"
DEFAULT_HTTP_PORT = 80
DEFAULT_HTTP_RETRIES = 1
DEFAULT_RTSP_PATH = "/user=admin&password=&channel=1&stream=1.sdp?real_stream"
DEFAULT_SNAPSHOT_PATH = "/webcapture.jpg?command=snap&channel=1"
DEFAULT_XM_PORT = 34567
COMMON_RTSP_PATHS = (
    "/streamtype=0",
    "/user=admin&password=&channel=1&stream=1.sdp?real_stream",
    "/user=admin&password=&channel=1&stream=0.sdp?real_stream",
    "/live/ch00_0",
    "/h264",
    "/stream0",
)
DOMAIN = "wjg_camera"
PROTOCOL_HTTP = "http_only"
PROTOCOL_RTSP = "rtsp"
PROTOCOL_XM = "xm_sdk"
PROTOCOL_ONVIF = "onvif"

ONVIF_SERVICE_DEVICE = "device"
ONVIF_SERVICE_MEDIA = "media"
ONVIF_SERVICE_PTZ = "ptz"
ONVIF_SERVICE_IMAGING = "imaging"
ONVIF_SERVICE_EVENTS = "events"

ONVIF_SERVICE_PATH_CANDIDATES: dict[str, tuple[str, ...]] = {
    ONVIF_SERVICE_DEVICE: ("/onvif/device_service", "/onvif/Device"),
    ONVIF_SERVICE_MEDIA: ("/onvif/Media", "/onvif/Media2", "/onvif/media_service"),
    ONVIF_SERVICE_PTZ: ("/onvif/PTZ", "/onvif/ptz_service"),
    ONVIF_SERVICE_IMAGING: ("/onvif/Imaging", "/onvif/imaging"),
    ONVIF_SERVICE_EVENTS: ("/onvif/Events", "/onvif/events_service"),
}

# XM-3820 benoetigt application/soap+xml (SOAP 1.2). text/xml als Fallback.
ONVIF_CONTENT_TYPE_CANDIDATES = (
    "application/soap+xml; charset=utf-8",
    "text/xml; charset=utf-8",
)

ONVIF_EVENT_RULES: dict[str, dict[str, Any]] = {
    "motion": {
        "item_keys": ("ismotion", "motion", "motionalarm", "videomotion"),
        "topic_keywords": ("motion",),
        "state_attr": None,
        "topic_only_true": True,
    },
    "tamper": {
        "item_keys": ("tamper", "sabotage", "is_tamper", "cellmotiondetector"),
        "topic_keywords": ("tamper", "sabotage"),
        "state_attr": "_tamper",
        "topic_only_true": True,
    },
    "signal_loss": {
        "item_keys": ("videoloss", "signal", "signal_loss", "loss"),
        "topic_keywords": ("videoloss", "signal"),
        "state_attr": "_signal_loss",
        "topic_only_true": True,
    },
}

ONVIF_SERVICE_OPTION_MAP: dict[str, str] = {
    ONVIF_SERVICE_DEVICE: CONF_ONVIF_DEVICE_PATH,
    ONVIF_SERVICE_MEDIA: CONF_ONVIF_MEDIA_PATH,
    ONVIF_SERVICE_PTZ: CONF_ONVIF_PTZ_PATH,
    ONVIF_SERVICE_IMAGING: CONF_ONVIF_IMAGING_PATH,
    ONVIF_SERVICE_EVENTS: CONF_ONVIF_EVENTS_PATH,
}

ONVIF_EVENT_OPTION_MAP: dict[str, dict[str, str]] = {
    "motion": {
        "item_keys": CONF_ONVIF_MOTION_ITEM_KEYS,
        "topic_keywords": CONF_ONVIF_MOTION_TOPIC_KEYWORDS,
    },
    "tamper": {
        "item_keys": CONF_ONVIF_TAMPER_ITEM_KEYS,
        "topic_keywords": CONF_ONVIF_TAMPER_TOPIC_KEYWORDS,
    },
    "signal_loss": {
        "item_keys": CONF_ONVIF_SIGNAL_ITEM_KEYS,
        "topic_keywords": CONF_ONVIF_SIGNAL_TOPIC_KEYWORDS,
    },
}

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=30)  # FIX: war 10s → Gerät wurde geflutet

# XM SDK Message IDs
XM_LOGIN_REQ       = 0x03E8  # 1000
XM_LOGIN_RSP       = 0x03E9
XM_KEEPALIVE_REQ   = 0x03EE  # 1006
XM_RECORD_START    = 0x041A
XM_RECORD_STOP     = 0x041B
XM_FILELIST_REQ    = 0x0592
XM_MOTION_REQ      = 0x0144


async def _await_if_needed(value: Any) -> Any:
    """Async und sync Rueckgaben einheitlich behandeln."""
    if inspect.isawaitable(value):
        return await value
    return value

def xm_packet(session_id: int, seq: int, msg_id: int, data: dict) -> bytes:
    """Erstellt ein XM SDK Binärpaket."""
    payload = json.dumps(data, separators=(",", ":")).encode()
    # FF 01 00 00 | SessionID(4) | Sequence(4) | 00 00 | MsgID(2) | DataLen(4)
    header = struct.pack(
        "<BBHIIBBHI",
        0xFF, 0x01, 0x0000,
        session_id, seq,
        0x00, 0x00,
        msg_id, len(payload)
    )
    return header + payload

def xm_parse(data: bytes) -> tuple[int, dict]:
    """Parst ein XM SDK Antwortpaket. Gibt (msg_id, body_dict) zurück."""
    if len(data) < 20:
        return 0, {}
    _, _, _, _, _, _, _, msg_id, data_len = struct.unpack("<BBHIIBBHI", data[:20])
    body_bytes = data[20: 20 + data_len]
    try:
        body = json.loads(body_bytes.decode("utf-8").strip("\x00"))
    except Exception:
        body = {}
    return msg_id, body


class XMClient:
    """Synchroner XM SDK TCP-Client (läuft in executor)."""

    def __init__(self, host: str, port: int, username: str, password: str):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._sock: socket.socket | None = None
        self._session_id = 0
        self._seq = 0

    def connect(self, timeout: float = 5.0) -> bool:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(timeout)
            self._sock.connect((self.host, self.port))
            return self._login()
        except Exception as e:
            _LOGGER.debug("XM connect fehlgeschlagen: %s", e)
            return False

    def _send_recv(self, msg_id: int, data: dict, recv_size: int = 2048) -> dict:
        if not self._sock:
            return {}
        pkt = xm_packet(self._session_id, self._seq, msg_id, data)
        self._seq += 1
        self._sock.sendall(pkt)
        resp = self._sock.recv(recv_size)
        _, body = xm_parse(resp)
        return body

    def _login(self) -> bool:
        # Passwort-Hash nach XM-Methode (MD5 mit Padding)
        raw = hashlib.md5(self.password.encode()).hexdigest().upper()
        pwd_hash = ""
        for i in range(0, 32, 2):
            c = (ord(raw[i]) + ord(raw[i+1])) % 0x62
            pwd_hash += chr(c + (0x41 if c < 0xA else 0x30 + 0x39 - 9))
        pwd_hash = pwd_hash[:8] if self.password else ""

        resp = self._send_recv(XM_LOGIN_REQ, {
            "EncryptType": "MD5",
            "LoginType": "DVRIP-Web",
            "PassWord": pwd_hash,
            "UserName": self.username,
        })
        ret = resp.get("Ret", -1)
        if ret in (100, 101):
            self._session_id = int(resp.get("SessionID", "0x0"), 16)
            return True
        _LOGGER.warning("XM Login fehlgeschlagen, Ret=%s", ret)
        return False

    def keepalive(self) -> bool:
        resp = self._send_recv(XM_KEEPALIVE_REQ, {"Type": "KeepAlive"})
        return resp.get("Ret", 0) == 100

    def start_recording(self, channel: int = 0) -> bool:
        resp = self._send_recv(XM_RECORD_START, {
            "Action": "StartRecord", "Parameter": {"Channel": channel}
        })
        return resp.get("Ret", 0) == 100

    def stop_recording(self, channel: int = 0) -> bool:
        resp = self._send_recv(XM_RECORD_STOP, {
            "Action": "StopRecord", "Parameter": {"Channel": channel}
        })
        return resp.get("Ret", 0) == 100

    def get_file_list(self, channel: int = 0, max_files: int = 50) -> list[dict]:
        resp = self._send_recv(XM_FILELIST_REQ, {
            "Action": "FindNextFile", "FileType": "h264",
            "StartTime": "2000-01-01 00:00:00",
            "EndTime": "2099-12-31 23:59:59",
            "Channel": channel, "Count": max_files
        }, recv_size=16384)
        return resp.get("Found", [])

    def get_motion_state(self) -> bool:
        resp = self._send_recv(XM_MOTION_REQ, {
            "Name": "MotionDetect", "SessionID": hex(self._session_id)
        })
        return resp.get("Ret", 0) == 100

    def ptz_command(self, code: int, speed: int = 5, channel: int = 0) -> bool:
        resp = self._send_recv(
            0x0601,
            {
                "Parameter": {
                    "Channel": channel,
                    "CommandValue": code,
                    "Speed": speed,
                }
            },
        )
        return resp.get("Ret", 0) == 100

    def disconnect(self) -> None:
        """Offene Socket-Verbindung schliessen."""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None


class WJGCameraCoordinator(DataUpdateCoordinator):
    """Haupt-Koordinator für die WJG Kamera."""

    @staticmethod
    def _normalize_http_retries(value: Any) -> int:
        """Retry-Wert robust auf den erlaubten Bereich 0..5 bringen."""
        try:
            retries = int(value)
        except (TypeError, ValueError):
            return DEFAULT_HTTP_RETRIES
        return max(0, min(5, retries))

    @staticmethod
    def _xm_ptz_one_shot(xm: "XMClient", code: int, speed: int) -> bool:
        """Ephemere XM-SDK-Verbindung fuer einen einzelnen PTZ-Befehl (Executor)."""
        if not xm.connect(timeout=3.0):
            raise RuntimeError("XM connect/login fehlgeschlagen")
        try:
            if not xm.ptz_command(code, speed):
                raise RuntimeError("XM PTZ-Befehl wurde vom Geraet abgelehnt")
            return True
        finally:
            xm.disconnect()

    @staticmethod
    def _xm_set_recording_one_shot(xm: "XMClient", enabled: bool) -> bool:
        """Ephemere XM-SDK-Verbindung fuer Start/Stop Aufnahme (Executor)."""
        if not xm.connect(timeout=3.0):
            raise RuntimeError("XM connect/login fehlgeschlagen")
        try:
            if enabled:
                ok = xm.start_recording(0)
            else:
                ok = xm.stop_recording(0)
            if not ok:
                raise RuntimeError("XM Aufnahme-Befehl wurde vom Geraet abgelehnt")
            return True
        finally:
            xm.disconnect()

    @staticmethod
    def _parse_csv_option(value: Any) -> tuple[str, ...]:
        """CSV-/Listenoptionen robust in eine normalisierte Tupelstruktur überführen."""
        if value is None:
            return ()
        if isinstance(value, (list, tuple, set)):
            parts = [str(item).strip().lower() for item in value]
        else:
            parts = [part.strip().lower() for part in str(value).split(",")]
        seen: list[str] = []
        for part in parts:
            if part and part not in seen:
                seen.append(part)
        return tuple(seen)

    def _apply_onvif_option_overrides(self, options: dict[str, Any]) -> None:
        """Gerätespezifische ONVIF-Servicepfade und Event-Aliase aus Optionen anwenden."""
        for service_key, option_key in ONVIF_SERVICE_OPTION_MAP.items():
            service_path = self._normalize_onvif_path(options.get(option_key))
            if service_path:
                self._onvif_service_paths[service_key] = service_path

        for rule_name, option_map in ONVIF_EVENT_OPTION_MAP.items():
            rule = dict(self._onvif_event_rules.get(rule_name, {}))
            for field_name, option_key in option_map.items():
                extras = self._parse_csv_option(options.get(option_key))
                if not extras:
                    continue
                base_values = tuple(
                    str(item).strip().lower() for item in rule.get(field_name, ()) if str(item).strip()
                )
                merged = list(base_values)
                for extra in extras:
                    if extra not in merged:
                        merged.append(extra)
                rule[field_name] = tuple(merged)
            self._onvif_event_rules[rule_name] = rule

    def is_adb_proxy(self) -> bool:
        """Erkennt, ob ADB-Proxy-Modus aktiv ist (localhost mit Port 8080/8081)."""
        return (
            self.host in ("127.0.0.1", "localhost")
            and self.rtsp_port == 8080
            and self.http_port == 8081
        )

    async def async_adb_proxy_check(self) -> bool:
        """Prüft, ob ADB-Proxy-Port erreichbar ist."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:8081/") as resp:
                    return resp.status == 200
        except Exception:
            return False

    # Beispiel für automatisches Umschalten auf ADB-Proxy, falls Ports erkannt werden
    async def async_prepare_connection(self) -> None:
        """ADB-Proxy-Erreichbarkeit bei lokalem Tunnel pruefen."""
        if self.is_adb_proxy():
            ok = await self.async_adb_proxy_check()
            if not ok:
                _LOGGER.warning(
                    "ADB-Proxy-Port 8081 nicht erreichbar. Bitte ADB-Tunnel pruefen!"
                )

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.entry = entry
        options = getattr(entry, "options", {}) or {}
        self.host: str = entry.data[CONF_HOST]
        self.username: str = entry.data.get(CONF_USERNAME, "admin")
        self.password: str = entry.data.get(CONF_PASSWORD, "")
        self.protocol: str = options.get(
            CONF_PROTOCOL,
            entry.data.get(CONF_PROTOCOL, PROTOCOL_RTSP),
        )
        self.rtsp_port: int = entry.data.get(CONF_RTSP_PORT, 554)
        self.http_port: int = entry.data.get(CONF_PORT, DEFAULT_HTTP_PORT)
        self.xm_port: int = DEFAULT_XM_PORT
        self.rtsp_path: str = options.get(
            CONF_RTSP_PATH,
            entry.data.get(CONF_RTSP_PATH, DEFAULT_RTSP_PATH),
        )
        self.snapshot_path: str = options.get(
            CONF_SNAPSHOT_PATH,
            entry.data.get(CONF_SNAPSHOT_PATH, DEFAULT_SNAPSHOT_PATH),
        )
        self.http_retries: int = self._normalize_http_retries(
            options.get(
                CONF_HTTP_RETRIES,
                entry.data.get(CONF_HTTP_RETRIES, DEFAULT_HTTP_RETRIES),
            )
        )
        self._session: aiohttp.ClientSession | None = None
        self._xm: XMClient | None = None
        self._recording: bool = False
        self._motion: bool = False
        self._last_motion_time: float = 0
        self._resolved_rtsp_url: str | None = None
        self._onvif = None
        # ONVIF direkt (Options haben Vorrang vor entry.data)
        self.onvif_port: int = int(
            options.get("onvif_port", entry.data.get("onvif_port", 8899))
        )
        # PTZ
        self._ptz_speed: int = 5
        self._ptz_presets: dict[str, str] = {}  # token -> name
        # Digital Zoom (Pillow-Crop für Snapshots, CSS-Sync über Lovelace-Karte)
        self._digital_zoom: float = 1.0
        self._digital_zoom_cx: float = 0.5   # Bildmittelpunkt X (0–1)
        self._digital_zoom_cy: float = 0.5   # Bildmittelpunkt Y (0–1)
        # Imaging
        self._imaging: dict[str, Any] = {}
        # Sensoren
        self._tamper: bool = False
        self._signal_loss: bool = False
        # Stream
        self._active_stream: str = "000"  # "000"=main, "001"=sub
        self._preferred_onvif_profile_token: str = str(
            options.get(CONF_ONVIF_PROFILE_TOKEN, "")
        ).strip()
        self._preferred_onvif_video_source_token: str = str(
            options.get(CONF_ONVIF_VIDEO_SOURCE_TOKEN, "")
        ).strip()
        # Audio
        self._microphone_enabled: bool = True
        self._audio_output_token: str = ""
        self._audio_output_level: int = 100
        self._audio_output_send_primacy: str = "www.onvif.org/ver10/schema/Always"
        # System
        self._fw_version: str = ""
        self._serial_number: str = ""
        self._mac_address: str = ""
        self._camera_time: str = ""
        self._update_count: int = 0
        self._event_pullpoint_path: str = ""
        self._event_task: asyncio.Task | None = None
        self._onvif_profile_tokens: dict[str, str] = {}
        self._onvif_video_source_token: str = "000"
        self._last_ptz_fault: str = ""
        # ── FIX: WSSE immer aktiviert für XM-3820 (PasswordDigest, leeres PW) ──
        # Die Kamera akzeptiert GetCapabilities ohne Auth, alle anderen Calls
        # (GetProfiles, PTZ, etc.) benötigen WSSE PasswordDigest mit leerem Passwort.
        self._onvif_wsse_enabled: bool = True
        self._onvif_content_type: str = ONVIF_CONTENT_TYPE_CANDIDATES[0]
        self._onvif_service_paths: dict[str, str] = {
            key: paths[0] for key, paths in ONVIF_SERVICE_PATH_CANDIDATES.items()
        }
        self._onvif_event_rules: dict[str, dict[str, Any]] = {
            name: dict(rule) for name, rule in ONVIF_EVENT_RULES.items()
        }
        self._apply_onvif_option_overrides(options)

    @property
    def last_ptz_fault(self) -> str:
        """Letzten ONVIF-PTZ-Fehlertext fuer UI-Rueckmeldungen bereitstellen."""
        return self._last_ptz_fault

    @property
    def onvif_wsse_enabled(self) -> bool:
        """Aktuellen WSSE-Modus fuer Diagnose und UI bereitstellen."""
        return self._onvif_wsse_enabled

    @property
    def onvif_service_paths(self) -> dict[str, str]:
        """Aktuelle ONVIF-Servicepfade fuer Diagnose und UI bereitstellen."""
        return dict(self._onvif_service_paths)

    @property
    def onvif_content_type(self) -> str:
        """Aktuell verwendeten SOAP Content-Type bereitstellen."""
        return self._onvif_content_type

    @staticmethod
    def _extract_onvif_fault_text(response_text: str) -> str:
        """Best effort: lesbaren Fault-Text aus SOAP-Antwort extrahieren."""
        if not response_text:
            return ""
        for tag in ("Text", "faultstring", "Subcode", "Value"):
            value = WJGCameraCoordinator._xml_text(response_text, tag)
            if value:
                return value
        return ""

    def _remember_ptz_fault_from_response(self, response_text: str) -> None:
        """PTZ-Fault aus SOAP-Antwort merken, falls vorhanden."""
        fault = self._extract_onvif_fault_text(response_text)
        if fault:
            self._last_ptz_fault = fault

    def _setup_onvif(self) -> None:
        """
        ONVIF-Initialisierung.

        FIX: python-onvif-zeep wird NICHT mehr verwendet — die Library erzwingt
        WSSE-Formate die XM/Xiongmai-Firmware ablehnt. Stattdessen nutzt der
        Coordinator seinen eigenen _onvif_soap / _wsse_header direkt.

        Verifiziert via Reverse Engineering (05/2026):
        - GetCapabilities: kein Auth nötig (HTTP 200)
        - GetProfiles/PTZ/etc.: WSSE PasswordDigest mit leerem Passwort (SHA1)
        - Port 34567 (XM SDK) auf dieser Kamera geschlossen → kein Fallback möglich
        """
        self._onvif = None  # python-onvif-zeep nicht initialisieren
        _LOGGER.info(
            "WJG XM-3820: ONVIF Direct-SOAP aktiv (kein python-onvif-zeep) "
            "– WSSE PasswordDigest, Host=%s Port=%s",
            self.host,
            self.onvif_port,
        )

    async def async_onvif_ptz(self, cmd: str, speed: float = 0.5) -> bool:
        """ONVIF PTZ-Befehl senden (up/down/left/right/zoom_in/zoom_out/stop)."""
        # python-onvif-zeep wird nicht mehr verwendet (self._onvif ist immer None)
        # Direkt über _onvif_soap_for senden — gleiche Logik wie async_ptz_command
        profile_token = await self._async_active_onvif_profile_token()
        spd = f"{speed:.2f}"
        soap_velocity_map: dict[str, str] = {
            "up":       f'<tt:PanTilt x="0.00" y="{spd}"/>',
            "down":     f'<tt:PanTilt x="0.00" y="-{spd}"/>',
            "left":     f'<tt:PanTilt x="-{spd}" y="0.00"/>',
            "right":    f'<tt:PanTilt x="{spd}" y="0.00"/>',
            "zoom_in":  f'<tt:Zoom x="{spd}"/>',
            "zoom_out": f'<tt:Zoom x="-{spd}"/>',
        }
        if cmd == "stop":
            return await self.async_ptz_stop()
        if cmd not in soap_velocity_map:
            return False
        resp = await self._onvif_soap_for(
            ONVIF_SERVICE_PTZ,
            f"<tptz:ContinuousMove>"
            f"<tptz:ProfileToken>{profile_token}</tptz:ProfileToken>"
            f"<tptz:Velocity>{soap_velocity_map[cmd]}</tptz:Velocity>"
            f"</tptz:ContinuousMove>",
        )
        return self._ptz_response_ok(resp, "ContinuousMove")

    async def async_onvif_stream_url(self) -> str | None:
        """ONVIF Stream-URL abrufen."""
        profile_token = await self._async_active_onvif_profile_token()
        resp = await self._onvif_soap_for(
            ONVIF_SERVICE_MEDIA,
            "<trt:GetStreamUri>"
            f"<trt:ProfileToken>{profile_token}</trt:ProfileToken>"
            "<trt:StreamSetup>"
            "<tt:Stream>RTP-Unicast</tt:Stream>"
            "<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>"
            "</trt:StreamSetup>"
            "</trt:GetStreamUri>",
        )
        raw_uri = self._xml_text(resp, "Uri")
        normalized_uri = self._normalize_onvif_rtsp_url(raw_uri)
        if normalized_uri:
            _LOGGER.debug(
                "ONVIF Stream-URL per Direct-SOAP aufgelöst: profile=%s raw=%s normalized=%s",
                profile_token,
                raw_uri,
                normalized_uri,
            )
            return normalized_uri
        return None

    def _normalize_onvif_rtsp_url(self, raw_url: str | None) -> str | None:
        """ONVIF-RTSP-URLs auf Host, Port und optionale Auth vervollständigen."""
        if not raw_url:
            return None
        parsed = urlparse(raw_url)
        if parsed.scheme.lower() != "rtsp":
            return raw_url

        hostname = parsed.hostname or self.host
        port = parsed.port or self.rtsp_port
        username = parsed.username
        password = parsed.password
        if username is None and self.username:
            username = quote(self.username or "admin", safe="")
            password = quote(self.password or "", safe="")

        if username is not None:
            auth = f"{username}:{password or ''}@"
        else:
            auth = ""
        netloc = f"{auth}{hostname}:{port}"
        path = parsed.path or "/"
        return urlunparse(("rtsp", netloc, path, parsed.params, parsed.query, parsed.fragment))

    def _render_rtsp_path(self) -> str:
        """RTSP-Pfad mit konfigurierten Zugangsdaten erzeugen."""
        username = quote(self.username or "admin", safe="")
        password = quote(self.password or "", safe="")
        return (
            self.rtsp_path
            .replace("{username}", username)
            .replace("{password}", password)
        )

    def _credential_authorities(self) -> list[str]:
        """Mögliche Auth-Varianten für RTSP-URLs erzeugen."""
        username = quote(self.username or "admin", safe="")
        password = quote(self.password or "", safe="")

        variants: list[str] = []
        if username and password:
            variants.append(f"{username}:{password}@")
        elif username:
            variants.extend((f"{username}:@", f"{username}@", ""))
        else:
            variants.append("")

        # Reihenfolge erhalten, Duplikate entfernen
        deduped: list[str] = []
        for authority in variants:
            if authority not in deduped:
                deduped.append(authority)
        return deduped

    def _build_rtsp_url(self, path: str, authority: str | None = None) -> str:
        """Vollständige RTSP-URL für einen Pfad erzeugen."""
        credentials = authority if authority is not None else self._credential_authorities()[0]
        return f"rtsp://{credentials}{self.host}:{self.rtsp_port}{path}"

    def _candidate_rtsp_paths(self) -> list[str]:
        """Konfigurierten Pfad plus bekannte Fallbacks in Prioritätsreihenfolge."""
        configured_path = self._render_rtsp_path()
        paths = [configured_path]
        for path in COMMON_RTSP_PATHS:
            rendered = (
                path.replace("{username}", quote(self.username or "admin", safe=""))
                .replace("{password}", quote(self.password or "", safe=""))
            )
            if rendered not in paths:
                paths.append(rendered)
        return paths

    def _candidate_rtsp_urls(self) -> list[str]:
        """RTSP-Kandidaten inkl. Auth-Varianten generieren."""
        urls: list[str] = []
        for path in self._candidate_rtsp_paths():
            for authority in self._credential_authorities():
                url = self._build_rtsp_url(path, authority)
                if url not in urls:
                    urls.append(url)
        return urls

    def _rtsp_url_has_video(self, rtsp_url: str, timeout: float = 4.0) -> bool:
        """Per RTSP DESCRIBE prüfen, ob die URL einen Video-Track liefert."""
        describe = (
            f"DESCRIBE {rtsp_url} RTSP/1.0\r\n"
            "CSeq: 1\r\n"
            "Accept: application/sdp\r\n\r\n"
        )
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            sock.connect((self.host, self.rtsp_port))
            sock.sendall(describe.encode())
            response = sock.recv(4096).decode("utf-8", errors="ignore")
            return "m=video" in response.lower()
        except Exception as e:
            _LOGGER.debug("RTSP DESCRIBE fehlgeschlagen für %s: %s", rtsp_url, e)
            return False
        finally:
            try:
                sock.close()
            except Exception:
                pass

    async def async_resolve_rtsp_path(self) -> None:
        """Ersten RTSP-Pfad mit Video-Track finden und merken."""
        if self.protocol not in (PROTOCOL_RTSP, PROTOCOL_ONVIF):
            return

        # Konfigurierten Pfad zuerst pruefen — ONVIF GetStreamUri liefert fuer
        # XM-Kameras oft fehlerhafte URLs (/streamtype=0).
        for rtsp_url in self._candidate_rtsp_urls():
            has_video = await self.hass.async_add_executor_job(
                self._rtsp_url_has_video,
                rtsp_url,
                4.0,
            )
            if has_video:
                self._resolved_rtsp_url = rtsp_url
                if rtsp_url != self._build_rtsp_url(self._render_rtsp_path()):
                    _LOGGER.info("RTSP-Fallback mit Video erkannt: %s", rtsp_url)
                return

        if self.protocol == PROTOCOL_ONVIF:
            onvif_url = await self.async_onvif_stream_url()
            if onvif_url:
                has_video = await self.hass.async_add_executor_job(
                    self._rtsp_url_has_video,
                    onvif_url,
                    4.0,
                )
                if has_video:
                    self._resolved_rtsp_url = onvif_url
                    _LOGGER.info("ONVIF Stream-URL als Fallback genutzt: %s", onvif_url)
                    return

        self._resolved_rtsp_url = self._build_rtsp_url(self._render_rtsp_path())

    @property
    def rtsp_url(self) -> str:
        if self._resolved_rtsp_url:
            return self._resolved_rtsp_url
        return self._build_rtsp_url(self._render_rtsp_path())

    @property
    def snapshot_url(self) -> str:
        return f"http://{self.host}:{self.http_port}{self.snapshot_path}"

    def _candidate_snapshot_urls(self) -> list[str]:
        """Mehrere Snapshot-URL-Kandidaten bereitstellen (XM/ONVIF Derivate)."""
        candidates: list[str] = [self.snapshot_url]
        fallback_paths = (
            "/webcapture.jpg?command=snap&channel=1",
            "/webcapture.jpg",
            "/cgi-bin/snapshot.cgi?channel=1",
            "/snap.jpg",
        )
        for path in fallback_paths:
            url = f"http://{self.host}:{self.http_port}{path}"
            if url not in candidates:
                candidates.append(url)

        if self.username:
            user_q = quote(self.username or "admin", safe="")
            pass_q = quote(self.password or "", safe="")
            with_query_auth: list[str] = []
            for url in candidates:
                separator = "&" if "?" in url else "?"
                url_with_auth = f"{url}{separator}user={user_q}&password={pass_q}"
                if url_with_auth not in candidates and url_with_auth not in with_query_auth:
                    with_query_auth.append(url_with_auth)
            candidates.extend(with_query_auth)

        return candidates

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def motion_detected(self) -> bool:
        """True zurueckgeben, solange die letzte Bewegung frisch genug ist."""
        return time.time() - self._last_motion_time < 30

    @property
    def last_motion_time(self) -> float:
        return self._last_motion_time

    async def async_setup(self) -> None:
        """Verbindung herstellen und testen."""
        self._session = aiohttp.ClientSession()
        session = self._session
        assert session is not None

        if self.protocol != PROTOCOL_ONVIF:
            # HTTP-Erreichbarkeit prüfen
            try:
                async with async_timeout.timeout(5):
                    async with session.get(
                        f"http://{self.host}:{self.http_port}/",
                        allow_redirects=True
                    ) as resp:
                        _LOGGER.debug(
                            "Kamera HTTP erreichbar, Status: %s", resp.status
                        )
            except Exception as e:
                _LOGGER.warning("HTTP nicht erreichbar: %s — versuche RTSP-only", e)

        # XM SDK verbinden (in executor, da synchron)
        if self.protocol == PROTOCOL_XM:
            await self.hass.async_add_executor_job(self._setup_xm)

        if self.protocol == PROTOCOL_ONVIF:
            _LOGGER.info(
                "WJG PTZ Build 2.2.2: ONVIF Direct-SOAP aktiv, WSSE=%s, "
                "Host=%s Port=%s – python-onvif-zeep deaktiviert",
                self._onvif_wsse_enabled,
                self.host,
                self.onvif_port,
            )
            # Läuft synchron, kein Executor nötig (setzt nur self._onvif = None)
            self._setup_onvif()
            try:
                await self._async_bootstrap_onvif_service_paths()
            except Exception as exc:
                _LOGGER.debug("ONVIF GetServices-Bootstrap fehlgeschlagen: %s", exc)

        await self.async_resolve_rtsp_path()

        # Einmalig Geräte-Infos und Bildeinstellungen laden
        try:
            await self.async_fetch_device_info()
        except Exception as exc:
            _LOGGER.debug("Geräte-Info nicht abrufbar: %s", exc)
        try:
            await self.async_fetch_imaging_settings()
        except Exception as exc:
            _LOGGER.debug("Imaging-Einstellungen nicht abrufbar: %s", exc)
        try:
            await self.async_ptz_get_presets()
        except Exception as exc:
            _LOGGER.debug("PTZ-Presets nicht abrufbar: %s", exc)
        try:
            await self.async_fetch_audio_settings()
        except Exception as exc:
            _LOGGER.debug("Audio-Einstellungen nicht abrufbar: %s", exc)

        if self.protocol == PROTOCOL_ONVIF:
            # kein update_xaddrs mehr (python-onvif-zeep nicht aktiv)
            self._event_task = asyncio.create_task(self._async_onvif_event_loop())

        await self.async_refresh()

    def _setup_xm(self) -> None:
        """XM SDK Client initialisieren (blockierend, im executor)."""
        client = XMClient(self.host, self.xm_port, self.username, self.password)
        if client.connect():
            self._xm = client
            _LOGGER.info(
                "XM SDK Verbindung erfolgreich zu %s:%s",
                self.host,
                self.xm_port,
            )
        else:
            _LOGGER.warning("XM SDK nicht verfügbar — Fallback auf HTTP")

    async def _tcp_port_reachable(self, port: int, timeout: float = 3.0) -> bool:
        """Prüft per TCP-Connect ob ein Port erreichbar ist (Fallback-Ping)."""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, port),
                timeout=timeout,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False

    async def _async_update_data(self) -> dict[str, Any]:
        """Kamera-Status aktualisieren."""
        self._update_count += 1
        data: dict[str, Any] = {
            "available": False,
            "recording": self._recording,
            "motion": self.motion_detected,
            "files": [],
        }

        # Snapshot abrufen um Erreichbarkeit zu prüfen
        session = self._session
        if session is not None:
            try:
                auth = None
                if self.username:
                    auth = aiohttp.BasicAuth(self.username, self.password or "")
                async with async_timeout.timeout(5):
                    async with session.get(
                        self.snapshot_url,
                        allow_redirects=True,
                        auth=auth,
                    ) as resp:
                        # 200 = OK, 401/403 = Kamera antwortet (Auth fehlt oder
                        # nicht nötig), trotzdem erreichbar
                        if resp.status in (200, 401, 403):
                            data["available"] = True
                        if resp.status == 200:
                            ct = resp.headers.get("Content-Type", "")
                            if "image" in ct:
                                data["snapshot_bytes"] = await resp.read()
            except Exception as e:
                _LOGGER.debug("Snapshot fehlgeschlagen: %s", e)

        # Fallback: TCP-Erreichbarkeit prüfen (nur primären Port, nicht alle 3)
        # FIX Bug 3: war {rtsp_port, http_port, xm_port} → 3 TCP-Connects pro Poll-Zyklus
        if not data["available"]:
            primary_port = (
                self.rtsp_port if self.protocol == PROTOCOL_RTSP else self.http_port
            )
            if primary_port and await self._tcp_port_reachable(primary_port):
                data["available"] = True
                _LOGGER.debug(
                    "Kamera via TCP-Port %s erreichbar (Snapshot nicht verfügbar)",
                    primary_port,
                )

        # XM Keepalive + Status
        if self._xm:
            try:
                ok = await self.hass.async_add_executor_job(self._xm.keepalive)
                if ok:
                    data["available"] = True
            except Exception as e:
                _LOGGER.debug("XM Keepalive fehlgeschlagen: %s — reconnect", e)
                await self.hass.async_add_executor_job(self._setup_xm)

        # Alle 180 Sekunden (6 × 30s): Kamerazeit; alle 900s (30 × 30s): Imaging refresh
        if self._update_count % 6 == 0:
            try:
                await self.async_fetch_camera_time()
            except Exception:
                pass
            if self._update_count % 30 == 0:
                try:
                    await self.async_fetch_imaging_settings()
                except Exception:
                    pass

        return data

    async def _async_http_get_data(
        self,
        url: str,
        timeout_seconds: int,
        retries: int | None = None,
        as_json: bool = False,
    ) -> bytes | dict[str, Any] | None:
        """HTTP GET mit kleinem Retry für transiente Fehler."""
        if not self._session:
            return None

        auth = None
        if self.username:
            auth = aiohttp.BasicAuth(self.username, self.password or "")

        effective_retries = self.http_retries if retries is None else retries
        attempts = max(1, effective_retries + 1)
        for attempt in range(attempts):
            try:
                async with async_timeout.timeout(timeout_seconds):
                    async with self._session.get(url, auth=auth) as resp:
                        if resp.status == 200:
                            if as_json:
                                return await resp.json()
                            return await resp.read()
            except Exception as e:
                _LOGGER.debug("HTTP GET fehlgeschlagen (%s): %s", url, e)

            if attempt < attempts - 1:
                await asyncio.sleep(0)

        return None

    async def async_set_recording(self, enabled: bool) -> bool:
        """Aufnahme starten oder stoppen."""
        xm_client = self._xm
        if xm_client is not None:
            if enabled:
                ok = await self.hass.async_add_executor_job(
                    xm_client.start_recording,
                    0,
                )
            else:
                ok = await self.hass.async_add_executor_job(
                    xm_client.stop_recording,
                    0,
                )
            if ok:
                self._recording = enabled
                return True

        # ONVIF-/HTTP-Modus: XM Einmal-Fallback trotzdem probieren (Port 34567).
        if xm_client is None:
            xm_tmp = XMClient(self.host, self.xm_port, self.username or "", self.password or "")
            try:
                _LOGGER.warning(
                    "Recording XM-SDK-Fallback gestartet (Port %s): enabled=%s",
                    self.xm_port,
                    enabled,
                )
                ok = await self.hass.async_add_executor_job(
                    self._xm_set_recording_one_shot,
                    xm_tmp,
                    enabled,
                )
                if ok:
                    self._recording = enabled
                    _LOGGER.warning(
                        "Recording ueber XM-SDK-Fallback erfolgreich: enabled=%s",
                        enabled,
                    )
                    return True
            except Exception as exc:
                _LOGGER.warning("Recording XM-SDK-Fallback fehlgeschlagen: %s", exc)

        # HTTP-Fallback (manche Kameras)
        if self._session:
            cmd = "start" if enabled else "stop"
            url = f"http://{self.host}:{self.http_port}/cgi-bin/record?cmd={cmd}&channel=1"
            data = await self._async_http_get_data(url, timeout_seconds=5)
            if isinstance(data, (bytes, bytearray)):
                self._recording = enabled
                return True
        return False

    async def async_get_file_list(self) -> list[dict]:
        """Dateiliste von der Kamera abrufen."""
        xm_client = self._xm
        if xm_client is not None:
            files = await self.hass.async_add_executor_job(
                xm_client.get_file_list,
                0,
                50,
            )
            return files

        # HTTP-Fallback
        if self._session:
            url = f"http://{self.host}:{self.http_port}/cgi-bin/fileman"
            data = await self._async_http_get_data(
                url,
                timeout_seconds=10,
                as_json=True,
            )
            if isinstance(data, dict):
                return data.get("files", [])
        return []

    async def async_snapshot(self) -> bytes | None:
        """Aktuelles Bild von der Kamera laden."""
        if not self._session:
            return None

        auth_candidates: list[aiohttp.BasicAuth | None] = [None]
        if self.username:
            auth_candidates = [
                aiohttp.BasicAuth(self.username, self.password or ""),
                None,
            ]

        attempts = max(1, self.http_retries + 1)
        for snapshot_url in self._candidate_snapshot_urls():
            for auth in auth_candidates:
                for attempt in range(attempts):
                    try:
                        async with async_timeout.timeout(5):
                            async with self._session.get(
                                snapshot_url,
                                allow_redirects=True,
                                auth=auth,
                            ) as resp:
                                if resp.status == 200:
                                    payload = await resp.read()
                                    if payload:
                                        return bytes(payload)
                                if resp.status in (401, 403) and auth is not None:
                                    break
                    except Exception as exc:
                        _LOGGER.debug(
                            "Snapshot fehlgeschlagen (%s, auth=%s): %s",
                            snapshot_url,
                            "on" if auth is not None else "off",
                            exc,
                        )
                    if attempt < attempts - 1:
                        await asyncio.sleep(0)
        return None

    @staticmethod
    def _ptz_http_payload_looks_ok(payload: bytes) -> bool:
        """HTTP-PTZ Antwortinhalt grob validieren."""
        if not payload:
            return True
        lowered = payload.decode("utf-8", errors="ignore").lower()
        bad_markers = (
            "404 not found",
            "unknown service",
            "unknown command",
            "invalid",
            "error",
            "failed",
            "not support",
            "unauthorized",
        )
        return not any(marker in lowered for marker in bad_markers)

    def _candidate_http_ptz_urls(self, cmd: str, speed: int) -> list[str]:
        """Mehrere PTZ-CGI-Endpunkte erzeugen."""
        safe_speed = max(1, min(8, int(speed)))
        candidates: list[str] = [
            (
                f"http://{self.host}:{self.http_port}/cgi-bin/ptz"
                f"?channel=1&cmd={cmd}&speed={safe_speed}"
            )
        ]

        hi3510_act = {
            "up": "up", "down": "down", "left": "left", "right": "right",
            "zoom_in": "zoomin", "zoom_out": "zoomout",
            "focus_in": "focusin", "focus_out": "focusout",
        }
        for base_path in ("/web/cgi-bin/hi3510/ptzctrl.cgi", "/cgi-bin/hi3510/ptzctrl.cgi"):
            if cmd == "stop":
                candidates.append(f"http://{self.host}:{self.http_port}{base_path}?-act=stop")
            elif cmd in hi3510_act:
                candidates.append(
                    f"http://{self.host}:{self.http_port}{base_path}"
                    f"?-step=0&-act={hi3510_act[cmd]}&-speed={safe_speed}"
                )

        decoder_cmds: dict[str, list[int]] = {
            "up": [0], "down": [2], "left": [4], "right": [6],
            "zoom_in": [16], "zoom_out": [18],
            "focus_in": [20], "focus_out": [22],
            "stop": [1, 3, 5, 7, 17, 19, 21, 23],
        }
        for command_code in decoder_cmds.get(cmd, []):
            base = (
                f"http://{self.host}:{self.http_port}/decoder_control.cgi"
                f"?command={command_code}&onestep=0"
            )
            candidates.append(base)
            if self.username:
                user_q = quote(self.username or "admin", safe="")
                pass_q = quote(self.password or "", safe="")
                candidates.append(f"{base}&user={user_q}&pwd={pass_q}")
                candidates.append(f"{base}&loginuse={user_q}&loginpas={pass_q}")

        deduped: list[str] = []
        for candidate in candidates:
            if candidate not in deduped:
                deduped.append(candidate)
        return deduped

    def _xm_soap_send(
        self, url: str, xml_bytes: bytes, extra_headers: dict | None = None
    ) -> tuple[int, str]:
        """Sendet SOAP-Request via urllib (kein aiohttp, keine Versionsabhaengigkeit)."""
        hdrs: dict[str, str] = {"Content-Type": "application/soap+xml; charset=utf-8"}
        if extra_headers:
            hdrs.update(extra_headers)
        req = _UrllibRequest(url, data=xml_bytes, headers=hdrs, method="POST")
        try:
            with _urlopen(req, timeout=6) as r:
                return r.status, r.read().decode("utf-8", errors="replace")
        except _UrllibHTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")
        except Exception as exc:
            return 0, str(exc)

    def _xm_camera_utcnow_sync(self) -> datetime.datetime:
        """Liest Kamera-UTC-Zeit via GetSystemDateAndTime (kein Auth noetig).

        Prueft ob Systemzeit von Kamera-Zeit abweicht – relevant fuer WSSE-Timestamps
        auf Proxmox-VMs mit Clock-Drift.
        """
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"'
            ' xmlns:tds="http://www.onvif.org/ver10/device/wsdl">'
            '<s:Body><tds:GetSystemDateAndTime/></s:Body>'
            '</s:Envelope>'
        ).encode("utf-8")
        url = f"http://{self.host}:{self.onvif_port}/onvif/device_service"
        try:
            status, text = self._xm_soap_send(url, body)
            if status == 200:
                def _int(tag: str) -> int:
                    m = re.search(rf"<(?:[^:>]+:)?{tag}[^>]*>(\d+)<", text)
                    return int(m.group(1)) if m else 0
                year = _int("Year")
                month = _int("Month") or 1
                day = _int("Day") or 1
                hour = _int("Hour")
                minute = _int("Minute")
                second = _int("Second")
                if year > 2000:
                    cam_dt = datetime.datetime(
                        year, month, day, hour, minute, second,
                        tzinfo=datetime.timezone.utc,
                    )
                    skew = abs(
                        (cam_dt - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
                    )
                    # FIX Bug 2: war immer WARNING → spam bei jedem PTZ-Aufruf.
                    # Nur warnen wenn Abweichung groß genug ist um WSSE zu brechen (>5s).
                    if skew > 5:
                        _LOGGER.warning(
                            "XM Kamera-Zeit weicht %.1fs ab – WSSE könnte fehlschlagen! "
                            "Kamera: %s  HA: %s",
                            skew,
                            cam_dt.isoformat(),
                            datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        )
                    else:
                        _LOGGER.debug(
                            "XM Kamera-Zeit: %s  Abweichung: %.1fs",
                            cam_dt.isoformat(), skew,
                        )
                    return cam_dt
        except Exception as exc:
            _LOGGER.debug("GetSystemDateAndTime fehlgeschlagen: %s", exc)
        return datetime.datetime.now(datetime.timezone.utc)

    def _xm_ptz_envelope(
        self,
        body: str,
        with_wsse: bool = True,
        utc_now: datetime.datetime | None = None,
    ) -> bytes:
        """Baut SOAP-Envelope fuer PTZ-Befehle.

        utc_now: Kamera-Zeit fuer clock-korrektes WSSE (verhindert Ablehnung bei
        Proxmox VM Clock-Drift).
        """
        ns = (
            ' xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"'
            ' xmlns:tt="http://www.onvif.org/ver10/schema"'
        )
        if with_wsse:
            ns += (
                ' xmlns:wsse="http://docs.oasis-open.org/wss/2004/01'
                '/oasis-200401-wss-wssecurity-secext-1.0.xsd"'
                ' xmlns:wsu="http://docs.oasis-open.org/wss/2004/01'
                '/oasis-200401-wss-wssecurity-utility-1.0.xsd"'
            )
        header = (
            f"<s:Header>{self._wsse_header(utc_now=utc_now)}</s:Header>"
            if with_wsse else ""
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"{ns}>'
            f"{header}"
            f"<s:Body>{body}</s:Body>"
            "</s:Envelope>"
        ).encode("utf-8")

    async def _xm_direct_ptz(self, cmd: str, speed: int = 5) -> bool:
        """Direkt-SOAP via urllib – versucht kein-Auth, WSSE und Basic-Auth."""
        if cmd == "stop":
            return await self._xm_direct_ptz_stop()
        spd = min(speed, 8) / 8
        vel_map = {
            "up":       (0.0,  spd,  0.0),
            "down":     (0.0, -spd,  0.0),
            "left":     (-spd, 0.0,  0.0),
            "right":    (spd,  0.0,  0.0),
            "zoom_in":  (0.0,  0.0,  spd),
            "zoom_out": (0.0,  0.0, -spd),
        }
        if cmd not in vel_map:
            return False
        pan, tilt, zoom = vel_map[cmd]
        move_body = (
            f"<tptz:ContinuousMove>"
            f"<tptz:ProfileToken>000</tptz:ProfileToken>"
            f"<tptz:Velocity>"
            f'<tt:PanTilt x="{pan:.2f}" y="{tilt:.2f}"/>'
            f'<tt:Zoom x="{zoom:.2f}"/>'
            f"</tptz:Velocity>"
            f"</tptz:ContinuousMove>"
        )
        url = f"http://{self.host}:{self.onvif_port}/onvif/PTZ"
        loop = asyncio.get_running_loop()

        def _fault(resp: str) -> str:
            """Extrahiert Fault-Text aus SOAP-Response fuer lesbare Logs."""
            m = re.search(r"<[^>:]+:?Text[^>]*>([^<]+)", resp)
            if m:
                return m.group(1).strip()
            m2 = re.search(r"<[^>:]+:?faultstring[^>]*>([^<]+)", resp)
            if m2:
                return m2.group(1).strip()
            return resp[300:700] if len(resp) > 300 else resp

        # --- Versuch 1: kein Auth ---
        xml_no_auth = self._xm_ptz_envelope(move_body, with_wsse=False)
        status, text = await loop.run_in_executor(
            None, lambda: self._xm_soap_send(url, xml_no_auth)
        )
        # FIX Bug 1: war WARNING → spam auch bei Erfolg. Nur Fehler als warning.
        _LOGGER.debug("XM PTZ [1/3] no-auth: HTTP %s | fault: %s", status, _fault(text))
        if status == 200 and "fault" not in text.lower():
            _LOGGER.info("XM PTZ '%s' OK via no-auth", cmd)
            self._last_ptz_fault = ""
            async def _stop1() -> None:
                await asyncio.sleep(0.8)
                await self._xm_direct_ptz_stop()
            self.hass.async_create_task(_stop1())
            return True

        # --- Versuch 2: WSSE PasswordDigest (mit Kamera-Zeit gegen Clock-Skew) ---
        cam_time = await loop.run_in_executor(None, self._xm_camera_utcnow_sync)
        xml_wsse = self._xm_ptz_envelope(move_body, with_wsse=True, utc_now=cam_time)
        status, text = await loop.run_in_executor(
            None, lambda: self._xm_soap_send(url, xml_wsse)
        )
        # FIX Bug 1: war WARNING → debug reicht für Diagnose
        _LOGGER.debug(
            "XM PTZ [2/3] wsse (cam_time=%s): HTTP %s | fault: %s",
            cam_time.strftime("%H:%M:%S"), status, _fault(text),
        )
        if status == 200 and "fault" not in text.lower():
            _LOGGER.info("XM PTZ '%s' OK via wsse", cmd)
            self._last_ptz_fault = ""
            async def _stop2() -> None:
                await asyncio.sleep(0.8)
                await self._xm_direct_ptz_stop()
            self.hass.async_create_task(_stop2())
            return True

        # --- Versuch 3: HTTP Basic Auth (kein WSSE) ---
        basic = base64.b64encode(
            f"{self.username}:{self.password or ''}".encode()
        ).decode()
        xml_basic = self._xm_ptz_envelope(move_body, with_wsse=False)
        basic_hdrs = {"Authorization": f"Basic {basic}"}
        status, text = await loop.run_in_executor(
            None, lambda: self._xm_soap_send(url, xml_basic, basic_hdrs)
        )
        # FIX Bug 1: war WARNING → debug reicht für Diagnose
        _LOGGER.debug("XM PTZ [3/3] basic-auth: HTTP %s | fault: %s", status, _fault(text))
        if status == 200 and "fault" not in text.lower():
            _LOGGER.info("XM PTZ '%s' OK via basic-auth", cmd)
            self._last_ptz_fault = ""
            async def _stop3() -> None:
                await asyncio.sleep(0.8)
                await self._xm_direct_ptz_stop()
            self.hass.async_create_task(_stop3())
            return True

        self._last_ptz_fault = f"XM PTZ alle 3 Varianten HTTP {status}"
        _LOGGER.warning(
            "XM PTZ '%s' fehlgeschlagen (no-auth/wsse/basic alle HTTP %s)", cmd, status
        )
        return False

    async def _xm_direct_ptz_stop(self) -> bool:
        """PTZ Stop via urllib – versucht kein-Auth, dann WSSE."""
        stop_body = (
            "<tptz:Stop><tptz:ProfileToken>000</tptz:ProfileToken>"
            "<tptz:PanTilt>true</tptz:PanTilt><tptz:Zoom>true</tptz:Zoom></tptz:Stop>"
        )
        url = f"http://{self.host}:{self.onvif_port}/onvif/PTZ"
        loop = asyncio.get_running_loop()
        for with_wsse in (False, True):
            _stop_xml = self._xm_ptz_envelope(stop_body, with_wsse=with_wsse)
            status, _ = await loop.run_in_executor(
                None, lambda: self._xm_soap_send(url, _stop_xml)
            )
            if status == 200:
                return True
        return False

    async def async_ptz_command(self, cmd: str, speed: int = 5) -> bool:
        """PTZ-Steuerbefehl senden (Start/Stop Up/Down/Left/Right/Zoom)."""
        self._last_ptz_fault = ""

        # XM-3820 hat kein optisches Zoom → Buttons steuern Digital-Zoom
        if cmd == "zoom_in":
            await self.async_digital_zoom_in()
            _LOGGER.info("Digital Zoom IN → %.1f×", self._digital_zoom)
            return True
        if cmd == "zoom_out":
            await self.async_digital_zoom_out()
            _LOGGER.info("Digital Zoom OUT → %.1f×", self._digital_zoom)
            return True

        ptz_map = {
            "up": 0x10, "down": 0x11, "left": 0x12, "right": 0x13,
            "focus_in": 0x03, "focus_out": 0x04, "stop": 0xFF
        }
        if cmd not in ptz_map:
            self._last_ptz_fault = f"Unbekannter PTZ-Befehl: {cmd}"
            _LOGGER.warning("Unbekannter PTZ-Befehl: %s", cmd)
            return False

        # Primärweg: XMSoapClient mit eigener frischer Session (verifiziert 13.05)
        if self.protocol == PROTOCOL_ONVIF:
            spd = min(speed, 8) / 8
            try:
                async with _XMSoapClient(
                    host=self.host,
                    onvif_port=self.onvif_port,
                    username=self.username,
                    password=self.password,
                ) as soap:
                    ok = await soap.ptz_command(cmd, speed=spd)
                if ok:
                    self._last_ptz_fault = ""
                    _LOGGER.info("PTZ '%s' OK via XMSoapClient", cmd)
                    return True
                self._last_ptz_fault = f"XMSoapClient PTZ fehlgeschlagen: {cmd}"
                _LOGGER.warning("XMSoapClient PTZ '%s' nicht erfolgreich", cmd)
            except Exception as soap_exc:
                self._last_ptz_fault = f"XMSoapClient Fehler: {soap_exc}"
                _LOGGER.warning("XMSoapClient PTZ '%s' Exception: %s", cmd, soap_exc)

        # ONVIF: Direct-SOAP ContinuousMove als primärer Weg
        if self.protocol == PROTOCOL_ONVIF:
            if cmd == "stop":
                return await self.async_ptz_stop()
            spd = f"{min(speed, 8) / 8:.2f}"
            # xm-soap.py bestaetigt: Velocity braucht IMMER beide Elemente.
            # Kamera gibt SOAP-Fault wenn PanTilt oder Zoom fehlt.
            # Hinweis: zoom_in/zoom_out werden bereits oben als Digital-Zoom
            # behandelt und kehren früh zurück – sie kommen hier nie an.
            soap_velocity_map: dict[str, str] = {
                "up":    f'<tt:PanTilt x="0.00" y="{spd}"/><tt:Zoom x="0.00"/>',
                "down":  f'<tt:PanTilt x="0.00" y="-{spd}"/><tt:Zoom x="0.00"/>',
                "left":  f'<tt:PanTilt x="-{spd}" y="0.00"/><tt:Zoom x="0.00"/>',
                "right": f'<tt:PanTilt x="{spd}" y="0.00"/><tt:Zoom x="0.00"/>',
            }
            # FIX Bug 4: soap_translation_map war eine 1:1-Kopie von soap_velocity_map → entfernt
            if cmd in soap_velocity_map:
                tried_tokens = await self._async_candidate_ptz_profile_tokens()
                for profile_token in tried_tokens:
                    resp = await self._async_ptz_soap_request(
                        "ContinuousMove",
                        f"<tptz:ContinuousMove>"
                        f"<tptz:ProfileToken>{profile_token}</tptz:ProfileToken>"
                        f"<tptz:Velocity>{soap_velocity_map[cmd]}</tptz:Velocity>"
                        f"</tptz:ContinuousMove>",
                    )
                    if self._ptz_response_ok(resp, "ContinuousMove"):
                        self._onvif_profile_tokens[self._active_stream] = profile_token
                        self._last_ptz_fault = ""
                        return True

                for profile_token in tried_tokens:
                    resp = await self._async_ptz_soap_request(
                        "ContinuousMove",
                        f"<tptz:ContinuousMove>"
                        f"<tptz:ProfileToken>{profile_token}</tptz:ProfileToken>"
                        f"<tptz:Velocity>{soap_velocity_map[cmd]}</tptz:Velocity>"
                        f"<tptz:Timeout>PT0.50S</tptz:Timeout>"
                        f"</tptz:ContinuousMove>",
                    )
                    if self._ptz_response_ok(resp, "ContinuousMove"):
                        self._onvif_profile_tokens[self._active_stream] = profile_token
                        self._last_ptz_fault = ""
                        return True

                for profile_token in tried_tokens:
                    resp = await self._async_ptz_soap_request(
                        "RelativeMove",
                        f"<tptz:RelativeMove>"
                        f"<tptz:ProfileToken>{profile_token}</tptz:ProfileToken>"
                        f"<tptz:Translation>{soap_velocity_map[cmd]}</tptz:Translation>"
                        f"</tptz:RelativeMove>",
                    )
                    if self._ptz_response_ok(resp, "RelativeMove"):
                        self._onvif_profile_tokens[self._active_stream] = profile_token
                        self._last_ptz_fault = ""
                        return True

                # python-onvif-zeep Fallback entfernt (self._onvif ist None)
                if not self._last_ptz_fault:
                    self._last_ptz_fault = "PTZ fehlgeschlagen (kein ONVIF-Response vom Geraet)"

        if self._xm:
            code = ptz_map[cmd]
            try:
                return await self.hass.async_add_executor_job(
                    self._xm.ptz_command, code, speed, 0
                )
            except Exception as e:
                self._last_ptz_fault = str(e)
                _LOGGER.error("PTZ-Befehl fehlgeschlagen: %s", e)

        # XM-SDK-Fallback (Port 34567 auf XM-3820 geschlossen, aber Code bleibt für
        # andere Geräte erhalten)
        if self.protocol == PROTOCOL_ONVIF and not self._xm:
            code = ptz_map[cmd]
            xm_tmp = XMClient(self.host, self.xm_port, self.username or "", self.password or "")
            try:
                ok = await self.hass.async_add_executor_job(
                    self._xm_ptz_one_shot, xm_tmp, code, speed
                )
                if ok:
                    self._last_ptz_fault = ""
                    return True
                self._last_ptz_fault = (
                    f"PTZ XM-SDK-Fallback fehlgeschlagen (Port {self.xm_port})"
                )
            except Exception as xm_exc:
                self._last_ptz_fault = f"PTZ XM-SDK-Fallback Fehler: {xm_exc}"

        # HTTP-Fallback
        if self._session:
            for url in self._candidate_http_ptz_urls(cmd, speed):
                data = await self._async_http_get_data(url, timeout_seconds=3)
                ok = isinstance(data, (bytes, bytearray)) and self._ptz_http_payload_looks_ok(bytes(data))
                if ok:
                    self._last_ptz_fault = ""
                    return True
            if not self._last_ptz_fault:
                self._last_ptz_fault = "PTZ HTTP-Fallback fehlgeschlagen"
            return False
        if not self._last_ptz_fault:
            self._last_ptz_fault = "PTZ fehlgeschlagen"
        return False

    # ── ONVIF Direct-SOAP helper ────────────────────────────────────────────

    def _wsse_header(self, utc_now: datetime.datetime | None = None) -> str:
        """WSSE PasswordDigest Header.

        utc_now: optionale Referenzzeit (z.B. Kamera-Zeit) um Clock-Skew
        bei Proxmox-VMs zu vermeiden.
        """
        nonce = os.urandom(16)
        now = (utc_now or datetime.datetime.now(datetime.timezone.utc)).replace(tzinfo=None)
        created = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
        pwd = (self.password or "").encode("utf-8")
        digest = base64.b64encode(
            hashlib.sha1(nonce + created.encode() + pwd).digest()
        ).decode()
        nonce_b64 = base64.b64encode(nonce).decode()
        return (
            '<wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01'
            '/oasis-200401-wss-wssecurity-secext-1.0.xsd"'
            ' xmlns:wsu="http://docs.oasis-open.org/wss/2004/01'
            '/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
            "<wsse:UsernameToken>"
            f"<wsse:Username>{self.username}</wsse:Username>"
            f'<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01'
            f'/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
            f"{digest}</wsse:Password>"
            f'<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01'
            f'/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
            f"{nonce_b64}</wsse:Nonce>"
            f"<wsu:Created>{created}</wsu:Created>"
            "</wsse:UsernameToken></wsse:Security>"
        )

    def _wsse_header_plaintext(self, password_override: str | None = None) -> str:
        """WSSE-Header mit PasswordText (Klartext)."""
        nonce = os.urandom(16)
        nonce_b64 = base64.b64encode(nonce).decode()
        created = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        pwd = password_override if password_override is not None else (self.password or "")
        return (
            '<wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01'
            '/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
            "<wsse:UsernameToken>"
            f"<wsse:Username>{self.username}</wsse:Username>"
            f'<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01'
            f'/oasis-200401-wss-username-token-profile-1.0#PasswordText">'
            f"{pwd}</wsse:Password>"
            f'<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01'
            f'/oasis-200401-wss-wssecurity-secext-1.0.xsd#Base64Binary">'
            f"{nonce_b64}</wsse:Nonce>"
            f'<wsu:Created xmlns:wsu="http://docs.oasis-open.org/wss/2004/01'
            f'/oasis-200401-wss-wssecurity-utility-1.0.xsd">{created}</wsu:Created>'
            "</wsse:UsernameToken></wsse:Security>"
        )

    async def _onvif_soap(
        self,
        service_path: str,
        body: str,
        use_auth: bool = True,
        timeout_seconds: int = 5,
    ) -> str:
        """ONVIF SOAP-Anfrage direkt via HTTP."""
        if not self._session:
            return ""
        auth_header = self._wsse_header() if use_auth else ""
        header_xml = f"<s:Header>{auth_header}</s:Header>" if auth_header else ""
        http_auth = None
        if self.username:
            http_auth = aiohttp.BasicAuth(self.username, self.password or "")
        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"'
            ' xmlns:tds="http://www.onvif.org/ver10/device/wsdl"'
            ' xmlns:trt="http://www.onvif.org/ver10/media/wsdl"'
            ' xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"'
            ' xmlns:tptz10="http://www.onvif.org/ver10/ptz/wsdl"'
            ' xmlns:tt="http://www.onvif.org/ver10/schema"'
            ' xmlns:timg="http://www.onvif.org/ver20/imaging/wsdl"'
            ' xmlns:tev="http://www.onvif.org/ver10/events/wsdl">'
            f"{header_xml}"
            f"<s:Body>{body}</s:Body>"
            "</s:Envelope>"
        )
        url = f"http://{self.host}:{self.onvif_port}{service_path}"
        content_types_to_try = [self._onvif_content_type] + [
            candidate
            for candidate in ONVIF_CONTENT_TYPE_CANDIDATES
            if candidate != self._onvif_content_type
        ]
        last_result = ""
        for content_type in content_types_to_try:
            # WSSE im Header genuegt — kein HTTP Basic Auth noetig (wie xm-soap.py).
            auth_candidates: list[aiohttp.BasicAuth | None] = [None]
            if http_auth is not None:
                auth_candidates.append(http_auth)
            for auth_candidate in auth_candidates:
                try:
                    async with async_timeout.timeout(timeout_seconds):
                        async with self._session.post(
                            url,
                            data=envelope.encode("utf-8"),
                            auth=auth_candidate,
                            headers={"Content-Type": content_type},
                        ) as resp:
                            result = await resp.text()
                    last_result = result
                    if self._looks_like_onvif_auth_fault(result):
                        _LOGGER.debug(
                            "ONVIF SOAP Auth-Fault mit content_type=%s path=%s",
                            content_type, service_path,
                        )
                        continue
                    if content_type != self._onvif_content_type:
                        self._onvif_content_type = content_type
                    return result
                except Exception as exc:
                    _LOGGER.debug("ONVIF SOAP [%s] fehlgeschlagen: %s", service_path, exc)
                    continue
        return last_result

    async def _onvif_soap_legacy(
        self,
        service_path: str,
        body: str,
        soap_action: str,
        timeout_seconds: int = 5,
    ) -> str:
        """ONVIF SOAP 1.1 Anfrage mit text/xml und SOAPAction für Legacy-Geräte."""
        if not self._session:
            return ""
        http_auth = None
        if self.username:
            http_auth = aiohttp.BasicAuth(self.username, self.password or "")
        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"'
            ' xmlns:tds="http://www.onvif.org/ver10/device/wsdl"'
            ' xmlns:trt="http://www.onvif.org/ver10/media/wsdl"'
            ' xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"'
            ' xmlns:tptz10="http://www.onvif.org/ver10/ptz/wsdl"'
            ' xmlns:tt="http://www.onvif.org/ver10/schema"'
            ' xmlns:timg="http://www.onvif.org/ver20/imaging/wsdl"'
            ' xmlns:tev="http://www.onvif.org/ver10/events/wsdl">'
            f"<soap:Body>{body}</soap:Body>"
            "</soap:Envelope>"
        )
        url = f"http://{self.host}:{self.onvif_port}{service_path}"
        auth_candidates: list[aiohttp.BasicAuth | None] = [http_auth] if http_auth is not None else [None]
        if http_auth is not None:
            auth_candidates.append(None)
        last_result = ""
        for auth_candidate in auth_candidates:
            try:
                async with async_timeout.timeout(timeout_seconds):
                    async with self._session.post(
                        url,
                        data=envelope.encode("utf-8"),
                        auth=auth_candidate,
                        headers={
                            "Content-Type": "text/xml; charset=utf-8",
                            "SOAPAction": f'"{soap_action}"',
                        },
                    ) as resp:
                        result = await resp.text()
                last_result = result
                if self._looks_like_onvif_auth_fault(result):
                    continue
                return result
            except Exception as exc:
                _LOGGER.debug("ONVIF SOAP 1.1 [%s] fehlgeschlagen: %s", service_path, exc)
                continue
        return last_result

    async def _onvif_soap_legacy_for(
        self,
        service_key: str,
        body: str,
        soap_action: str,
        timeout_seconds: int = 5,
    ) -> str:
        """SOAP 1.1 Variante mit Service-Fallbacks."""
        last_response = ""
        for service_path in self._candidate_onvif_service_paths(service_key):
            response_text = await self._onvif_soap_legacy(
                service_path, body, soap_action, timeout_seconds=timeout_seconds,
            )
            if self._looks_like_onvif_fault(response_text):
                last_response = response_text
                continue
            if not self._looks_like_wrong_onvif_path(response_text):
                self._remember_onvif_service_path(service_key, service_path)
                return response_text
            last_response = response_text
        return last_response

    async def _onvif_soap_with_wsse_text(
        self,
        service_path: str,
        body: str,
        password_override: str | None = None,
        timeout_seconds: int = 5,
    ) -> str:
        """ONVIF SOAP-Anfrage mit WSSE PasswordText."""
        if not self._session:
            return ""
        wsse_hdr = self._wsse_header_plaintext(password_override)
        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"'
            ' xmlns:tds="http://www.onvif.org/ver10/device/wsdl"'
            ' xmlns:trt="http://www.onvif.org/ver10/media/wsdl"'
            ' xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"'
            ' xmlns:tptz10="http://www.onvif.org/ver10/ptz/wsdl"'
            ' xmlns:tt="http://www.onvif.org/ver10/schema"'
            ' xmlns:timg="http://www.onvif.org/ver20/imaging/wsdl"'
            ' xmlns:tev="http://www.onvif.org/ver10/events/wsdl">'
            f"<s:Header>{wsse_hdr}</s:Header>"
            f"<s:Body>{body}</s:Body>"
            "</s:Envelope>"
        )
        url = f"http://{self.host}:{self.onvif_port}{service_path}"
        last = ""
        for auth in ([None] if not self.username else [None, aiohttp.BasicAuth(self.username, self.password or "")]):
            try:
                async with async_timeout.timeout(timeout_seconds):
                    async with self._session.post(
                        url, data=envelope.encode("utf-8"), auth=auth,
                        headers={"Content-Type": self._onvif_content_type},
                    ) as resp:
                        last = await resp.text()
                if not self._looks_like_onvif_auth_fault(last):
                    return last
            except Exception as exc:
                _LOGGER.debug("ONVIF WSSE-Text [%s] fehlgeschlagen: %s", service_path, exc)
        return last

    async def _async_ptz_soap_request(self, action_name: str, body: str) -> str:
        """PTZ-Request über SOAP 1.2/1.1 sowie v20/v10 Namespace probieren."""
        variants = (
            (body, f"http://www.onvif.org/ver20/ptz/wsdl/{action_name}"),
            (self._ptz_body_ver10(body), f"http://www.onvif.org/ver10/ptz/wsdl/{action_name}"),
        )
        last_response = ""
        for candidate_body, soap_action in variants:
            response_text = await self._onvif_soap_for(ONVIF_SERVICE_PTZ, candidate_body)
            if self._ptz_response_ok(response_text, action_name):
                return response_text
            self._remember_ptz_fault_from_response(response_text)
            last_response = response_text

            legacy_response = await self._onvif_soap_legacy_for(
                ONVIF_SERVICE_PTZ, candidate_body, soap_action,
            )
            if self._ptz_response_ok(legacy_response, action_name):
                return legacy_response
            self._remember_ptz_fault_from_response(legacy_response)
            if legacy_response:
                last_response = legacy_response

        # WSSE PasswordText-Fallback nur wenn explizit aktiviert
        if self._looks_like_onvif_auth_fault(last_response) and self._onvif_wsse_enabled:
            password_variants = [self.password or ""]
            if self.password:
                password_variants.append("")
            for pwd_variant in password_variants:
                for service_path in self._candidate_onvif_service_paths(ONVIF_SERVICE_PTZ):
                    for candidate_body, _ in variants:
                        resp = await self._onvif_soap_with_wsse_text(
                            service_path, candidate_body, password_override=pwd_variant
                        )
                        if self._ptz_response_ok(resp, action_name):
                            return resp
                        if resp:
                            self._remember_ptz_fault_from_response(resp)
                            last_response = resp

        return last_response

    @staticmethod
    def _xml_text(text: str, tag: str) -> str:
        """Ersten Textwert eines einfachen XML-Tags extrahieren."""
        m = re.search(
            rf"<(?:[^:>]+:)?{re.escape(tag)}(?:\s[^>]*)?>([^<]*)</(?:[^:>]+:)?{re.escape(tag)}>",
            text, re.DOTALL,
        )
        return m.group(1).strip() if m else ""

    @staticmethod
    def _ptz_body_ver10(body: str) -> str:
        """PTZ SOAP-Body von v20 auf v10 umschreiben."""
        if not body:
            return body
        return body.replace("<tptz:", "<tptz10:").replace("</tptz:", "</tptz10:")

    @staticmethod
    def _ptz_response_ok(response_text: str, action_name: str) -> bool:
        """PTZ-Erfolg robust für v20/v10 Prefix erkennen."""
        if not response_text:
            return False
        return (
            f"{action_name}Response" in response_text
            or f"tptz:{action_name}Response" in response_text
            or f"tptz10:{action_name}Response" in response_text
        )

    @staticmethod
    def _xml_all(text: str, tag: str) -> list[str]:
        """Alle Textwerte eines XML-Tags extrahieren."""
        return [
            m.group(1).strip()
            for m in re.finditer(
                rf"<(?:[^:>]+:)?{re.escape(tag)}(?:\s[^>]*)?>([^<]*)</(?:[^:>]+:)?{re.escape(tag)}>",
                text, re.DOTALL,
            )
        ]

    @staticmethod
    def _xml_fragment(text: str, tag: str) -> str:
        """Den Inhalt des ersten XML-Tags extrahieren."""
        match = re.search(
            rf"<(?:[^:>]+:)?{re.escape(tag)}(?:\s[^>]*)?>(.*?)</(?:[^:>]+:)?{re.escape(tag)}>",
            text, re.DOTALL,
        )
        return match.group(1) if match else text

    @staticmethod
    def _clamp_float(value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(maximum, value))

    @staticmethod
    def _normalize_onvif_path(path: str | None) -> str:
        """XAddr-/Pfadangabe auf einen stabilen ONVIF-Pfad reduzieren."""
        if not path:
            return ""
        parsed = urlparse(path)
        normalized = parsed.path or str(path)
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        return normalized

    @staticmethod
    def _looks_like_wrong_onvif_path(response_text: str) -> bool:
        if not response_text:
            return True
        lowered = response_text.lower()
        if "<html" in lowered and "not found" in lowered:
            return True
        return any(marker in lowered for marker in ("404 not found", "not found", "unknown service", "invalid service"))

    @staticmethod
    def _looks_like_onvif_fault(response_text: str) -> bool:
        if not response_text:
            return False
        lowered = response_text.lower()
        return (
            "<fault" in lowered or ":fault" in lowered
            or "faultcode" in lowered or "faultstring" in lowered
        )

    @staticmethod
    def _looks_like_onvif_auth_fault(response_text: str) -> bool:
        if not response_text:
            return False
        lowered = response_text.lower()
        markers = (
            "the security token could not be authenticated",
            "could not be authenticated or authorized",
            "an error was discovered processing the <wsse:security> header",
            "an error was discovered processing the &lt;wsse:security&gt; header",
            "notauthorized", "not authorized",
            "wsse:failedauthentication", "failedauthentication",
        )
        return any(marker in lowered for marker in markers)

    @staticmethod
    def _service_key_for_onvif_path(path: str) -> str | None:
        normalized = path.lower()
        if "device" in normalized:
            return ONVIF_SERVICE_DEVICE
        if "ptz" in normalized:
            return ONVIF_SERVICE_PTZ
        if "imag" in normalized:
            return ONVIF_SERVICE_IMAGING
        if "event" in normalized:
            return ONVIF_SERVICE_EVENTS
        if "media" in normalized:
            return ONVIF_SERVICE_MEDIA
        return None

    def _remember_onvif_service_path(self, service_key: str, service_path: str) -> None:
        normalized = self._normalize_onvif_path(service_path)
        if normalized:
            self._onvif_service_paths[service_key] = normalized

    def _learn_onvif_service_paths(self, response_text: str) -> None:
        seen_service_keys: set[str] = set()
        for xaddr in re.findall(r"<[^>]*XAddr[^>]*>([^<]+)</[^>]*XAddr>", response_text, re.IGNORECASE):
            normalized = self._normalize_onvif_path(xaddr)
            service_key = self._service_key_for_onvif_path(normalized)
            if service_key and service_key not in seen_service_keys:
                self._remember_onvif_service_path(service_key, normalized)
                seen_service_keys.add(service_key)

    async def _async_bootstrap_onvif_service_paths(self) -> None:
        """Direkt per GetServices die vom Gerät gemeldeten XAddrs laden."""
        body = "<tds:GetServices><tds:IncludeCapability>true</tds:IncludeCapability></tds:GetServices>"
        for service_path in self._candidate_onvif_service_paths(ONVIF_SERVICE_DEVICE):
            response_text = await self._onvif_soap(service_path, body, use_auth=False)
            if response_text and "GetServicesResponse" in response_text:
                self._remember_onvif_service_path(ONVIF_SERVICE_DEVICE, service_path)
                self._learn_onvif_service_paths(response_text)
                _LOGGER.debug("ONVIF GetServices-Bootstrap erfolgreich: %s", self._onvif_service_paths)
                return

    def _candidate_onvif_service_paths(self, service_key: str) -> list[str]:
        candidates: list[str] = []
        preferred = self._normalize_onvif_path(self._onvif_service_paths.get(service_key))
        if preferred:
            candidates.append(preferred)
        for path in ONVIF_SERVICE_PATH_CANDIDATES.get(service_key, ()):
            normalized = self._normalize_onvif_path(path)
            if normalized and normalized not in candidates:
                candidates.append(normalized)
        return candidates

    async def _onvif_soap_for(
        self,
        service_key: str,
        body: str,
        use_auth: bool = True,
        timeout_seconds: int = 5,
    ) -> str:
        """ONVIF-Request mit Service-Fallbacks senden."""
        last_response = ""
        effective_use_auth = bool(use_auth and self._onvif_wsse_enabled)
        for service_path in self._candidate_onvif_service_paths(service_key):
            response_text = await self._onvif_soap(
                service_path, body, use_auth=effective_use_auth, timeout_seconds=timeout_seconds,
            )
            if effective_use_auth and self._looks_like_onvif_auth_fault(response_text):
                # Auth-Fault: NICHT dauerhaft deaktivieren (XM braucht WSSE)
                # Stattdessen: ohne HTTP BasicAuth nochmal versuchen
                _LOGGER.debug(
                    "ONVIF Auth-Fault bei %s – versuche ohne HTTP-BasicAuth",
                    service_path,
                )
                response_text = await self._onvif_soap(
                    service_path, body, use_auth=effective_use_auth, timeout_seconds=timeout_seconds,
                )
            if self._looks_like_onvif_fault(response_text):
                last_response = response_text
                continue
            if not self._looks_like_wrong_onvif_path(response_text):
                self._remember_onvif_service_path(service_key, service_path)
                return response_text
            last_response = response_text
        return last_response

    async def _async_load_onvif_tokens(self) -> None:
        """ONVIF Profile- und VideoSource-Tokens laden."""
        if self._preferred_onvif_profile_token and self._preferred_onvif_video_source_token:
            self._onvif_profile_tokens.setdefault(self._active_stream, self._preferred_onvif_profile_token)
            self._onvif_video_source_token = self._preferred_onvif_video_source_token
            return
        if self._onvif_profile_tokens and self._onvif_video_source_token:
            return

        profiles_resp = await self._onvif_soap_for(ONVIF_SERVICE_MEDIA, "<trt:GetProfiles/>")
        profile_tokens = re.findall(
            r"<[^>]*Profile[^>]*\btoken=\"([^\"]+)\"", profiles_resp,
        )
        profiles = [type("Profile", (), {"token": t})() for t in profile_tokens]
        source_token = self._xml_text(profiles_resp, "SourceToken")
        if source_token:
            self._onvif_video_source_token = str(source_token)

        mapping: dict[str, str] = {}
        for index, profile in enumerate(profiles):
            token = getattr(profile, "token", None)
            if not token:
                continue
            logical_token = "000" if index == 0 else "001" if index == 1 else str(token)
            mapping[logical_token] = str(token)

        if mapping:
            first_token = next(iter(mapping.values()))
            mapping.setdefault("000", first_token)
            mapping.setdefault("001", mapping["000"])
            self._onvif_profile_tokens = mapping
            if self._preferred_onvif_profile_token:
                self._onvif_profile_tokens[self._active_stream] = self._preferred_onvif_profile_token
            _LOGGER.debug("ONVIF Profile-Tokens geladen: %s", self._onvif_profile_tokens)

    async def _async_active_onvif_profile_token(self) -> str:
        if self._preferred_onvif_profile_token:
            return self._preferred_onvif_profile_token
        await self._async_load_onvif_tokens()
        if self._onvif_profile_tokens:
            return self._onvif_profile_tokens.get(
                self._active_stream,
                next(iter(self._onvif_profile_tokens.values())),
            )
        return self._active_stream or "000"

    async def _async_onvif_video_source_token(self) -> str:
        if self._preferred_onvif_video_source_token:
            return self._preferred_onvif_video_source_token
        await self._async_load_onvif_tokens()
        return self._onvif_video_source_token or "000"

    async def _async_candidate_ptz_profile_tokens(self) -> list[str]:
        await self._async_load_onvif_tokens()
        candidates: list[str] = []

        def _add(token: Any) -> None:
            value = str(token or "").strip()
            if value and value not in candidates:
                candidates.append(value)

        _add(self._preferred_onvif_profile_token)
        _add(self._onvif_profile_tokens.get(self._active_stream))
        _add(self._active_stream)
        _add("000")
        _add("001")
        for token in self._onvif_profile_tokens.values():
            _add(token)
        return candidates

    # ── PTZ ─────────────────────────────────────────────────────────────────

    @property
    def ptz_speed(self) -> int:
        return self._ptz_speed

    async def async_set_ptz_speed(self, speed: int) -> None:
        self._ptz_speed = max(1, min(8, speed))

    # ── Digital Zoom ────────────────────────────────────────────────────────

    @property
    def digital_zoom(self) -> float:
        return self._digital_zoom

    @property
    def digital_zoom_center(self) -> tuple[float, float]:
        return (self._digital_zoom_cx, self._digital_zoom_cy)

    async def async_digital_zoom_in(self) -> None:
        self._digital_zoom = min(8.0, round(self._digital_zoom * 1.5, 1))

    async def async_digital_zoom_out(self) -> None:
        self._digital_zoom = max(1.0, round(self._digital_zoom / 1.5, 1))

    async def async_digital_zoom_reset(self) -> None:
        self._digital_zoom = 1.0
        self._digital_zoom_cx = 0.5
        self._digital_zoom_cy = 0.5

    async def async_digital_zoom_set(
        self, zoom: float, cx: float = 0.5, cy: float = 0.5
    ) -> None:
        self._digital_zoom = max(1.0, min(8.0, float(zoom)))
        self._digital_zoom_cx = max(0.0, min(1.0, float(cx)))
        self._digital_zoom_cy = max(0.0, min(1.0, float(cy)))

    @property
    def ptz_presets(self) -> dict[str, str]:
        return dict(self._ptz_presets)

    async def async_ptz_home(self) -> bool:
        spd = self._ptz_speed / 8
        try:
            async with _XMSoapClient(
                host=self.host,
                onvif_port=self.onvif_port,
                username=self.username,
                password=self.password,
            ) as soap:
                return await soap.ptz_goto_home(speed=spd)
        except Exception as exc:
            _LOGGER.warning("ptz_home Fehler: %s", exc)
            return False

    async def async_ptz_set_home(self) -> bool:
        try:
            async with _XMSoapClient(
                host=self.host,
                onvif_port=self.onvif_port,
                username=self.username,
                password=self.password,
            ) as soap:
                return await soap.ptz_set_home()
        except Exception as exc:
            _LOGGER.warning("ptz_set_home Fehler: %s", exc)
            return False

    async def async_ptz_stop(self) -> bool:
        try:
            async with _XMSoapClient(
                host=self.host,
                onvif_port=self.onvif_port,
                username=self.username,
                password=self.password,
            ) as soap:
                return await soap.ptz_stop()
        except Exception as exc:
            _LOGGER.warning("ptz_stop Fehler: %s", exc)
            return False

    async def async_ptz_get_presets(self) -> dict[str, str]:
        profile_token = await self._async_active_onvif_profile_token()
        resp = await self._onvif_soap_for(
            ONVIF_SERVICE_PTZ,
            f"<tptz:GetPresets><tptz:ProfileToken>{profile_token}</tptz:ProfileToken></tptz:GetPresets>",
        )
        presets: dict[str, str] = {}
        for m in re.finditer(
            r'<[^>]*Preset[^>]*token=["\']([^"\']+)["\'][^>]*>(.*?)</[^>]*Preset>',
            resp, re.DOTALL,
        ):
            token = m.group(1)
            nm = re.search(r"<[^>]*Name[^>]*>([^<]+)<", m.group(2))
            presets[token] = nm.group(1) if nm else f"Preset {token}"
        self._ptz_presets = presets
        return presets

    async def async_ptz_goto_preset(self, token: str) -> bool:
        spd = self._ptz_speed / 8
        try:
            async with _XMSoapClient(
                host=self.host,
                onvif_port=self.onvif_port,
                username=self.username,
                password=self.password,
            ) as soap:
                return await soap.ptz_goto_preset(preset_token=token, speed=spd)
        except Exception as exc:
            _LOGGER.warning("ptz_goto_preset Fehler: %s", exc)
            return False

    async def async_ptz_set_preset(self, name: str, token: str | None = None) -> str | None:
        try:
            async with _XMSoapClient(
                host=self.host,
                onvif_port=self.onvif_port,
                username=self.username,
                password=self.password,
            ) as soap:
                new_token = await soap.ptz_set_preset(name=name, preset_token=token)
            if new_token:
                self._ptz_presets[new_token] = name
                return new_token
            return None
        except Exception as exc:
            _LOGGER.warning("ptz_set_preset Fehler: %s", exc)
            return None

    async def async_ptz_delete_preset(self, token: str) -> bool:
        profile_token = await self._async_active_onvif_profile_token()
        resp = await self._onvif_soap_for(
            ONVIF_SERVICE_PTZ,
            f"<tptz:RemovePreset>"
            f"<tptz:ProfileToken>{profile_token}</tptz:ProfileToken>"
            f"<tptz:PresetToken>{token}</tptz:PresetToken>"
            f"</tptz:RemovePreset>",
        )
        ok = "RemovePresetResponse" in resp
        if ok:
            self._ptz_presets.pop(token, None)
        return ok

    # ── Imaging ─────────────────────────────────────────────────────────────

    @property
    def imaging(self) -> dict[str, Any]:
        return dict(self._imaging)

    async def async_fetch_imaging_settings(self) -> bool:
        video_source_token = await self._async_onvif_video_source_token()
        resp = await self._onvif_soap_for(
            ONVIF_SERVICE_IMAGING,
            f"<timg:GetImagingSettings>"
            f"<timg:VideoSourceToken>{video_source_token}</timg:VideoSourceToken>"
            f"</timg:GetImagingSettings>",
        )
        if not resp or "ImagingSettings" not in resp:
            return False
        imaging_block = self._xml_fragment(resp, "ImagingSettings")

        def _f(tag: str, default: float) -> float:
            v = self._xml_text(imaging_block, tag)
            try:
                return float(v)
            except (ValueError, TypeError):
                return default

        def _s(tag: str, default: str) -> str:
            v = self._xml_text(imaging_block, tag)
            return v if v else default

        wdr_m = re.search(r"<[^>]*WideDynamicRange[^>]*>(.*?)</[^>]*WideDynamicRange>", imaging_block, re.DOTALL)
        wdr_enabled = False
        wdr_level = 50.0
        if wdr_m:
            wdr_content = wdr_m.group(1)
            mode_m = re.search(r"<[^>]*Mode[^>]*>([^<]+)<", wdr_content)
            if mode_m:
                wdr_enabled = mode_m.group(1).strip() == "ON"
            level_m = re.search(r"<[^>]*Level[^>]*>([^<]+)<", wdr_content)
            if level_m:
                try:
                    wdr_level = float(level_m.group(1))
                except ValueError:
                    pass

        wb_mode = "AUTO"
        wb_cr = 50.0
        wb_cb = 50.0
        wb_m = re.search(r"<[^>]*WhiteBalance[^>]*>(.*?)</[^>]*WhiteBalance>", imaging_block, re.DOTALL)
        if wb_m:
            wb_content = wb_m.group(1)
            mode_m2 = re.search(r"<[^>]*Mode[^>]*>([^<]+)<", wb_content)
            if mode_m2:
                wb_mode = mode_m2.group(1).strip()
            cr_m = re.search(r"<[^>]*CrGain[^>]*>([^<]+)<", wb_content)
            if cr_m:
                try:
                    wb_cr = float(cr_m.group(1))
                except ValueError:
                    pass
            cb_m = re.search(r"<[^>]*CbGain[^>]*>([^<]+)<", wb_content)
            if cb_m:
                try:
                    wb_cb = float(cb_m.group(1))
                except ValueError:
                    pass

        exp_mode = "AUTO"
        exp_prio = "LowNoise"
        exp_time = 50.0
        exp_gain = 50.0
        exp_m = re.search(r"<[^>]*Exposure[^>]*>(.*?)</[^>]*Exposure>", imaging_block, re.DOTALL)
        if exp_m:
            exp_content = exp_m.group(1)
            mode_m3 = re.search(r"<[^>]*Mode[^>]*>([^<]+)<", exp_content)
            if mode_m3:
                exp_mode = mode_m3.group(1).strip()
            prio_m = re.search(r"<[^>]*Priority[^>]*>([^<]+)<", exp_content)
            if prio_m:
                exp_prio = prio_m.group(1).strip()
            et_m = re.search(r"<[^>]*ExposureTime[^>]*>([^<]+)<", exp_content)
            if et_m:
                try:
                    exp_time = float(et_m.group(1))
                except ValueError:
                    pass
            gain_m = re.search(r"<[^>]*Gain[^>]*>([^<]+)<", exp_content)
            if gain_m:
                try:
                    exp_gain = float(gain_m.group(1))
                except ValueError:
                    pass

        blc_m = re.search(r"<[^>]*BacklightCompensation[^>]*>(.*?)</[^>]*BacklightCompensation>", imaging_block, re.DOTALL)
        blc_mode = "OFF"
        if blc_m:
            blc_mode_m = re.search(r"<[^>]*Mode[^>]*>([^<]+)<", blc_m.group(1))
            if blc_mode_m:
                blc_mode = blc_mode_m.group(1).strip()

        self._imaging = {
            "brightness": self._clamp_float(_f("Brightness", 50), 0, 100),
            "contrast": self._clamp_float(_f("Contrast", 50), 0, 100),
            "saturation": self._clamp_float(_f("ColorSaturation", 50), 0, 100),
            "sharpness": self._clamp_float(_f("Sharpness", 0), 0, 15),
            "ir_cut": _s("IrCutFilter", "AUTO"),
            "wdr_enabled": wdr_enabled,
            "wdr_level": self._clamp_float(wdr_level, 0, 100),
            "wb_mode": wb_mode,
            "wb_cr": self._clamp_float(wb_cr, 0, 100),
            "wb_cb": self._clamp_float(wb_cb, 0, 100),
            "exposure_mode": exp_mode,
            "exposure_priority": exp_prio,
            "exposure_time": self._clamp_float(exp_time, 0, 100),
            "gain": self._clamp_float(exp_gain, 0, 100),
            "backlight": blc_mode,
        }
        return True

    async def async_set_imaging_setting(self, key: str, value: Any) -> bool:
        if not self._imaging:
            await self.async_fetch_imaging_settings()
        self._imaging[key] = value
        video_source_token = await self._async_onvif_video_source_token()

        img = self._imaging
        wdr_mode = "ON" if img.get("wdr_enabled", False) else "OFF"
        ir_cut = img.get("ir_cut", "AUTO")
        exp_mode = img.get("exposure_mode", "AUTO")
        exp_prio = img.get("exposure_priority", "LowNoise")
        wb_mode = img.get("wb_mode", "AUTO")
        blc = img.get("backlight", "OFF")

        body = (
            "<timg:SetImagingSettings>"
            f"<timg:VideoSourceToken>{video_source_token}</timg:VideoSourceToken>"
            "<timg:ImagingSettings>"
            f"<tt:BacklightCompensation><tt:Mode>{blc}</tt:Mode></tt:BacklightCompensation>"
            f"<tt:Brightness>{img.get('brightness', 50)}</tt:Brightness>"
            f"<tt:ColorSaturation>{img.get('saturation', 50)}</tt:ColorSaturation>"
            f"<tt:Contrast>{img.get('contrast', 50)}</tt:Contrast>"
            f"<tt:Exposure><tt:Mode>{exp_mode}</tt:Mode>"
            f"<tt:Priority>{exp_prio}</tt:Priority></tt:Exposure>"
            f"<tt:Focus><tt:AutoFocusMode>AUTO</tt:AutoFocusMode></tt:Focus>"
            f"<tt:IrCutFilter>{ir_cut}</tt:IrCutFilter>"
            f"<tt:Sharpness>{img.get('sharpness', 0)}</tt:Sharpness>"
            f"<tt:WideDynamicRange><tt:Mode>{wdr_mode}</tt:Mode>"
            f"<tt:Level>{img.get('wdr_level', 50)}</tt:Level></tt:WideDynamicRange>"
            f"<tt:WhiteBalance><tt:Mode>{wb_mode}</tt:Mode>"
            f"<tt:CrGain>{img.get('wb_cr', 50)}</tt:CrGain>"
            f"<tt:CbGain>{img.get('wb_cb', 50)}</tt:CbGain></tt:WhiteBalance>"
            "</timg:ImagingSettings>"
            "</timg:SetImagingSettings>"
        )
        resp = await self._onvif_soap_for(ONVIF_SERVICE_IMAGING, body)
        return "SetImagingSettingsResponse" in resp

    # ── Sensoren ─────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_event_bool(value: str | None) -> bool | None:
        if value is None:
            return None
        norm = str(value).strip().lower()
        if norm in {"1", "true", "on", "yes", "active"}:
            return True
        if norm in {"0", "false", "off", "no", "inactive"}:
            return False
        return None

    @staticmethod
    def _parse_event_items(xml_fragment: str) -> dict[str, str]:
        items: dict[str, str] = {}
        pattern = re.compile(
            r'<[^>]*(?:SimpleItem|ElementItem)[^>]*Name=["\']([^"\']+)["\'][^>]*Value=["\']([^"\']*)["\']',
            re.IGNORECASE,
        )
        for match in pattern.finditer(xml_fragment):
            items[match.group(1).strip().lower()] = match.group(2).strip()
        return items

    def _apply_onvif_event(self, topic: str, items: dict[str, str]) -> bool:
        changed = False
        topic_l = (topic or "").lower()

        for rule_name, rule in self._onvif_event_rules.items():
            item_keys = tuple(str(key).strip().lower() for key in rule.get("item_keys", ()))
            values = [self._parse_event_bool(items.get(key)) for key in item_keys if key in items]
            topic_keywords = tuple(str(k).strip().lower() for k in rule.get("topic_keywords", ()))
            topic_match = any(keyword and keyword in topic_l for keyword in topic_keywords)
            truthy = any(value is True for value in values)
            if rule_name == "motion":
                if truthy or (topic_match and not values and rule.get("topic_only_true", False)):
                    self._last_motion_time = time.time()
                    changed = True
                continue

            state_attr = rule.get("state_attr")
            if not state_attr:
                continue
            current_value = bool(getattr(self, state_attr, False))
            new_value: bool | None = None
            if values:
                new_value = truthy
            elif topic_match and rule.get("topic_only_true", False):
                new_value = True
            if new_value is not None and new_value != current_value:
                setattr(self, state_attr, new_value)
                changed = True

        return changed

    async def async_onvif_pull_messages_once(self) -> bool:
        if not self._event_pullpoint_path:
            return False

        resp = await self._onvif_soap(
            self._event_pullpoint_path,
            "<tev:PullMessages>"
            "<tev:Timeout>PT15S</tev:Timeout>"
            "<tev:MessageLimit>16</tev:MessageLimit>"
            "</tev:PullMessages>",
            use_auth=False,
            timeout_seconds=20,
        )
        if not resp:
            return False

        changed = False
        for m in re.finditer(
            r"<[^>]*NotificationMessage[^>]*>(.*?)</[^>]*NotificationMessage>",
            resp, re.DOTALL,
        ):
            block = m.group(1)
            topic = self._xml_text(block, "Topic")
            items = self._parse_event_items(block)
            if self._apply_onvif_event(topic, items):
                changed = True

        if changed:
            self.async_update_listeners()

        return "PullMessagesResponse" in resp

    async def async_onvif_create_pullpoint(self) -> bool:
        resp = await self._onvif_soap_for(
            ONVIF_SERVICE_EVENTS,
            "<tev:CreatePullPointSubscription>"
            "<tev:InitialTerminationTime>PT30M</tev:InitialTerminationTime>"
            "</tev:CreatePullPointSubscription>",
        )
        if not resp:
            return False

        address = self._xml_text(resp, "Address")
        if not address:
            self._event_pullpoint_path = "/onvif/Events"
            return True

        if address.startswith("http://") or address.startswith("https://"):
            parsed = urlparse(address)
            self._event_pullpoint_path = parsed.path or "/onvif/Events"
        elif address.startswith("/"):
            self._event_pullpoint_path = address
        else:
            self._event_pullpoint_path = f"/{address}"
        self._remember_onvif_service_path(ONVIF_SERVICE_EVENTS, self._event_pullpoint_path)
        return True

    async def _async_onvif_event_loop(self) -> None:
        """ONVIF Pull-Point Event-Loop mit Reconnect/Backoff."""
        backoff_seconds = 1
        try:
            while True:
                if not self._session:
                    await asyncio.sleep(1)
                    continue

                if not self._event_pullpoint_path:
                    ok = await self.async_onvif_create_pullpoint()
                    if not ok:
                        await asyncio.sleep(backoff_seconds)
                        backoff_seconds = min(backoff_seconds * 2, 30)
                        continue

                ok = await self.async_onvif_pull_messages_once()
                if ok:
                    backoff_seconds = 1
                    continue

                self._event_pullpoint_path = ""
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 30)
        except Exception as exc:
            _LOGGER.debug("ONVIF Event-Loop beendet: %s", exc)

    @property
    def tamper_detected(self) -> bool:
        return self._tamper

    @property
    def signal_loss(self) -> bool:
        return self._signal_loss

    # ── Stream-Profil ────────────────────────────────────────────────────────

    @property
    def active_stream(self) -> str:
        return self._active_stream

    async def async_set_stream_profile(self, profile_token: str) -> None:
        self._active_stream = profile_token
        if self.protocol == PROTOCOL_ONVIF:
            onvif_url = await self.async_onvif_stream_url()
            if onvif_url:
                self._resolved_rtsp_url = onvif_url
            else:
                stream_type = "0" if profile_token == "000" else "1"
                authority = self._credential_authorities()[0]
                self._resolved_rtsp_url = (
                    f"rtsp://{authority}{self.host}:{self.rtsp_port}/streamtype={stream_type}"
                )
        else:
            stream_type = "0" if profile_token == "000" else "1"
            authority = self._credential_authorities()[0]
            self._resolved_rtsp_url = (
                f"rtsp://{authority}{self.host}:{self.rtsp_port}/streamtype={stream_type}"
            )
        self.async_update_listeners()

    # ── Audio ────────────────────────────────────────────────────────────────

    @property
    def microphone_enabled(self) -> bool:
        return self._microphone_enabled

    async def async_fetch_audio_settings(self) -> bool:
        resp = await self._onvif_soap_for(ONVIF_SERVICE_MEDIA, "<trt:GetAudioOutputConfigurations/>")
        if not resp:
            return False
        if (
            "AudioOutputConfiguration" not in resp
            and "GetAudioOutputConfigurationsResponse" not in resp
            and "Configurations" not in resp
        ):
            return False

        token_match = re.search(
            r'<[^>]*(?:AudioOutputConfiguration|Configurations)[^>]*token=["\']([^"\']+)["\']',
            resp, re.IGNORECASE,
        )
        if token_match:
            self._audio_output_token = token_match.group(1)

        level_raw = self._xml_text(resp, "OutputLevel")
        try:
            level = int(float(level_raw)) if level_raw else self._audio_output_level
        except (TypeError, ValueError):
            level = self._audio_output_level
        self._audio_output_level = max(0, min(100, level))

        send_primacy = self._xml_text(resp, "SendPrimacy")
        if send_primacy:
            self._audio_output_send_primacy = send_primacy

        self._microphone_enabled = self._audio_output_level > 0
        return True

    async def async_set_microphone_enabled(self, enabled: bool) -> bool:
        if not self._audio_output_token:
            await self.async_fetch_audio_settings()
        if not self._audio_output_token:
            return False

        current_on_level = self._audio_output_level if self._audio_output_level > 0 else 100
        target_level = current_on_level if enabled else 0
        target_level = max(0, min(100, target_level))

        send_primacy = self._audio_output_send_primacy or "www.onvif.org/ver10/schema/Always"
        body = (
            "<trt:SetAudioOutputConfiguration>"
            f'<trt:Configuration token="{self._audio_output_token}">'
            "<tt:Name>AudioOut</tt:Name>"
            f"<tt:OutputToken>{self._audio_output_token}</tt:OutputToken>"
            f"<tt:SendPrimacy>{send_primacy}</tt:SendPrimacy>"
            f"<tt:OutputLevel>{target_level}</tt:OutputLevel>"
            "</trt:Configuration>"
            "<trt:ForcePersistence>true</trt:ForcePersistence>"
            "</trt:SetAudioOutputConfiguration>"
        )
        resp = await self._onvif_soap_for(ONVIF_SERVICE_MEDIA, body)
        ok = "SetAudioOutputConfigurationResponse" in resp
        if ok:
            self._audio_output_level = target_level
            self._microphone_enabled = enabled
            self.async_update_listeners()
        return ok

    # ── System ───────────────────────────────────────────────────────────────

    @property
    def firmware_version(self) -> str:
        return self._fw_version

    @property
    def serial_number(self) -> str:
        return self._serial_number

    @property
    def mac_address(self) -> str:
        return self._mac_address

    @property
    def camera_time(self) -> str:
        return self._camera_time

    async def async_fetch_device_info(self) -> None:
        resp = await self._onvif_soap_for(
            ONVIF_SERVICE_DEVICE, "<tds:GetDeviceInformation/>", use_auth=False,
        )
        self._fw_version = self._xml_text(resp, "FirmwareVersion")
        self._serial_number = self._xml_text(resp, "SerialNumber")
        net_resp = await self._onvif_soap_for(ONVIF_SERVICE_DEVICE, "<tds:GetNetworkInterfaces/>")
        self._mac_address = self._xml_text(net_resp, "HwAddress")

    async def async_fetch_camera_time(self) -> None:
        resp = await self._onvif_soap_for(
            ONVIF_SERVICE_DEVICE, "<tds:GetSystemDateAndTime/>", use_auth=False,
        )
        hour = self._xml_text(resp, "Hour")
        minute = self._xml_text(resp, "Minute")
        second = self._xml_text(resp, "Second")
        year = self._xml_text(resp, "Year")
        month = self._xml_text(resp, "Month")
        day = self._xml_text(resp, "Day")
        if year:
            self._camera_time = (
                f"{year}-{month.zfill(2)}-{day.zfill(2)} "
                f"{hour.zfill(2)}:{minute.zfill(2)}:{second.zfill(2)}"
            )

    async def async_reboot(self) -> bool:
        resp = await self._onvif_soap_for(ONVIF_SERVICE_DEVICE, "<tds:SystemReboot/>")
        return "SystemRebootResponse" in resp

    async def async_ntp_sync(self) -> bool:
        resp = await self._onvif_soap_for(ONVIF_SERVICE_DEVICE, "<tds:GetNTP/>")
        return bool(resp)

    async def async_shutdown(self) -> None:
        """Verbindungen schließen."""
        if self._event_task:
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
            self._event_task = None
        self._event_pullpoint_path = ""
        if self._session:
            await self._session.close()
            self._session = None
        if self._xm:
            await self.hass.async_add_executor_job(self._xm.disconnect)
            self._xm = None
