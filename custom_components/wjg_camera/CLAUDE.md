# WJG XM-3820 Camera Bridge — Kritisches Wissen für Claude

## Motion Detection via ONVIF PullPoint (Fix v2.2.25 — Mai 2026)

### Problem
`binary_sensor.motion_detected` blieb immer OFF, obwohl die Kamera-Hardware Bewegung erkannte.

### Ursache
Die Kamera verlangt WSSE-Auth für `CreatePullPointSubscription` (HTTP 400 ohne Auth).
`async_onvif_create_pullpoint()` nutzte `_onvif_soap_for()` mit `_onvif_wsse_enabled=False`
→ keine Auth → HTTP 400 → Event-Loop schlug still fehl → kein Motion-Event in HA.

### Lösung (identisch zum PTZ-Fix)
`async_onvif_create_pullpoint()` nutzt jetzt `_XMSoapClient()` mit frischer WSSE-Session.
`async_onvif_pull_messages_once()` nutzt jetzt `use_auth=True`.

### Port-Situation (bestätigt 29.05.2026)
- Port 34567 (XM-SDK/DVRIP): `ConnectionRefused` — Dienst läuft NICHT auf der Kamera
- Port 15668 (alternatives DVRIP): `ConnectionRefused` — ebenfalls nicht vorhanden
- SD-Karte Dateiliste: **unmöglich** ohne diese Ports (Firmware-Entscheidung)
- python-dvr würde dasselbe Problem haben (gleiches Protokoll, gleiche Ports)
- Kamera-UID aus pcapng: `16000102c0abce2bos9ixucsiajt20ch`

### Netzwerk-Captures (pcapng-Analyse)
Die Captures zeigen ein ANDERES XM-Gerät (IP .31) das Port 15668 nutzte — nicht unsere Kamera.
Unsere Kamera (.49) hat Port 34567/15668 schlicht nicht geöffnet.

---

## Das PTZ-Problem und die Lösung (Mai 2025)

### Symptom
PTZ-Befehle schlagen mit HTTP 400 fehl:
```
The security token could not be authenticated or authorized
```

### Ursache: IP-basiertes Session-Caching der XM/Xiongmai-Firmware
Die XM-3820-Kamera (Firmware Xiongmai) verwendet **IP-basiertes Session-Caching**:
- Nach mehreren fehlgeschlagenen WSSE-Authentifizierungsversuchen von einer IP wird diese IP in einen Lockout-Zustand versetzt
- HA's persistente aiohttp-Session (`self._session`) löst diesen Lockout aus, da sie dieselbe TCP-Verbindung / denselben Session-State wiederverwendet
- Der Lockout überlebt HA-Neustarts — nur ein **Kamera-Neustart** setzt ihn zurück

### Die funktionierende Lösung: XMSoapClient mit frischer Session pro Befehl
**Datei:** `xm_soap.py` → `class XMSoapClient`

```python
# RICHTIG: self._soap() erstellt einen frischen XMSoapClient FÜR DIESE KAMERA.
# Der async context manager erstellt eine NEUE aiohttp.ClientSession pro Befehl.
async with self._soap() as soap:
    ok = await soap.ptz_command(cmd, speed=spd)

# FALSCH: Niemals self._session (die persistente Coordinator-Session) für PTZ-ONVIF-SOAP nutzen
resp = await self._onvif_soap_for(ONVIF_SERVICE_PTZ, body)  # → HTTP 400 nach Lockout
```

### Multi-Device (Fix v2.2.34 — Juni 2026)
**Problem:** `_XMSoapClient()` wurde ohne Argumente erzeugt und nutzte die
hardcodierten Modul-Konstanten (`CAMERA_HOST = "192.168.178.49"` usw.). Dadurch
gingen PTZ **und** Motion-PullPoint bei JEDER Kamera an dieselbe IP — Steuerung
mehrerer Geräte unmöglich.

**Lösung:** `coordinator._soap()` reicht `host/username/password/onvif_port/
profile_token` aus dem jeweiligen Coordinator an `XMSoapClient(...)` durch.
`XMSoapClient.__init__` baut die ONVIF-Endpunkte pro Instanz aus Host+Port.
Die Modul-Konstanten bleiben nur noch als Fallback bestehen.

**Regel:** PTZ-/Event-Aufrufe IMMER über `async with self._soap() as soap:` —
nie wieder `_XMSoapClient()` ohne Argumente (verdrahtet sonst wieder auf .49).

### Warum frische Sessions funktionieren
Eine neue `aiohttp.ClientSession` pro PTZ-Befehl erstellt eine neue TCP-Verbindung ohne Session-History.
Die Kamera behandelt diese als "neuen Client" und überspringt den IP-Lockout-Check.

