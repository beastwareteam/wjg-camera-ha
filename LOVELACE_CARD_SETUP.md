# Lovelace Custom Card Setup — WJG Camera Card 2

## Problem
Die Custom Card `wjg-camera-card` funktioniert nicht im Dashboard, weil HA sie nicht kennt.

**Fehler im Dashboard:**
```
Custom element doesn't exist: custom:wjg-camera-card
```

---

## Lösung: Manuelle Registrierung in Home Assistant

### Schritt 1: Zu den Lovelace-Ressourcen gehen
```
Home Assistant
  → Settings (⚙️)
  → Dashboards
  → Lovelace-Ressourcen (rechts oben, wenn "Lovelace"-Überschrift sichtbar)
  → "+ Ressource"
```

### Schritt 2: URL eintragen
Verwende die vollständige URL zur Card:

**Für `wjg-camera-card.js` (v3.2 mit PTZ + Zoom):**
```
/local/wjg_camera/wjg-camera-card.js
```

**Typ wählen:**
```
Ressourcentyp: JavaScript Module
```

### Schritt 3: Speichern & Seite aktualisieren
1. **Speichern** klicken
2. **F5** drücken (oder Seite im Browser aktualisieren)
3. HA sollte die Card jetzt erkennen

---

## Alternative: Direkt im Dashboard-YAML

Falls du die Ressource im YAML eintragen möchtest (nicht über UI):

**2. `configuration.yaml` öffnen** (oder `lovelace.yaml`)

**2. Folgendes hinzufügen:**
```yaml
# Am Anfang der Datei
resources:
  - url: /local/wjg_camera/wjg-camera-card.js
    type: module
```

**3. HA neustarten**

---

## Wenn immer noch nicht funktioniert

### Problem: "Module nicht gefunden"

**Überprüfe:**
1. **Integration neu laden:**
   - Settings → Devices & Services
   - WJG Camera Bridge → ⋮ → Reload

2. **Browser-Cache leeren:**
   - Strg+Shift+Delete (Chrome/Firefox)
   - Oder: Privates Fenster öffnen

3. **Server-Logs überprüfen:**
   - Settings → System → Logs
   - Filtere nach "wjg_camera"
   - Suche nach HTTP 404 Fehlern für `/wjg_camera/wjg-camera-card2.js`

### Problem: Karte lädt, zeigt aber nur "Fehler"

**Das bedeutet:** JavaScript-Fehler in der Card.

1. **Developer-Console öffnen:** F12
2. **Console-Tab** wählen
3. **Fehler anschauen** — kopier die Fehlermeldung

---

## Schnelle Test-YAML (ohne Registrierung)

Falls du die Card nur **kurz testen** möchtest ohne komplettes Dashboard:

```yaml
type: custom:wjg-camera-card
entity: camera.wjg_xm_3820
title: "Test"
show_ptz: true
show_zoom_bar: true
```

Wenn diese YAML funktioniert → die Card ist korrekt registriert ✅

---

## Support

Falls die Card immer noch nicht funktioniert:

1. **Prüfe:** Existiert die Datei wirklich?
   ```bash
   ls -la custom_components/wjg_camera/www/wjg-camera-card2.js
   ```

2. **Prüfe:** Ist der static path registriert?
   - Gehe zu `http://192.168.1.x:8123/local/wjg_camera/wjg-camera-card.js`
   - Falls **404** → Datei nicht erreichbar
   - Falls **JavaScript-Code** sichtbar → OK ✅

3. **Integration neu laden:**
   ```
   Settings → Devices & Services → WJG Camera → Reload
   ```

---

## Erwartete Funktionalität

Wenn die Card richtig funktioniert, siehst du:

```
┌─────────────────────────────────────────┐
│  WJG XM-3820 Live                       │
├─────────────────────────────────────────┤
│                                         │
│    [RTSP Live-Stream / Snapshot]        │
│                                         │
│  ┌────────────────────────────────────┐ │
│  │ ← → ↑ ↓ (PTZ Overlay Buttons)     │ │
│  │ + − (Zoom Buttons)                 │ │
│  └────────────────────────────────────┘ │
│                                         │
│  [0.5x] [═════════] [2.0x] (Zoom-Bar) │
│                                         │
│  Bewegung 🟢 Manipulation 🔴 Signal 🟢 │
│                                         │
└─────────────────────────────────────────┘
```

---

**Dokumentation:** 23. Mai 2026  
**Card-Version:** v3.2 (Maximum Edition)  
**Kompabilität:** Home Assistant 2024.x+
