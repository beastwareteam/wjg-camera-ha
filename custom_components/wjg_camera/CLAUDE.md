# WJG XM-3820 Camera Bridge — Kritisches Wissen für Claude

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
# RICHTIG: Immer so verwenden — async context manager erstellt NEUE aiohttp.ClientSession
async with _XMSoapClient() as soap:
    ok = await soap.ptz_command(cmd, speed=spd)

# FALSCH: Niemals self._session (die persistente Coordinator-Session) für PTZ-ONVIF-SOAP nutzen
resp = await self._onvif_soap_for(ONVIF_SERVICE_PTZ, body)  # → HTTP 400 nach Lockout
```

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
3. **Der `async with _XMSoapClient() as soap:` Pattern** muss beibehalten werden — kein globaler oder geteilter Client.

---

## Lockout-Recovery
Falls PTZ wieder mit HTTP 400 fehlschlägt:
1. Kamera physisch neu starten (Strom trennen oder HA-Button "Kamera neu starten")
2. HA-Integration neu laden (HACS → WJG Camera Bridge → Reload)
3. Prüfen ob `xm_soap.py` noch `async with XMSoapClient()` verwendet (frische Session)

---

## Hardcodierte Konstanten (xm_soap.py)
```python
CAMERA_HOST = "192.168.178.49"
CAMERA_USERNAME = "admin"
CAMERA_PASSWORD = ""
ONVIF_PORT = 8899
PROFILE_TOKEN = "000"
PTZ_SPEED = 0.4       # Default-Geschwindigkeit
PTZ_STOP_DELAY = 0.8  # Sekunden Auto-Stop
```
Für Änderungen der Kamera-IP oder Credentials: diese Konstanten in `xm_soap.py` anpassen.
