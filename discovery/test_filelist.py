"""
Test-Skript für Dateilisten-Abruf der WJG XM-3820
Prüft XM-SDK und HTTP-Fallback
"""

import asyncio
import aiohttp
import socket
from urllib.parse import quote

HOST = "192.168.178.49"
HTTP_PORT = 80
XM_PORT = 34567
USERNAME = "admin"
PASSWORD = ""

async def test_http_fallback():
    """HTTP Fallback-Methode testen."""
    print("\n" + "="*60)
    print("TEST 1: HTTP /cgi-bin/fileman (Fallback)")
    print("="*60)
    
    try:
        url = f"http://{HOST}:{HTTP_PORT}/cgi-bin/fileman"
        async with aiohttp.ClientSession() as session:
            auth = aiohttp.BasicAuth(USERNAME, PASSWORD) if USERNAME else None
            async with session.get(url, auth=auth, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                print(f"Status: {resp.status}")
                data = await resp.json()
                print(f"Response: {data}")
                if isinstance(data, dict):
                    files = data.get("files", [])
                    print(f"✓ Dateien gefunden: {len(files)}")
                    for f in files[:5]:
                        print(f"  - {f}")
    except Exception as e:
        print(f"✗ Fehler: {e}")


async def test_xm_sdk():
    """XM-SDK Port-Verbindung testen."""
    print("\n" + "="*60)
    print("TEST 2: XM-SDK Socket (Port 34567)")
    print("="*60)
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((HOST, XM_PORT))
        sock.close()
        
        if result == 0:
            print(f"✓ Port {XM_PORT} ist offen (XM-SDK erreichbar)")
        else:
            print(f"✗ Port {XM_PORT} nicht erreichbar (Fehler: {result})")
    except Exception as e:
        print(f"✗ Socket-Fehler: {e}")


async def test_http_snapshot():
    """HTTP Snapshot testen (für Basis-Konnektivität)."""
    print("\n" + "="*60)
    print("TEST 3: HTTP Snapshot (Basis-Konnektivität)")
    print("="*60)
    
    try:
        url = f"http://{HOST}:{HTTP_PORT}/webcapture.jpg?command=snap&channel=1"
        async with aiohttp.ClientSession() as session:
            auth = aiohttp.BasicAuth(USERNAME, PASSWORD) if USERNAME else None
            async with session.get(url, auth=auth, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                print(f"Status: {resp.status}")
                data = await resp.read()
                print(f"✓ Snapshot abgerufen: {len(data)} bytes")
    except Exception as e:
        print(f"✗ Fehler: {e}")


async def test_onvif_recording_list():
    """Alternative: ONVIF Recording List (falls vorhanden)."""
    print("\n" + "="*60)
    print("TEST 4: ONVIF Recording (Alternative)")
    print("="*60)
    
    try:
        # ONVIF Events Service (kann auch Recording-Info haben)
        url = f"http://{HOST}:{HTTP_PORT}/onvif/Device"
        async with aiohttp.ClientSession() as session:
            auth = aiohttp.BasicAuth(USERNAME, PASSWORD) if USERNAME else None
            headers = {"Content-Type": "application/soap+xml"}
            
            # Einfache SOAP-Anfrage zum Testen
            soap = """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope">
  <SOAP-ENV:Body>
    <tt:GetRecordings xmlns:tt="http://www.onvif.org/ver10/recording/wsdl"/>
  </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""
            
            async with session.post(url, data=soap, headers=headers, auth=auth, 
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                print(f"Status: {resp.status}")
                text = await resp.text()
                if "Recording" in text:
                    print(f"✓ ONVIF Recording Info gefunden")
                else:
                    print(f"✗ Keine Recording-Info in Response")
    except Exception as e:
        print(f"✗ Fehler: {e}")


async def main():
    print("\n╔" + "="*58 + "╗")
    print("║" + " WJG XM-3820 Dateilisten-Abruf Test ".center(58) + "║")
    print("╚" + "="*58 + "╝")
    print(f"\nKamera: {HOST}:{HTTP_PORT}")
    print(f"XM-SDK: {HOST}:{XM_PORT}")
    print(f"Username: {USERNAME}")
    
    await test_http_snapshot()
    await test_xm_sdk()
    await test_http_fallback()
    await test_onvif_recording_list()
    
    print("\n" + "="*60)
    print("DIAGNOSE:")
    print("="*60)
    print("""
Wenn TEST 1 ✓ erfolgreich ist:
  → Die Dateiliste SOLLTE funktionieren
  → Prüfe HA-Logs: Einstellungen → System → Protokolle (Filter: wjg_camera)

Wenn nur TEST 2 erfolgreich ist (XM-SDK Port offen):
  → XM-SDK ist erreichbar, aber HTTP-Fallback wird nicht genutzt
  → Wahrscheinlich: XM-Client sendet Daten, aber Parser scheitert
  
Wenn nur TEST 3 erfolgreich ist:
  → HTTP-Basis-Verbindung OK, aber /cgi-bin/fileman nicht implementiert
  → Kamera könnte andere Dateilisten-URL verwenden
  
Wenn alle Tests ✗ fehlschlagen:
  → Netzwerk-/Firewall-Problem
  → Credentials falsch
""")


if __name__ == "__main__":
    asyncio.run(main())
