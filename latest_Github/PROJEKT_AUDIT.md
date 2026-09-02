# Projekt-Audit: Autodarts Webinterface Installation
**Stand:** 2026-09-03 · **Reviewer:** Claude Sonnet 4.6  
**Build-Tag:** WEBPANEL-CALLERTOGGLE-CALLERWATCH-UPDATEFALLBACK-20260902-01

---

## 1. Projektverständnis / Architektur-Überblick

### Was das Projekt macht
Ein selbst-gehostetes **Flask-Webpanel** für Autodarts auf einem Raspberry Pi (oder Mini-PC).  
Der Nutzer verbindet sich über ein eigenes WLAN-Access-Point (`Autodartsinstall*`) und richtet  
dann über die Weboberfläche WLAN, Kamera, LED-Stripes (WLED), darts-caller und darts-wled ein.

### Hauptkomponenten

| Datei / Ordner | Funktion |
|---|---|
| `autodarts-web.py` | Flask-Backend (~3600 Zeilen), alle Routes, Systemlogik |
| `autodarts-button-led.py` | GPIO-Script: Hardware-Taster + LED-Status-Ampel am Pi |
| `autodarts-webpanel-update.sh` | Self-Update-Script: zieht Dateien von GitHub, installiert Webpanel |
| `autodarts-extensions-update.sh` | Updater für darts-caller / darts-wled |
| `autodarts-extensions-v2-install.sh` | Erstinstallation Extensions V2 |
| `autodarts-caller-auth-reset.sh` | Reset der Caller-Anmeldedaten |
| `fix_ap_internet_sharing_v3.sh` | AP-Internet-Durchleitung reparieren |
| `templates/` | Jinja2-HTML-Templates |
| `static/js/` | Frontend-JavaScript (i18n, main, WLED, Ping, Cam, etc.) |
| `static/lang/` | Sprachdateien DE/EN + `config_lang.json` |
| `static/css/` | Seitenspezifische CSS |
| `theme/` | 20+ Autodarts-App-Themes (CSS + JPG-Preview) |

### Technologie-Stack
- **Backend:** Python 3, Flask, systemd via subprocess, nmcli, v4l2-ctl, iw, vcgencmd
- **Frontend:** Vanilla JS (kein Framework), i18n-System über API-Call
- **Update-Mechanismus:** GitHub Raw + curl, webpanel.zip, einzelne Dateien
- **Service-Management:** systemd (systemctl), systemd-run für Hintergrundprozesse

---

## 2. Gefundene Bugs

### B-01 · `main.js` – lokale `tr`-Funktion überschreibt globale
**Datei:** `static/js/main.js`, Zeile ~1011  
In `initApClientInternetUi` ist eine lokale `tr`-Funktion definiert, die die globale `window.t`/`tr`-Funktion überschattet. Die lokale Version unterstützt **keine Variablen-Ersetzung** (`{key}`), was bei zukünftigen i18n-Keys zu stillen Fehlern führt.
```js
// Problem: lokale tr-Def überschattet globale
function applyState(ok, note) {
  const tr = (key, fallback) => { ... }  // keine vars-Unterstützung!
```
**Fix:** Lokale `tr`-Definition entfernen, `window.t` direkt verwenden.

### B-02 · Leftover-Kommentar in `main.js`
**Datei:** `static/js/main.js`, Zeile 23  
```js
initVideoModalUi(); // hinzufügen
```
Entwicklungskommentar, der nicht entfernt wurde. Kein Funktionsfehler, aber unprofessionell.

### B-03 · `autodarts-button-led.py` – `systemctl is-active` ohne Timeout
**Datei:** `autodarts-button-led.py`, Zeile 100–105  
Die `is_autodarts_active()`-Funktion im Button-Script hat kein `timeout`-Argument, während `autodarts-web.py` dieselbe Funktion **mit** `timeout=1.0` implementiert. Wenn systemctl hängt, blockiert das den gesamten LED-Manager-Thread.
```python
# Kein timeout → kann hängen
result = subprocess.run(["systemctl", "is-active", SERVICE_NAME], ...)
```
**Fix:** `timeout=2` hinzufügen (analog zu autodarts-web.py).

