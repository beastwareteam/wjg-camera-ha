# WJG XM-3820 Camera Bridge — Kritisches Wissen für Claude

## PTZ-Geschwindigkeit: Event-Abfrage war die Ursache (v2.2.60 — September 2026) ⭐ AKTUELL

### Gewünschtes Verhalten (Nutzer-Vorgabe)
**1 Tastendruck = 1 Klick.** Stufe 1 = sehr kurzer Klick, Stufe 8 = langer Klick,
benachbarte Stufen spürbar verschieden. KEINE Serie von Einzelklicks.

### Stand v2.2.59 (live bestätigt bis v2.2.58)
- **Ursache der Latenz BESTÄTIGT:** der ONVIF-Event-Loop (PullMessages ~1×/s,
  XM hält jede Anfrage ~1 s, arbeitet SOAP seriell). Mit Pause während PTZ
  (v2.2.58) sank die Stop-Antwort live von ~1,3–1,5 s auf **~0,2 s**; Stufe 1
  fühlt sich „genau wie gewollt“ an.
- Rest-Schwankung v2.2.58: Move-Antwort 0,08–0,98 s (die Kamera arbeitet ein
  gerade abgebrochenes PullMessages intern noch ab). Da die Haltedauer ab dem
  SENDEN zählte, fraß diese Wartezeit die Haltedauer → Stufe 2/4 manchmal wie 1.
- **v2.2.59:** Haltedauer zählt ab der **Move-Antwort** (vorher bewegt sich die
  Kamera noch nicht). Tabelle `PTZ_MOVE_DURATIONS = (0.0, 0.15, 0.3, 0.5, 0.7,
  0.95, 1.25, 1.6)` = Sekunden nach der Move-Antwort; Stufe 1 = 0 (Stop direkt).
- **v2.2.59:** `EVENT_PULL_PAUSE_SECS = 2.0` Pause zwischen zwei PullMessages →
  Kamera meist frei, PTZ-Start wartet seltener. Bewegung erkennt bei der
  XM-3820 ohnehin Kanal 2 (ONVIF liefert dauerhaft ismotion=false).
- Live v2.2.59: Stop-Antwort stabil ~0,15–0,35 s; Move-Antwort meist
  0,06–0,18 s, aber in ~30 % der Klicks 0,4–0,95 s (Klick trifft ein laufendes
  PullMessages: 1 s Abfrage je 3 s Zyklus) → Stufen noch uneinheitlich.
- **v2.2.60:** Option `motion_onvif_events` („Kamera-Ereignisse abfragen“,
  Kanal 1, Default AN für andere Kameras). Bei der XM-3820 AUS schalten: kein
  PullMessages mehr → Kamera bearbeitet nur PTZ. Folge: Sabotage-/Signalverlust-
  Sensoren (kommen aus ONVIF-Events) bleiben aus; Bewegung weiter über Kanal 2.
- Live v2.2.60 (Kanal 1 aus): Move-Antwort 0,05–0,22 s, Stufen gleichmäßig —
  Nutzer: „fühlt sich gut an“. **PTZ damit gelöst.**

## Kanal 2: Empfindlichkeit einstellbar (v2.2.61)
- `motion_rtsp_pixel_threshold` (Standard 30) und `motion_rtsp_trigger_percent`
  (Standard 6 %) sind Optionen; vorher fest im Code (bis 19.08. 15 / 2 %).
- Live-Befund: Aufnahme startet ~5–8 s nach Bewegungsbeginn. Anteile: Erkennung
  (Schwelle + Bildabstand + Stream-Latenz, ~2–3 s) — per Optionen verkürzbar —
  und Aufnahmestart (neue RTSP-Verbindung + Warten auf Keyframe, ~2–5 s) — NICHT
  per Schwellen lösbar; dafür bräuchte es einen Vorlauf-Puffer aus dem Kanal-2-
  Stream (noch nicht umgesetzt).

### Live gemessen (25.09.2026, .49)
v2.2.55 (Stop nach Move-Antwort):
| Stufe | Soll | Move-Antwort | Stop-Antwort |
|---|---|---|---|
| 1 | 0,00 s | 0,82 / 1,21 s | 2,18 / 2,60 s |
| 2 | 0,10 s | 0,97 / 0,66 s | 2,42 / 2,07 s |
| 4 | 0,45 s | 0,98 / 1,07 s | 2,27 / 2,44 s |
| 8 | 2,00 s | 1,09 / 0,83 s | 3,51 / 3,75 s |

