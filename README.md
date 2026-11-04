# Xiaomi Mi Portable Photo Printer an der Photobooth-App (Raspberry Pi 4, Trixie)

Bindet den Xiaomi Mi Portable Photo Printer (Modell `hannto.printer.basil`, ZINK 2×3") an die
[photobooth-app](https://github.com/photobooth-app/photobooth-app) an.

## Basiert auf

* **[fliptheweb/mi-portable-photo-printer](https://github.com/fliptheweb/mi-portable-photo-printer)**
  (MIT, „mizink“): reverse-engineertes Protokoll des Druckers (Frame-Format, RC4 mit Geräte-Token,
  JSON-RPC, JPEG-Upload). Dessen Dateien `crypto.py`, `protocol.py`, `printer.py`, `image.py`,
  `config.py`, `cli.py` liegen unverändert in `mizink/`; Lizenz in `LICENSE.mizink`.
* **Eigene Ergänzungen:** Linux-Bluetooth-Transport (`transport.py`), Warteschlange (`spool.py`),
  Hintergrunddienst (`worker.py`), systemd-Unit, Installer, Kopplungsskript, Tests.
* **[photobooth-app](https://github.com/photobooth-app/photobooth-app):** ruft den konfigurierbaren
  „Share command“ als Shell-Befehl auf (Platzhalter `{filename}`, `{copies}`, …, Timeout 6 s).
* Zum Vergleich: [tuat-yate/xiaomi-photo-printer](https://github.com/tuat-yate/xiaomi-photo-printer)
  für den *Pro* (gleiche Frames, aber DH + AES) – nicht Teil dieses Pakets.

## Architektur

```
Photobooth-App ──(Share command, <6 s)──▶ mizink-enqueue ──▶ /var/spool/mizink/*.json+*.img
                                                                      │
                                    mizink-worker (systemd) ◀─────────┘
                                          │  Bildaufbereitung 1040×1560 JPEG, Retry, Reconnect, Keep-Alive
                                          ▼
                              RFCOMM-Kanal 1 (Bluetooth Classic, RC4) ──▶ Mi Photo Printer
```

## Befehle im Alltag

| Befehl | Zweck |
|---|---|
| `mizink-enqueue [--copies N] datei.jpg` | Bild in die Warteschlange legen (das ruft die Photobooth-App auf) |
| `mizink-enqueue --status` | Zustand des Dienstes (connected/printing/offline), Akku, Warteschlange |
| `sudo systemctl status mizink-worker` / `journalctl -u mizink-worker -f` | Dienst und Log |
| `sudo systemctl stop mizink-worker` + `mizink status \| info \| print datei.jpg` | Direktzugriff (Drucker erlaubt nur **eine** Verbindung) |

## Wichtige Fakten vorab

* **Kein BLE, sondern Bluetooth Classic (RFCOMM/SPP, Kanal 1).** Das Protokoll stammt aus
  [fliptheweb/mi-portable-photo-printer](https://github.com/fliptheweb/mi-portable-photo-printer)
  (MIT, hier als Paket `mizink/` mitgeliefert). Dort fehlte nur der Linux-Transport – den
  liefert `mizink/transport.py` (Standard-Python-Socket, kein `rfcomm bind`, kein pyserial).
* **Das andere Repo (tuat-yate) ist für den *Pro* (DH + AES) – nicht für dein Modell.** Das
  normale Modell nutzt RC4 mit dem 12-Byte-Geräte-Token. Falls dein Drucker „Pro“ heißt, passt
  dieses Paket nicht.
* **Du brauchst den Geräte-Token** (siehe Schritt 3). Er geht nie über Bluetooth.
* **Die Photobooth-App erlaubt Druckbefehlen nur 6 Sekunden**, ein ZINK-Druck dauert ~1 Minute.
  Darum: `mizink-enqueue` legt das Bild in 10 ms in eine Warteschlange, der Dienst
  `mizink-worker` druckt im Hintergrund (Auto-Reconnect, Keep-Alive, Retry, `failed/`-Ordner).
* ⚠️ **Auf echter Hardware noch ungetestet.** Protokoll/Warteschlange/Worker sind offline gegen
  einen simulierten Drucker getestet (`tests/`); der Upstream wurde nur auf macOS erprobt.

## Schritt 1 – Installation

```bash
scp -r mi-printer-pi pi@<pi-adresse>:~/      # oder USB-Stick / git
cd ~/mi-printer-pi
python3 -m unittest discover -s tests         # optional: Offline-Selbsttest
sudo ./install.sh <benutzer-der-photobooth-app>
```

## Schritt 2 – Drucker koppeln (einmalig)

Drucker einschalten, **am Handy Bluetooth/Mi Home schließen** (der Drucker nimmt nur eine
Verbindung an), dann:

```bash
sudo ./pair.sh            # scannt, fragt nach der MAC, macht pair + trust
```

Fragt BlueZ nach einer Bestätigung/PIN, bestätige in `bluetoothctl` mit `yes` bzw. probiere `0000`.

## Schritt 3 – Token besorgen

Der Drucker muss einmal in der **Mi Home**-App eingerichtet sein, dann liegt der Token in deinem
Xiaomi-Konto. Einer dieser Wege:

1. **Mi Cloud (empfohlen):** [Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor)
   auf deinem PC ausführen, mit Xiaomi-Konto + Region anmelden, bei „Mi Portable Photo Printer“
   (`hannto.printer.basil`) den `token` (24 Hex-Zeichen) kopieren.
2. **Home Assistant:** falls der Drucker dort über `hass-xiaomi-miot` hängt, hilft
   `tools/extract_token_from_ha.py` aus dem Upstream-Repo.
3. **Ohne Token:** Keystream aus einem Bluetooth-Mitschnitt (Upstream `docs/GET_KEY.md`, braucht macOS).

Dann eintragen (Token ist ein Geheimnis, nicht ins Git einchecken):

```bash
sudo nano /etc/mizink/config.json
```
```json
{ "address": "F0:13:C1:3F:63:94", "token": "deine24hexzeichen00000000",
  "fit": "cover", "rotate": "auto", "keep_awake": true }
```

| Option | Bedeutung |
|---|---|
| `fit` | `cover` füllt das 2:3-Papier randlos (beschneidet), `contain` lässt weißen Rand, `stretch` verzerrt |
| `rotate` | `auto` dreht Querformat-Bilder um 90°, damit sie ins Hochformat-Papier passen |
| `keep_awake` | `true`: Dienst hält den Drucker per `retime` dauerhaft an (kein 10-Min-Auto-Aus, aber Akku/Strom!). `false`: Verbindung nur bei Druckaufträgen – Drucker dann vorher einschalten |

## Schritt 4 – Testen

Zuerst ohne Papier (nur Verbindung + Authentifizierung) mit der Upstream-CLI – dazu den Dienst
kurz stoppen, weil der Drucker nur **eine** Verbindung zulässt:

```bash
sudo systemctl stop mizink-worker
mizink status            # Akku/Zustand; Fehler "timeout" = falscher Token
mizink info              # Firmware, Seriennummer
sudo systemctl start mizink-worker
```

Dann Testdruck über die Warteschlange:

```bash
mizink-enqueue ~/foto.jpg
journalctl -u mizink-worker -f      # Verlauf
mizink-enqueue --status             # Zustand, Akku, Warteschlange
```

## Schritt 5 – In der Photobooth-App einrichten

Admin-Center → **Share / Print Actions** → Aktion „Printing“ (und „Printing copies“):

* **Share command:**
  ```
  mizink-enqueue --copies {copies} "{filename}"
  ```
* **Printer name:** beliebig (z. B. `mi-printer`)
* **Check if printer is idle:** **aus** (das nutzt `lpstat`/CUPS, das es hier nicht gibt)
* **Share blocked time:** ~60 s, damit Gäste nicht mehrfach auslösen
  (Warteschlange ist zusätzlich auf 10 Aufträge begrenzt)

Tipp: Gestalte Collage/Ausgabe im Verhältnis **2:3 hochkant** (1040×1560 px wird gedruckt),
dann gibt es keinen Beschnitt.

## Fehlersuche

| Symptom | Ursache / Lösung |
|---|---|
| `RFCOMM-Verbindung ... fehlgeschlagen` | Drucker aus, nicht gekoppelt, oder noch mit Handy verbunden. `bluetoothctl info <MAC>` prüfen |
| `timeout waiting for reply` | Falscher Token (Drucker antwortet verschlüsselt, Antworten sind Müll) |
| Verbindung bricht nach ~15 s ab | Worker läuft nicht / `keep_awake: false` ohne Auftrag – normal |
| `printer stayed busy (-8006)` | Drucker wärmt auf oder druckt noch; Papier/Klappe prüfen |
| Aufträge landen in `/var/spool/mizink/failed/` | 3 Fehlversuche (Papier leer, Klappe offen …). Dateien dort prüfen, mit `mv` zurück ins Spool-Verzeichnis |
| Pairing „stale“ | `bluetoothctl remove <MAC>`, dann `./pair.sh` erneut |

## Dateien

```
mizink/          Upstream-Code (crypto, protocol, printer, image, cli) + transport.py (Linux), spool.py, worker.py
systemd/         mizink-worker.service
install.sh       Installation nach /opt/mizink, Wrapper in /usr/local/bin
pair.sh          Bluetooth-Kopplung
tests/           Offline-Tests mit simuliertem Drucker
LICENSE.mizink   MIT-Lizenz des Upstream-Projekts
```

Deinstallation: `sudo systemctl disable --now mizink-worker; sudo rm -rf /opt/mizink /etc/mizink /var/spool/mizink /usr/local/bin/mizink* /etc/systemd/system/mizink-worker.service`

## Lizenz & Haftung

MIT (siehe `LICENSE`); der Upstream-Code steht unter dessen MIT-Lizenz (`LICENSE.mizink`).
Inoffiziell, nicht von Xiaomi unterstützt; Nutzung auf eigene Gefahr. Token und Keystream sind
Geheimnisse – nie einchecken (`.gitignore` schließt `config.json` aus).