### B-04 · Race-Condition in `led_manager` / Button-Handler
**Datei:** `autodarts-button-led.py`, globale Variablen `press_time`, `shutdown_armed`  
Diese Variablen werden aus dem LED-Manager-Thread **und** den GPIO-Callback-Threads (`on_press`, `on_release`) ohne Lock gelesen/geschrieben. In Python ist das bei einfachen Reads/Writes zwar durch den GIL meistens sicher, aber nicht garantiert – besonders beim kombinierten Check-then-Act in `led_manager`:
```python
if press_time is not None and not shutdown_armed:
    if time.monotonic() - press_time >= SHUTDOWN_MIN:
        shutdown_armed = True
```
**Fix:** `threading.Lock()` für `press_time` und `shutdown_armed` verwenden.

### B-05 · `start_autodarts_update_background` – offene Datei-Handle
**Datei:** `autodarts-web.py`, ca. Zeile 2041  
`logf = open(...)` wird übergeben an `subprocess.Popen(stdout=logf)`, aber `logf.close()` wird nie explizit aufgerufen. Das Handle wird erst beim Garbage-Collect geschlossen – was bei langlebigen Flask-Prozessen zu File-Descriptor-Leaks führen kann.
```python
logf = open(AUTODARTS_UPDATE_LOG, "a", encoding="utf-8")
# ... logf wird nie geschlossen
p = subprocess.Popen(..., stdout=logf, stderr=logf)
```
**Fix:** `with open(...) as logf: p = subprocess.Popen(...)` – aber Achtung: `close_fds=True` ist bereits gesetzt, also müsste `logf` nach `Popen` explizit mit `logf.close()` geschlossen werden.

### B-06 · Cache-Dict nicht thread-safe
**Dateien:** `autodarts-web.py`  
Mehrere Modul-Level-Dicts werden als Cache genutzt (`INDEX_STATS_CACHE`, `WLED_STATUS_CACHE`, `_AUTODARTS_LATEST_CACHE`, `AUTODARTS_VERSION_CACHE`), ohne `threading.Lock`. Flask kann multi-threaded laufen (Gunicorn/Waitress), was zu korrupten Cache-Inhalten führen kann.

---

## 3. Sicherheitsprobleme

### S-01 · KRITISCH – `chmod 777` auf System-Verzeichnisse
**Datei:** `autodarts-webpanel-update.sh`, Zeile 733 + überall im Script  
```bash
chmod 777 "${BIN_DIR}" "${DATA_DIR}" "${STATE_DIR}"  # BIN_DIR = /usr/local/bin!
chmod 777 "${dst}"   # für jede installierte Datei
chmod 777 "${BIN_DIR}/autodarts-web.py"
```
`/usr/local/bin` world-writable zu machen ist ein **kritisches Sicherheitsrisiko**. Jeder Prozess/Nutzer auf dem System kann dann beliebige Binaries dort ersetzen. Auf einem Netzwerkgerät ist das besonders gefährlich.
**Fix:** Maximale Permissions: `755` für Verzeichnisse, `644`/`755` (executable) für Dateien.

### S-02 · HOCH – Schwaches Admin-Passwort als Default
**Datei:** `autodarts-web.py`, Zeile ~381  
```python
"admin_password": "1234",
```
Das Standard-Admin-Passwort "1234" wird bei vielen Installationen nie geändert. Es gibt keinen Zwang beim Erststart, ein neues zu setzen.
**Fix:** Zufälliges Passwort beim ersten Start generieren und dem Nutzer anzeigen, oder erzwingen, dass es geändert werden muss.

### S-03 · HOCH – Schwacher Flask Session-Secret
**Datei:** `autodarts-web.py`, Zeile 50  
```python
app.secret_key = os.environ.get('AUTODARTS_WEB_SECRET', 'autodarts-web-admin')
```
Der Default-Key `'autodarts-web-admin'` ist bekannt (Open Source auf GitHub). Damit können Sessions gefälscht werden (Session-Forging).
**Fix:** Beim ersten Start einen kryptografisch zufälligen Key generieren und persistent speichern (z.B. in `/var/lib/autodarts/secret.key`).

