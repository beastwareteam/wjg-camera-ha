# WJG XM-3820 Camera Bridge – Home Assistant Integration

**Kamera:** WJG / Tenganda XM-3820 · **Chipset:** XM (Xiongmai) / GK-Serie  
**App:** iCam365 (Shenzhen Tange) · **Version:** 2.1.0 · **HA:** ≥ 2024.1

> Vollständige lokale Home-Assistant-Integration ohne Cloud-Abhängigkeit.  
> RTSP-Livestream, ONVIF-Steuerung, PTZ mit 21 Buttons, Imaging-Einstellungen, Events und mehr.

---

## Inhalt

- [Architektur](#architektur)
- [Voraussetzungen](#voraussetzungen)
- [Installation](#installation)
- [Konfiguration](#konfiguration)
- [Feature-Übersicht (49 Entitäten)](#feature-übersicht)
- [Protokolle](#protokolle)
- [Netzwerk-Routing](#netzwerk-routing)
- [ONVIF Details](#onvif-details)
- [Entwicklung & Tests](#entwicklung--tests)

---

## Architektur

```
┌──────────────────────────────────────────────────────────────┐
│  WJG XM-3820 Kamera (WiFi)                                   │
│  Hotspot: GW_AP_XXXX  │  Ports: 554·80·8899·34567           │
└─────────────┬────────────────────────────────────────────────┘
              │  WiFi / LAN
              ▼
┌──────────────────────────────────────────────────────────────┐
│  custom_components/wjg_camera                                │
│  ┌─────────┐ ┌──────────┐ ┌─────────────┐ ┌─────────────┐  │
│  │  RTSP   │ │HTTP Snap │ │  XM SDK     │ │   ONVIF     │  │
│  │ Stream  │ │ shot     │ │  Port 34567 │ │  Port 8899  │  │
│  └─────────┘ └──────────┘ └─────────────┘ └─────────────┘  │
└─────────────┬────────────────────────────────────────────────┘
              ▼
┌──────────────────────────────────────────────────────────────┐
│  Home Assistant  │  49 Entitäten in 7 Plattformen           │
│  camera · switch · binary_sensor · button                    │
│  sensor · number · select                                    │
└──────────────────────────────────────────────────────────────┘
```

---

## Voraussetzungen

| Komponente | Version |
|---|---|
| Home Assistant | ≥ 2024.1 |
| Python | ≥ 3.11 |
| `onvif-zeep` | ≥ 0.2.12 |
| `ffmpeg-python` | ≥ 0.2.0 |

---

## Installation

### HACS (empfohlen)

1. HACS → **Integrationen** → ⋮ → **Benutzerdefiniertes Repository**
2. URL: `https://github.com/beastwareteam/wjg-camera-ha`  · Kategorie: **Integration**
3. Herunterladen → Home Assistant neu starten

### Manuell

```bash
cp -r custom_components/wjg_camera /config/custom_components/
# Home Assistant neu starten
```

**Einstellungen → Integrationen → Integration hinzufügen → "WJG Camera"**

---

## Konfiguration

### Ersteinrichtung (Config Flow)

| Feld | Standard | Beschreibung |
|---|---|---|
| Host | `192.168.4.1` | IP-Adresse der Kamera |
| Benutzername | `admin` | Kamera-Login |
| Passwort | *(leer)* | Kamera-Passwort |
| HTTP-Port | `80` | Port der Weboberfläche / Snapshots |
| RTSP-Port | `554` | Port des Videostreams |
| ONVIF-Port | `8899` | Port für ONVIF-Dienste (PTZ, Events, Imaging) |
| Protokoll | `rtsp` | Verbindungstyp → [Protokolle](#protokolle) |
| HTTP-Retries | `1` | Wiederholversuche bei HTTP-Fehlern (0–5) |
| RTSP-Pfad | `…stream=1.sdp…` | Pfad des Videostreams |
| Snapshot-Pfad | `/webcapture.jpg…` | Pfad für Standbilder |

> **Hotspot-Modus:** IP `192.168.4.1`, SSID `GW_AP_XXXX`

### Kamera-IP ermitteln

```bash
# Automatischer Scan:
python3 discovery/scan_camera.py

# Bekanntes Subnetz:
python3 discovery/scan_camera.py --subnet 192.168.178

# Hotspot-Modus:
python3 discovery/scan_camera.py --ip 192.168.4.1
```

### Optionen nachträglich anpassen

**Einstellungen → Integrationen → WJG Camera → Konfigurieren**  
Protokoll, RTSP-Pfad, Snapshot-Pfad, HTTP-Retries und ONVIF-Port sind jederzeit änderbar.

---

## Feature-Übersicht

Die Integration stellt **49 Entitäten** in **7 Plattformen** bereit.

---

### 📹 Kamera – `camera` (1 Entität)

| Feature | Details |
|---|---|
| RTSP-Livestream | H.265 1080p (Hauptstream) oder H.265 360p (Substream) |
| Automatische Stream-URL-Erkennung | Testet Kandidaten-Pfade per RTSP DESCRIBE, wählt ersten mit Video-Track |
| ONVIF Stream-URL | Automatischer Abruf via `GetStreamUri` wenn Protokoll = ONVIF |
| Snapshot | Aktuelles Standbild per HTTP-Abruf |

**RTSP-URLs:**
```
# Hauptstream (Full HD):
rtsp://admin:@KAMERA-IP:554/user=admin&password=&channel=1&stream=0.sdp?real_stream

# Substream (niedrige Last):
rtsp://admin:@KAMERA-IP:554/user=admin&password=&channel=1&stream=1.sdp?real_stream

# Fallback-Pfade (automatisch erkannt):
rtsp://KAMERA-IP:554/live/ch00_0
rtsp://KAMERA-IP:554/h264
rtsp://KAMERA-IP:554/stream0
```

---

### 🔄 Schalter – `switch` (4 Entitäten)

| Entität | Entity-ID-Suffix | Beschreibung |
|---|---|---|
| **Aufnahme** | `_recording` | SD-Karten-Aufnahme starten / stoppen (XM SDK oder HTTP) |
| **WDR** | `_wdr` | Wide Dynamic Range ein-/ausschalten (ONVIF Imaging) |
| **IR-Cut** | `_ir_cut` | Infrarot-Sperrfilter manuell schalten (ONVIF Imaging) |
| **Mikrofon** | `_microphone` | Kamera-Mikrofon aktivieren / deaktivieren (ONVIF Audio) |

---

### 🚨 Binärsensoren – `binary_sensor` (3 Entitäten)

| Entität | Geräteklasse | Beschreibung |
|---|---|---|
| **Bewegung** | `motion` | Bewegungserkennung – aktiv für 30 s, Attribut `last_motion` |
| **Manipulation** | `tamper` | Kamera-Sabotagedetektor via ONVIF Events |
| **Signalverlust** | `problem` | Videosignal-Verlust via ONVIF Events |

Events werden über ONVIF Pull-Point in Echtzeit empfangen.

---

### 🔘 Buttons – `button` (21 Entitäten)

#### PTZ-Richtungen (6)

| Button | Befehl | Icon |
|---|---|---|
| PTZ Hoch | `up` | `mdi:arrow-up-bold-circle` |
| PTZ Runter | `down` | `mdi:arrow-down-bold-circle` |
| PTZ Links | `left` | `mdi:arrow-left-bold-circle` |
| PTZ Rechts | `right` | `mdi:arrow-right-bold-circle` |
| PTZ Zoom + | `zoom_in` | `mdi:magnify-plus` |
| PTZ Zoom − | `zoom_out` | `mdi:magnify-minus` |

#### PTZ-Steuerung (3)

| Button | Beschreibung |
|---|---|
| **PTZ Home** | Heimposition anfahren (`GotoHomePosition`) |
| **PTZ Home setzen** | Aktuelle Position als Heimposition speichern (`SetHomePosition`) |
| **PTZ Stopp** | Laufende Bewegung sofort anhalten (`Stop`) |

#### PTZ-Presets (8 – je 4 Speichern / Anfahren)

| Button | Beschreibung |
|---|---|
| PTZ Preset 1–4 speichern | Aktuelle Kameraposition unter Slot 1–4 ablegen |
| PTZ Preset 1–4 anfahren | Gespeicherte Position 1–4 direkt ansteuern |

#### System (3)

| Button | Beschreibung |
|---|---|
| **Snapshot** | Aktuelles Standbild abrufen |
| **Neustart** | Kamera neu starten (`SystemReboot`) |
| **NTP-Sync** | Kamera-Zeit mit NTP-Server synchronisieren |

---

### 📊 Sensoren – `sensor` (6 Entitäten)

| Entität | Beschreibung |
|---|---|
| **Dateiliste** | Anzahl Dateien auf der SD-Karte; Attribut `files` enthält vollständige Liste |
| **Firmware** | Firmware-Version der Kamera |
| **Seriennummer** | Seriennummer des Geräts |
| **MAC-Adresse** | Netzwerk-Hardwareadresse |
| **Kamerazeit** | Aktuelle Systemzeit der Kamera |
| **Aktiver Stream** | Zeigt ob Haupt- (`000`) oder Substream (`001`) aktiv ist |

---

### 🔢 Zahlen (Schieberegler) – `number` (8 Entitäten)

| Entität | Bereich | Einheit | Beschreibung |
|---|---|---|---|
| **Helligkeit** | 0–100 | % | ONVIF Imaging Brightness |
| **Kontrast** | 0–100 | % | ONVIF Imaging Contrast |
| **Sättigung** | 0–100 | % | ONVIF Imaging Saturation |
| **Schärfe** | 0–15 | – | ONVIF Imaging Sharpness |
| **WDR-Stärke** | 0–100 | % | Wide Dynamic Range Level |
| **Weißabgleich CrGain** | 0–100 | – | Chrominanz-Rot-Gain (manueller WB) |
| **Weißabgleich CbGain** | 0–100 | – | Chrominanz-Blau-Gain (manueller WB) |
| **PTZ-Geschwindigkeit** | 1–8 | – | Bewegungsgeschwindigkeit für alle PTZ-Befehle |

---

### 🔽 Auswahllisten – `select` (6 Entitäten)

| Entität | Optionen | Beschreibung |
|---|---|---|
| **PTZ-Preset anfahren** | *(dynamisch aus Kamera)* | Dropdown über alle gespeicherten ONVIF-Presets |
| **IR-Modus** | AUTO / ON / OFF | Infrarot-Filter-Steuerung |
| **Belichtungs-Modus** | AUTO / MANUAL | Kamera-Belichtungsregelung |
| **Belichtungs-Priorität** | LowNoise / FrameRate | Bildqualität vs. Framerate |
| **Weißabgleich** | AUTO / MANUAL | Farbtemperatur-Regelung |
| **Stream-Qualität** | Hauptstream (H265 1080p) / Substream (H265 360p) | ONVIF-Profil umschalten |

---

### Entitäten-Zusammenfassung

| Plattform | Entitäten |
|---|---|
| `camera` | 1 |
| `switch` | 4 |
| `binary_sensor` | 3 |
| `button` | 21 |
| `sensor` | 6 |
| `number` | 8 |
| `select` | 6 |
| **Gesamt** | **49** |

---

## Protokolle

| Protokoll | Schlüssel | Beschreibung |
|---|---|---|
| **RTSP** | `rtsp` | Standard-Videostream; PTZ über HTTP-CGI-Fallback |
| **HTTP only** | `http_only` | Nur Snapshots, kein RTSP |
| **XM SDK** | `xm_sdk` | Proprietäres XM/Tenganda-Protokoll (Port 34567); PTZ, Aufnahme, Dateiliste |
| **ONVIF** | `onvif` | ONVIF-Profil S/T; PTZ SOAP, Events, Imaging, Audio |

---

## ONVIF-Details

### PTZ-Routing (ONVIF)

Alle 21 PTZ-Buttons nutzen bei `protocol=onvif` direkte SOAP-Aufrufe:

| Methode | SOAP-Befehl |
|---|---|
| Richtungstasten | `ContinuousMove` mit `PanTilt`-Velocity |
| Zoom | `ContinuousMove` mit `Zoom`-Velocity |
| Stopp | `Stop` |
| Heimposition | `GotoHomePosition` |
| Preset anfahren | `GotoPreset` |
| Preset speichern | `SetPreset` |

Geschwindigkeit: PTZ-Geschwindigkeit 1–8 wird auf 0.125–1.0 skaliert.  
Fallback: Bei SOAP-Fehler automatisch auf python-onvif library.

### Events (Pull-Point)

- ONVIF Pull-Point Subscription für Motion, Tamper, Signal Loss
- Automatischer Reconnect mit exponentiellem Backoff (1 s → max. 30 s)
- Authentifizierung: WS-Security UsernameToken (SHA-1 WSSE-Digest)

### Verfügbare ONVIF-Dienste

| Dienst | Endpunkt |
|---|---|
| Device | `/onvif/device_service` |
| Media | Über python-onvif library |
| PTZ | `/onvif/PTZ` |
| Imaging | `/onvif/imaging` |
| Events | `/onvif/events` |

---

## Netzwerk-Routing

### Kamera im Heimnetz (empfohlen)

```
Kamera ←→ Router (feste IP via DHCP) ←→ Home Assistant
```

### ADB-Proxy (Android-Gerät als Tunnel)

```bash
# USB-Debugging aktivieren, dann:
adb forward tcp:8080 tcp:554   # RTSP-Tunnel
adb forward tcp:8081 tcp:80    # HTTP-Tunnel

# In HA konfigurieren:
# Host: 127.0.0.1 · RTSP-Port: 8080 · HTTP-Port: 8081
```

### PiHole-Sperrliste (Cloud-Dienste blockieren)

```
*.tange365.com
*.icam365.com
*.p2p*.net
*.tutk.com        # P2P SDK Throughtek
*.iotcplatform.com # P2P SDK iOTC
```

---

## Entwicklung & Tests

```bash
# Abhängigkeiten installieren
pip install -r requirements-dev.txt

# Alle Tests ausführen
pytest

# Mit Ausgabe
pytest -v

# Einzelne Datei
pytest tests/test_coordinator.py -v
```

### Testabdeckung (110 Tests · 0 Fehler)

| Testdatei | Beschreibung |
|---|---|
| `test_config_flow_and_init.py` | Config Flow, Options Flow, Entry-Setup |
| `test_coordinator.py` | Coordinator-Logik, ONVIF-Init, State-Handling |
| `test_entities.py` | Alle Entitäten-Plattformen |
| `test_http_fallback.py` | HTTP-Retry- und Fallback-Verhalten |
| `test_init_lifecycle.py` | Laden/Entladen der Integration |
| `test_platform_setup.py` | Plattform-Registrierung |
| `test_ptz_filelist_onvif.py` | PTZ-Befehle, Dateiliste, ONVIF-Methoden |
| `test_xm_protocol.py` | XM SDK Protokoll-Kommunikation |

---

## Links

- [Issue Tracker](https://github.com/beastwareteam/wjg-camera-ha/issues)
- [Dokumentation](https://github.com/beastwareteam/wjg-camera-ha/tree/main/custom_components/wjg_camera)
- [HACS](https://hacs.xyz/)

---

*Version 2.1.0 · Hersteller: WJG / Tenganda · Modell: XM-3820 · IoT-Klasse: local_polling*
