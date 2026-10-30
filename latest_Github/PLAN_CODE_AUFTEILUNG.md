# Plan: autodarts-web.py in Module aufteilen

> Stand: 2026-10-30 · Ausgangslage: `autodarts-web.py` hat ~10.140 Zeilen (Horror zu warten).
> Ziel: in kleine, wartbare `.py`-Dateien aufteilen — **ohne** den Self-Update-Mechanismus zu brechen.

---

## Sicherung / Rücksprung-Punkt

- **Git-Tag: `backup-vor-refactor`** → markiert den stabilen Stand **vor** jeder Aufteilung (Release v2.100, eine Datei).
- Zurück zur stabilen Version:
  ```bash
  git checkout backup-vor-refactor -- autodarts-web.py   # nur die eine Datei zurückholen
  # oder komplett:
  git checkout backup-vor-refactor
  ```
- Aktueller Code ist **unverändert** (bisher nur Analyse + Tag gesetzt).

---

## Wie das Webpanel deployed wird (der entscheidende Rahmen)

- Die App läuft als **eine Datei**: `/usr/local/bin/autodarts-web.py` (Start via `app.run(host="0.0.0.0", port=80)` am Ende).
- Update-Script `autodarts-webpanel-update.sh` lädt `webpanel.zip` und installiert daraus **nur**:
  - die Datei `autodarts-web.py`
  - die Ordner `templates`, `static`, `theme`, `configs` (Zeile 113: `for dir_name in templates static theme configs`)
- Lose `.py`-Dateien neben `autodarts-web.py` im Zip werden **ignoriert** → würden NICHT auf den Pi kopiert.
- **Grundgesetz:** Den Updater, der schon auf einem Pi liegt, können wir nicht rückwirkend ändern. Neue Module müssen also von etwas kopiert werden, das der **alte** Updater schon kann.

---

## Gewählter Weg: Option A — Piggyback im `configs/`-Ordner

Der alte Updater kopiert `configs/` **jetzt schon komplett**. Also legen wir die Module dort rein:

```
Zip / Repo:   configs/webapp/wlan.py, camera.py, wled.py, ...
Pi (deployed): /usr/local/bin/configs/webapp/...   (alter Updater kopiert automatisch mit)
```

In der schlanken `autodarts-web.py` genügt **eine** Zeile, danach saubere Imports:
```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "configs"))
from webapp.app import app          # Beispiel
from webapp.wlan import get_wifi_status
```

**Warum Option A:**
- ✅ Ein einziges Release (kein zweistufiger Rollout)
- ✅ Kein Absturz-Risiko beim Update (Updater kopiert `configs` schon lange)
- ✅ Echte getrennte Dateien zum Warten
- ✅ Updater bleibt **unverändert**
- ⚠️ Einziger Schönheitsfehler: Ordner liegt physisch unter `configs/` (im Code nur in der einen `sys.path`-Zeile sichtbar)

**Wichtig beim Zippen:** `configs/` muss IMMER sowohl `configs/wled/presets_default.json` ALS AUCH `configs/webapp/...` enthalten, weil der Updater `configs` vor dem Kopieren per `rm -rf` leert und komplett neu aus dem Zip befüllt. Nichts darf fehlen.

### Noch offener Sicherheits-Check
- Bestätigt: `configs`-Kopieren ist in V2.002, V2.003, latest vorhanden.
- Offen: ab welcher Version genau `configs` in der Kopier-Liste ist (für sehr alte Pis). Revisionskette: `UPDATE_REVISIONS="1.808;latest"`.

---

## Verworfene Alternativen

- **Zwei-Release-Rollout** (erst Updater lernt `webapp/`, dann Split): vom User abgelehnt — kein zweistufiger Rollout gewünscht.
- **Updater um einen Ordner erweitern** (`... configs webapp`): bräuchte wieder zwei Releases wegen Henne-Ei.
- **Build-Schritt** (viele Dev-Dateien → 1 Datei zusammenkleben): Updater unverändert, aber manueller Build-Schritt vor jedem Release + Tracebacks zeigen auf zusammengebaute Datei.

---

## Vorgeschlagene Modulstruktur (in `configs/webapp/`)

