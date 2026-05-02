#!/usr/bin/env python3
"""
WJG XM-3820 Camera Discovery & API Probe
=========================================
Schließe dein Gerät (HA-Server oder Laptop) an dasselbe WLAN wie die Kamera an.
Wenn die Kamera noch im Hotspot-Modus ist (GW_AP_XXXX), verbinde dich mit diesem
WLAN und führe dieses Skript aus.

Aufruf:
    python3 scan_camera.py
    python3 scan_camera.py --subnet 192.168.4   # eigenes Subnetz
    python3 scan_camera.py --ip 192.168.1.1      # direkt eine IP testen
"""
import socket
import sys
import argparse
import concurrent.futures
import urllib.request
import urllib.error
import json
import struct

COMMON_CAMERA_IPS = [
    "192.168.1.1",    # Hotspot-Modus: Kamera ist oft das Gateway
    "192.168.1.10",   # XM-Serie Standard
    "192.168.4.1",    # GW_AP Hotspot typisch
    "192.168.10.1",
    "192.168.100.1",
]

PORTS_TO_SCAN = {
    80:    "HTTP Web UI",
    554:   "RTSP Stream",
    8899:  "ONVIF",
    34567: "XM SDK (binär)",
    8080:  "HTTP alternativ",
    443:   "HTTPS",
    21:    "FTP (Dateizugriff)",
    23:    "Telnet (Debug)",
}

RTSP_PATHS = [
    "/streamtype=0",
    "/user=admin&password=&channel=1&stream=0.sdp?real_stream",
    "/user=admin&password=&channel=1&stream=1.sdp?real_stream",
    "/live/ch00_0",
    "/live/ch01_0",
    "/h264",
    "/mpeg4",
    "/stream0",
    "/cam/realmonitor?channel=1&subtype=0",
    "/videoMain",
    "/video.h264",
    "/11",
]

HTTP_PATHS = [
    "/",
    "/webcapture.jpg?command=snap&channel=1",
    "/cgi-bin/snapshot.cgi",
    "/snapshot.jpg",
    "/image/jpeg.cgi",
    "/tmpfs/auto.jpg",
    "/onvif/device_service",
]

def check_port(ip, port, timeout=1.5):
    """Prüft ob ein Port offen ist."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False

def check_http(ip, path, port=80, timeout=3):
    """Versucht HTTP GET und gibt Status + Content-Type zurück."""
    url = f"http://{ip}:{port}{path}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ct = resp.headers.get("Content-Type", "")
            length = resp.headers.get("Content-Length", "?")
            return resp.status, ct, length
    except urllib.error.HTTPError as e:
        return e.code, "", ""
    except Exception:
        return None, "", ""

def check_rtsp(ip, path, port=554, timeout=3):
    """Sendet RTSP OPTIONS und prüft auf Antwort."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((ip, port))
        msg = f"OPTIONS rtsp://{ip}:{port}{path} RTSP/1.0\r\nCSeq: 1\r\n\r\n"
        s.send(msg.encode())
        resp = s.recv(1024).decode("utf-8", errors="ignore")
        s.close()
        if "RTSP/1.0" in resp or "200 OK" in resp:
            return True, resp[:120]
    except Exception:
        pass
    return False, ""

def try_xm_login(ip, port=34567, timeout=3):
    """Versucht XM SDK Login (Binärprotokoll auf Port 34567)."""
    # XM SDK Login-Paket (Header + JSON-Body)
    payload = json.dumps({
        "EncryptType": "MD5",
        "LoginType": "DVRIP-Web",
        "PassWord": "",
        "UserName": "admin"
    }).encode()
    # XM Header: Magic(0xFF) + Version(0x01) + SessionID(4B) + Sequence(4B)
    #            + Total(1B) + Current(1B) + MessageID(2B) + DataLen(4B)
    header = struct.pack("<BBHIIBBHI",
        0xFF, 0x01, 0x0000, 0x00000000, 0x00000000,
        0x00, 0x00, 0x03E8, len(payload))  # 0x03E8 = 1000 = LOGIN_REQ2
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((ip, port))
        s.send(header + payload)
        resp = s.recv(2048)
        s.close()
        if len(resp) > 20:
            body = resp[20:].decode("utf-8", errors="ignore").strip("\x00")
            try:
                data = json.loads(body)
                return True, data.get("Ret", "?"), data.get("SessionID", "")
            except Exception:
                return True, "response received", ""
    except Exception:
        pass
    return False, "", ""

