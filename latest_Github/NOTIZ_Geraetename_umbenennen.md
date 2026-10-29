# Gerätename (Hostname) umbenennen – Notiz

## Problem
Der Pi verwendet **cloud-init** mit `manage_etc_hosts: true`.
Das bedeutet: bei jedem Boot überschreibt cloud-init die `/etc/hosts` aus einem Template.
Änderungen an `/etc/hostname` und `/etc/hosts` überleben den Neustart daher nicht.

## Was funktioniert hat (manuell per Putty)
```bash
sudo hostnamectl set-hostname NeuerName
# /etc/hosts anpassen
sudo sed -i 's/AlterName/NeuerName/g' /etc/hosts
```
→ Wirkt sofort (runtime), aber nach Reboot ist der alte Name wieder da.

## Was nötig wäre für eine permanente Lösung

### Option A – cloud-init deaktivieren (empfohlen)
```bash
# In /etc/cloud/cloud.cfg die Zeile ändern:
sudo sed -i 's/manage_etc_hosts: true/manage_etc_hosts: false/' /etc/cloud/cloud.cfg

# Danach Hostname setzen:
sudo hostnamectl set-hostname NeuerName
sudo sed -i 's/AlterName/NeuerName/g' /etc/hosts
sudo reboot
```
**Einmalig machen, dann bleibt es dauerhaft.**

### Option B – cloud-init Template anpassen
```bash
sudo nano /etc/cloud/templates/hosts.debian.tmpl
# Dort den alten Namen durch den neuen ersetzen
```

## Warum es im Webpanel nicht einfach geht
- cloud-init regeneriert `/etc/hosts` beim Boot aus dem Template
- Das Webpanel kann zwar `/etc/hostname` und `/etc/hosts` schreiben
- Aber ohne `manage_etc_hosts: false` in `cloud.cfg` ist der Effekt nach Reboot weg
- cloud-init ist eine externe Schicht, die nach dem Webpanel-Start greift

## Empfehlung
Einmalig per Putty **Option A** ausführen — danach könnte ein Webpanel-Button problemlos funktionieren.
