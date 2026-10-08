# WLED LED Steuerung – Technische Dokumentation

## Übersicht

Das WLED-System erlaubt es, bis zu 3 WLED-Controller (ESP8266/ESP32) mit dem Autodarts-Setup zu verbinden. Spielereignisse (Leg gewonnen, Bust, hoher Finish usw.) triggern automatisch LED-Effekte über Presets, die vorher auf den WLED-Controllern gespeichert werden.

---

## 1. Datei-Struktur

| Datei | Zweck |
|-------|-------|
| `autodarts-web.py` | Flask-Backend – alle WLED-Logik, API-Routen, Hilfsfunktionen |
| `templates/wled_presets.html` | Preset-Editor HTML-Template |
| `static/js/wled_presets.js` | Preset-Editor Logik (~1478 Zeilen) |
| `static/css/wled_presets.css` | Preset-Editor Styling |
| `templates/index.html` | Hauptoberfläche mit WLED-Steuerung |
| `static/js/main.js` | WLED-Integration im Haupt-UI |

---

## 2. Konfiguration & Speicherung

### Multi-WLED Konfiguration (aktuelles System)

**Pfad:** `/var/lib/autodarts/wled-targets.json`

```json
{
  "master_enabled": true,
  "targets": [
    {"label": "Dart LED1", "host": "Dart-Led1.local", "enabled": true},
    {"label": "Dart LED2", "host": "Dart-Led2.local", "enabled": false},
    {"label": "Dart LED3", "host": "Dart-Led3.local", "enabled": false}
  ]
}
```

### Legacy-Konfiguration (einzel WLED)

**Pfad:** `/var/lib/autodarts/wled-enabled.json`  
**Standard-Host:** `"Dart-Led.local"`  
**Auto-Migration:** Falls Multi-Config nicht existiert, migriert das System automatisch vom alten Format.

### Preset-Konfiguration (darts-wled)

**Datei:** `/var/lib/autodarts/config/darts-wled/start-custom.sh`  
**Schlüsselzeile:** Enthält `-WEPS` gefolgt von den Ziel-Hosts (als Python-Script-Argumente).

```bash
exec python darts-wled.py \
  -CON "127.0.0.1:8079" \
  -WEPS "Dart-Led1.local" \
  -IDE   "ps|1" \
  -G     "ps|2|4" \
  ...
```

---

## 3. WLED HTTP-API (Webpanel → WLED-Controller)

Der WLED-Controller wird direkt über seine HTTP REST API angesprochen.

### Preset speichern / senden

```
POST http://<wled-host>/json/state
```

```json
{
  "psave": 5,
  "n": "Leg gewonnen",
  "ib": true,
  "sb": true,
  "on": true,
  "bri": 180,
  "seg": [{
    "id": 0,
    "col": [[255, 160, 0], [0, 0, 0], [0, 0, 0]],
    "fx": 0,
    "sx": 128,
    "ix": 128,
    "pal": 0
  }]
}
```

Python-Funktion: `_wled_json_post(ip_or_host, payload, "/json/state", timeout_s=1.8)`

### Preset löschen

```
POST http://<wled-host>/json/state
```

```json
{"pdel": 5}
```

Python-Funktion: `_wled_json_post(ip, {"pdel": preset}, "/json/state", timeout_s=1.8)`

### Aktuellen State abrufen

```
GET http://<wled-host>/json/state
```

Gibt den aktuellen WLED-State zurück (inkl. aktivem Preset).  
Python-Funktion: `_wled_json_get(ip, "/json/state", timeout_s=1.2)`

### Erreichbarkeit prüfen

```
GET http://<wled-host>/json/info
```

Python-Funktion: `is_wled_reachable(ip_or_host, timeout_sec=1.2)`

---

## 4. Webpanel API-Routen (Frontend ↔ Backend)

### Presets laden

```
GET /api/wled-presets/load
```

**Response:**
```json
{
  "ok": true,
  "msg": "...",
  "rows": [{
    "preset": 5,
    "kind": "G",
    "arg": "-G",
    "label": "Leg gewonnen",
    "duration": true,
    "seconds": 4
  }],
  "wepsText": "\"Dart-Led1.local\"",
  "path": "/var/lib/autodarts/config/darts-wled/start-custom.sh"
}
```

### Presets speichern

```
POST /api/wled-presets/save
```

**Request:**
```json
{
  "rows": [{"preset": 5, "kind": "G", "arg": "-G", "label": "Leg gewonnen", "seconds": 4}]
}
```

**Nebeneffekt:** Startet `darts-wled.service` neu, falls aktiv.

### Preset an WLED-Controller senden

```
POST /api/wled-presets/send
```

**Request:**
```json
{
  "preset": 5,
  "slot": 1,
  "host": "Dart-Led1.local"
}
```

