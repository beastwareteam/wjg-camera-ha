"""
PTZ-Diagnose direkt vom HA-Host ausfuehren.
Ausfuehren via HA SSH-Terminal:
    python3 /config/custom_components/wjg_camera/test_ptz_from_ha.py

Oder falls asyncio Policy fehlt (Linux):
    python3 test_ptz_from_ha.py
"""
import asyncio
import aiohttp
import base64
import hashlib
import os
import sys
import datetime

HOST = "192.168.178.49"
PORT = 8899
URL  = f"http://{HOST}:{PORT}/onvif/PTZ"

NS = (
    ' xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"'
    ' xmlns:tt="http://www.onvif.org/ver10/schema"'
    ' xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"'
    ' xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"'
)


def build_wsse(user="admin", pwd=""):
    nonce = os.urandom(16)
    now = datetime.datetime.utcnow()
    created = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + pwd.encode()).digest()
    ).decode()
    nonce_b64 = base64.b64encode(nonce).decode()
    return (
        '<wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01'
        '/oasis-200401-wss-wssecurity-secext-1.0.xsd"'
        ' xmlns:wsu="http://docs.oasis-open.org/wss/2004/01'
        '/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
        "<wsse:UsernameToken>"
        f"<wsse:Username>{user}</wsse:Username>"
        f'<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01'
        f'/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
        f"{digest}</wsse:Password>"
        f'<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01'
        f'/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
        f"{nonce_b64}</wsse:Nonce>"
        f"<wsu:Created>{created}</wsu:Created>"
        "</wsse:UsernameToken></wsse:Security>"
    )


def make_envelope(body, user="admin", pwd=""):
    h = build_wsse(user, pwd)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"{NS}>'
        f"<s:Header>{h}</s:Header>"
        f"<s:Body>{body}</s:Body>"
        "</s:Envelope>"
    ).encode("utf-8")


MOVE_R = (
    '<tptz:ContinuousMove><tptz:ProfileToken>000</tptz:ProfileToken>'
    '<tptz:Velocity><tt:PanTilt x="0.40" y="0.00"/><tt:Zoom x="0.00"/>'
    '</tptz:Velocity></tptz:ContinuousMove>'
)
STOP = (
    '<tptz:Stop><tptz:ProfileToken>000</tptz:ProfileToken>'
    '<tptz:PanTilt>true</tptz:PanTilt><tptz:Zoom>true</tptz:Zoom></tptz:Stop>'
)


async def main():
    print(f"Python:  {sys.version}")
    print(f"aiohttp: {aiohttp.__version__}")
    print(f"Target:  {URL}")
    print()

    ct = {"Content-Type": "application/soap+xml; charset=utf-8"}
    connector = aiohttp.TCPConnector(force_close=True)
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=8),
        headers=ct,
    ) as s:

        print("=== Test 1: ContinuousMove rechts (WSSE admin/'') ===")
        data = make_envelope(MOVE_R)
        async with s.post(URL, data=data) as r:
            t = await r.text()
        ok = r.status == 200 and "fault" not in t.lower()
        print(f"  HTTP {r.status}  OK={ok}")
        if not ok:
            print(f"  Response: {t[:400]}")
        else:
            print("  Kamera sollte sich jetzt bewegen!")

        await asyncio.sleep(1.0)

        print()
        print("=== Test 2: Stop (WSSE admin/'') ===")
        async with s.post(URL, data=make_envelope(STOP)) as r:
            t = await r.text()
        print(f"  HTTP {r.status}  OK={r.status == 200 and 'fault' not in t.lower()}")

        print()
        print("=== Test 3: Ohne Auth ===")
        no_auth = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"{NS}>'
            f"<s:Body>{MOVE_R}</s:Body>"
            "</s:Envelope>"
        ).encode("utf-8")
        async with s.post(URL, data=no_auth) as r:
            t = await r.text()
        print(f"  HTTP {r.status}  (erwartet 400)")


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

asyncio.run(main())
