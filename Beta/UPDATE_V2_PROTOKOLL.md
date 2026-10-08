# Update V2.000 — Arbeitsprotokoll

**Ziel:** Einmaliges großes Update das alle Systeme auf Autodarts v2-Architektur bringt.  
**Danach:** Zukünftige Updates prüfen nur noch „ist Autodarts v2 aktiv?" → fertig.  
**Aktueller Stand:** v1.803 (stable)

---

## Warum V2.000?

Autodarts v2 läuft als systemd **User-Service** für User „peter" auf Port **3180**.  
Das ist eine andere Architektur als v1 (System-Service, anderer Port).  
Die Migration muss einmalig, sicher und testbar sein — daher auch der Beta-Kanal.

---

## Neue Dateien (werden über Updater verteilt)

| Datei | Zweck | Status |
|---|---|---|
| `autodarts-userctl` | Wrapper: Root-Prozesse können User-Service steuern | ✅ Fertig |
| `autodarts-v2-migration.sh` | Einmalige v1→v2 Migration | ✅ Fertig |
| `autodarts-webpanel-update.sh` | Beta-Kanal + v2-Migration integriert | ✅ Fertig |

---

## Phasen

### Phase A — autodarts-userctl ✅ (08.10.2026)
Wrapper-Script damit Root-Prozesse (Flask, button-led) den User-Service steuern können.  
`/usr/local/bin/autodarts-userctl` → steuert `autodarts.service` für User „peter".  
Zwei Methoden: `systemctl -M peter@.host` (bevorzugt) → `sudo -u peter` (Fallback).