def scan_ip(ip, verbose=True):
    """Vollständiger Scan einer IP-Adresse."""
    results = {"ip": ip, "open_ports": [], "rtsp_urls": [], "http_endpoints": [],
               "xm_sdk": False, "summary": []}
    
    print(f"\n{'='*60}")
    print(f"  Scanning: {ip}")
    print(f"{'='*60}")
    
    # Port-Scan
    print("\n[1] Port-Scan...")
    for port, desc in PORTS_TO_SCAN.items():
        if check_port(ip, port):
            results["open_ports"].append(port)
            print(f"    ✅ Port {port:5d}  ({desc})")
        else:
            print(f"    ❌ Port {port:5d}  ({desc})")
    
    if not results["open_ports"]:
        print(f"\n  ⚠️  Keine offenen Ports — Kamera nicht erreichbar auf {ip}")
        return results
    
    # HTTP-Endpunkte
    if 80 in results["open_ports"] or 8080 in results["open_ports"]:
        http_port = 80 if 80 in results["open_ports"] else 8080
        print(f"\n[2] HTTP-Endpunkte auf Port {http_port}...")
        for path in HTTP_PATHS:
            status, ct, length = check_http(ip, path, http_port)
            if status and status < 500:
                results["http_endpoints"].append({
                    "url": f"http://{ip}:{http_port}{path}",
                    "status": status, "content_type": ct, "length": length
                })
                marker = "📷" if "image" in ct.lower() else "🌐"
                print(f"    {marker} [{status}] {path}  ({ct}, {length}B)")
    
    # RTSP-Streams
    if 554 in results["open_ports"]:
        print(f"\n[3] RTSP-Stream-Pfade auf Port 554...")
        for path in RTSP_PATHS:
            ok, resp = check_rtsp(ip, path)
            if ok:
                url = f"rtsp://{ip}:554{path}"
                results["rtsp_urls"].append(url)
                print(f"    ✅ {url}")
                if len(results["rtsp_urls"]) >= 2:
                    print("       (weitere Pfade übersprungen — genug gefunden)")
                    break
            else:
                print(f"    ❌ /...{path[-30:]}")
    
    # XM SDK
    if 34567 in results["open_ports"]:
        print(f"\n[4] XM SDK auf Port 34567...")
        ok, ret, sid = try_xm_login(ip)
        results["xm_sdk"] = ok
        if ok:
            print(f"    ✅ XM Protokoll erkannt! Ret={ret}, SessionID={sid}")
        else:
            print(f"    ❌ Kein XM SDK")
    
    # ONVIF
    if 8899 in results["open_ports"]:
        print(f"\n[5] ONVIF auf Port 8899...")
        status, ct, _ = check_http(ip, "/onvif/device_service", 8899)
        if status:
            print(f"    ✅ ONVIF erreichbar (HTTP {status})")
        else:
            print(f"    ❌ ONVIF nicht verfügbar")
    
    # Zusammenfassung
    print(f"\n{'─'*60}")
    print(f"  ZUSAMMENFASSUNG für {ip}")
    print(f"{'─'*60}")
    
    if results["rtsp_urls"]:
        print(f"\n  📹 Für HA Camera Entity verwenden:")
        for url in results["rtsp_urls"][:2]:
            print(f"     {url}")
    
    if results["http_endpoints"]:
        snap = [e for e in results["http_endpoints"] if "snap" in e["url"] or "image" in e["content_type"].lower()]
        if snap:
            print(f"\n  📷 Snapshot URL:")
            print(f"     {snap[0]['url']}")
    
    if results["xm_sdk"]:
        print(f"\n  🔧 XM SDK verfügbar → Aufnahme-Steuerung möglich")
    
    return results