### S-04 · MITTEL – Kein CSRF-Schutz
**Datei:** Alle Templates mit state-ändernden Forms  
Forms verwenden nur `window.confirm()` als "CSRF-Schutz" – das schützt nicht gegen tatsächliche CSRF-Angriffe. Ein bösartiger Link/Seite kann POST-Requests zu den Routes senden.
**Fix:** Flask-WTF CSRF-Tokens oder eigenes CSRF-Token-System implementieren.

### S-05 · MITTEL – Remote-Script ohne Verifikation ausgeführt
**Datei:** `autodarts-webpanel-update.sh`, UVC-Hack  
```bash
bash <(curl -sL get.autodarts.io/uvc)
```
Remote-Script wird ohne Hash-Verifikation ausgeführt. MITM-Angriff möglich.
**Fix:** Script herunterladen, SHA256-Hash gegen bekannten Wert prüfen, dann ausführen.

### S-06 · NIEDRIG – Admin-GPIO-Bild-Pfad hardcoded
**Datei:** `autodarts-web.py`, Zeile 140  
```python
ADMIN_GPIO_IMAGE = "/home/peter/autodarts-data/GPIO_Setup.jpeg"
```
Pfad mit hardcoded Username. Falls der User nicht "peter" heißt, ist das Bild nicht auffindbar.

---

## 4. Code-Qualität / Verbesserungen

### Q-01 · `autodarts-web.py` ist zu groß (~3600+ Zeilen)
**Problem:** Eine einzelne Python-Datei mit allen Routes, Hilfsfunktionen, Systemlogik und Konfiguration. Schwer zu navigieren, schwer zu testen.  
**Empfehlung:** Flask Blueprints einführen:
```
autodarts/
  __init__.py       (app factory)
  blueprints/
    admin.py        (admin routes)
    wifi.py         (wifi routes)
    camera.py       (camera routes)
    wled.py         (wled routes)
    updates.py      (update routes)
    api.py          (JSON API routes)
  services/
    system.py       (subprocess-Wrappers)
    settings.py     (settings load/save)
    network.py      (nmcli-Wrappers)
```

### Q-02 · Hardcoded Username "peter"
**Problem:** Der Username "peter" erscheint an ~15 Stellen im Code als Pfad oder sudo-Ziel:
```python
"/home/peter/autodarts-data"
"/home/peter/.config/darts-caller/tokens.json"
popen_cmd = ["sudo", "-u", "peter", ...]
```
**Empfehlung:** Dynamisch ermitteln:
```python
AUTODARTS_USER = os.environ.get("AUTODARTS_USER", pwd.getpwuid(os.getuid()).pw_name)
```

### Q-03 · Versionsverwaltung inkonsistent
**Problem:** `WEBPANEL_HARDCODED_VERSION = "1.762"` direkt im Python-Script. Parallel gibt es eine `version.txt` und `WEBPANEL_VERSION_FILE`. Drei Quellen für dieselbe Info.  
**Empfehlung:** Nur `version.txt` als Single Source of Truth. `WEBPANEL_HARDCODED_VERSION` entfernen.

### Q-04 · i18n lädt bei jedem Seitenaufruf neu
**Datei:** `static/js/i18n.js`  
Die Sprachdaten werden bei jedem Seitenaufruf über `/api/langs` geladen (kein Browser-Caching, kein localStorage). Das verzögert sichtbar den ersten Render.  
**Empfehlung:** Sprachdaten per `localStorage` mit Version-Key cachen:
```js
const cached = localStorage.getItem('lang_cache_v' + appVersion);
if (cached) { window.lang_config = JSON.parse(cached); return; }
```

### Q-05 · `safe_cam_config` schreibt direkt ohne atomares Rename
**Datei:** `autodarts-web.py`, Funktion `save_cam_config`  
```python
with open(CAM_CONFIG_PATH, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2)
```
Während des Schreibens könnte die Datei korrumpiert werden (Stromausfall, Prozess-Kill). `autodarts-button-led.py` macht es korrekt mit atomarem `tmp_path.replace(CAM_CFG_PATH)`. 
**Fix:** Gleiche Methode wie in `autodarts-button-led.py` verwenden.

