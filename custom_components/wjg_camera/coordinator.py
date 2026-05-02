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
from urllib.parse import quote

import aiohttp
import async_timeout

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator


# Konstanten lokal definieren, um zirkulären Import zu vermeiden
CONF_PROTOCOL = "protocol"
CONF_HTTP_RETRIES = "http_retries"
CONF_RTSP_PATH = "rtsp_path"
CONF_RTSP_PORT = "rtsp_port"
CONF_SNAPSHOT_PATH = "snapshot_path"
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

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=10)

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
        import hashlib
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
        self.host: str = entry.data[CONF_HOST]
        self.username: str = entry.data.get(CONF_USERNAME, "admin")
        self.password: str = entry.data.get(CONF_PASSWORD, "")
        self.protocol: str = entry.data.get(CONF_PROTOCOL, PROTOCOL_RTSP)
        self.rtsp_port: int = entry.data.get(CONF_RTSP_PORT, 554)
        self.http_port: int = entry.data.get(CONF_PORT, DEFAULT_HTTP_PORT)
        self.xm_port: int = DEFAULT_XM_PORT
        self.rtsp_path: str = entry.data.get(CONF_RTSP_PATH, DEFAULT_RTSP_PATH)
        self.snapshot_path: str = entry.data.get(
            CONF_SNAPSHOT_PATH, DEFAULT_SNAPSHOT_PATH
        )
        options = getattr(entry, "options", {}) or {}
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
        # ONVIF direkt
        self.onvif_port: int = entry.data.get("onvif_port", 8899)
        # PTZ
        self._ptz_speed: int = 5
        self._ptz_presets: dict[str, str] = {}  # token -> name
        # Imaging
        self._imaging: dict[str, Any] = {}
        # Sensoren
        self._tamper: bool = False
        self._signal_loss: bool = False
        # Stream
        self._active_stream: str = "000"  # "000"=main, "001"=sub
        # System
        self._fw_version: str = ""
        self._serial_number: str = ""
        self._mac_address: str = ""
        self._camera_time: str = ""
        self._update_count: int = 0
        if self.protocol == "onvif":
            try:
                from onvif import ONVIFCamera

                self._onvif = ONVIFCamera(
                    self.host,
                    entry.data.get("onvif_port", 8899),
                    self.username,
                    self.password
                )
            except Exception as e:
                _LOGGER.error("ONVIF-Initialisierung fehlgeschlagen: %s", e)

    async def async_onvif_ptz(self, cmd: str, speed: float = 0.5) -> bool:
        """ONVIF PTZ-Befehl senden (up/down/left/right/zoom_in/zoom_out/stop)."""
        if not self._onvif:
            return False
        try:
            media_service = self._onvif.create_media_service()
            ptz_service = self._onvif.create_ptz_service()
            profiles = await _await_if_needed(media_service.GetProfiles())
            profile = profiles[0]
            req = ptz_service.create_type('ContinuousMove')
            req.ProfileToken = profile.token
            req.Velocity = {}
            if cmd == "up":
                req.Velocity = {"PanTilt": {"x": 0, "y": speed}}
            elif cmd == "down":
                req.Velocity = {"PanTilt": {"x": 0, "y": -speed}}
            elif cmd == "left":
                req.Velocity = {"PanTilt": {"x": -speed, "y": 0}}
            elif cmd == "right":
                req.Velocity = {"PanTilt": {"x": speed, "y": 0}}
            elif cmd == "zoom_in":
                req.Velocity = {"Zoom": {"x": speed}}
            elif cmd == "zoom_out":
                req.Velocity = {"Zoom": {"x": -speed}}
            elif cmd == "stop":
                await _await_if_needed(ptz_service.Stop({'ProfileToken': profile.token}))
                return True
            else:
                return False
            await _await_if_needed(ptz_service.ContinuousMove(req))
            return True
        except Exception as e:
            _LOGGER.error("ONVIF PTZ-Befehl fehlgeschlagen: %s", e)
            return False

    async def async_onvif_stream_url(self) -> str | None:
        """ONVIF Stream-URL abrufen."""
        if not self._onvif:
            return None
        try:
            media_service = self._onvif.create_media_service()
            profiles = await _await_if_needed(media_service.GetProfiles())
            profile = profiles[0]
            req = media_service.create_type('GetStreamUri')
            req.ProfileToken = profile.token
            req.StreamSetup = {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}}
            uri = await _await_if_needed(media_service.GetStreamUri(req))
            return uri.Uri
        except Exception as e:
            _LOGGER.error("ONVIF Stream-URL konnte nicht abgerufen werden: %s", e)
            return None

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

        if self.protocol == PROTOCOL_ONVIF:
            onvif_url = await self.async_onvif_stream_url()
            if onvif_url:
                self._resolved_rtsp_url = onvif_url
            return

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

        self._resolved_rtsp_url = self._build_rtsp_url(self._render_rtsp_path())

    @property
    def rtsp_url(self) -> str:
        if self._resolved_rtsp_url:
            return self._resolved_rtsp_url
        return self._build_rtsp_url(self._render_rtsp_path())

    @property
    def snapshot_url(self) -> str:
        return f"http://{self.host}:{self.http_port}{self.snapshot_path}"

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

        # Fallback: TCP-Erreichbarkeit prüfen (RTSP-Port oder HTTP-Port)
        if not data["available"]:
            for port in {self.rtsp_port, self.http_port, self.xm_port}:
                if port and await self._tcp_port_reachable(port):
                    data["available"] = True
                    _LOGGER.debug(
                        "Kamera via TCP-Port %s erreichbar (Snapshot nicht verfügbar)",
                        port,
                    )
                    break

        # XM Keepalive + Status
        if self._xm:
            try:
                ok = await self.hass.async_add_executor_job(self._xm.keepalive)
                if ok:
                    data["available"] = True
            except Exception as e:
                _LOGGER.debug("XM Keepalive fehlgeschlagen: %s — reconnect", e)
                await self.hass.async_add_executor_job(self._setup_xm)

        # Alle 60 Sekunden: Kamerazeit + Imaging refresh
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
        """HTTP GET mit kleinem Retry für transiente Fehler.

        Gibt bei Erfolg gelesene Daten (bytes oder dict) zurück, sonst None.
        """
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
        data = await self._async_http_get_data(
            self.snapshot_url,
            timeout_seconds=5,
        )
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        return None

    async def async_ptz_command(self, cmd: str, speed: int = 5) -> bool:
        """PTZ-Steuerbefehl senden (Start/Stop Up/Down/Left/Right/Zoom)."""
        ptz_map = {
            "up": 0x10, "down": 0x11, "left": 0x12, "right": 0x13,
            "zoom_in": 0x01, "zoom_out": 0x02, "focus_in": 0x03,
            "focus_out": 0x04, "stop": 0xFF
        }
        if cmd not in ptz_map:
            _LOGGER.warning("Unbekannter PTZ-Befehl: %s", cmd)
            return False

        if self._xm:
            code = ptz_map[cmd]
            try:
                return await self.hass.async_add_executor_job(
                    self._xm.ptz_command, code, speed, 0
                )
            except Exception as e:
                _LOGGER.error("PTZ-Befehl fehlgeschlagen: %s", e)

        # HTTP-Fallback
        if self._session:
            url = (f"http://{self.host}:{self.http_port}/cgi-bin/ptz"
                   f"?channel=1&cmd={cmd}&speed={speed}")
            data = await self._async_http_get_data(url, timeout_seconds=3)
            return isinstance(data, (bytes, bytearray))
        return False

    # ── ONVIF Direct-SOAP helper ────────────────────────────────────────────

    def _wsse_header(self) -> str:
        nonce = os.urandom(16)
        created = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        pwd = (self.password or "").encode("utf-8")
        digest = base64.b64encode(
            hashlib.sha1(nonce + created.encode() + pwd).digest()
        ).decode()
        nonce_b64 = base64.b64encode(nonce).decode()
        return (
            '<wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01'
            '/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
            "<wsse:UsernameToken>"
            f"<wsse:Username>{self.username}</wsse:Username>"
            f'<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01'
            f'/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
            f"{digest}</wsse:Password>"
            f'<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01'
            f'/oasis-200401-wss-wssecurity-secext-1.0.xsd#Base64Binary">'
            f"{nonce_b64}</wsse:Nonce>"
            f'<wsu:Created xmlns:wsu="http://docs.oasis-open.org/wss/2004/01'
            f'/oasis-200401-wss-wssecurity-utility-1.0.xsd">{created}</wsu:Created>'
            "</wsse:UsernameToken></wsse:Security>"
        )

    async def _onvif_soap(
        self, service_path: str, body: str, use_auth: bool = True
    ) -> str:
        """ONVIF SOAP-Anfrage direkt via HTTP."""
        if not self._session:
            return ""
        auth_header = self._wsse_header() if use_auth else ""
        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"'
            ' xmlns:tds="http://www.onvif.org/ver10/device/wsdl"'
            ' xmlns:trt="http://www.onvif.org/ver10/media/wsdl"'
            ' xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"'
            ' xmlns:tt="http://www.onvif.org/ver10/schema"'
            ' xmlns:timg="http://www.onvif.org/ver20/imaging/wsdl"'
            ' xmlns:tev="http://www.onvif.org/ver10/events/wsdl">'
            f"<s:Header>{auth_header}</s:Header>"
            f"<s:Body>{body}</s:Body>"
            "</s:Envelope>"
        )
        url = f"http://{self.host}:{self.onvif_port}{service_path}"
        try:
            async with async_timeout.timeout(5):
                async with self._session.post(
                    url,
                    data=envelope.encode("utf-8"),
                    headers={"Content-Type": "application/soap+xml; charset=utf-8"},
                ) as resp:
                    return await resp.text()
        except Exception as exc:
            _LOGGER.debug("ONVIF SOAP [%s] fehlgeschlagen: %s", service_path, exc)
            return ""

    @staticmethod
    def _xml_text(text: str, tag: str) -> str:
        """Ersten Textwert eines einfachen XML-Tags extrahieren."""
        m = re.search(rf"<[^>]*{re.escape(tag)}[^>/]*>([^<]*)<", text)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _xml_all(text: str, tag: str) -> list[str]:
        """Alle Textwerte eines XML-Tags extrahieren."""
        return [
            m.group(1).strip()
            for m in re.finditer(rf"<[^>]*{re.escape(tag)}[^>/]*>([^<]*)<", text)
        ]

    # ── PTZ ─────────────────────────────────────────────────────────────────

    @property
    def ptz_speed(self) -> int:
        return self._ptz_speed

    async def async_set_ptz_speed(self, speed: int) -> None:
        self._ptz_speed = max(1, min(8, speed))

    @property
    def ptz_presets(self) -> dict[str, str]:
        return dict(self._ptz_presets)

    async def async_ptz_home(self) -> bool:
        """PTZ-Heimposition anfahren (ONVIF GoToHomePosition)."""
        spd = f"{self._ptz_speed / 8:.2f}"
        resp = await self._onvif_soap(
            "/onvif/PTZ",
            f"<tptz:GotoHomePosition>"
            f"<tptz:ProfileToken>000</tptz:ProfileToken>"
            f'<tptz:Speed><tt:PanTilt x="{spd}" y="{spd}"/></tptz:Speed>'
            f"</tptz:GotoHomePosition>",
        )
        return "GotoHomePositionResponse" in resp

    async def async_ptz_set_home(self) -> bool:
        """Aktuelle Position als Heimposition speichern."""
        resp = await self._onvif_soap(
            "/onvif/PTZ",
            "<tptz:SetHomePosition>"
            "<tptz:ProfileToken>000</tptz:ProfileToken>"
            "</tptz:SetHomePosition>",
        )
        return "SetHomePositionResponse" in resp

    async def async_ptz_stop(self) -> bool:
        """Laufende PTZ-Bewegung sofort stoppen."""
        resp = await self._onvif_soap(
            "/onvif/PTZ",
            "<tptz:Stop>"
            "<tptz:ProfileToken>000</tptz:ProfileToken>"
            "<tptz:PanTilt>true</tptz:PanTilt>"
            "<tptz:Zoom>true</tptz:Zoom>"
            "</tptz:Stop>",
        )
        return "StopResponse" in resp

    async def async_ptz_get_presets(self) -> dict[str, str]:
        """Alle PTZ-Presets laden (token -> name)."""
        resp = await self._onvif_soap(
            "/onvif/PTZ",
            "<tptz:GetPresets><tptz:ProfileToken>000</tptz:ProfileToken></tptz:GetPresets>",
        )
        presets: dict[str, str] = {}
        for m in re.finditer(
            r'<[^>]*Preset[^>]*token=["\']([^"\']+)["\'][^>]*>(.*?)</[^>]*Preset>',
            resp,
            re.DOTALL,
        ):
            token = m.group(1)
            nm = re.search(r"<[^>]*Name[^>]*>([^<]+)<", m.group(2))
            presets[token] = nm.group(1) if nm else f"Preset {token}"
        self._ptz_presets = presets
        return presets

    async def async_ptz_goto_preset(self, token: str) -> bool:
        """Preset anfahren."""
        spd = f"{self._ptz_speed / 8:.2f}"
        resp = await self._onvif_soap(
            "/onvif/PTZ",
            f"<tptz:GotoPreset>"
            f"<tptz:ProfileToken>000</tptz:ProfileToken>"
            f"<tptz:PresetToken>{token}</tptz:PresetToken>"
            f'<tptz:Speed><tt:PanTilt x="{spd}" y="{spd}"/>'
            f'<tt:Zoom x="{spd}"/></tptz:Speed>'
            f"</tptz:GotoPreset>",
        )
        return "GotoPresetResponse" in resp

    async def async_ptz_set_preset(
        self, name: str, token: str | None = None
    ) -> str | None:
        """Aktuelle Position als Preset speichern. Gibt neuen Token zurück."""
        tok_xml = f"<tptz:PresetToken>{token}</tptz:PresetToken>" if token else ""
        resp = await self._onvif_soap(
            "/onvif/PTZ",
            f"<tptz:SetPreset>"
            f"<tptz:ProfileToken>000</tptz:ProfileToken>"
            f"<tptz:PresetName>{name}</tptz:PresetName>"
            f"{tok_xml}"
            f"</tptz:SetPreset>",
        )
        new_token = self._xml_text(resp, "PresetToken")
        if new_token:
            self._ptz_presets[new_token] = name
            return new_token
        return None

    async def async_ptz_delete_preset(self, token: str) -> bool:
        """Preset löschen."""
        resp = await self._onvif_soap(
            "/onvif/PTZ",
            f"<tptz:RemovePreset>"
            f"<tptz:ProfileToken>000</tptz:ProfileToken>"
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
        """Bildeinstellungen von Kamera laden und in self._imaging speichern."""
        resp = await self._onvif_soap(
            "/onvif/Imaging",
            "<timg:GetImagingSettings>"
            "<timg:VideoSourceToken>000</timg:VideoSourceToken>"
            "</timg:GetImagingSettings>",
        )
        if not resp or "ImagingSettings" not in resp:
            return False

        def _f(tag: str, default: float) -> float:
            v = self._xml_text(resp, tag)
            try:
                return float(v)
            except (ValueError, TypeError):
                return default

        def _s(tag: str, default: str) -> str:
            v = self._xml_text(resp, tag)
            return v if v else default

        # WDR nested
        wdr_m = re.search(
            r"<[^>]*WideDynamicRange[^>]*>(.*?)</[^>]*WideDynamicRange>",
            resp,
            re.DOTALL,
        )
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

        # WhiteBalance nested
        wb_mode = "AUTO"
        wb_cr = 50.0
        wb_cb = 50.0
        wb_m = re.search(
            r"<[^>]*WhiteBalance[^>]*>(.*?)</[^>]*WhiteBalance>",
            resp,
            re.DOTALL,
        )
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

        # Exposure nested
        exp_mode = "AUTO"
        exp_prio = "LowNoise"
        exp_time = 50.0
        exp_gain = 50.0
        exp_m = re.search(
            r"<[^>]*Exposure[^>]*>(.*?)</[^>]*Exposure>", resp, re.DOTALL
        )
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

        # BLC
        blc_m = re.search(
            r"<[^>]*BacklightCompensation[^>]*>(.*?)</[^>]*BacklightCompensation>",
            resp,
            re.DOTALL,
        )
        blc_mode = "OFF"
        if blc_m:
            blc_mode_m = re.search(r"<[^>]*Mode[^>]*>([^<]+)<", blc_m.group(1))
            if blc_mode_m:
                blc_mode = blc_mode_m.group(1).strip()

        self._imaging = {
            "brightness": _f("Brightness", 50),
            "contrast": _f("Contrast", 50),
            "saturation": _f("ColorSaturation", 50),
            "sharpness": _f("Sharpness", 0),
            "ir_cut": _s("IrCutFilter", "AUTO"),
            "wdr_enabled": wdr_enabled,
            "wdr_level": wdr_level,
            "wb_mode": wb_mode,
            "wb_cr": wb_cr,
            "wb_cb": wb_cb,
            "exposure_mode": exp_mode,
            "exposure_priority": exp_prio,
            "exposure_time": exp_time,
            "gain": exp_gain,
            "backlight": blc_mode,
        }
        return True

    async def async_set_imaging_setting(self, key: str, value: Any) -> bool:
        """Einzelne Bildeinstellung setzen und an Kamera senden."""
        if not self._imaging:
            await self.async_fetch_imaging_settings()
        self._imaging[key] = value

        img = self._imaging
        wdr_mode = "ON" if img.get("wdr_enabled", False) else "OFF"
        ir_cut = img.get("ir_cut", "AUTO")
        exp_mode = img.get("exposure_mode", "AUTO")
        exp_prio = img.get("exposure_priority", "LowNoise")
        wb_mode = img.get("wb_mode", "AUTO")
        blc = img.get("backlight", "OFF")

        body = (
            "<timg:SetImagingSettings>"
            "<timg:VideoSourceToken>000</timg:VideoSourceToken>"
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
        resp = await self._onvif_soap("/onvif/Imaging", body)
        return "SetImagingSettingsResponse" in resp

    # ── Sensoren (Tamper, Signal Loss) ──────────────────────────────────────

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
        """Aktiven RTSP-Stream-Profil wechseln (000=main, 001=sub)."""
        self._active_stream = profile_token
        stream_type = "0" if profile_token == "000" else "1"
        authority = self._credential_authorities()[0]
        self._resolved_rtsp_url = (
            f"rtsp://{authority}{self.host}:{self.rtsp_port}/streamtype={stream_type}"
        )
        self.async_update_listeners()

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
        """Geräte-Infos einmalig laden (Firmware, Serial, MAC)."""
        resp = await self._onvif_soap(
            "/onvif/device_service",
            "<tds:GetDeviceInformation/>",
            use_auth=False,
        )
        self._fw_version = self._xml_text(resp, "FirmwareVersion")
        self._serial_number = self._xml_text(resp, "SerialNumber")
        net_resp = await self._onvif_soap(
            "/onvif/device_service",
            "<tds:GetNetworkInterfaces/>",
        )
        self._mac_address = self._xml_text(net_resp, "HwAddress")

    async def async_fetch_camera_time(self) -> None:
        """Systemzeit der Kamera abrufen."""
        resp = await self._onvif_soap(
            "/onvif/device_service",
            "<tds:GetSystemDateAndTime/>",
            use_auth=False,
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
        """Kamera neu starten (ONVIF SystemReboot)."""
        resp = await self._onvif_soap(
            "/onvif/device_service",
            "<tds:SystemReboot/>",
        )
        return "SystemRebootResponse" in resp

    async def async_ntp_sync(self) -> bool:
        """NTP-Zeitserver synchronisieren."""
        resp = await self._onvif_soap(
            "/onvif/device_service",
            "<tds:GetNTP/>",
        )
        return bool(resp)

    async def async_shutdown(self) -> None:
        """Verbindungen schließen."""
        if self._session:
            await self._session.close()
            self._session = None
        if self._xm:
            await self.hass.async_add_executor_job(self._xm.disconnect)
            self._xm = None
