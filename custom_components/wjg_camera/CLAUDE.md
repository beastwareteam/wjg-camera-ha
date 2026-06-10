# WJG XM-3820 Camera Bridge — Kritisches Wissen für Claude

## PTZ-Geschwindigkeit: Stop-Delay (Fix v2.2.42 — Juni 2026) ⭐ AKTUELL

### LIVE VERIFIZIERT (10.06.2026, Diagnose `ptz_speed_diag.py` an der .49)
**Die Kamera fährt bei ContinuousMove BIS ZUM Stop weiter** — langer Halt =
weitere Strecke, kurzer Halt = kürzere. Konkret beobachtet: 2,0 s-Halt bewegte
deutlich weiter als 0,2 s-Halt. Das **widerlegt** die frühere Annahme „Kamera
macht festen Schritt und stoppt selbst" (die 02.06-Notiz war falsch). Nur der
Velocity-WERT wird ignoriert, die **Dauer NICHT**.

### Mechanismus (Stop-Delay-Stepping)
1 Tap = **1 ContinuousMove + proportionaler Sleep + Stop**. Die Stufe (1–8)
steuert die Haltedauer linear über `ptz_stop_delay_for_speed(speed)`:
- `PTZ_MIN_STOP_DELAY = 0.2 s` (Stufe 1, kleiner Tipp)
- `PTZ_MAX_STOP_DELAY = 1.5 s` (Stufe 8, große Schwenkung)
- linear dazwischen (Stufe N → `0.2 + (N-1)/7 * 1.3` s)

Gilt in **beiden** Pfaden: `XMSoapClient.ptz_command()` (xm_soap.py, Primär) und
`_async_fallback_ptz_pulse()` (coordinator.py, Direct-SOAP-Fallback). Beide
nutzen denselben Helper `ptz_stop_delay_for_speed` aus xm_soap.

### Warum die Vorgänger scheiterten
- **v2.2.40 (N Pulse):** Stufe 1 = 1 Puls × 0.35 s war als „Tipp" zu groß und
  fühlte sich bei höheren Stufen wie Hold-to-Move an.
- **v2.2.41 (1 Puls, Dauer 0.044–0.35 s):** richtiger Mechanismus, aber der
  Bereich war viel zu klein/eng — bei so kurzen Dauern dominiert die
  Netzwerk-Latenz, alle Stufen fühlten sich gleich an. v2.2.42 nutzt den großen,
  klar getrennten Bereich 0.2–1.5 s (wie das alte v2.2.21).

### Regeln
- **Stop-Delay ist der richtige Ansatz** (entgegen der alten CLAUDE.md-Warnung).
  Bei „alle Stufen gleich" zuerst den **Bereich vergrößern**, nicht den
  Mechanismus wechseln.
- Tuning nur über `PTZ_MIN_STOP_DELAY`/`PTZ_MAX_STOP_DELAY`.
- RelativeMove ist als Alternative vorhanden (Kamera akzeptiert es, Bereich
  −1…1, via `GetNodes` bestätigt) — bei Bedarf als noch präziserer Ersatz prüfbar.

## Kamera-Uhrzeit-Spam (Fix v2.2.41 — Juni 2026)
`WJGCameraTimeSensor` gibt `coordinator.camera_time` zurück — ein String
`"YYYY-MM-DD HH:MM:SS"`. Da sich dieser String jede Sekunde ändert, erzeugte
jede Koordinator-Abfrage (alle 60 s) einen HA-State-Change → Logbuch-Eintrag
jede Minute. **Fix:** Polling von `% 6` (60 s) auf `% 180` (30 min) geändert.
Imaging-Settings-Fetch wurde dabei von der Kamerazeit-Abfrage entkoppelt und
läuft weiterhin alle 5 Minuten (`% 30`).

---

## Netzwerkflut + Stepping-Verlust (Fix v2.2.40 — Juni 2026)

