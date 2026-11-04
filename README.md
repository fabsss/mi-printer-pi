# Xiaomi Mi Portable Photo Printer for the photobooth-app (Raspberry Pi 4, Trixie)

Connects the Xiaomi Mi Portable Photo Printer (model `hannto.printer.basil`, ZINK 2×3") to the
[photobooth-app](https://github.com/photobooth-app/photobooth-app) on Raspberry Pi OS (Debian Trixie).

## Based on

* **[fliptheweb/mi-portable-photo-printer](https://github.com/fliptheweb/mi-portable-photo-printer)**
  (MIT, "mizink"): reverse-engineered printer protocol (frame format, RC4 with the device token,
  JSON-RPC, JPEG upload). Its files `crypto.py`, `protocol.py`, `printer.py`, `image.py`,
  `config.py` and `cli.py` are included unchanged in `mizink/`; license in `LICENSE.mizink`.
* **Added here:** Linux Bluetooth transport (`transport.py`), print queue (`spool.py`),
  background service (`worker.py`), systemd unit, installer, pairing script, tests.
* **[photobooth-app](https://github.com/photobooth-app/photobooth-app):** runs the configurable
  "share command" as a shell command (placeholders `{filename}`, `{copies}`, …, 6 s timeout).
* For comparison: [tuat-yate/xiaomi-photo-printer](https://github.com/tuat-yate/xiaomi-photo-printer)
  targets the *Pro* model (same frames, but Diffie-Hellman + AES) – not part of this package.

## Architecture

```
photobooth-app ──(share command, <6 s)──▶ mizink-enqueue ──▶ /var/spool/mizink/*.json + *.img
                                                                      │
                                    mizink-worker (systemd) ◀─────────┘
                                          │  resize to 1040×1560 JPEG, retry, reconnect, keep-alive
                                          ▼
                              RFCOMM channel 1 (Bluetooth Classic, RC4) ──▶ Mi Photo Printer
```

## Key facts

* **Not BLE – Bluetooth Classic (RFCOMM/SPP, channel 1).** The upstream project only had a macOS
  transport. `mizink/transport.py` provides the Linux one (plain Python sockets; no `rfcomm bind`,
  no pyserial).
* **The tuat-yate repo is for the *Pro* (DH + AES), not the regular model.** The regular model uses
  RC4 with the 12-byte device token. If your printer is the "Pro", this package does not fit.
* **You need the device token** (step 3). It is never sent over Bluetooth.
* **The photobooth-app gives a print command only 6 seconds**, but a ZINK print takes ~1 minute.
  So `mizink-enqueue` drops the image into a queue in milliseconds and the `mizink-worker`
  service prints in the background (auto-reconnect, keep-alive, retries, `failed/` folder).
* ⚠️ **Not yet tested on real hardware.** Protocol, queue and worker are tested offline against a
  simulated printer (`tests/`); upstream was only verified on macOS.

## Daily commands

| Command | Purpose |
|---|---|
| `mizink-enqueue [--copies N] file.jpg` | Queue an image (this is what the photobooth-app calls) |
| `mizink-enqueue --status` | Service state (connected/printing/offline), battery, queue length |
| `sudo systemctl status mizink-worker` / `journalctl -u mizink-worker -f` | Service and log |
| `sudo systemctl stop mizink-worker`, then `mizink status \| info \| print file.jpg` | Direct access (the printer accepts only **one** connection) |

## Step 1 – Install

```bash
git clone https://github.com/fabsss/mi-printer-pi && cd mi-printer-pi   # private repo: gh auth login first
python3 -m unittest discover -s tests          # optional offline self-test
sudo ./install.sh <user-running-the-photobooth-app>
```

The spool directory `/var/spool/mizink` is group-writable (`mizink` group) only; the installer adds
the photobooth user to that group. **Restart the photobooth-app service afterwards** so it picks up
the new group.

## Step 2 – Pair the printer (once)

Switch the printer on and **close Bluetooth/Mi Home on your phone** (the printer accepts one
connection at a time), then:

```bash
sudo ./pair.sh            # scans, asks for the MAC, runs pair + trust
```

If BlueZ asks for confirmation/PIN, answer `yes` in `bluetoothctl` or try `0000`.

## Step 3 – Get the token

Set the printer up once in the **Mi Home** app; the token is then stored in your Xiaomi account.
Pick one:

1. **Mi Cloud (recommended):** run [Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor)
   on your PC, log in with your Xiaomi account and region, and copy the `token` (24 hex characters)
   of "Mi Portable Photo Printer" (`hannto.printer.basil`).
2. **Home Assistant:** if the printer is added via `hass-xiaomi-miot`, use
   `tools/extract_token_from_ha.py` from the upstream repo.
3. **Without a token:** recover a keystream from a Bluetooth capture (upstream `docs/GET_KEY.md`,
   needs macOS).

Then enter it (the token is a secret – never commit it):

```bash
sudo nano /etc/mizink/config.json
```
```json
{ "address": "F0:13:C1:3F:63:94", "token": "your24hexchars00000000000",
  "fit": "cover", "rotate": "auto", "keep_awake": true }
```

| Option | Meaning |
|---|---|
| `fit` | `cover` fills the 2:3 paper without borders (crops), `contain` keeps the whole image with white borders, `stretch` distorts |
| `rotate` | `auto` rotates landscape images by 90° so they fit the portrait paper; `none` disables it |
| `keep_awake` | `true`: the service keeps the printer on via `retime` (no 10-minute auto-off, but uses battery/power). `false`: connects only when a job is queued – switch the printer on beforehand |
| `max_attempts` | Attempts per job before it moves to `failed/` (default 3) |

## Step 4 – Test

First without paper (connection + authentication only) using the upstream CLI. Stop the service
briefly because the printer allows only **one** connection:

```bash
sudo systemctl stop mizink-worker
mizink status            # battery/state; a "timeout" usually means a wrong token
mizink info              # firmware, serial number
sudo systemctl start mizink-worker
```

Then a test print through the queue:

```bash
mizink-enqueue ~/photo.jpg
journalctl -u mizink-worker -f      # progress
mizink-enqueue --status
```

## Step 5 – Configure the photobooth-app

Admin center → **Share / Print Actions** → action "Printing" (and "Printing copies"):

* **Share command:**
  ```
  mizink-enqueue --copies {copies} "{filename}"
  ```
* **Printer name:** anything (e.g. `mi-printer`)
* **Check if printer is idle:** **off** (it uses `lpstat`/CUPS, which is not involved here)
* **Share blocked time:** ~60 s so guests don't trigger repeatedly
  (the queue is additionally limited to 10 jobs)

Tip: design your collage/output as **2:3 portrait** (1040×1560 px is printed) to avoid cropping.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `RFCOMM connection ... failed` | Printer off, not paired, or still connected to the phone. Check `bluetoothctl info <MAC>` |
| `timeout waiting for reply` | Wrong token (replies can't be decrypted) |
| Link drops after ~15 s | No worker running, or `keep_awake: false` with no job – normal |
| `printer stayed busy (-8006)` | Printer is warming up or still printing; check paper/cover |
| Jobs end up in `/var/spool/mizink/failed/` | Failed `max_attempts` times (no paper, cover open, …). Fix the cause, then `mv` the files back into the spool folder |
| Stale pairing | `bluetoothctl remove <MAC>`, then run `./pair.sh` again |

## Files

```
mizink/          upstream code (crypto, protocol, printer, image, cli) + transport.py (Linux), spool.py, worker.py
systemd/         mizink-worker.service
install.sh       installs to /opt/mizink, wrappers in /usr/local/bin
pair.sh          Bluetooth pairing
tests/           offline tests with a simulated printer
LICENSE.mizink   MIT license of the upstream project
```

Uninstall: `sudo systemctl disable --now mizink-worker; sudo rm -rf /opt/mizink /etc/mizink /var/spool/mizink /usr/local/bin/mizink* /etc/systemd/system/mizink-worker.service`

## License & disclaimer

MIT (see `LICENSE`); the upstream code is under its own MIT license (`LICENSE.mizink`).
Unofficial, not supported by Xiaomi; use at your own risk. The token and keystream are secrets –
never commit them (`.gitignore` excludes `config.json`).
