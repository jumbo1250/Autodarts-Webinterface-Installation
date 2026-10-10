# 08 — Captive Portal für den Autodarts-AP

## Was ist ein Captive Portal und warum öffnet sich der Browser automatisch?

Jedes Betriebssystem macht unmittelbar nach dem WLAN-Verbinden einen automatischen
**Connectivity-Check** — ein HTTP-Request an eine bekannte URL:

| Betriebssystem | geprüfte URL |
|----------------|-------------|
| Android        | `connectivitycheck.gstatic.com/generate_204` |
| iOS / macOS    | `captive.apple.com/hotspot-detect.html` |
| Windows        | `www.msftconnecttest.com/connecttest.txt` |

Wenn die Antwort **nicht** die erwartete ist (z. B. Redirect statt 204),
erkennt das OS: *"Kein echtes Internet, hier ist ein Portal"* →
Browser öffnet sich automatisch mit der Ziel-URL.

Genau das machen WLED, Shelly, Fritz!Box, Hotel-WLANs usw.

---

## Die bestehende AP-Infrastruktur (was bereits da ist)

Aus dem Code (`fix_ap_internet_sharing_v3.sh`):

| Komponente | Wert |
|-----------|------|
| AP-Interface | `wlan_ap` |
| AP-Verbindungsname | `Autodarts-AP` |
| AP-IP | `10.77.0.1` |
| Uplinks | `wlan0`, `eth0` |
| NAT/Forwarding | nftables (`autodarts_ap` Tabelle, Masquerade) |
| Dispatcher | `/etc/NetworkManager/dispatcher.d/91-webpanel_AP` |
| Service | `webpanel_AP.service` |

Die AP-Clients bekommen ihre IP vom DHCP-Server.
**Offene Frage (vor Implementierung klären):**
Läuft NetworkManager im `ipv4.method shared` Modus für `wlan_ap`?

```bash
nmcli connection show Autodarts-AP | grep ipv4.method
```

- **`shared`** → NetworkManager startet bereits einen eigenen dnsmasq für `wlan_ap`.
  → Captive-Portal-Config geht in `/etc/NetworkManager/dnsmasq-shared.d/`
- **`manual` oder `auto`** → eigener dnsmasq muss deployed werden,
  nur für `wlan_ap` gebunden.

---

## Warum das naiv-aggressive `address=/#/10.77.0.1` NICHT funktioniert

Der "alles umleiten"-Ansatz vergiftet **alle** DNS-Anfragen:

```ini
address=/#/10.77.0.1   # FALSCH — vergiftet alles
```

Das würde brechen:
- `darts-wled` fragt `Dart-Led1.local` → bekommt 10.77.0.1 → verbindet den Pi statt den ESP → **WLED kaputt**
- GitHub-Calls für Updates → landen auf dem Pi → **Updates kaputt**
- Jede externe API → falsch aufgelöst

### `.local` ist sowieso sicher

WLED-ESPs nutzen `mDNS` (Multicast-DNS, Port 5353) für `.local`-Auflösung —
das läuft **nicht** über den normalen DNS-Port 53 und wird von dnsmasq
standardmäßig nicht beeinflusst. `Dart-Led1.local` ist also in jedem Fall sicher.

---

## Der richtige Ansatz: nur die Captive-Portal-Domains umleiten

Statt allem nur die **bekannten OS-Check-Domains** abfangen:

```ini
# /etc/NetworkManager/dnsmasq-shared.d/captive-portal.conf
# Captive Portal: nur OS-Connectivity-Checks abfangen
address=/connectivitycheck.gstatic.com/10.77.0.1
address=/connectivitycheck.android.com/10.77.0.1
address=/clients3.google.com/10.77.0.1
address=/captive.apple.com/10.77.0.1
address=/www.apple.com/10.77.0.1
address=/www.msftconnecttest.com/10.77.0.1
address=/www.msftncsi.com/10.77.0.1
address=/detectportal.firefox.com/10.77.0.1
```