def network_sweep(subnet, timeout=0.8):
    """Scannt ein /24 Subnetz nach offenen Ports 80/554."""
    print(f"\n  Suche Kameras in {subnet}.0/24 (Ports 80, 554)...")
    found = []
    
    def probe(host):
        if check_port(host, 80, timeout) or check_port(host, 554, timeout):
            return host
        return None
    
    hosts = [f"{subnet}.{i}" for i in range(1, 255)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = {executor.submit(probe, h): h for h in hosts}
        for f in concurrent.futures.as_completed(futures):
            result = f.result()
            if result:
                found.append(result)
                print(f"  🎯 Gerät gefunden: {result}")
    return found

def main():
    parser = argparse.ArgumentParser(description="WJG XM-3820 Kamera-Scanner")
    parser.add_argument("--ip", help="Direkt eine IP scannen (z.B. 192.168.1.1)")
    parser.add_argument("--subnet", help="Subnetz sweepen (z.B. 192.168.1)", default=None)
    args = parser.parse_args()
    
    print("\n" + "="*60)
    print("  WJG XM-3820 — Kamera Discovery Tool")
    print("  Für HA OS Bridge-Entwicklung")
    print("="*60)
    
    targets = []
    
    if args.ip:
        targets = [args.ip]
    elif args.subnet:
        targets = network_sweep(args.subnet)
    else:
        # Automatisch: bekannte IPs + schneller Sweep
        print("\n  Versuche bekannte Kamera-IPs...")
        for ip in COMMON_CAMERA_IPS:
            if check_port(ip, 80, 0.8) or check_port(ip, 554, 0.8):
                print(f"  ✅ Gerät auf {ip} erreichbar!")
                targets.append(ip)
        
        if not targets:
            print("\n  Keine bekannten IPs erreichbar. Starte Netzwerk-Sweep...")
            # Versuche eigene IP ermitteln
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.connect(("8.8.8.8", 80))
                    local_ip = s.getsockname()[0]
                subnet = ".".join(local_ip.split(".")[:3])
                targets = network_sweep(subnet)
            except Exception as e:
                print(f"  Fehler: {e}")
    
    if not targets:
        print("\n  ❌ Keine Geräte gefunden.")
        print("\n  Tipps:")
        print("  • Stelle sicher, dass du mit dem Kamera-WLAN verbunden bist")
        print("  • Kamera-Hotspot heißt wahrscheinlich: GW_AP_XXXX")
        print("  • Versuche: python3 scan_camera.py --subnet 192.168.4")
        sys.exit(1)
    
    all_results = []
    for ip in targets:
        result = scan_ip(ip)
        all_results.append(result)
    
    # Konfigurationsdatei ausgeben
    print(f"\n{'='*60}")
    print("  ERGEBNIS FÜR HA KONFIGURATION")
    print(f"{'='*60}")
    
    for r in all_results:
        if r["rtsp_urls"] or r["http_endpoints"]:
            print(f"\n  Für configuration.yaml (Kamera: {r['ip']}):")
            print(f"\n  camera:")
            print(f"    - platform: generic")
            if r["rtsp_urls"]:
                print(f'      stream_source: "{r["rtsp_urls"][0]}"')
            snaps = [e for e in r["http_endpoints"] if "snap" in e["url"] or "image" in e.get("content_type","").lower()]
            if snaps:
                print(f'      still_image_url: "{snaps[0]["url"]}"')
            print(f'      verify_ssl: false')

if __name__ == "__main__":
    main()
