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
- BUILD-Tag in update.sh: `WEBPANEL-UPDATER-V2-BETA-20261008-01`
- **Nächster Schritt:** Dateien in Beta-Branch pushen → auf Pi testen

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