Effekt:
- Handy verbindet → OS-Check landet auf Pi → Redirect → Browser öffnet Webpanel ✅
- `Dart-Led1.local` → geht via mDNS, unberührt ✅
- GitHub, externe APIs → normales DNS über den echten Uplink ✅
- Alle anderen Domains → normal aufgelöst ✅

---

## Was Flask dafür braucht

Flask muss auf die Probe-URLs antworten — mit einem **Redirect auf das Webpanel**.

Neue Routen (minimal, hardcoded):

```python
@app.route("/generate_204")             # Android
@app.route("/gen_204")                  # Android alt
@app.route("/hotspot-detect.html")      # iOS / macOS
@app.route("/library/test/success.html") # iOS alt
@app.route("/connecttest.txt")          # Windows
@app.route("/redirect")                 # Windows alt
@app.route("/ncsi.txt")                 # Windows alt
def captive_portal_probe():
    return redirect("http://10.77.0.1/", code=302)
```

Wichtig: Die Redirect-Ziel-URL ist `http://10.77.0.1/` — die feste AP-IP,
keine dynamische Host-Erkennung nötig.

---

## Port-Frage: Auf welchem Port läuft Flask?

Der AP-Client macht den Connectivity-Check immer auf **Port 80**.
Flask läuft möglicherweise auf einem anderen Port (z. B. 5000 oder 8080).

**Zwei Möglichkeiten:**

### Option A: nftables-Redirect (empfohlen, kein Flask-Umbau nötig)

Eingehende HTTP-Anfragen (Port 80) von AP-Clients auf den Flask-Port umleiten:

```
# nft — AP-Clients Port 80 → Flask (z. B. Port 5000)
nft add rule inet autodarts_ap prerouting \
  iifname "wlan_ap" tcp dport 80 redirect to :5000
```

Damit muss Flask nicht auf Port 80 laufen (was root erfordern würde).
Diese Regel wird in `fix_ap_internet_sharing_v3.sh` / `webpanel_AP_apply.sh` eingebaut.

### Option B: Flask direkt auf Port 80 (nicht empfohlen)

Würde root-Rechte für Flask erfordern — unnötig komplex.

---

## Wo die Änderungen hingehören

| Änderung | Datei |
|---------|-------|
| dnsmasq Captive-Portal-Config | neues Script in `fix_ap_internet_sharing_v3.sh` oder eigenständiges `autodarts-ap-captive-portal.sh` |
| nftables Port-80-Redirect | `webpanel_AP_apply.sh` (inline in `fix_ap_internet_sharing_v3.sh`) |
| Flask Probe-Routen | `autodarts-web.py` |
| Webpanel-Update FILES-Array | `autodarts-webpanel-update.sh` (falls neues Script) |

---

## Sicherheitsfragen / Abgrenzung

- **Kein** DNS-Redirect für `.local` Domains
- **Kein** Redirect auf Port 443 (HTTPS) — Captive Portals funktionieren nur über HTTP
- Der Redirect zeigt nur auf `http://10.77.0.1/` — das Webpanel selbst, keine separate Seite
- Bestehende nftables-Tabelle `autodarts_ap` wird nur um eine Regel erweitert — nicht neu gebaut
- Die Captive-Portal-Routen in Flask sind **lesend**, kein State, kein Session-Zugriff

---

## Klärungsbedarf vor Implementierung

1. **`nmcli connection show Autodarts-AP | grep ipv4.method`**
   → Ist es `shared` oder etwas anderes?
   → Bestimmt ob dnsmasq-shared.d oder ein eigener dnsmasq-Dienst nötig ist.

2. **Auf welchem Port läuft Flask?**
   → Bestimmt den nftables-Redirect-Zielport.

3. **Soll das Captive Portal nur beim AP-Modus aktiv sein** oder immer?
   → Empfehlung: nur wenn AP aktiv ist (dnsmasq-Config ist AP-gebunden, nftables-Regel ebenfalls).