### Q-06 · Update-Script bettet gesamten Updater als Heredoc ein
**Datei:** `autodarts-webpanel-update.sh`, Funktion `install_autodarts_update_fallback`  
Das gesamte `autodarts-safe-updater.sh` ist als heredoc eingebettet (~100 Zeilen). Bei Änderungen muss man an zwei Stellen editieren.  
**Empfehlung:** Als separate Datei im Repo führen und nur herunterladen.

### Q-07 · `run_once` Marker können veralten
**Datei:** `autodarts-webpanel-update.sh`  
`run_once "Kernel_hold_2026-07-06_off"` – der Marker-Name enthält ein fest eingebautes Datum. Nach einem Kernel-Update oder Reinstall kann der Marker fehlen, und die Aktion wird erneut ausgeführt. Das ist gewollt für Kernel-abhängige Aktionen, aber verwirrend.

### Q-08 · `/usr/local/bin` als Webpanel-Installationsort
**Problem:** Web-App-Dateien (`autodarts-web.py`, `templates/`, `static/`) liegen in `/usr/local/bin`. Dieser Ordner ist für ausführbare Binaries gedacht, nicht für Web-Apps.  
**Empfehlung:** Besser: `/opt/autodarts-webpanel/` oder `/var/lib/autodarts-webpanel/`.

### B-07 · `darts-caller.service` startet zu früh beim Booten
**Datei:** `autodarts-webpanel-update.sh`, neue Funktion `install_caller_boot_stabilize`  
Nach einem Kaltstart oder Reboot startet `darts-caller.service` bevor Netzwerk und `autodarts.service` bereit sind → Verbindungsfehler, die erst nach manuellem Neustart verschwinden.

**Fix (zwei Stellen):**

1. **`autodarts-extensions-v2-install.sh`** – Service-Definition direkt angepasst (Zeile 920–938). Die Boot-Stabilize-Settings sind jetzt direkt im Service baked-in:
```ini
[Unit]
Wants=network-online.target autodarts.service
After=network-online.target autodarts.service

[Service]
ExecStartPre=/bin/sleep 12
RestartSec=8
```

2. **`autodarts-webpanel-update.sh`** – Funktion `install_caller_boot_stabilize` schreibt denselben Inhalt als Drop-in `/etc/systemd/system/darts-caller.service.d/boot-stabilize.conf` bei jedem Webpanel-Update (Sicherheitsnetz für Systeme ohne Reinstall). Prüft ob `darts-caller.service` existiert (skip wenn nicht). Ist idempotent.

> **Wichtig:** `autodarts-extensions-v2-install.sh` Zeile 963 löscht `$CALLER_OVERRIDE_DIR` bei jedem (Re)install — deshalb sind die Settings direkt im Service (Punkt 1) die primäre Lösung, der Drop-in (Punkt 2) ist das Fallback.

---

## 5. Fehlende Infra / Nice-to-have

| # | Was fehlt | Priorität |
|---|---|---|
| I-01 | `requirements.txt` (Flask, gpiozero, …) | Hoch |
| I-02 | Tests (unit/integration) | Hoch |
| I-03 | `.gitignore` | Mittel |
| I-04 | Systemd-Service-Files im Repo | Mittel |
| I-09 | `darts-caller.service` Boot-Stabilize Drop-in | Hoch |
| I-05 | Rate-Limiting auf Admin-Login (Brute-Force-Schutz) | Mittel |
| I-06 | Proper 404/500-Fehlerseiten in Flask | Niedrig |
| I-07 | Log-Rotation für `/var/log/autodarts_*.log` (logrotate-Konfig) | Niedrig |
| I-08 | Changelog / CHANGELOG.md | Niedrig |

---

## 6. Was gut gemacht ist

- **GPIO-Script:** Gut strukturiert, saubere LED-Logik, robustes Diagnose-Logging beim Restart
- **Atomares JSON-Schreiben** (tmp + replace) in button-led.py
- **Fallback-Kette** für WiFi-Signal (proc → iw → nmcli) – sehr durchdacht
- **Multi-URL-Fallback** für Autodarts-Installer (.io → .com) mit Integritätsprüfung
- **Redaktion von Secrets** in Journal-Streams (passwort, token)
- **Caching** an vielen Stellen (Version, Index-Stats, WLED-Status) um subprocess-Last zu reduzieren
- **i18n-System** vollständig mit Fallbacks, data-key, data-placeholder-key, etc.
- **Webpanel-Update-Polling** im Frontend (reconnect nach Service-Neustart) – sehr gute UX
- **UVC-Backup-System** mit Safety-Checks vor und nach dem Hack
- **AP-Kanal-Wechsel** mit Countdown und Cancel-Möglichkeit – robust gelöst

