"""
Lokaler Test: ONVIF PullPoint Motion Detection (v2.2.25)
Zeigt Raw-XML damit wir sehen was die Kamera wirklich sendet.

Ausfuehren:
    .venv/Scripts/python test_motion_events.py

Beenden: Strg+C
"""
import asyncio
import sys
import os
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "custom_components", "wjg_camera"))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import aiohttp
import base64
import hashlib
from datetime import datetime, timezone

CAMERA_HOST = "192.168.178.49"
ONVIF_PORT  = 8899
EVENTS_URL  = f"http://{CAMERA_HOST}:{ONVIF_PORT}/onvif/Events"


def wsse_header() -> str:
    nonce = os.urandom(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + b"").digest()
    ).decode()
    nonce_b64 = base64.b64encode(nonce).decode()
    return (
        '<wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"'
        ' xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
        '<wsse:UsernameToken>'
        '<wsse:Username>admin</wsse:Username>'
        f'<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</wsse:Password>'
        f'<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce_b64}</wsse:Nonce>'
        f'<wsu:Created>{created}</wsu:Created>'
        '</wsse:UsernameToken></wsse:Security>'
    )


def envelope(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"'
        ' xmlns:tev="http://www.onvif.org/ver10/events/wsdl"'
        ' xmlns:tt="http://www.onvif.org/ver10/schema"'
        ' xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"'
        ' xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
        f'<s:Header>{wsse_header()}</s:Header>'
        f'<s:Body>{body}</s:Body>'
        '</s:Envelope>'
    )


async def soap_post(session: aiohttp.ClientSession, url: str, body: str) -> str:
    async with session.post(
        url,
        data=envelope(body).encode("utf-8"),
        headers={"Content-Type": "application/soap+xml; charset=utf-8"},
    ) as resp:
        return await resp.text()


def parse_simple_items(xml_text: str) -> dict:
    """Alle SimpleItem Name/Value Paare aus einem XML-Block extrahieren."""
    items = {}
    for m in re.finditer(
        r'<[^>]*SimpleItem[^>]*Name=["\']([^"\']+)["\'][^>]*Value=["\']([^"\']*)["\']',
        xml_text, re.IGNORECASE
    ):
        items[m.group(1)] = m.group(2)
    for m in re.finditer(
        r'<[^>]*SimpleItem[^>]*Value=["\']([^"\']*)["\'][^>]*Name=["\']([^"\']+)["\']',
        xml_text, re.IGNORECASE
    ):
        items[m.group(2)] = m.group(1)
    return items


async def main() -> None:
    print("=== WJG Motion Detection Test (Raw-XML Analyse) ===")
    print(f"Kamera: {CAMERA_HOST}:{ONVIF_PORT}")
    print()

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:

        # Subscription erstellen
        print("[1/2] Erstelle PullPoint-Subscription...")
        resp = await soap_post(
            session, EVENTS_URL,
            "<tev:CreatePullPointSubscription>"
            "<tev:InitialTerminationTime>PT600S</tev:InitialTerminationTime>"
            "</tev:CreatePullPointSubscription>"
        )

        # Subscription-URL aus Antwort extrahieren
        addr_match = re.search(r'<[^>]*Address[^>]*>([^<]+)</[^>]*Address>', resp)
        if not addr_match:
            print("FEHLER: Keine Subscription-URL in Antwort")
            print(resp[:500])
            return

        sub_url = addr_match.group(1).strip()
        print(f"OK  URL: {sub_url}")
        print()

        print("[2/2] Polling (Strg+C zum Beenden) ...")
        print("      Stelle dich vor die Kamera und bewege dich!")
        print()

        poll = 0
        while True:
            poll += 1
            raw = await soap_post(
                session, sub_url,
                "<tev:PullMessages>"
                "<tev:Timeout>PT10S</tev:Timeout>"
                "<tev:MessageLimit>20</tev:MessageLimit>"
                "</tev:PullMessages>"
            )

            # Alle NotificationMessage-Bloecke finden
            blocks = re.findall(
                r'<[^>]*NotificationMessage[^>]*>(.*?)</[^>]*NotificationMessage>',
                raw, re.DOTALL
            )

            if not blocks:
                print(f"  Poll #{poll:03d}: (keine Events)")
            else:
                print(f"  Poll #{poll:03d}: {len(blocks)} Event(s)")
                for i, block in enumerate(blocks, 1):
                    # Topic
                    topic_m = re.search(r'<[^>]*Topic[^>]*>([^<]+)</[^>]*Topic>', block)
                    topic = topic_m.group(1).strip() if topic_m else "?"

                    # Alle SimpleItems
                    items = parse_simple_items(block)

                    print(f"    Event {i}: {topic}")
                    if items:
                        for name, value in items.items():
                            is_motion = name.lower() in ("ismotion", "motion", "motionalarm")
                            flag = " <-- MOTION KEY" if is_motion else ""
                            print(f"      SimpleItem: {name} = '{value}'{flag}")

                            # Was wuerde der Coordinator daraus machen?
                            if is_motion:
                                truthy = str(value).lower() not in ("", "0", "false", "no", "off")
                                print(f"      Coordinator wertet als: {'MOTION ON' if truthy else 'MOTION OFF'}")
                    else:
                        # Kein SimpleItem gefunden — zeige rohe Items
                        raw_items = re.findall(r'<[^>]*Item[^>]*/>', block)
                        print(f"      Rohe Items: {raw_items[:3]}")

                    print()

            await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTest beendet.")