**Response:**
```json
{
  "ok": true,
  "msg": "Preset 5 saved",
  "host": "Dart-Led1.local",
  "ip": "192.168.1.100",
  "preset": 5,
  "name": "Leg gewonnen"
}
```

**Ablauf intern:**
1. Hostname zu IP auflösen (avahi für `.local`-Namen)
2. Erreichbarkeit prüfen (`GET /json/info`)
3. Mutation-Lock acquiren (verhindert gleichzeitige Operationen)
4. Aktuellen WLED-State prüfen (`GET /json/state`)
5. Falls an: Preset laden (`POST {"ps": 5}`), dann über-speichern (`POST {"psave": 5, ...}`)
6. Falls aus: Standard-Orangelicht + psave senden
7. 1,1 Sekunden warten (Mutex-Cooldown)
8. Lock freigeben

### Preset von WLED-Controller löschen

```
POST /api/wled-presets/delete
```

**Request:** Gleich wie send.

**Response:** Lösch-Bestätigung.

**Ablauf intern:**
1. Mutation-Lock acquiren
2. `{"pdel": preset_nummer}` senden
3. Lock freigeben (serialisiert, verhindert ESP-Instabilität)

### WLED-Status abfragen

```
GET /api/wled/status
```

**Response:**
```json
{
  "bands": [
    {"slot": 1, "enabled": true, "online": true, "ip": "192.168.1.100"},
    {"slot": 2, "enabled": false, "online": null, "ip": null},
    {"slot": 3, "enabled": false, "online": null, "ip": null}
  ],
  "caller_enabled": true,
  "caller_wled_auth_active": true,
  "wled_service_active": true,
  "wled_service_enabled": true,
  "watchdog_timer_active": true
}
```

**Features:**
- Parallele Checks aller Targets (schneller)
- 3-Sekunden Cache TTL (`WLED_STATUS_CACHE`)
- DNS-Auflösung via avahi für `.local`-Namen

---

## 5. Preset-Typen & Argumente

### Feste Ereignis-Trigger

| Argument | Ereignis | Mit Dauer? |
|----------|----------|-----------|
| `-IDE` bis `-IDE6` | Spieler 1–6 Idle/Zug | Nein (dauerhaft) |
| `-G` | Leg gewonnen | Ja (z.B. 4 s) |
| `-M` | Match gewonnen | Ja (z.B. 5 s) |
| `-B` | Bust | Ja (z.B. 3–5 s) |
| `-PJ` | Spieler beigetreten | Ja |
| `-PL` | Spieler verlassen | Ja |
| `-BSE` | Board gestoppt | Ja |
| `-CE` | Kalibrierung | Ja |
| `-HF` | Hoher Finish | Ja (z.B. 5 s) |
| `-TOE` | Checkout/Takeout | Ja (z.B. 3 s) |
| `-DSBULL` | Bull/Bullseye getroffen | Ja (z.B. 3 s) |
| `-S0` | Score 0 | Ja (z.B. 3 s) |
| `-SLE` | Sleep-Effekt | Ja |
| `-DS1` bis `-DS20` | Einzelner Dart (1–20) | Ja (z.B. 3 s) |

### Score-basierte Typen

- `-SN` (N = 0–180): Exakter Score als Trigger
- `-A1`, `-A2`, `-A3`: Score-Bereiche (auto-zugewiesen)

### Optionen / Konfiguration

| Argument | Typ | Standard | Bereich | Zweck |
|----------|-----|---------|---------|-------|
| `-HFO` | int | 100 | 2–170 | High-Finish-Schwellenwert |
| `-OFF` | bool | 1 | 0/1 | Nach Match ausschalten |
| `-SOFF` | bool | 1 | 0/1 | Beim Start ausschalten |
| `-SLET` | int | 300 | 1–86400 | Sleep-Timeout (Sekunden) |
| `-SLEOFF` | int | 0 | 0–1440 | Nach Sleep ausschalten (Minuten) |
| `-BRI` | int | 175 | 1–255 | Effekt-Helligkeit |
| `-DU` | int | 5 | 0–999 | Globale Dauer (Sekunden) |
| `-BSS` | float | 0.4 | 0–60 | Board-Stop-Verzögerung (s) |
| `-BSW` | bool | 1 | 0/1 | Board nach Gewinn stoppen |

### Erweiterte Typen (Expert-Modus)

- `-DMU`: Dart-Multiplikator/Feld  
  Beispiel: `"-DMU" "3=ps|5" "t20=ps|10"`
- `-CMB`: Dart-Combo  
  Beispiel: `"-CMB" "t20,t20,t20=ps|4|d:8"`
- `-PIDE`: Spieler-Name Idle  
  Beispiel: `"-PIDE" "john=ps|10"`

---

## 6. Datenfluss: Preset senden (End-to-End)