---

## 7. Arbeitsplan (priorisiert)

### Sprint 1 – Kritische Sicherheit
- [ ] **S-01** `chmod 777` → maximal `755`/`644` überall im Update-Script
- [ ] **S-03** Flask Secret-Key zufällig generieren und persistent speichern
- [ ] **S-02** Admin-Passwort-Ersteinrichtung erzwingen (oder zufällig generieren)

### Sprint 2 – Bugs
- [x] **B-01** Lokale `tr`-Funktion in `initApClientInternetUi` entfernen *(erledigt 2026-09-02)*
- [x] **B-02** Kommentar `// hinzufügen` in main.js entfernen *(erledigt 2026-09-02)*
- [x] **B-03** Timeout in `autodarts-button-led.py` `is_autodarts_active()` hinzufügen *(erledigt 2026-09-02)*
- [ ] **B-04** `threading.Lock()` für Button-LED globale Variablen
- [ ] **B-05** `logf`-Handle nach `Popen` schließen
- [ ] **Q-05** `save_cam_config` atomar machen

### Sprint 3 – Code-Qualität
- [x] **Lang-Duplikate** `themes.sync_hint` + `themes.install_hint` doppelt in lang_en.json + lang_de.json → alte Einträge entfernt *(erledigt 2026-09-03)*
- [x] **I-09** `darts-caller.service` Boot-Stabilize Drop-in in Update-Script integriert (`install_caller_boot_stabilize` Funktion) *(erledigt 2026-09-03)*
- [ ] **Q-02** Hardcoded "peter" überall durch `AUTODARTS_USER`-Variable ersetzen
- [ ] **Q-03** `WEBPANEL_HARDCODED_VERSION` entfernen, nur `version.txt` nutzen
- [ ] **I-01** `requirements.txt` erstellen

### Sprint 4 – Performance / UX
- [ ] **Q-04** i18n mit `localStorage`-Cache ausstatten
- [ ] **B-06** Thread-safe Cache-Wrappers mit `threading.Lock()`
- [ ] **S-04** CSRF-Schutz (Flask-WTF oder eigenes Token)

### Sprint 5 – Architektur (größere Umstrukturierung)
- [ ] **Q-01** `autodarts-web.py` in Blueprints aufteilen
- [ ] **Q-08** Installationsort von `/usr/local/bin` nach `/opt/autodarts-webpanel` verlagern
- [ ] **I-03** `.gitignore` anlegen
- [ ] **I-04** Systemd-Service-Files ins Repo aufnehmen

---

## 8. Offene Fragen / Zu klären

1. Läuft der Flask-Server als root oder als User "peter"? (Hat Einfluss auf sudo-Logik in Update-Script)
2. Soll das Admin-Passwort weiterhin in `webpanel-settings.json` liegen, oder besser in einem separaten secrets-File mit restriktiven Permissions?
3. Ist `wlan_ap` als AP-Interface-Name immer fix, oder soll das konfigurierbar werden?
4. Sollen neue Themes weiterhin manuell ins `theme/`-Verzeichnis gelegt werden, oder soll es einen "Theme Store"-Mechanismus geben?
5. Warum liegt `fix_ap_internet_sharing_v3.sh` noch im Repo wenn er beim Update zu `autodarts-ap-internet-fix.sh` umbenannt wird? Redundanz bereinigen?
6. Gibt es eine Strategie für Kernel-Updates? Der UVC-Hack-Marker ist Kernel-spezifisch, aber der Kernel-Hold könnte irgendwann gelöst werden müssen.

---

*Dieses Dokument ist der Ausgangspunkt für die Arbeit. Jeder Sprint-Task wird als eigener Commit/Branch umgesetzt.*