### Problem 1: Dauerhafte Netzwerkflut sobald die Integration läuft
**Ursache (Kettenreaktion):**
- Motion-"Kanal 2" (`_async_rtsp_motion_loop`) startete ALLE 8 s einen
  ffmpeg-Prozess mit voller RTSP-Verbindung zum Full-HD-Hauptstream.
- Schwelle 2 % Pixeländerung → Fehlalarme (Licht, IR-Umschaltung, Rauschen).
- JEDER Motion-Trigger startete via `_trigger_motion_recording` eine
  ffmpeg-HD-Aufnahme → quasi Dauerstream.

**Lösung (Optionen im Options-Flow, Funktionserhalt):**
- `motion_rtsp_diff` (Default **AUS**): Kanal 2 ist dreifach redundant zu
  ONVIF-PullPoint (Kanal 1, verifiziert) + UDP-Monitor (Kanal 3, passiv/lastfrei).
- `motion_rtsp_interval` (Default 30 s, min. 10): Intervall, falls Kanal 2 aktiviert wird.
- `motion_auto_record` (Default **AN**): Auto-Aufnahme bleibt erhalten — triggert
  ohne Kanal 2 nur noch durch echte Kamera-Events.
- `motion_record_cooldown` (Default 30 s): kein ffmpeg-Neustart im Sekundentakt.
- Zusätzlich: `_async_port_open_cached` (TCP-Status-Cache) — XM-SDK-(34567)- und
  HTTP-(80)-Fallbacks rennen nicht mehr bei jedem PTZ-Tastendruck gegen bekannte
  geschlossene Ports. Snapshot hat einen 5-Min-Negativ-Cache für Port 80.

### Problem 2: PTZ-Stepping wirkungslos, sobald der Primärpfad fehlschlägt
Der Direct-SOAP-Fallback in `async_ptz_command` sendete ein EINZELNES
`ContinuousMove` mit Velocity — XM ignoriert Velocity → fester Mini-Schritt,
Stufe 1–8 ohne Wirkung. **Fix:** `_async_fallback_ptz_pulse` pulst jetzt auch im
Fallback (Stufe N → N Pulse, gleiche Konstanten wie xm_soap). Außerdem gibt
`XMSoapClient.ptz_command` jetzt `moved` zurück statt des letzten Puls-Status:
Nach erfolgter Bewegung darf der Coordinator NICHT mit dem nächsten
Profile-Token erneut pulsen (Extra-Strecke).

### Snapshot ohne Port 80 (XM-3820: Port 80 ist ZU — live verifiziert 10.06.2026)
`async_snapshot` → HTTP-Kandidaten (falls Port offen) → sonst
`_async_rtsp_frame_snapshot`: ffmpeg-Einzelframe aus dem RTSP-Stream mit
10-s-Kurz-Cache + Lock. Damit liefert die Kamera-Entity echte Bilder statt des
1×1-Fallback-PNG.

### Live-Diagnose .49 (10.06.2026)
- Ports: 554 OFFEN, 8899 OFFEN, **80 ZU, 34567 ZU** (NETZWERK_DIAGNOSE-Doku vom
  23.05. ist veraltet, README hatte recht).
- WSSE mit lokaler Uhrzeit funktioniert trotz +2 h Kamera-Uhr-Offset.
- Puls-Stepping am Gerät verifiziert: Stufe 1 = 1 Puls (~0,8 s), Stufe 8 = 8 Pulse (~5,9 s).
- GetPresets liefert 127 vorbelegte Slots; Imaging-Endpoint ist `/onvif/imaging`
  (klein geschrieben); Events: `tns1:RuleEngine/CellMotionDetector/Motion`.

### Tests / CI
- `tests/conftest.py` stellt den XMSoapClient-Primärpfad per autouse-Fixture
  offline (`OfflineXMSoapStub`) und nullt die Puls-Wartezeiten. OHNE diesen Stub
  würden die Unit-Tests REALE PTZ-Befehle an eine erreichbare Kamera senden
  (Tests hardcoden 192.168.178.49) und die Kamera physisch bewegen!
- `DataUpdateCoordinator` bekommt seit v2.2.40 `config_entry=entry` explizit
  (HA ≥2025 Pflicht; Dummy-Entries in Tests brauchen `async_on_unload`).