```
User klickt "Senden" im Preset-Editor
        ↓
wled_presets.js
  POST /api/wled-presets/send
  { preset: 5, slot: 1, host: "Dart-Led1.local" }
        ↓
autodarts-web.py (Flask)
  1. Hostname → IP (avahi/DNS)
  2. GET /json/info  → Erreichbarkeit
  3. Mutex-Lock acquiren
  4. GET /json/state → WLED an/aus?
  5a. Falls AN:
      POST /json/state {"ps": 5}           (Preset laden)
      POST /json/state {"psave": 5, ...}   (Preset überschreiben)
  5b. Falls AUS:
      POST /json/state {"psave": 5, "on": true, "bri": 175, "col": [255,160,0], ...}
  6. 1,1 s warten
  7. Lock freigeben
        ↓
Response: { ok: true, msg: "Preset 5 saved", ip: "...", ... }
        ↓
UI: Status "OK" anzeigen
```

---

## 7. Python-Hilfsfunktionen (Backend)

### Konfiguration

```python
load_wled_config() -> dict
save_wled_config(cfg: dict)
get_enabled_wled_hosts(cfg: dict) -> list[str]
```

### Preset-Datei-Verwaltung

```python
load_wled_presets_state() -> tuple[bool, str, list[dict], str]
save_wled_presets_state(rows_payload) -> tuple[bool, str, list[dict], str]
_wled_presets_read_file() -> list[str]
_wled_presets_row_from_line(line: str) -> dict | None
```

### WLED-Kommunikation (mit Mutation-Locks)

```python
_wled_json_get(ip_or_host, path="/json/state", timeout_s=1.2)
_wled_json_post(ip_or_host, payload, path="/json/state", timeout_s=1.8)
_wled_begin_mutation(ip_or_host, min_spacing_s=1.1)
_wled_end_mutation(key, lock, settle_s=0.9)
```

### Target-Auflösung

```python
_resolve_wled_target_for_slot_or_host(slot, fallback_host=None)
_wled_check_one(host) -> tuple[bool, str | None]
is_wled_reachable(ip_or_host, timeout_sec=1.2) -> bool
```

### Service-Verwaltung

```python
apply_wled_services_from_config(cfg, restart=False) -> tuple[bool, str]
update_darts_wled_start_custom_weps(hosts) -> tuple[bool, str]
```

---

## 8. Systemd-Services & Integration

| Service | Zweck |
|---------|-------|
| `darts-wled.service` | WLED-Daemon |
| `darts-caller.service` | Caller-Daemon (Voraussetzung für WLED) |
| `autodarts-wled-reconnect-watchdog.timer` | Gesundheits-Check Timer |

**Abhängigkeiten:**
- WLED läuft nur, wenn Caller aktiv ist
- Caller kann ohne WLED laufen
- Gemeinsamer Watchdog überwacht beide

---

## 9. Caching & Timing

| Cache | TTL | Zweck |
|-------|-----|-------|
| WLED-Erreichbarkeit | 3 s | Status-Abfragen |
| DNS-Auflösung | 60 s | `.local`-Hostnamen |
| HTTP-Responses | 4 s | WLED-State |
| Mutation-Spacing | 1,1 s | Zwischen Preset-Operationen |
| Lock-Settle | 0,9 s | Nach Mutation |

---

## 10. Standard-Werte

| Parameter | Standard |
|-----------|---------|
| Helligkeit | 175/255 |
| Ereignis-Dauer | 5 Sekunden |
| Sleep-Timeout | 300 Sekunden |
| Board-Stop-Verzögerung | 0,4 Sekunden |
| Standard-Farbe | Orange `[255, 160, 0]` |

---

## 11. Preset-Editor UI (wled_presets.js)

### Visuelles Dartboard
- Interaktives Dartboard zur Feld-Auswahl (1–20, Bull, Single Bull)
- Mehrfach-Auswahl (bis zu 3 Darts)
- Bulk-Buttons: "Alle Singles", "Alle Doubles", "Alle Triples"

### Target-Auswahl
- Mehrfach-Auswahl für bis zu 3 WLED-Controller (Slots)
- Online/Offline-Status-Anzeige
- Auswahl wird in `localStorage` gecacht

### Preset-Verwaltung (Zeilen)
- Inline Preset-Nummer (auto-vergeben)
- Argument-Anzeige und Konfiguration
- Dauer in Sekunden (für Events)
- Zusatzfelder (Score, Bereich, Wert, erweiterte Optionen)
- Status-Indikatoren: **Nicht gespeichert / Nicht gesendet / OK**

### Code-Vorschau
- Live-Vorschau der `start-custom.sh`-Ausgabe
- Zeigt tatsächlichen Python-Befehl mit allen Argumenten
- Debug-Modus umschaltbar

---

## 12. Sprachunterstützung

UI-Strings über `static/lang/lang_*.json`:
- `lang_de.json` – Deutsch
- `lang_en.json` – Englisch

Deckt alle UI-Elemente, Meldungen und Fehlerbeschreibungen ab.
