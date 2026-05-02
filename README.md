# WJG XM-3820 → Home Assistant OS Bridge

**Kamera:** WJG / Tenganda XM-3820  
**App:** iCam365 (Shenzhen Tange)  
**Chipset:** XM (Xiongmai) / GK-Serie  
**HA-Version:** 2024.x+
**PiHole:** *.tange365.com, *.icam365.com, *.p2p*.net, *.tutk.com← P2P SDK von Throughtek, *.iotcplatform.com← P2P SDK von iOTC
---

## Architektur

```
┌─────────────────────────────────────────────────────────────┐
│  WJG XM-3820 Kamera (WiFi)                                  │
│  Hotspot: GW_AP_XXXX oder im Heim-WLAN                      │
│  Ports: 80 (HTTP) · 554 (RTSP) · 34567 (XM SDK) · 8899    │
└────────────────┬────────────────────────────────────────────┘
                 │  WiFi / LAN
                 ▼
┌─────────────────────────────────────────────────────────────┐
│  custom_components/wjg_camera                               │
│  ┌──────────┐  ┌─────────────┐  ┌──────────────────────┐  │
│  │ RTSP     │  │ HTTP Snap   │  │ XM SDK (Port 34567)  │  │
│  │ Stream   │  │ shot        │  │ Aufnahme / PTZ       │  │
│  └──────────┘  └─────────────┘  └──────────────────────┘  │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│  Home Assistant OS                                          │
│  camera.wjg_xm_3820 · switch.aufnahme · binary_sensor.motion │
│  button.ptz_* · sensor.dateiliste                           │
│  Lovelace Dashboard mit Livestream + Steuerung              │
└─────────────────────────────────────────────────────────────┘
```

---

## Schritt 1: Kamera-IP herausfinden

### Option A – Kamera im Heimnetz (empfohlen)
1. iCam365-App → Kamera mit deinem WLAN verbinden  
2. Dann Discovery-Script auf dem HA-Server ausführen:

```bash
cd discovery/
python3 scan_camera.py
# Oder mit bekanntem Subnetz:
python3 scan_camera.py --subnet 192.168.178
```

### Option B – Kamera im Hotspot-Modus (GW_AP_XXXX)
1. HA-Server oder Laptop mit dem Hotspot `GW_AP_XXXX` verbinden  
2. Typische Kamera-IP: `192.168.4.1`

```bash
python3 scan_camera.py --ip 192.168.4.1
```

### Option C – Router-DHCP-Liste
Im Router (z.B. FritzBox) unter "Heimnetz → Netzwerk" nachschauen, welches Gerät sich als `GW_AP_...` oder `IPC_...` eingetragen hat.

---

## Schritt 2: Integration installieren

```bash
# HA-Konfigurationsordner: meist /config oder /homeassistant
cp -r custom_components/wjg_camera /config/custom_components/
```

HA neu starten, dann:  
**Einstellungen → Integrationen → Integration hinzufügen → "WJG Camera"**

### Konfigurationsfelder

| Feld | Beispielwert | Beschreibung |
|---|---|---|
| Host | `192.168.178.42` | IP der Kamera im Heimnetz |
| Benutzername | `admin` | Standard: `admin` |
| Passwort | _(leer)_ | Meist kein Passwort |
| HTTP-Port | `80` | Snapshot & Web UI |
| RTSP-Port | `554` | Livestream |
| Protokoll | `rtsp` | Empfohlen |
| RTSP-Pfad | `/user=admin&password=&channel=1&stream=0.sdp?real_stream` | XM-Standard |
| Snapshot-Pfad | `/webcapture.jpg?command=snap&channel=1` | |

---

## Schritt 3: Dashboard einrichten

```bash
# Dashboard-Datei kopieren
cp lovelace/dashboard.yaml /config/lovelace/

# In configuration.yaml eintragen:
lovelace:
  resources:
    - url: /hacsfiles/mushroom/mushroom.js
      type: module
    - url: /hacsfiles/button-card/button-card.js
      type: module
```

---

## Netzwerk-Routing (kritisch!)

### Problem
Die Kamera muss **dauerhaft im selben Netzwerk** wie HA sein.  
Im Hotspot-Modus (`GW_AP_XXXX`) kann sich jeweils **nur ein Gerät** verbinden.

### Lösung A: Kamera ins Heimnetz einbinden
```
Kamera ←→ Heim-Router ←→ HA OS
```
→ Kamera per iCam365-App ins Heimnetz einbinden  
→ Im Router einen festen DHCP-Eintrag für die Kamera setzen

### Lösung B: Zweiter WLAN-Adapter (Hotspot-Modus, wenn nötig)
```
Kamera (GW_AP_XXXX)
     ↑↓
  HA-Server  (wlan0 → Heim-WLAN, wlan1 → Kamera-Hotspot)
     ↑↓
  Home Assistant
```

Auf dem HA-Server (Raspberry Pi / x86):
```bash
# Zweites WLAN-Interface mit Kamera verbinden
nmcli dev wifi connect "GW_AP_ABCD" password "" ifname wlan1

# Route zur Kamera über wlan1
ip route add 192.168.4.0/24 via 192.168.4.1 dev wlan1

# IP-Forwarding aktivieren (optional)
echo 1 > /proc/sys/net/ipv4/ip_forward
```

### Lösung C: ADB (wenn Android-Gerät mit iCam365 als Proxy)
```
Kamera (GW_AP_XXXX)
     ↑↓
  Android-Phone (iCam365 aktiv, USB-Debugging an)
     ↑↓ ADB-Tunnel
  HA-Server
```

```bash
# ADB-Tunnel: Android-Port an lokalen Port weiterleiten
adb forward tcp:8080 tcp:554    # RTSP-Tunnel
adb forward tcp:8081 tcp:80     # HTTP-Tunnel

# Dann in HA-Konfiguration:
# Host: 127.0.0.1
# RTSP-Port: 8080
# HTTP-Port: 8081
```