---

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

### Geschwindigkeitsregelung — Stop-Delay (seit v2.2.42, siehe Top-Abschnitt)
- `self._ptz_speed` in coordinator: int 1–8 (von Number-Entity gesetzt), pro Kamera.
- Normalisierung: `spd = self._ptz_speed / 8` → float 0.125–1.0 für XMSoapClient.
- `button.py` → `WJGPTZButton.async_press` übergibt `self.coordinator.ptz_speed`.
- **LIVE KORRIGIERT (10.06.2026):** Die frühere 02.06-Notiz „XM ignoriert auch die
  Bewegungsdauer / Kamera stoppt selbst" ist **FALSCH**. Die Kamera fährt bis zum
  Stop; nur der Velocity-WERT wird ignoriert. → Stop-Delay funktioniert.
- **Aktuelle Lösung:** 1 Tap = 1 `ContinuousMove` + `ptz_stop_delay_for_speed(spd)`
  Sleep + `Stop`. Stufe 1 → 0.2 s, Stufe 8 → 1.5 s (linear). KEIN N-Puls-Stepping
  mehr (v2.2.39–v2.2.41 überholt).
- **Default seit v2.2.35: `self._ptz_speed = 1`** (langsamste Stufe), pro Kamera getrennt.
- Historie (überholt, NICHT wiederbeleben): v2.2.39–v2.2.40 = N Pulse (Stufe 1 zu groß);
  v2.2.41 = 1 Puls mit Dauer 0.044–0.35 s (Bereich zu klein → alle Stufen gefühlt gleich).

### PTZ Profile-Token-Retry (seit v2.2.39)
- `async_ptz_command` probiert bei Fehlschlag der Reihe nach Tokens: konfigurierter
  Token (`CONF_ONVIF_PROFILE_TOKEN`) → `000` → `001` → `002`.
- Ein funktionierender Token wird in `self._preferred_onvif_profile_token` gemerkt
  (künftig zuerst probiert). Behebt Kameras, die PTZ unter einem anderen Token
  erwarten (Symptom: eine Kamera reagiert nicht aufs Steuerkreuz, andere baugleiche
  schon — z. B. .49 vs .50/.51).

### Multi-Device Geschwindigkeit / Lovelace-Card (Fix v2.2.35 → robust v2.2.36)
**Nicht-offensichtliche Falle:** HA hängt das Kollisions-Suffix bei gleichnamigen
Geräten an unterschiedlichen Positionen an:
- Kamera 2: `camera.wjg_xm_3820_2` ABER `number.wjg_xm_3820_ptz_geschwindigkeit_2`
  (Suffix am ENDE der jeweils ganzen entity_id).
Daher lässt sich die Speed-/Button-Entity NICHT zuverlässig per String-Manipulation
aus der Kamera-entity_id ableiten.

**Lösung v2.2.36 (`wjg-camera-card.js`):** Auflösung über die **Entity-Registry**
(`hass.entities`). `_findOnDevice(domain, baseSuffix)` sucht eine Entity auf
DEMSELBEN `device_id` wie die Kamera, deren entity_id auf `baseSuffix` endet
(Regex `baseSuffix(_\d+)?$` → deckt umbenannte UND auto-nummerierte Geräte ab).
- `_speedEntity()`: Config `ptz_speed_entity` → `_findOnDevice('number','_ptz_geschwindigkeit')`
  → String-Ableitung nur wenn existent → sonst `null`.
- `_callPTZ()`: Config `ptz_entities[dir]` → `_findOnDevice('button', '_ptz_<dir>')`
  → Legacy `ptz_service` NUR wenn explizit gesetzt (kein Default mehr).
Dadurch genügt im Dashboard `entity: camera.<cam>` pro Karte; jede Karte steuert
garantiert ihre EIGENE Kamera. KEINEN harten Default-Entity-Fallback (z. B. auf
`number.wjg_xm_3820_ptz_geschwindigkeit`) wieder einbauen.

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
