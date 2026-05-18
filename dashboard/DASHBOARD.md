# WJG XM-3820 — Dashboard Setup & Konfiguration

> **Integration:** `wjg_camera` · **Version:** 2.2.23+  
> **Kamera:** WJG XM-3820 · **IP:** 192.168.178.49 · **ONVIF-Port:** 8899

---

## Inhaltsverzeichnis

1. [Voraussetzungen](#1-voraussetzungen)
2. [Lovelace-Ressource registrieren](#2-lovelace-ressource-registrieren)
3. [Dashboard importieren](#3-dashboard-importieren)
4. [Karten-Referenz](#4-karten-referenz)
5. [wjg-camera-card Konfiguration](#5-wjg-camera-card-konfiguration)
6. [Vollständige Dashboard-YAML](#6-vollständige-dashboard-yaml)
7. [Bedienung Zoom & Resize](#7-bedienung-zoom--resize)
8. [Entitäten-Übersicht](#8-entitäten-übersicht)
9. [Problemlösung](#9-problemlösung)

---

## 1. Voraussetzungen

| Schritt | Status |
|---|---|
| Integration `wjg_camera` über HACS installiert | ✅ |
| Integration geladen und Kamera erreichbar | ✅ |
| HA Neustart nach Installation | ✅ |
| Lovelace-Ressource registriert (→ Schritt 2) | ⬜ |

---

## 2. Lovelace-Ressource registrieren

Die Zoom-Karte (`wjg-camera-card`) muss **einmalig** als Lovelace-Ressource hinzugefügt werden.

### Weg A — Über HA-Oberfläche (empfohlen)

1. **Einstellungen** → **Dashboards** → oben rechts **⋮** → **Ressourcen**
2. **+ Ressource hinzufügen**
3. Eingaben:

   | Feld | Wert |
   |---|---|
   | URL | `/wjg_camera/wjg-camera-card.js` |
   | Ressourcentyp | **JavaScript Modul** |

4. **Erstellen** → Seite neu laden (F5)

### Weg B — Über `configuration.yaml`

```yaml
lovelace:
  resources:
    - url: /wjg_camera/wjg-camera-card.js
      type: module
```

→ HA neu starten.

> **Hinweis:** Nach jedem Update der Integration (neue Version) Browser-Cache leeren:  
> `Strg+Shift+R` (Windows/Linux) · `Cmd+Shift+R` (Mac)

---

## 3. Dashboard importieren

### Neues Dashboard anlegen

1. **Einstellungen** → **Dashboards** → **+ Dashboard hinzufügen**
2. Titel: `WJG Kamera` · Icon: `mdi:cctv` · URL: `wjg-kamera`
3. Dashboard öffnen → **Bearbeiten** (Stift-Symbol) → **⋮** → **YAML-Editor**
4. Gesamten Inhalt aus [Abschnitt 6](#6-vollständige-dashboard-yaml) einfügen → **Speichern**

### Bestehendes Dashboard ersetzen

Dashboard öffnen → **Bearbeiten** → **⋮** → **YAML-Editor** → Inhalt ersetzen.

---

## 4. Karten-Referenz

### Ansichten im Dashboard

| Tab | Inhalt |
|---|---|
| **Live** `mdi:video` | Stream + PTZ-Steuerung + Schnellaktionen + Status |
| **PTZ** `mdi:camera-control` | Vollständige PTZ-Steuerung + Geschwindigkeit + Presets |
| **Bild** `mdi:image-edit` | IR/Nacht, Helligkeit, Kontrast, WDR, Belichtung, Weißabgleich |
| **Stream** `mdi:video-wireless` | Qualität, Mikrofon, RTSP-URL, Protokoll |
| **Sicherheit** `mdi:shield-check` | Sensoren, Logbuch, Verlaufsgraph |
| **System** `mdi:cog-outline` | Firmware, Seriennummer, SD-Karte, Neustart, NTP |

---

## 5. wjg-camera-card Konfiguration

Die Karte ersetzt `picture-glance` vollständig — **nur ein Stream** wird geladen.

### Minimale Konfiguration

```yaml
type: custom:wjg-camera-card
entity: camera.wjg_xm_3820
```

### Vollständige Konfiguration mit allen Optionen

```yaml
type: custom:wjg-camera-card
entity: camera.wjg_xm_3820
title: WJG XM-3820 Live        # Titelzeile über dem Stream (weglassen = kein Titel)
show_zoom_bar: true             # Zoom-Leiste unten anzeigen (default: true)
badges:                         # Status-Icons als Overlay oben rechts
  - entity: binary_sensor.wjg_xm_3820_bewegung
    icon: mdi:motion-sensor     # Orange wenn aktiv
  - entity: binary_sensor.wjg_xm_3820_manipulation
    icon: mdi:shield-alert      # Orange wenn aktiv
  - entity: switch.wjg_xm_3820_aufnahme
    icon: mdi:record-circle     # Rot wenn Aufnahme läuft
```

### Badge-Farben

| Entitätstyp | Zustand `on` | Zustand `off` |
|---|---|---|
| `binary_sensor.*` | 🟠 Orange | ⚫ Dunkel |
| `switch.*` | 🔴 Rot | ⚫ Dunkel |

---

## 6. Vollständige Dashboard-YAML

> Diesen Block komplett kopieren und im YAML-Editor des Dashboards einfügen.

```yaml
title: WJG Kamera
path: wjg-kamera
icon: mdi:cctv
views:

  # ════════════════════════════════════════════════════════════════════════════
  # TAB 1 — LIVE
  # ════════════════════════════════════════════════════════════════════════════
  - title: Live
    path: live
    icon: mdi:video
    badges:
      - entity: binary_sensor.wjg_xm_3820_bewegung
        name: Bewegung
      - entity: binary_sensor.wjg_xm_3820_manipulation
        name: Tamper
      - entity: binary_sensor.wjg_xm_3820_signalverlust
        name: Signal
      - entity: switch.wjg_xm_3820_aufnahme
        name: Aufnahme
    cards:

      # Kamera-Stream mit Zoom, Badges und Resize-Handles
      # (ersetzt picture-glance — nur EIN Stream!)
      - type: custom:wjg-camera-card
        entity: camera.wjg_xm_3820
        title: WJG XM-3820 Live
        badges:
          - entity: binary_sensor.wjg_xm_3820_bewegung
            icon: mdi:motion-sensor
          - entity: binary_sensor.wjg_xm_3820_manipulation
            icon: mdi:shield-alert
          - entity: switch.wjg_xm_3820_aufnahme
            icon: mdi:record-circle

      # PTZ-Steuerung 3×3
      - square: false
        type: grid
        title: PTZ-Steuerung
        columns: 3
        cards:
          - type: button
            icon: mdi:home-map-marker
            name: Home
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_home
          - type: button
            icon: mdi:arrow-up-bold-circle
            name: Hoch
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_up
          - type: button
            icon: mdi:magnify-plus
            name: Zoom +
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_zoom_in
          - type: button
            icon: mdi:arrow-left-bold-circle
            name: Links
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_left
          - type: button
            icon: mdi:stop-circle
            name: Stopp
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_stopp
          - type: button
            icon: mdi:arrow-right-bold-circle
            name: Rechts
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_right
          - type: button
            icon: mdi:arrow-down-bold-circle
            name: Runter
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_down
          - type: button
            icon: mdi:home-edit
            name: Home setzen
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_home_setzen
          - type: button
            icon: mdi:magnify-minus
            name: Zoom -
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_zoom_out

      # Schnellaktionen
      - type: horizontal-stack
        cards:
          - type: button
            name: Snapshot
            icon: mdi:camera-iris
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_snapshot
          - type: button
            name: Aufnahme EIN
            icon: mdi:record
            tap_action:
              action: call-service
              service: switch.turn_on
              target:
                entity_id: switch.wjg_xm_3820_aufnahme
          - type: button
            name: Aufnahme AUS
            icon: mdi:stop
            tap_action:
              action: call-service
              service: switch.turn_off
              target:
                entity_id: switch.wjg_xm_3820_aufnahme

      # Status-Zeile
      - type: horizontal-stack
        cards:
          - type: entity
            entity: switch.wjg_xm_3820_aufnahme
            name: Aufnahme
            icon: mdi:record-circle
            state_color: true
            tap_action:
              action: toggle
          - type: entity
            entity: binary_sensor.wjg_xm_3820_bewegung
            name: Bewegung
            state_color: true
          - type: entity
            entity: binary_sensor.wjg_xm_3820_manipulation
            name: Tamper
            state_color: true
          - type: entity
            entity: binary_sensor.wjg_xm_3820_signalverlust
            name: Signal
            state_color: true

  # ════════════════════════════════════════════════════════════════════════════
  # TAB 2 — PTZ
  # ════════════════════════════════════════════════════════════════════════════
  - title: PTZ
    path: ptz
    icon: mdi:camera-control
    cards:

      - type: grid
        title: PTZ-Steuerung
        columns: 3
        square: true
        cards:
          - type: button
            icon: mdi:arrow-up-bold-circle
            name: Hoch
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_up
          - type: button
            icon: mdi:home-map-marker
            name: Home
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_home
          - type: button
            icon: mdi:magnify-plus
            name: Zoom +
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_zoom_in
          - type: button
            icon: mdi:arrow-left-bold-circle
            name: Links
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_left
          - type: button
            icon: mdi:stop-circle
            name: Stopp
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_stopp
          - type: button
            icon: mdi:arrow-right-bold-circle
            name: Rechts
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_right
          - type: button
            icon: mdi:arrow-down-bold-circle
            name: Runter
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_down
          - type: button
            icon: mdi:home-edit
            name: Home setzen
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_home_setzen
          - type: button
            icon: mdi:magnify-minus
            name: Zoom -
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_zoom_out

      - type: entities
        title: PTZ-Geschwindigkeit
        entities:
          - entity: number.wjg_xm_3820_ptz_geschwindigkeit
            name: Geschwindigkeit (1–8)

      - type: entities
        title: Preset anfahren
        entities:
          - entity: select.wjg_xm_3820_ptz_preset_anfahren
            name: Preset wählen & anfahren

      - type: grid
        title: Presets speichern
        columns: 4
        square: false
        cards:
          - type: button
            name: Preset 1 speichern
            icon: mdi:map-marker-plus
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_preset_1_speichern
          - type: button
            name: Preset 2 speichern
            icon: mdi:map-marker-plus
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_preset_2_speichern
          - type: button
            name: Preset 3 speichern
            icon: mdi:map-marker-plus
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_preset_3_speichern
          - type: button
            name: Preset 4 speichern
            icon: mdi:map-marker-plus
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_preset_4_speichern

      - type: grid
        title: Presets direkt anfahren
        columns: 4
        square: false
        cards:
          - type: button
            name: Preset 1
            icon: mdi:map-marker-radius
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_preset_1_anfahren
          - type: button
            name: Preset 2
            icon: mdi:map-marker-radius
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_preset_2_anfahren
          - type: button
            name: Preset 3
            icon: mdi:map-marker-radius
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_preset_3_anfahren
          - type: button
            name: Preset 4
            icon: mdi:map-marker-radius
            tap_action:
              action: call-service
              service: button.press
              target:
                entity_id: button.wjg_xm_3820_ptz_preset_4_anfahren

  # ════════════════════════════════════════════════════════════════════════════
  # TAB 3 — BILD
  # ════════════════════════════════════════════════════════════════════════════
  - title: Bild
    path: bild
    icon: mdi:image-edit
    cards:
      - type: entities
        title: IR & Nachtmodus
        entities:
          - entity: switch.wjg_xm_3820_ir_cut_nachtmodus
            name: IR-Cut manuell (Nacht)
          - entity: select.wjg_xm_3820_ir_modus
            name: IR-Modus (Auto/Tag/Nacht)
      - type: entities
        title: Bildparameter
        entities:
          - entity: number.wjg_xm_3820_helligkeit
            name: Helligkeit
          - entity: number.wjg_xm_3820_kontrast
            name: Kontrast
          - entity: number.wjg_xm_3820_saettigung
            name: Sättigung
          - entity: number.wjg_xm_3820_schaerfe
            name: Schärfe
      - type: entities
        title: WDR (Weitbereichsdynamik)
        entities:
          - entity: switch.wjg_xm_3820_wdr
            name: WDR aktiv
          - entity: number.wjg_xm_3820_wdr_starke
            name: WDR-Stärke
      - type: entities
        title: Belichtung
        entities:
          - entity: select.wjg_xm_3820_belichtungs_modus
            name: Modus
          - entity: select.wjg_xm_3820_belichtungs_prioritat
            name: Priorität
      - type: entities
        title: Weißabgleich
        entities:
          - entity: select.wjg_xm_3820_weissabgleich
            name: Modus
          - entity: number.wjg_xm_3820_weissabgleich_crgain
            name: CrGain
          - entity: number.wjg_xm_3820_weissabgleich_cbgain
            name: CbGain

  # ════════════════════════════════════════════════════════════════════════════
  # TAB 4 — STREAM
  # ════════════════════════════════════════════════════════════════════════════
  - title: Stream
    path: stream
    icon: mdi:video-wireless
    cards:
      - type: entities
        title: Stream-Konfiguration
        entities:
          - entity: select.wjg_xm_3820_stream_qualitat
            name: Stream-Qualität
          - entity: sensor.wjg_xm_3820_aktiver_stream
            name: Aktiv
          - entity: switch.wjg_xm_3820_mikrofon
            name: Mikrofon
      - type: entities
        title: Verbindungsdetails
        entities:
          - entity: camera.wjg_xm_3820
            name: Protokoll
            attribute: protocol
            icon: mdi:protocol
          - entity: camera.wjg_xm_3820
            name: RTSP-URL
            attribute: rtsp_url
            icon: mdi:video-wireless

  # ════════════════════════════════════════════════════════════════════════════
  # TAB 5 — SICHERHEIT
  # ════════════════════════════════════════════════════════════════════════════
  - title: Sicherheit
    path: sicherheit
    icon: mdi:shield-check
    cards:
      - type: entities
        title: Sensor-Status
        entities:
          - entity: binary_sensor.wjg_xm_3820_bewegung
            name: Bewegungserkennung
          - entity: binary_sensor.wjg_xm_3820_manipulation
            name: Kamera-Manipulation
          - entity: binary_sensor.wjg_xm_3820_signalverlust
            name: Videosignal-Verlust
      - type: logbook
        title: Kamera-Ereignisse (48h)
        hours_to_show: 48
        entities:
          - camera.wjg_xm_3820
          - binary_sensor.wjg_xm_3820_bewegung
          - binary_sensor.wjg_xm_3820_manipulation
          - switch.wjg_xm_3820_aufnahme
      - type: history-graph
        title: Sicherheits-Verlauf (24h)
        hours_to_show: 24
        entities:
          - entity: binary_sensor.wjg_xm_3820_bewegung
            name: Bewegung
          - entity: binary_sensor.wjg_xm_3820_manipulation
            name: Tamper
          - entity: binary_sensor.wjg_xm_3820_signalverlust
            name: Signal

  # ════════════════════════════════════════════════════════════════════════════
  # TAB 6 — SYSTEM
  # ════════════════════════════════════════════════════════════════════════════
  - title: System
    path: system
    icon: mdi:cog-outline
    cards:
      - type: entities
        title: Kamera-Informationen
        entities:
          - entity: sensor.wjg_xm_3820_firmware
            name: Firmware
            icon: mdi:chip
          - entity: sensor.wjg_xm_3820_seriennummer
            name: Seriennummer
          - entity: sensor.wjg_xm_3820_mac_adresse
            name: MAC-Adresse
          - entity: sensor.wjg_xm_3820_kamera_uhrzeit
            name: Kamera-Uhrzeit
      - type: entities
        title: SD-Karte
        entities:
          - entity: sensor.wjg_xm_3820_dateiliste
            name: Anzahl Aufnahmen
      - type: history-graph
        title: Aufnahme-Aktivität (24h)
        hours_to_show: 24
        entities:
          - entity: switch.wjg_xm_3820_aufnahme
            name: Aufnahme
      - type: entities
        title: System-Aktionen
        entities:
          - type: button
            entity: button.wjg_xm_3820_kamera_neu_starten
            name: Kamera neu starten
            icon: mdi:restart
          - type: button
            entity: button.wjg_xm_3820_ntp_synchronisieren
            name: NTP synchronisieren
            icon: mdi:clock-check
```

---

## 7. Bedienung Zoom & Resize

### Zoom

| Aktion | Desktop | Mobile |
|---|---|---|
| Reinzoomen | Mausrad nach oben | Zwei Finger auseinander (Pinch) |
| Rauszoomen | Mausrad nach unten | Zwei Finger zusammen |
| Verschieben | Linke Maustaste gedrückt halten + ziehen | Ein Finger ziehen (wenn gezoomt) |
| Preset 1× / 2× / 4× / 8× | Buttons in der Zoom-Leiste | Buttons tippen |
| Zoom zurücksetzen | Button **↺** | Button **↺** tippen |

> Zoom-Buttons (Zoom + / Zoom −) im PTZ-Grid steuern den **Digital-Zoom** des Servers  
> (Pillow-Crop des Snapshots + Sync mit der Lovelace-Karte).

### Resize — Kartengröße anpassen

| Handle | Position | Funktion |
|---|---|---|
| Unterkante | Unterer Rand, Mitte | Höhe vergrößern / verkleinern |
| Rechte Kante | Rechter Rand, Mitte | Breite → Höhe wird proportional angepasst |
| Ecke | Unten rechts (Grip-Dreieck) | Beides gleichzeitig |

Die eingestellte Größe wird im Browser-LocalStorage gespeichert und bleibt nach dem Reload erhalten.  
**Gespeichert pro Entity-ID** — mehrere Karten können unterschiedliche Größen haben.

---

## 8. Entitäten-Übersicht

### Kamera

| Entity | Beschreibung |
|---|---|
| `camera.wjg_xm_3820` | Haupt-Kamera-Entity (Stream + Snapshot) |

### Buttons

| Entity | Beschreibung |
|---|---|
| `button.wjg_xm_3820_ptz_up` | PTZ Hoch |
| `button.wjg_xm_3820_ptz_down` | PTZ Runter |
| `button.wjg_xm_3820_ptz_left` | PTZ Links |
| `button.wjg_xm_3820_ptz_right` | PTZ Rechts |
| `button.wjg_xm_3820_ptz_zoom_in` | Digital Zoom + (Stufe ×1.5) |
| `button.wjg_xm_3820_ptz_zoom_out` | Digital Zoom − (Stufe ÷1.5) |
| `button.wjg_xm_3820_ptz_stopp` | PTZ Bewegung stoppen |
| `button.wjg_xm_3820_ptz_home` | Home-Position anfahren |
| `button.wjg_xm_3820_ptz_home_setzen` | Aktuelle Position als Home speichern |
| `button.wjg_xm_3820_ptz_preset_1_speichern` … `_4_` | Preset 1–4 speichern |
| `button.wjg_xm_3820_ptz_preset_1_anfahren` … `_4_` | Preset 1–4 anfahren |
| `button.wjg_xm_3820_digital_zoom_reset` | Digital-Zoom auf 1× zurücksetzen |
| `button.wjg_xm_3820_snapshot` | Snapshot auslösen |
| `button.wjg_xm_3820_kamera_neu_starten` | Kamera-Neustart |
| `button.wjg_xm_3820_ntp_synchronisieren` | NTP-Zeit synchronisieren |

### Sensoren & Schalter

| Entity | Beschreibung |
|---|---|
| `binary_sensor.wjg_xm_3820_bewegung` | Bewegungserkennung |
| `binary_sensor.wjg_xm_3820_manipulation` | Kamera-Tamper |
| `binary_sensor.wjg_xm_3820_signalverlust` | Videosignal-Verlust |
| `switch.wjg_xm_3820_aufnahme` | Aufnahme EIN/AUS |
| `switch.wjg_xm_3820_ir_cut_nachtmodus` | IR-Cut manuell |
| `switch.wjg_xm_3820_wdr` | WDR aktiv |
| `switch.wjg_xm_3820_mikrofon` | Mikrofon |

### Number / Select / Sensor

| Entity | Beschreibung |
|---|---|
| `number.wjg_xm_3820_ptz_geschwindigkeit` | PTZ-Geschwindigkeit 1–8 |
| `number.wjg_xm_3820_helligkeit` | Helligkeit 0–100 |
| `number.wjg_xm_3820_kontrast` | Kontrast 0–100 |
| `number.wjg_xm_3820_saettigung` | Sättigung 0–100 |
| `number.wjg_xm_3820_schaerfe` | Schärfe 0–15 |
| `number.wjg_xm_3820_wdr_starke` | WDR-Stärke 0–100 |
| `number.wjg_xm_3820_weissabgleich_crgain` | WB CrGain 0–100 |
| `number.wjg_xm_3820_weissabgleich_cbgain` | WB CbGain 0–100 |
| `select.wjg_xm_3820_ir_modus` | IR-Modus Auto/Tag/Nacht |
| `select.wjg_xm_3820_belichtungs_modus` | Belichtungsmodus |
| `select.wjg_xm_3820_belichtungs_prioritat` | Belichtungspriorität |
| `select.wjg_xm_3820_weissabgleich` | Weißabgleich-Modus |
| `select.wjg_xm_3820_stream_qualitat` | Stream-Qualität |
| `select.wjg_xm_3820_ptz_preset_anfahren` | Preset-Auswahl & Anfahren |
| `sensor.wjg_xm_3820_firmware` | Firmware-Version |
| `sensor.wjg_xm_3820_seriennummer` | Seriennummer |
| `sensor.wjg_xm_3820_mac_adresse` | MAC-Adresse |
| `sensor.wjg_xm_3820_kamera_uhrzeit` | Kamera-Systemzeit |
| `sensor.wjg_xm_3820_aktiver_stream` | Aktiver Stream-Kanal |
| `sensor.wjg_xm_3820_dateiliste` | Anzahl Aufnahmen auf SD |

### HA-Service

| Service | Parameter | Beschreibung |
|---|---|---|
| `wjg_camera.set_digital_zoom` | `entity_id`, `zoom` (1–10), `cx` (0–1), `cy` (0–1) | Digital-Zoom + Mittelpunkt setzen |

---

## 9. Problemlösung

### Karte wird nicht gefunden (`custom:wjg-camera-card unknown`)

→ Lovelace-Ressource nicht registriert. Siehe [Schritt 2](#2-lovelace-ressource-registrieren).  
→ Browser-Cache leeren: `Strg+Shift+R`

### Kein Stream sichtbar

1. HA-Log prüfen: **Einstellungen → System → Protokolle** → nach `wjg_camera` suchen
2. Kamera per Ping erreichbar? `ping 192.168.178.49`
3. Integration neu laden: **Einstellungen → Geräte & Dienste → WJG Camera → ⋮ → Neu laden**

### PTZ reagiert nicht

1. HA-Log auf `XMSoapClient` filtern
2. HTTP 400 → IP-Lockout der Kamera → **Kamera neu starten** (Button oder Strom trennen)
3. Nach Neustart Integration neu laden

### Zoom-Buttons ändern nichts am Stream

Zoom-Buttons (Zoom+ / Zoom−) steuern den **Digital-Zoom des Snapshots** (server-seitig).  
Der **Live-Stream** wird nur durch die interaktiven Zoom-Controls der `wjg-camera-card` beeinflusst (CSS-Transform, client-seitig).

### Karte-Größe nach Reload zurückgesetzt

LocalStorage wurde gelöscht (z. B. Browser-Datenschutz-Modus). Im normalen Modus bleibt die Größe erhalten.

---

*Letzte Aktualisierung: Version 2.2.23 · WJG Camera Bridge*
