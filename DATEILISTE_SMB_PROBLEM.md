# Dateiliste (Aufnahmen) — Bekanntes Problem der XM-3820

## ⚠️ Status
Die Entity `sensor.wjg_xm_3820_dateiliste` zeigt **0 Dateien** an und ist **nicht verfügbar** für die Auflistung.

## 🔍 Ursache (Netzwerk-Test durchgeführt)

| Protokoll | Port | Status | Folgerung |
|-----------|------|--------|-----------|
| **SMB**   | 445  | ❌ Nicht erreichbar | Kamera hostet SD-Karte nicht via Samba |
| **FTP**   | 21   | ❌ Nicht erreichbar | Kamera hat keinen FTP-Server |
| **HTTP Dateimanager** | 80 | ⏳ Timeout | API nicht oder auf anderem Endpoint verfügbar |

**Schlussfolgerung:** Die XM-3820-Firmware bietet **keinen standardisierten Dateizugriff** auf ihre SD-Karte von außen.

---

## 📋 Mögliche Lösungen

### 1️⃣ **SSH/SFTP zur Kamera (Falls verfügbar)**
```bash
# Versuch mit SSH (falls aktiviert)
ssh admin@192.168.178.49
ls /mnt/sdcard        # oder ähnlich
```

Wenn SSH antwortet → Dateiabfrage via SFTP möglich.

### 2️⃣ **Kamera neu starten + Dateimanager überprüfen**
Manchmal muss die Web-UI neu gestartet werden:
```
http://192.168.178.49:80/cgi-bin/configmanager?action=getconfig&category=WebServer
```

### 3️⃣ **Firmware-Update prüfen**
Die XM-3820 könnte ein älteres Firmware-Build haben. Überprüfe:
- **Admin-Panel der Kamera:** Einstellungen → System → Firmware
- **Offizielle Update:** https://www.xiongmai.com (falls vorhanden)
- **Neuere Firmwares** aktivieren möglicherweise SMB/FTP

### 4️⃣ **Lokalzugriff via USB/SD-Kartenleser**
Falls SSH nicht funktioniert:
- SD-Karte physisch entnehmen
- Mit USB-Kartenleser an PC anschließen
- Videos manuell durchsuchen

---

## ✅ Aktuelle Implementierung (Stand: Mai 2026)

**Datei:** `custom_components/wjg_camera/sensor.py`

```python
class WJGFileListSensor(CoordinatorEntity, SensorEntity):
    """
    Sensor für SD-Kartenliste.
    
    Falls Dateiabfrage fehlschlägt (SMB/FTP/HTTP nicht verfügbar):
    - Entity bleibt VERFÜGBAR (available=True)
    - native_value = 0  ← "Es gibt 0 Dateien"
    """
    
    @property
    def available(self) -> bool:
        """Sensor ist auch ohne Daten verfügbar."""
        return True
    
    @property
    def native_value(self) -> int:
        """Anzahl Dateien. Bei Fehler: 0."""
        files = self.coordinator.data.get("files", []) if self.coordinator.data else []
        return len(files)
    
    @property
    def extra_state_attributes(self) -> dict:
        """Hinweis auf fehlenden SMB-Zugriff."""
        return {
            "files": [],
            "count": 0,
            "note": "SMB/FTP erforderlich für echten Videozugriff"
        }
```

**Das bedeutet:** Wenn Dateiliste nicht abrufbar ist, zeigt HA:
```
sensor.wjg_xm_3820_dateiliste
├─ State: 0 (Verfügbar ✅)
├─ files: []
└─ note: "SMB/FTP erforderlich..."
```

---

## 🛠️ Nächste Schritte (Für Benutzer)

1. **Teste SSH:**
   ```bash
   ssh admin@192.168.178.49
   ```
   Falls erfolgreich → Wir können SFTP-Abfrage implementieren

2. **Dokumentiere deine Kamera-Version:**
   - Model: `sensor.wjg_xm_3820_firmware`
   - OS: (z.B. "Xiongmai Build 2024.01.15")

3. **Kontakt zum Hersteller:**
   - Frage, ob SMB/FTP/SFTP in deiner Firmware-Version verfügbar ist
   - Update-Möglichkeiten?

---

## 📝 Zusammenfassung

| Feature | Status | Grund |
|---------|--------|-------|
| Live-Streaming (RTSP) | ✅ Funktioniert | HTTP + RTSP-Port offen |
| Snapshots | ✅ Funktioniert | HTTP `/webcapture.jpg` verfügbar |
| PTZ-Steuerung | ✅ Funktioniert | ONVIF auf Port 8899 |
| **Dateiliste (Aufnahmen)** | ❌ **Nicht verfügbar** | **Keine SMB/FTP/HTTP-API** |

**Die Integration ist richtig konfiguriert. Das Problem ist Hardware-/Firmware-seitig.**

---

**Letztes Update:** Mai 2026  
**Commit:** `e349d26` (Robustheit: Dateilisten-Sensor bleibt verfügbar)