### Authentifizierung
- **Methode:** WSSE PasswordDigest (SHA1)
- **Formel:** `SHA1(nonce_raw + created_utf8 + password_utf8)`
- **Credentials:** `admin` / `""` (leeres Passwort — XM-Standard)
- **SOAP-Version:** 1.2 (`xmlns:s="http://www.w3.org/2003/05/soap-envelope"`)
- **Content-Type:** `application/soap+xml; charset=utf-8`
- **Profile-Token:** `"000"` (hardcodiert, aus GetProfiles verifiziert)
- **ONVIF-Port:** 8899

### Clock Skew — KEIN Problem
Die Kamera-Uhr ist ca. 2 Stunden vor UTC (Timezone-Fehlkonfiguration in der Firmware).
WSSE funktioniert trotzdem — die Kamera akzeptiert diese Zeitdifferenz.
**Clock Skew ist NICHT die Ursache für Auth-Fehler.**

---

## Architektur

### PTZ-Hauptpfad (funktionierend seit v2.2.19)
`coordinator.py` → `async_ptz_command()` → `async with _XMSoapClient() as soap: soap.ptz_command()`

### Alle PTZ-Methoden verwenden XMSoapClient (seit v2.2.20)
- `async_ptz_command` — Richtungsbewegung (right/left/up/down/zoom_in/zoom_out)
- `async_ptz_stop` — Bewegung stoppen
- `async_ptz_home` — Home-Position anfahren
- `async_ptz_set_home` — aktuelle Position als Home speichern
- `async_ptz_goto_preset` — Preset anfahren
- `async_ptz_set_preset` — Preset speichern

### Geschwindigkeitsregelung (doppelter Ansatz — seit v2.2.21)
- `self._ptz_speed` in coordinator: int 1–8 (von Number-Entity gesetzt)
- Normalisierung: `spd = self._ptz_speed / 8` → float 0.125–1.0 für XMSoapClient
- `button.py` → `WJGPTZButton.async_press` übergibt `self.coordinator._ptz_speed` explizit
- **XM ignoriert Velocity-Wert**: XM-Firmware akzeptiert ContinuousMove-SOAP, ignoriert aber
  häufig die Velocity-Werte (bewegt sich immer mit Maximalgeschwindigkeit)
- **Fix**: `ptz_command` in `xm_soap.py` variiert ZUSÄTZLICH den Stop-Delay proportional zur speed:
  `stop_delay = max(0.15, min(1.5, PTZ_STOP_DELAY * speed / PTZ_SPEED))`
  → speed=0.125 → ~0.15s; speed=0.4 → 0.8s; speed=1.0 → 1.5s
  → Bewegungsdistanz ist proportional, egal ob Velocity honoriert wird oder nicht
- Debug-Log zeigt velocity und stop_delay für jeden PTZ-Befehl

---

## Was NICHT geändert werden darf

1. **`async_ptz_command` und alle anderen `async_ptz_*` Methoden** dürfen NICHT auf `self._onvif_soap_for()` / `self._session` zurückwechseln.
2. **`xm_soap.py`** darf NICHT in `xm-soap.py` umbenannt werden (Python kann Module mit Bindestrichen nicht importieren).
3. **Der `async with self._soap() as soap:` Pattern** muss beibehalten werden — kein globaler oder geteilter Client. `self._soap()` erzeugt pro Befehl einen frischen, kameraspezifischen `XMSoapClient`. NICHT durch `_XMSoapClient()` ohne Argumente ersetzen (sonst wieder auf 192.168.178.49 verdrahtet).

---

## Lockout-Recovery
Falls PTZ wieder mit HTTP 400 fehlschlägt:
1. Kamera physisch neu starten (Strom trennen oder HA-Button "Kamera neu starten")
2. HA-Integration neu laden (HACS → WJG Camera Bridge → Reload)
3. Prüfen ob `xm_soap.py` noch `async with XMSoapClient()` verwendet (frische Session)

---

## Konstanten in xm_soap.py (seit v2.2.34 nur noch FALLBACK)
```python
CAMERA_HOST = "192.168.178.49"   # Fallback, falls _soap() keinen host übergibt
CAMERA_USERNAME = "admin"
CAMERA_PASSWORD = ""
ONVIF_PORT = 8899
PROFILE_TOKEN = "000"
PTZ_SPEED = 0.4       # Default-Geschwindigkeit
PTZ_STOP_DELAY = 0.8  # Sekunden Auto-Stop
```
**Wichtig:** Host/Credentials/Port kommen im Normalbetrieb aus dem Config-Entry
(`coordinator._soap()` → `XMSoapClient(host=..., username=..., ...)`). Die
Konstanten greifen nur, wenn `XMSoapClient()` ohne diese Argumente erzeugt wird
(z. B. der Schnelltest `_test()`). Kamera-IP/Credentials NICHT mehr hier ändern,
sondern über den HA-Config-Flow je Kamera.