v2.2.56 (Früh-Stop nach 0,3/0,45 s, VOR der Move-Antwort gesendet): Nutzer
spürte **trotzdem nur Stufe 8** als anders, dazu **Ruckeln**. Move-Antwort
schwankte 0,09–2,3 s, Stop-Antwort immer ~1,3–1,5 s nach dem Senden.

**Schluss:** Die Kamera arbeitet Anfragen offenbar seriell ab; ein Stop wirkt
frühestens nach ~1,5 s. Über Stop-Timing sind Klicks unter ~1,5 s NICHT
erreichbar. Kürzere Klicks gehen nur, wenn die Kamera **selbst** stoppt.

### Mechanismus (seit v2.2.57, Haltedauer-Bezug seit v2.2.59)
- 1 `ContinuousMove` (Velocity = Stufe/8) → Move-Antwort abwarten → Haltedauer
  aus `PTZ_MOVE_DURATIONS` (ab Move-Antwort) → `Stop` (1× Retry).
  Früh-Stop wieder entfernt (brachte nichts, ruckelte).
- Auch bei als Fehler gemeldetem Move wird vorsorglich gestoppt; bei Abbruch
  (`CancelledError`) wird der Move verworfen und gestoppt.
- INFO-Log je Klick: Soll, Move-Antwort, Stop gesendet, Stop-Antwort.
  INFO erscheint NICHT in der HA-Problemansicht → Integration → ⋮ →
  „Debug-Protokollierung aktivieren“, klicken, deaktivieren → Log-Download.
- PTZ (Richtung, Home, Preset) läuft in `_ptz_motion_quiet()` (verschachtelungs-
  sicher): Bewegungs-Trigger aller Kanäle werden währenddessen und
  `PTZ_MOTION_QUIET_SECS` (8 s) danach ignoriert. **Live bestätigt:** keine
  Aufnahmen mehr durch PTZ.

### Ursache der Latenz: ONVIF-Event-Abfrage blockiert die Kamera (v2.2.58)
Im Juni (v2.2.40, live) dauerte eine SOAP-Anfrage ~0,13 s (8 Pulse = 5,9 s),
jetzt ~1 s. Live-Tests 25.09.2026:
- `RelativeMove`: wird mit OK quittiert, Kamera bewegt sich NICHT (Bildvergleich 0,0 %).
- `ContinuousMove` + `<Timeout>`: Timeout wird ignoriert (fährt bis zum Stop).
- Kanal 2 (RTSP-Bildvergleich) AUS: Latenz unverändert → **nicht** die Ursache.
- Der ONVIF-Event-Loop fragt je Kamera ~1×/s `PullMessages` ab; die XM hält
  jede Anfrage ~1 s und liefert nur `ismotion=false`. Da die Kamera SOAP
  seriell abarbeitet, wartet jeder PTZ-Befehl auf das laufende PullMessages
  (passt zu Move-Antwort 0,2–1,2 s gleichverteilt, Stop ~1,3 s).
**Fix:** `_ptz_motion_quiet()` setzt `_ptz_idle` zurück und bricht ein laufendes
PullMessages ab (`_event_pull_task`); der Event-Loop wartet auf `_ptz_idle`
und macht danach mit derselben Subscription weiter. Live noch zu bestätigen
(Timing-Log: Move-/Stop-Antwort sollten deutlich unter 1 s fallen).

### Diagnose-Aktion `wjg_camera.ptz_test`
Liefert die Antwort direkt in Entwicklerwerkzeuge → Aktionen:
- `method: relative, value: 0.01–1.0` → `RelativeMove`, kein Stop.
- `method: timeout, value: s` → `ContinuousMove` mit `<tptz:Timeout>`,
  Sicherheits-Stop erst `PTZ_TEST_SAFETY_STOP_SECS` (3 s) danach.
- `method: continuous, value: s` → heutiges Verfahren zum Vergleich.
Ergebnis 25.09.2026: beide Methoden funktionieren an der XM-3820 NICHT (s. o.).

### Historie — was NICHT funktionierte
- v2.2.39/40, v2.2.53: N Pulse pro Druck → mehrere zu lange Einzelklicks.
- v2.2.41: 1 Puls 0,044–0,35 s → alle Stufen gleich (Latenz dominiert).
- v2.2.42–v2.2.52: 1 Klick 0,2–1,5 s, Velocity fest 1.0 → alle Stufen gleich.
- v2.2.54: 1 Klick linear 0,25–1,5 s → Stufe 1 zu lang, 1 ≈ 2.
- v2.2.55: Stop nach Move-Antwort (~1 s) → Stufe 1–4 identisch.
- v2.2.56: Früh-Stop vor der Move-Antwort → weiter nur Stufe 8 anders + Ruckeln.