### Phase B — autodarts-v2-migration.sh ✅ (08.10.2026)
`/usr/local/bin/autodarts-v2-migration.sh` — idempotent:
1. Prüft ob v2 bereits läuft (User-Service aktiv ODER Port 3180) → skip
2. Linger aktivieren (Service überlebt ohne Login)
3. v1 System-Service stoppen + autodartsupdater deaktivieren
4. Offizieller Installer von get.autodarts.io / get.autodarts.com (als User „peter")
5. User-Service starten + enable
6. Verifizierung Port 3180 (10 × 2s)

### Phase C — Updater: Beta-Kanal + v2-Integration ✅ (08.10.2026)
- Kanal via Env-Variable: `AUTODARTS_WEBPANEL_CHANNEL=beta` oder `=stable`
- Beta: `BASE_URL = /main/Beta`, `FORCE=1` (kein Versionscheck)
- Stable: `BASE_URL = /main/latest`, normaler Versionscheck
- `autodarts-userctl` + `autodarts-v2-migration.sh` in FILES-Array
- `run_v2_migration_if_downloaded()` — selbes DOWNLOADED[]-Pattern wie andere Hooks

### Phase D — Webpanel: Beta-Kanal UI ✅ (08.10.2026)
- Checkbox-Toggle in Admin-Bereich: Stable / Beta
- `save_setting("webpanel_channel", ...)` schreibt atomar in webpanel-settings.json
- Route `POST /api/webpanel/channel` → JSON Response
- `start_webpanel_update_background()` liest Kanal und setzt `AUTODARTS_WEBPANEL_CHANNEL` Env-Var
- i18n Keys in lang_de.json + lang_en.json eingetragen

### Phase F — Dartscheiben-Konfigurator ✅ (08.10.2026)
- `is_autodarts_v2_running()` — Socket-Check Port 3180
- `is_ttyd_running()` — Socket-Check Port 7681
- Template-Variablen: `autodarts_v2_running`, `autodarts_ttyd_running`, `darts_ttyd_url`
- UI in Section 2: Status-Dot, Buttons (Manager, Terminal, Konfigurator einblenden)
- Eingeblendet: 3 Kamera-Streams (MJPEG img-Tags) + eingebetteter iframe Board-Manager
- Fallback: img onerror → "kein Signal", Link "Im neuen Tab öffnen"

### Phase E — Version auf 2.000 + Release ✅ (08.10.2026)
- version.txt → `2.000`
- BUILD-Tag in update.sh: `WEBPANEL-UPDATER-V2-REVCHAIN-20261008-02`
- **Nächster Schritt:** Dateien in Beta-Branch pushen → auf Pi testen

### Phase G — Auto-Update AN/AUS komplett entfernt ✅ (08.10.2026)
- `autodarts-web.py`: Funktionen, Routen, Template-Vars entfernt (aus Vorherige Session)
- `templates/index.html`: Sysinfo-Bar + Section-2-Block entfernt
- `static/lang/lang_de.json` + `lang_en.json`: alle `autoupdate.*` + `common.autoupdate_*` + `sysinfo.autoupdate_label` + `confirm.autoupdate_enable` Keys entfernt
- Keine JS-Referenzen auf autoupdate gefunden

### Phase H — Revisionskette/Runner + Slim-Updater ✅ (08.10.2026)
- `latest_Github/update-revisions.conf`: `UPDATE_REVISIONS="1.808;latest"`
- `latest_Github/autodarts-webpanel-update.sh`: **komplett neu geschrieben, 282 Zeilen** (war ~880)
  - Nur noch: Revision-Chain-Runner, Versionscheck, webpanel.zip installieren, Einzeldateien, Service-Restart
  - Entfernt: UVC-Hack, autodartsupdater-Fallback, Desktop-Migration, Caller-Boot-Stabilize, AP-Internet-Fix, Extensions-Repair, Poweroff-Shortcut, Display-Tools
  - FILES: nur noch button-led, extensions-update, caller-auth-reset, version.txt, updater selbst
  - Beta-Kanal: überspringt Revisionskette komplett
  - Log: `===== Installierte Version: X | Update-Kette: 1.808;latest =====`
- `1.808/autodarts-webpanel-update.sh`: Checkpoint-Script (lädt + startet v2-Migration, setzt Version 1.808)
- `1.808/autodarts-v2-migration.sh` + `1.808/autodarts-userctl`: Dateien für Checkpoint-Download

---

---

## Push-Anleitung (einmalig)

```bash
# Im Repo-Root ausführen:
cd ~/Documents/GitHub/Autodarts-Webinterface-Installation

# 1. Remote-Stand holen (du bist 1 Commit hinter origin/main)
git pull

# 2. Alle geänderten + neuen Dateien stagen
git add latest_Github/autodarts-webpanel-update.sh
git add latest_Github/autodarts-web.py
git add latest_Github/templates/index.html
git add latest_Github/static/lang/lang_de.json
git add latest_Github/static/lang/lang_en.json
git add latest_Github/update-revisions.conf
git add latest_Github/UPDATE_V2_PROTOKOLL.md
git add 2.00/

# 3. Commit
git commit -m "v2.000: Revisionskette, Auto-Update entfernt, Beta-Kanal, v2-Migration"

# 4. Push
git push
```

**Was danach auf dem Pi testen:**
```bash
# Beta-Kanal manuell starten (als root):
AUTODARTS_WEBPANEL_CHANNEL=beta sudo /usr/local/bin/autodarts-webpanel-update.sh

# Log live mitverfolgen:
tail -f /var/log/autodarts_webpanel_update.log

# Erwartete Log-Ausgabe (stable, System auf < 2.00):
# ===== Installierte Version: 1.803 | Update-Kette: 2.00;latest =====
# CHAIN: Checkpoint 2.00 -> starten
# [CP-2.00] ===== Checkpoint 2.00 START =====
# ...V2 Migration...
# [CP-2.00] ===== Checkpoint 2.00 OK =====
# CHAIN: Lokale Version nach Checkpoint 2.00: 2.00
# ===== Revisionskette abgeschlossen -> weiter mit latest =====
# ...normales Webpanel-Update...
# ===== Webpanel Update OK =====

# Prüfen ob v2 läuft:
systemctl --user -M peter@.host status autodarts.service
curl -s http://localhost:3180/api/version
```

---

## Bereits erledigt (Vorarbeit aus letzter Session)

| Was | Wann |
|---|---|
| Bootstrap-Updater: `bash -n` Check + exec-Subshell | 04.10.2026 |
| `autodartsDisplayBox` hidden-Bug gefixt | 04.10.2026 |
| Themes-Section umbenannt (5. Autodarts Layout/Theme) | 04.10.2026 |
| Auflösung: Reboot-Warnung + „Fix einstellen"-Button | 04.10.2026 |
| `ensure_display_tools()` in Updater (lxrandr, wdisplays) | 04.10.2026 |
| `autodarts-poweroff-shortcut.sh` + Updater-Integration | 04.10.2026 |

---

## Grundregel für alle Scripts

```
1. Prüfe ob Zustand bereits korrekt → skip (idempotent)
2. Führe Aktion aus
3. Verifiziere das Ergebnis
4. Marker setzen
```

Nie blind reinstallieren wenn v2 bereits gesund läuft.