---

## RTSP-URLs (Übersicht)

```
# Hauptstream (Full HD):
rtsp://admin:@KAMERA-IP:554/user=admin&password=&channel=1&stream=0.sdp?real_stream

# Substream (niedrige Auflösung, weniger Last):
rtsp://admin:@KAMERA-IP:554/user=admin&password=&channel=1&stream=1.sdp?real_stream

# Falls obige nicht funktionieren – Alternativen:
rtsp://KAMERA-IP:554/live/ch00_0
rtsp://KAMERA-IP:554/h264
rtsp://KAMERA-IP:554/stream0
```

---

## Verfügbare HA Services

Nach der Installation sind folgende Services verfügbar:

```yaml
# Aufnahme starten
service: switch.turn_on
target:
  entity_id: switch.wjg_xm_3820_aufnahme

# Snapshot speichern
service: camera.snapshot
target:
  entity_id: camera.wjg_xm_3820
data:
  filename: /config/www/snapshot.jpg

# RTSP-Stream-URL abfragen
service: camera.get_stream_url
target:
  entity_id: camera.wjg_xm_3820
```

Zusätzliche Entitäten:

- PTZ-Buttons als `button`-Entities (`button.*_ptz_up`, `button.*_ptz_left`, usw.)
- Bewegungsmelder als `binary_sensor`
- Dateiliste / SD-Karte als `sensor` mit Datei-Metadaten in den Attributen

### Fallback- und Recovery-Verhalten

- HTTP-Fallback für Aufnahme, PTZ, Snapshot und Dateiliste ist integriert
- Aufnahme, PTZ, Snapshot und Dateiliste nutzen einen kurzen Retry bei transienten HTTP-Fehlern
- Retry-Anzahl ist in den Integrationsoptionen als `http_retries` (0-5) konfigurierbar
- XM-Keepalive wird überwacht; bei Fehlern wird ein Reconnect-Pfad ausgelöst
- Shutdown schließt HTTP-Session und XM-Verbindung sauber und idempotent

### Optionen nach der Einrichtung

Über den Options-Flow der Integration lassen sich nachträglich anpassen:

- `protocol`
- `rtsp_path`
- `snapshot_path`
- `http_retries` für HTTP-Fallback und Retry-Verhalten

Alte Config-Entries ohne diese Option bleiben kompatibel; fehlende oder ungültige Retry-Werte werden intern sicher auf Default bzw. den erlaubten Bereich 0 bis 5 normalisiert.

---

## Entwicklung und Tests

Für die lokale Entwicklung wird ein eigenes virtuelles Environment empfohlen.

### Windows / PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

### Tests ausführen

```powershell
.\.venv\Scripts\python -m pytest tests -q
```

Erwarteter Stand:

```text
67 passed
```

### VS Code

Das Projekt enthält lokale Workspace-Settings für:

- Interpreter: `.venv\Scripts\python.exe`
- pytest-Test-Discovery auf `tests`
- zusätzliche Analysepfade für `custom_components`

Falls VS Code noch alte Importfehler zeigt, das Fenster neu laden oder den Python-Interpreter einmal neu auswählen.

---

## Fehlerbehebung

### "Kamera nicht erreichbar" nach Installation
→ `python3 discovery/scan_camera.py --ip DEINE-IP` ausführen  
→ Prüfen ob Ports 80 und/oder 554 offen sind

### RTSP-Stream lädt nicht in HA
→ VLC zum Testen verwenden: `vlc RTSP-URL`  
→ Alternativen RTSP-Pfade im Scan-Script prüfen  
→ In HA `stream:` in configuration.yaml aktivieren

### Aufnahme-Schalter funktioniert nicht
→ Discovery-Script: prüfen ob Port 34567 offen ist  
→ Protokoll auf `xm_sdk` umstellen in den Optionen  
→ Log prüfen: `Einstellungen → System → Protokolle`

### Snapshot-URL liefert 404
→ Snapshot-Pfad in den Optionen auf `/snapshot.jpg` oder `/image` ändern  
→ Kamera-Webinterface manuell öffnen: `http://KAMERA-IP/`

---

## Entwicklungsstand

| Feature | Status |
|---|---|
| RTSP Livestream | ✅ Implementiert |
| HTTP Snapshot | ✅ Implementiert |
| Aufnahme Start/Stop | ✅ Implementiert (XM SDK + HTTP) |
| Bewegungserkennungs-Sensor | ✅ Implementiert |
| Config Flow (UI-Assistent) | ✅ Implementiert |
| Lovelace Dashboard | ✅ Implementiert |
| PTZ-Steuerung | ✅ Basis implementiert (Buttons + Coordinator) |
| Dateiliste / SD-Karte | ✅ Basis implementiert (Sensor + Attribute) |
| ONVIF-Support | ✅ Basis implementiert (Stream/PTZ) |
| ADB-Proxy-Modus | ✅ Basis implementiert |
| Authentifizierung (Passwort) | 🔧 XM MD5-Hash implementiert |
| HTTP-Fallback-Härtung | ✅ Non-200/Exception/Retry getestet |
| XM-Recovery-Härtung | ✅ Keepalive/Reconnect getestet |
| Shutdown-Härtung | ✅ Session-Close + Disconnect + Idempotenz getestet |
| Options-Flow-Härtung | ✅ Retry-Defaults, Alt-Entries und Range-Normalisierung getestet |
| Automatisierte Tests | ✅ pytest + venv + CI + lokale VS-Code-Konfiguration |

---

*WJG XM-3820 HA Bridge — entwickelt mit ❤️ für lokale Kontrolle*