```
configs/webapp/
├── __init__.py
├── app.py              # Flask-App-Init, before_request
├── config.py           # Konstanten, Pfade, DEFAULT_SETTINGS
├── settings.py         # load_settings(), save_setting(), refresh_settings_if_needed()
├── utils/
│   ├── helpers.py      # t(), _forbidden_response(), _json_nocache(), redact_journal_line()
│   └── system.py       # service_* , _run_root(), _run_systemctl()
├── network/
│   ├── wifi.py         # get_wifi_status(), Signal-Helpers, Dongle-Reset
│   ├── ap.py           # AP-Status, Channel-Reboot
│   └── ping.py         # Gateway, _ping_worker(), start_ping_test()
├── modules/
│   ├── autodarts.py    # Version, Update-Logic, _get_ad_user_ctx(), Autostart, Restart
│   ├── webpanel.py     # Webpanel-Update, UVC-Backup, Version-Check, OS-Update
│   ├── extensions.py   # darts-caller, darts-wled, Extensions-V2, Watchdog
│   ├── wled.py         # WLED-Config, Presets
│   ├── camera.py       # detect_cameras(), Slots, Symlinks, v4l2
│   ├── display.py      # xrandr, wayland, Pi5-Headless
│   ├── theme.py        # Autodarts-Themes
│   ├── caller.py       # Caller-Credentials, Local-Install, BAT-Renderer
│   ├── audio.py        # pipewire, Sinks, Volume
│   ├── monitor.py      # Pi-Monitor, tail_file()
│   ├── firewall.py     # UFW
│   └── lang.py         # Sprach-JSON
└── routes/             # Flask Blueprints, je Feature eine Datei
    ├── main.py         # index(), help_page()
    ├── wifi.py, camera.py, wled.py, autodarts.py,
    ├── admin.py, audio.py, lang.py, system.py
```

`/usr/local/bin/autodarts-web.py` wird zur **schlanken Startdatei** (sys.path-Zeile + Imports + `app.run()`).

---

## Import-Regeln (gegen Zirkel-Importe)

- `routes/` darf aus `modules/`, `network/`, `utils/`, `config`, `settings` importieren
- `modules/`, `network/` dürfen aus `utils/`, `config`, `settings` importieren
- `config` und `settings` importieren von **niemandem** (müssen zuerst fertig sein)
- Globale Variablen (`SETTINGS`, `ADMIN_PASSWORD`, `app`) zentral in `config`/`settings`/`app`

---

## ⚠️ Vor dem Aufteilen zu bereinigen: doppelte Funktionen

Mehrfach definierte Funktionen in `autodarts-web.py` — Python nutzt jeweils die LETZTE, die früheren sind toter Code:

| Funktion | 1. Def | 2. Def |
|---|---|---|
| `get_webpanel_version()` | ~2013 | ~2280 |
| `fetch_latest_webpanel_version()` | ~2040 | ~2307 |
| `load_webpanel_update_check()` / `save_...` | ~2052 | ~2319 |
| `load_webpanel_update_state()` / `save_...` | ~2069 | ~2336 |
| `service_enable_now/enable/disable_now/restart/stop/is_enabled` | ~2684 | ~3329 |

---

## Empfohlene Reihenfolge (Session für Session)

1. `configs/webapp/`-Gerüst + `__init__.py` + schlanke `autodarts-web.py` (nur sys.path + import) — **lokal testen, dass es startet**
2. `config.py` + `settings.py` extrahieren (keine Abhängigkeiten)
3. `utils/system.py` (dabei Service-Duplikate bereinigen!)
4. `modules/autodarts.py` (Auto-Update, Restart, Autostart)
5. Danach `routes/` als Blueprints — eine Blueprint pro Session
6. Jeweils nach jedem Schritt: lokal starten + commiten

---

## Merksätze

- Arbeit NUR in `latest_Github/` — niemals nach `latest/` kopieren/pushen (macht der User selbst).
- Nach jedem funktionierenden Schritt committen → kleine, sichere Rücksprung-Punkte.
- `configs/webapp/` + `configs/wled/` müssen immer gemeinsam im Zip liegen.
