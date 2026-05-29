"""
xm_soap.py – Direkter ONVIF-SOAP-Client für XM/Xiongmai-Kameras (WJG XM-3820).

Ersetzt python-onvif-zeep vollständig. Keine WSSE-Library nötig.
Auth: PasswordDigest (SHA1, leer), Profile-Token: "000", PTZ-Endpoint: Port 8899.

Hardcodierte Defaults für WJG XM-3820 – kein Config-Flow-Fehler mehr möglich.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

# ── Hardcodierte Kamera-Konstanten ───────────────────────────────────────────

CAMERA_HOST = "192.168.178.49"
CAMERA_USERNAME = "admin"
CAMERA_PASSWORD = ""          # XM-Standard: leer
ONVIF_PORT = 8899
RTSP_PORT = 554
SNAPSHOT_PORT = 80
PROFILE_TOKEN = "000"         # aus GetProfiles verifiziert
PTZ_TOKEN = "000"

ONVIF_BASE = f"http://{CAMERA_HOST}:{ONVIF_PORT}"
ENDPOINT_DEVICE = f"{ONVIF_BASE}/onvif/device_service"
ENDPOINT_MEDIA = f"{ONVIF_BASE}/onvif/Media"
ENDPOINT_PTZ = f"{ONVIF_BASE}/onvif/PTZ"
ENDPOINT_EVENTS = f"{ONVIF_BASE}/onvif/Events"
ENDPOINT_IMAGING = f"{ONVIF_BASE}/onvif/imaging"

RTSP_MAIN = (
    f"rtsp://{CAMERA_USERNAME}:@{CAMERA_HOST}:{RTSP_PORT}"
    "/user=admin&password=&channel=1&stream=0.sdp?real_stream"
)
RTSP_SUB = (
    f"rtsp://{CAMERA_USERNAME}:@{CAMERA_HOST}:{RTSP_PORT}"
    "/user=admin&password=&channel=1&stream=1.sdp?real_stream"
)
SNAPSHOT_URL = f"http://{CAMERA_HOST}/webcapture.jpg?command=snap&channel=1"

PTZ_SPEED = 0.4   # Geschwindigkeit 0.1–1.0
PTZ_STOP_DELAY = 0.8  # Sekunden bis Auto-Stop

# ── WSSE Auth ─────────────────────────────────────────────────────────────────

def _wsse_header(username: str = CAMERA_USERNAME, password: str = CAMERA_PASSWORD) -> str:
    """Erzeugt WSSE PasswordDigest Header – einzige Auth-Methode die XM akzeptiert."""
    nonce_bytes = os.urandom(16)
    nonce_b64 = base64.b64encode(nonce_bytes).decode()
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    # SHA1(nonce_raw + created_utf8 + password_utf8)
    digest_raw = (
        nonce_bytes
        + created.encode("utf-8")
        + password.encode("utf-8")
    )
    digest_b64 = base64.b64encode(hashlib.sha1(digest_raw).digest()).decode()

    return f"""<wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
               xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
  <wsse:UsernameToken>
    <wsse:Username>{username}</wsse:Username>
    <wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest_b64}</wsse:Password>
    <wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce_b64}</wsse:Nonce>
    <wsu:Created>{created}</wsu:Created>
  </wsse:UsernameToken>
</wsse:Security>"""


def _envelope(body: str, auth: bool = True) -> str:
    """Wraps body in SOAP Envelope. auth=False für GetCapabilities (kein Auth nötig)."""
    header = f"<s:Header>{_wsse_header()}</s:Header>" if auth else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
            xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"
            xmlns:tds="http://www.onvif.org/ver10/device/wsdl"
            xmlns:tt="http://www.onvif.org/ver10/schema"
            xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
            xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
  {header}
  <s:Body>{body}</s:Body>
</s:Envelope>"""


# ── Async SOAP Client ─────────────────────────────────────────────────────────