### Regeln
- Den Stop nach der Move-Antwort (und den vorsorglichen Stop bei Fehler/Abbruch)
  NIE entfernen.
- Keine weiteren Stop-Timing-Experimente, solange die Latenz hoch ist. Erst die
  Latenz senken (Event-Abfrage während PTZ pausieren), dann die Stufen tunen.
- Den Event-Loop NIE ohne Pause-Mechanismus parallel zu PTZ laufen lassen und
  die Pause zwischen PullMessages (`EVENT_PULL_PAUSE_SECS`) nicht entfernen.
- Haltedauer NIE wieder ab dem Senden rechnen (Wartezeit ≠ Bewegung).
- Optionen: `strings.json`/`translations/*.json` brauchen `"options"` auf
  OBERSTER Ebene (lag bis v2.2.57 fälschlich in `"config"` → Rohnamen im
  Formular). Options-Änderung lädt die Integration per Update-Listener neu.

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
  offline (`OfflineXMSoapStub`), nullt die Klick-Dauer und stubbt
  `_tcp_port_reachable` / `_rtsp_url_has_video` (sonst echte Socket-Timeouts,
  vorher ~48 s pro `async_setup`-Test). OHNE diesen Stub
  würden die Unit-Tests REALE PTZ-Befehle an eine erreichbare Kamera senden
  (Tests hardcoden 192.168.178.49) und die Kamera physisch bewegen!
- `DataUpdateCoordinator` bekommt seit v2.2.40 `config_entry=entry` explizit
  (HA ≥2025 Pflicht; Dummy-Entries in Tests brauchen `async_on_unload`).

---

## Re-Initialisierung nach Reset (Fix v2.2.52 — August 2026)

### Symptom
Nach einem Reset (HA-Neustart mit noch bootender Kamera **oder** Kamera-Reboot) kam die
Integration nicht von selbst zurück. Erst **Integration neu laden** von Hand stellte sie her.

### Ursache 1 — `async_setup()` konnte gar nicht fehlschlagen
Jede Abfrage darin war in `try/except` gefasst, und die ungefassten warfen ebenfalls nicht:
`async_resolve_rtsp_path()` fällt am Ende auf eine gebaute URL zurück, `async_refresh()` der
`DataUpdateCoordinator` schluckt Fehler grundsätzlich. Damit war das `ConfigEntryNotReady` in
`__init__.py` **toter Code** für den Offline-Fall: HA hielt den Entry für *geladen* und
wiederholte nichts.

### Ursache 2 — die Einmal-Abfragen wurden nie wiederholt
`_async_bootstrap_onvif_service_paths` · `async_resolve_rtsp_path` · `async_fetch_device_info`
· `async_ptz_get_presets` · `async_fetch_audio_settings` hatten **je genau einen Aufrufer**:
`async_setup()`. `_async_update_data()` prüfte nur TCP-Erreichbarkeit und XM-Keepalive.
Kam die Kamera zurück, ging `available` wieder auf True — ONVIF-Service-Pfade, RTSP-URL,
Firmware/Seriennummer, Presets und Audio-Token blieben aber auf dem Stand des Ausfalls.
Genau deshalb half nur das Neuladen: es ist der einzige Weg, der `async_setup()` erneut fuhr.

### Ursache 3 — `async_reboot()` räumte nichts ab
Der Button "Kamera neu starten" schickte `SystemReboot` und war fertig. Der Coordinator lief
mit dem alten Zustand samt toter PullPoint-Subscription weiter.

### Lösung
| Baustein | Wirkung |
|---|---|
| `_async_any_port_reachable()` als **erste** Zeile in `async_setup()` | Kamera stumm ⇒ `ConnectionError` ⇒ `ConfigEntryNotReady` ⇒ HA wiederholt das Setup selbständig. Steht vor `ClientSession()`/Tasks, also nichts aufzuräumen |
| `async_bootstrap_device()` | Die fünf Einmal-Abfragen ausgelagert — aufrufbar auch außerhalb des Setups |
| `_bootstrap_succeeded()` | Beleg statt Ausbleiben eines Fehlers: ONVIF ⇒ `_fw_version`/`_serial_number`/`_mac_address` gelesen; sonst `_rtsp_probe_confirmed` (nur eine bestandene DESCRIBE-Probe, **nicht** die Fallback-URL) |
| Haken in `_async_update_data()` | `available` weg ⇒ `_bootstrapped = False`; `available` zurück ⇒ `_schedule_bootstrap()` |
| `BOOTSTRAP_RETRY_INTERVAL = 60.0` + `_bootstrap_task` | Kein Doppellauf, kein 10-Sekunden-Takt gegen eine halb antwortende Kamera |
| `_bootstrap_backoff` → `BOOTSTRAP_MAX_BACKOFF = 600.0` | Bleibt der Bootstrap ohne Beleg, verdoppelt sich der Abstand bis 10 min. Erreichbar-aber-stumm ist sonst ein Dauerprobe-Zustand; bei Erfolg **und** nach `async_reboot()` fällt er auf 60 s zurück |
| `async_reboot()` | Setzt bei Erfolg `_bootstrapped=False`, `_last_bootstrap_attempt=0.0`, `_event_pullpoint_path=""` |

