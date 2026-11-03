# Handoff für neuen Claude-Chat — Clean-Start

> Zweck: Dieser Chat hatte beschädigte Daten. User lädt frische Daten ins Projekt und startet einen NEUEN Chat.
> Diese Notiz enthält alles, was der neue Chat ohne Vorwissen braucht.
> **Zeilennummern sind ca.-Werte** (frische Daten können verschoben sein) → lieber per Funktions-/ID-Namen suchen.

---

## Rahmen / Grundregeln (WICHTIG)

- Projekt: **Autodarts Webpanel** (Flask/Python). Hauptdatei `autodarts-web.py` (~10.140 Zeilen).
- Arbeitsordner: **`latest_Github/`** — hier wird gearbeitet.
- ⛔ **NIEMALS** nach `latest/` kopieren/pushen — das macht der User selbst.
- Deployment: `webpanel.zip`. Der Updater (`autodarts-webpanel-update.sh`) installiert daraus NUR `autodarts-web.py` + Ordner `templates static theme configs` + bestimmte Einzeldateien. Lose `.py`-Dateien im Zip-Root werden ignoriert.
- Rücksprung-Punkt: Git-Tag **`backup-vor-refactor`** (stabiler Stand).

---

## AUFGABE 1 — v2-Welcome-Popup stabiler machen (das eigentliche To-Do)

### Hintergrund (analysiert, bestätigt)
Es gibt beim Migrieren auf Autodarts 2.0 ein Popup ("Glückwunsch, Autodarts 2.0 installiert" + Button "Systemaktualität prüfen" = Chain-Fix). Dieses Popup kam beim User nicht.

**Ursachen (zwei getrennte Probleme):**

1. **Fragiles Flag (Hauptproblem):**
   `check_and_consume_v2_welcome()` (in `autodarts-web.py`, Funktion ~Zeile 7085, aufgerufen im `index()`-`render_template` ~Zeile 7364) setzt das Flag `V2_WELCOME_FLAG_FILE` (`/var/lib/autodarts/v2-welcome-shown.flag`) **beim RENDERN der Seite** — nicht beim Klick. Es ist ein "check AND consume" Einmal-Schuss. Folge: der erste `/`-Request, der die Bedingung erfüllt, verbrennt das Flag server-seitig — **auch ein unsichtbarer/halb geladener Request oder nervöses F5**. Danach kommt das Popup nie wieder.

2. **z-index-Konflikt (Nebenproblem):**
   - Welcome-Overlay `#v2WelcomeOverlay` nutzt Klasse `.video-modal-overlay` → **z-index 3000** (in `static/css/main.css` ~Zeile 336). Server-gerendert mit `is-open`, sofort sichtbar.
   - Autodarts-Update-Modal `#adUpdateModal` → **z-index 9000** inline (in `templates/index.html` ~Zeile 1342), per JS `showAdUpdateModal()` (~Zeile 1470) gezeigt wenn ein Update fertig ist.
   - Da Auto-Update default AN ist, startet beim ersten Laden gleichzeitig ein Autodarts-Update. Dessen Ergebnis-Popup (9000) legt sich **über** das Welcome-Popup (3000) → User sieht nur das Update-Popup.

### ⚠️ Bereits geklärt — KEIN Handlungsbedarf (nicht "fixen"!)
Das Welcome-Flag steuert **NUR das Popup**. Die eigentliche Installation ist komplett **getrennt und idempotent**:
`autodarts-v2-migration.sh` entscheidet über `classify_state()` (~Zeile 461-535) anhand des **echten** Autodarts-Zustands. Bei `V2_HEALTHY` → `write_state "already_v2"` → **Skip, kein Installer** (§18 Idempotenz).
→ Ein fehlendes/verbrauchtes Flag löst **NIEMALS** eine Neuinstallation aus. Diese Trennung ist korrekt und soll so bleiben.

### Fixes (das ist zu tun)
- **Fix 1 (Kern):** Flag erst beim **Klick** auf den Button setzen, nicht beim Render.
  - `check_and_consume_v2_welcome()` so umbauen, dass es nur noch **prüft** (ob Popup gezeigt werden soll) und das Flag NICHT mehr setzt.
  - Das Setzen des Flags in einen API-Call verlagern, den der Button auslöst. Kandidat: die bestehende Route `/api/v2-chain-fix` (`api_v2_chain_fix()`, ~Zeile 8688) erweitern, sodass sie das Flag setzt — oder eine eigene kleine Route "welcome-dismiss".
  - Damit kann kein Refresh das Popup mehr wegfressen; es verschwindet nur durch echten Klick/Dismiss.
- **Fix 2 (optional, UX):** Entweder Welcome-Overlay z-index > 9000 setzen, ODER `adUpdateModal` unterdrücken solange das Welcome-Overlay offen ist. Damit das wichtigere Migrations-Popup nicht vom Update-Ergebnis-Popup begraben wird.

### Referenz-Stellen (per Name suchen, Zeilen ca.)
- `check_and_consume_v2_welcome` → `autodarts-web.py` ~7085, Aufruf ~7364
- `V2_WELCOME_FLAG_FILE` Konstante → ~220
- `api_v2_chain_fix` / Route `/api/v2-chain-fix` → ~8688
- `#v2WelcomeOverlay` HTML + Button `v2ChainFix()` → `templates/index.html` ~1555-1591
- `#adUpdateModal` + `showAdUpdateModal()` → `templates/index.html` ~1342 / ~1470
- `.video-modal-overlay` CSS (z-index 3000) → `static/css/main.css` ~336
- Migration-Entscheidung `classify_state` / `write_state` → `autodarts-v2-migration.sh` ~461-535

---

## AUFGABE 2 — Refactoring autodarts-web.py (großer Plan, separat)

Falls vorhanden: Details in `PLAN_CODE_AUFTEILUNG.md`. Kurzfassung:

- Ziel: Die ~10.140-Zeilen-Datei in saubere Module aufteilen.
- **Gewählter Weg = Option A (Piggyback):** Module nach `configs/webapp/` legen. In `autodarts-web.py` eine Zeile `sys.path.insert(0, BASE_DIR + "/configs")`, dann saubere Imports `from webapp.xxx import ...`.
  - Warum: Der Updater kopiert `configs/` **schon jetzt** → funktioniert in EINEM Release, kein zweistufiger Rollout, kein Absturz-Risiko, Updater unverändert.
  - Beim Zippen: `configs/` muss immer `configs/wled/presets_default.json` UND `configs/webapp/...` enthalten (Updater macht `rm -rf configs` + Neukopie).
- **Vor dem Aufteilen:** doppelte Funktionen bereinigen (z.B. `get_webpanel_version`, `fetch_latest_webpanel_version`, `load/save_webpanel_update_*`, `service_enable_now/enable/...` sind je zweimal definiert; Python nutzt die letzte, Rest ist toter Code).
- Reihenfolge: config.py + settings.py zuerst → utils/system.py → modules/ → routes/ (Blueprints). Nach jedem Schritt lokal starten + committen.

---

## Nach jedem funktionierenden Schritt
- Lokal testen (`python autodarts-web.py` startet ohne Import-Fehler).
- Committen → kleine Rücksprung-Punkte.
- NICHT nach `latest/` kopieren.