class XMSoapClient:
    """
    Asynchroner SOAP-Client für WJG XM-3820.
    Ersetzt ONVIFCamera aus python-onvif-zeep komplett.
    Alle Methoden sind async und HA-kompatibel (kein Executor nötig).
    """

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self) -> "XMSoapClient":
        if self._owns_session:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8),
                headers={"Content-Type": "application/soap+xml; charset=utf-8"},
            )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._owns_session and self._session:
            await self._session.close()

    async def _post(self, endpoint: str, body: str, auth: bool = True) -> ET.Element | None:
        """Sendet SOAP-Request und gibt geparsten XML-Root zurück."""
        soap = _envelope(body, auth=auth)
        try:
            assert self._session is not None
            async with self._session.post(endpoint, data=soap.encode("utf-8")) as resp:
                text = await resp.text()
                if resp.status != 200:
                    _LOGGER.warning("SOAP %s HTTP %s: %s", endpoint, resp.status, text[:200])
                    return None
                return ET.fromstring(text)
        except aiohttp.ClientError as e:
            _LOGGER.error("SOAP Verbindungsfehler %s: %s", endpoint, e)
            return None
        except ET.ParseError as e:
            _LOGGER.error("SOAP XML-Parsefehler %s: %s", endpoint, e)
            return None

    # ── Device ────────────────────────────────────────────────────────────────

    async def get_capabilities(self) -> ET.Element | None:
        """Kein Auth nötig – Discovery-Call."""
        body = """<tds:GetCapabilities>
  <tds:Category>All</tds:Category>
</tds:GetCapabilities>"""
        return await self._post(ENDPOINT_DEVICE, body, auth=False)

    async def ping(self) -> bool:
        """Schneller Erreichbarkeits-Check ohne Auth."""
        result = await self.get_capabilities()
        return result is not None

    # ── Media ─────────────────────────────────────────────────────────────────

    async def get_profiles(self) -> list[dict[str, str]]:
        """Gibt Liste der Profile zurück: [{token, name}, ...]"""
        body = "<trt:GetProfiles/>"
        root = await self._post(ENDPOINT_MEDIA, body)
        if root is None:
            return []
        ns = {"trt": "http://www.onvif.org/ver10/media/wsdl",
              "tt": "http://www.onvif.org/ver10/schema"}
        profiles = []
        for p in root.findall(".//trt:Profiles", ns):
            profiles.append({
                "token": p.get("token", ""),
                "name": (p.find("tt:Name", ns) or ET.Element("x")).text or "",
            })
        return profiles

    async def get_stream_uri(self, token: str = PROFILE_TOKEN) -> str | None:
        """Gibt RTSP-URI vom Gerät zurück."""
        body = f"""<trt:GetStreamUri>
  <trt:StreamSetup>
    <tt:Stream>RTP-Unicast</tt:Stream>
    <tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>
  </trt:StreamSetup>
  <trt:ProfileToken>{token}</trt:ProfileToken>
</trt:GetStreamUri>"""
        root = await self._post(ENDPOINT_MEDIA, body)
        if root is None:
            return RTSP_MAIN
        ns = {"trt": "http://www.onvif.org/ver10/media/wsdl",
              "tt": "http://www.onvif.org/ver10/schema"}
        uri_el = root.find(".//tt:Uri", ns)
        return uri_el.text if uri_el is not None else RTSP_MAIN

    # ── PTZ ───────────────────────────────────────────────────────────────────

    async def ptz_continuous_move(
        self,
        pan: float = 0.0,
        tilt: float = 0.0,
        zoom: float = 0.0,
        token: str = PROFILE_TOKEN,
    ) -> bool:
        """Startet kontinuierliche PTZ-Bewegung. Werte: -1.0 bis 1.0."""
        body = f"""<tptz:ContinuousMove>
  <tptz:ProfileToken>{token}</tptz:ProfileToken>
  <tptz:Velocity>
    <tt:PanTilt x="{pan:.2f}" y="{tilt:.2f}"/>
    <tt:Zoom x="{zoom:.2f}"/>
  </tptz:Velocity>
</tptz:ContinuousMove>"""
        result = await self._post(ENDPOINT_PTZ, body)
        return result is not None

    async def ptz_stop(self, token: str = PROFILE_TOKEN) -> bool:
        """Stoppt alle PTZ-Bewegungen."""
        body = f"""<tptz:Stop>
  <tptz:ProfileToken>{token}</tptz:ProfileToken>
  <tptz:PanTilt>true</tptz:PanTilt>
  <tptz:Zoom>true</tptz:Zoom>
</tptz:Stop>"""
        result = await self._post(ENDPOINT_PTZ, body)
        return result is not None

    async def ptz_relative_move(
        self,
        pan: float = 0.0,
        tilt: float = 0.0,
        zoom: float = 0.0,
        token: str = PROFILE_TOKEN,
    ) -> bool:
        """Relative PTZ-Bewegung (Translation)."""
        body = f"""<tptz:RelativeMove>
  <tptz:ProfileToken>{token}</tptz:ProfileToken>
  <tptz:Translation>
    <tt:PanTilt x="{pan:.2f}" y="{tilt:.2f}"/>
    <tt:Zoom x="{zoom:.2f}"/>
  </tptz:Translation>
</tptz:RelativeMove>"""
        result = await self._post(ENDPOINT_PTZ, body)
        return result is not None

    # ── Convenience PTZ-Shortcuts ─────────────────────────────────────────────

    async def ptz_right(self, speed: float = PTZ_SPEED) -> bool:
        return await self.ptz_continuous_move(pan=speed)

    async def ptz_left(self, speed: float = PTZ_SPEED) -> bool:
        return await self.ptz_continuous_move(pan=-speed)

    async def ptz_up(self, speed: float = PTZ_SPEED) -> bool:
        return await self.ptz_continuous_move(tilt=speed)

    async def ptz_down(self, speed: float = PTZ_SPEED) -> bool:
        return await self.ptz_continuous_move(tilt=-speed)

    async def ptz_zoom_in(self, speed: float = PTZ_SPEED) -> bool:
        return await self.ptz_continuous_move(zoom=speed)

    async def ptz_zoom_out(self, speed: float = PTZ_SPEED) -> bool:
        return await self.ptz_continuous_move(zoom=-speed)

    async def ptz_command(self, direction: str, speed: float = PTZ_SPEED) -> bool:
        """
        Einheitlicher PTZ-Aufruf für HA-Buttons.
        direction: right | left | up | down | zoom_in | zoom_out | stop
        speed: 0.0–1.0 (normalisiert aus HA-Stufe 1–8)

        XM-Firmware ignoriert Velocity häufig — Bewegungsdistanz wird daher
        ZUSÄTZLICH über den Stop-Delay gesteuert (0.15s–1.5s linear zu speed).
        So funktioniert die Geschwindigkeitsstufe auch ohne Velocity-Support.
        """
        dispatch = {
            "right":    (speed,   0.0,   0.0),
            "left":     (-speed,  0.0,   0.0),
            "up":       (0.0,     speed, 0.0),
            "down":     (0.0,    -speed, 0.0),
            "zoom_in":  (0.0,     0.0,   speed),
            "zoom_out": (0.0,     0.0,  -speed),
            "stop":     None,
        }
        if direction == "stop":
            return await self.ptz_stop()
        coords = dispatch.get(direction)
        if coords is None:
            _LOGGER.error("Unbekannte PTZ-Richtung: %s", direction)
            return False
        pan, tilt, zoom = coords

        # Stop-Delay proportional zu speed: bei speed=PTZ_SPEED bleibt das
        # bisherige PTZ_STOP_DELAY erhalten; bei kleinen Werten kurzer Delay,
        # bei speed=1.0 maximal 1.5s. XM-Kameras ignorieren oft Velocity →
        # dieser Delay ist die primäre Geschwindigkeitsregelung.
        stop_delay = max(0.15, min(1.5, PTZ_STOP_DELAY * speed / PTZ_SPEED))

        _LOGGER.debug("PTZ '%s': velocity=%.2f stop_delay=%.2fs", direction, speed, stop_delay)

        ok = await self.ptz_continuous_move(pan=pan, tilt=tilt, zoom=zoom)
        if ok:
            await asyncio.sleep(stop_delay)
            await self.ptz_stop()
        return ok

    async def ptz_goto_home(self, speed: float = PTZ_SPEED, token: str = PROFILE_TOKEN) -> bool:
        """Fährt zur gespeicherten Home-Position."""
        body = (
            f"<tptz:GotoHomePosition>"
            f"<tptz:ProfileToken>{token}</tptz:ProfileToken>"
            f'<tptz:Speed><tt:PanTilt x="{speed:.2f}" y="{speed:.2f}"/></tptz:Speed>'
            f"</tptz:GotoHomePosition>"
        )
        result = await self._post(ENDPOINT_PTZ, body)
        return result is not None

    async def ptz_set_home(self, token: str = PROFILE_TOKEN) -> bool:
        """Speichert aktuelle Position als Home."""
        body = (
            f"<tptz:SetHomePosition>"
            f"<tptz:ProfileToken>{token}</tptz:ProfileToken>"
            f"</tptz:SetHomePosition>"
        )
        result = await self._post(ENDPOINT_PTZ, body)
        return result is not None

    async def ptz_goto_preset(
        self, preset_token: str, speed: float = PTZ_SPEED, token: str = PROFILE_TOKEN
    ) -> bool:
        """Fährt zu einem gespeicherten Preset."""
        body = (
            f"<tptz:GotoPreset>"
            f"<tptz:ProfileToken>{token}</tptz:ProfileToken>"
            f"<tptz:PresetToken>{preset_token}</tptz:PresetToken>"
            f'<tptz:Speed><tt:PanTilt x="{speed:.2f}" y="{speed:.2f}"/>'
            f'<tt:Zoom x="{speed:.2f}"/></tptz:Speed>'
            f"</tptz:GotoPreset>"
        )
        result = await self._post(ENDPOINT_PTZ, body)
        return result is not None

    async def ptz_set_preset(
        self, name: str, preset_token: str | None = None, token: str = PROFILE_TOKEN
    ) -> str | None:
        """Speichert aktuelle Position als Preset. Gibt PresetToken zurück."""
        tok_xml = f"<tptz:PresetToken>{preset_token}</tptz:PresetToken>" if preset_token else ""
        body = (
            f"<tptz:SetPreset>"
            f"<tptz:ProfileToken>{token}</tptz:ProfileToken>"
            f"<tptz:PresetName>{name}</tptz:PresetName>"
            f"{tok_xml}"
            f"</tptz:SetPreset>"
        )
        root = await self._post(ENDPOINT_PTZ, body)
        if root is None:
            return None
        for el in root.iter():
            if el.tag.split("}")[-1] == "PresetToken" and el.text:
                return el.text.strip()
        return None

    async def ptz_get_presets(self, token: str = PROFILE_TOKEN) -> dict[str, str]:
        """Gibt alle gespeicherten Presets zurück: {preset_token: name}."""
        body = (
            f"<tptz:GetPresets>"
            f"<tptz:ProfileToken>{token}</tptz:ProfileToken>"
            f"</tptz:GetPresets>"
        )
        root = await self._post(ENDPOINT_PTZ, body)
        presets: dict[str, str] = {}
        if root is None:
            return presets
        for el in root.iter():
            local = el.tag.split("}")[-1]
            if local == "Preset":
                ptok = el.get("token", "")
                pname = ""
                for child in el:
                    if child.tag.split("}")[-1] == "Name":
                        pname = child.text or ""
                if ptok:
                    presets[ptok] = pname or f"Preset {ptok}"
        return presets

    # ── Events (Pull-Point) ───────────────────────────────────────────────────

    async def create_pull_point_subscription(self) -> str | None:
        """Erstellt PullPoint-Subscription, gibt Endpoint-URL zurück."""
        body = """<tev:CreatePullPointSubscription xmlns:tev="http://www.onvif.org/ver10/events/wsdl">
  <tev:InitialTerminationTime>PT600S</tev:InitialTerminationTime>
</tev:CreatePullPointSubscription>"""
        root = await self._post(ENDPOINT_EVENTS, body)
        if root is None:
            return None
        addr = root.find(".//{http://www.w3.org/2005/08/addressing}Address")
        return addr.text if addr is not None else None

    async def pull_messages(self, subscription_url: str) -> list[dict[str, Any]]:
        """Zieht Events vom PullPoint. Gibt Liste von Event-Dicts zurück."""
        body = """<tev:PullMessages xmlns:tev="http://www.onvif.org/ver10/events/wsdl">
  <tev:Timeout>PT5S</tev:Timeout>
  <tev:MessageLimit>10</tev:MessageLimit>
</tev:PullMessages>"""
        root = await self._post(subscription_url, body)
        if root is None:
            return []

        events: list[dict[str, Any]] = []
        ns = {"wsnt": "http://docs.oasis-open.org/wsn/b-2",
              "tt": "http://www.onvif.org/ver10/schema"}
        motion_names = {"ismotion", "motion", "motionalarm", "videomotion"}
        for msg in root.findall(".//wsnt:NotificationMessage", ns):
            topic_el = msg.find("wsnt:Topic", ns)
            if topic_el is None:
                continue
            # Alle SimpleItems sammeln — IsMotion hat Vorrang vor dem ersten Item
            all_items = msg.findall(".//tt:SimpleItem", ns)
            value: Any = None
            for item in all_items:
                name = (item.get("Name") or "").lower()
                if name in motion_names:
                    value = item.get("Value")
                    break
            if value is None and all_items:
                value = all_items[0].get("Value")
            events.append({
                "topic": topic_el.text or "",
                "value": value,
            })
        return events


# ── Globale Singleton-Instanz für HA-Coordinator ─────────────────────────────

_client: XMSoapClient | None = None


async def get_client() -> XMSoapClient:
    """Gibt globalen Client zurück – lazy init."""
    global _client
    if _client is None:
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=8),
            headers={"Content-Type": "application/soap+xml; charset=utf-8"},
        )
        _client = XMSoapClient(session=session)
    return _client


async def close_client() -> None:
    """Schließt globale Session – im HA unload_entry aufrufen."""
    global _client
    if _client and _client._session:
        await _client._session.close()
    _client = None


# ── Schnelltest (direkt ausführen) ────────────────────────────────────────────

async def _test() -> None:
    async with XMSoapClient() as c:
        print("=== Ping ===")
        print(await c.ping())

        print("\n=== Profile ===")
        for p in await c.get_profiles():
            print(p)

        print("\n=== Stream URI ===")
        print(await c.get_stream_uri())

        print("\n=== PTZ right (0.5s) ===")
        ok = await c.ptz_continuous_move(pan=PTZ_SPEED)
        print("Move OK:", ok)
        await asyncio.sleep(0.5)
        ok = await c.ptz_stop()
        print("Stop OK:", ok)


if __name__ == "__main__":
    asyncio.run(_test())