`_event_pullpoint_path = ""` nach gelungener Re-Initialisierung ist bewusst **derselbe**
Recovery-Pfad, den `_async_onvif_event_loop()` intern schon nutzt — die Schleife legt beim
nächsten Durchlauf eine frische Subscription an.

### Was NICHT geändert werden darf
- Die Erreichbarkeitsprüfung muss **vor** `self._session = aiohttp.ClientSession()` stehen.
  Danach würde jeder ConfigEntryNotReady-Retry Session und drei Event-Tasks lecken.
- `_bootstrap_succeeded()` darf nicht auf "keine Exception" zurückfallen. Die ONVIF-Abfragen
  werfen bei stummer Kamera nicht — sie liefern leere SOAP-Antworten, und
  `_xml_text("")` gibt `""`. Ausbleiben eines Fehlers ist kein Beleg.
- `_onvif_wsse_enabled` startet absichtlich auf `False` (Zeile ~563) und wird in
  `_onvif_soap_for` nur ausgeschaltet, nie ein. Das ist **kein** Reset-Problem: der Wert ist
  nach Neustart und nach Neuladen identisch. Nicht "reparieren", ohne den PTZ-Pfad
  (`xm_soap.XMSoapClient`, eigene WSSE-Session) mitzudenken.

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

### Geschwindigkeitsregelung — Einzel-Klick (seit v2.2.59, siehe Top-Abschnitt)
- `self._ptz_speed` in coordinator: int 1–8 (von Number-Entity gesetzt), pro Kamera.
- Normalisierung: `spd = self._ptz_speed / 8` → float 0.125–1.0 für XMSoapClient.
- `button.py` → `WJGPTZButton.async_press` übergibt `self.coordinator.ptz_speed`.
- 1 Tap = 1 `ContinuousMove` (Velocity = spd), nach der Move-Antwort
  `ptz_move_duration_for_speed(spd)` warten, dann `Stop`.
- **Default seit v2.2.35: `self._ptz_speed = 1`** (langsamste Stufe), pro Kamera getrennt.

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

### Automatisierte Durchsetzung (seit August 2026)
Diese drei Regeln wurden wiederholt unbeabsichtigt verletzt (zuletzt: `async_ptz_get_presets`/`async_ptz_delete_preset` liefen über `_onvif_soap_for(ONVIF_SERVICE_PTZ, ...)` statt `self._soap()`). Deshalb geprüft von:
- **`scripts/check_architecture_rules.py`** — eigenständig ausführbar (`python scripts/check_architecture_rules.py`), prüft alle drei Regeln per AST-Analyse von `coordinator.py`.
- **CI** (`.github/workflows/ci.yml`) — läuft bei jedem Push/PR, kann nicht umgangen werden.
- **Lokaler Pre-Push-Hook** (`.githooks/pre-push`) — prüft Architektur-Regeln + volle Testsuite VOR jedem `git push`. Einmalig pro Klon aktivieren:
  ```
  cp .githooks/pre-push .git/hooks/pre-push   # (Git Bash: zusätzlich chmod +x)
  ```
  Nicht über `git config core.hooksPath` global aktiviert — bewusste Opt-in-Installation pro Klon statt automatisch fremde Hooks auszuführen.

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
PTZ_SPEED = 0.4           # Default-Geschwindigkeit
PTZ_MOVE_DURATIONS = (0.0, 0.15, 0.3, 0.5, 0.7, 0.95, 1.25, 1.6)  # Halten nach Move-Antwort, Stufe 1–8
```
**Wichtig:** Host/Credentials/Port kommen im Normalbetrieb aus dem Config-Entry
(`coordinator._soap()` → `XMSoapClient(host=..., username=..., ...)`). Die
Konstanten greifen nur, wenn `XMSoapClient()` ohne diese Argumente erzeugt wird
(z. B. der Schnelltest `_test()`). Kamera-IP/Credentials NICHT mehr hier ändern,
sondern über den HA-Config-Flow je Kamera.
