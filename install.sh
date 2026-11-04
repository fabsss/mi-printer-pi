#!/usr/bin/env bash
# Installiert den Mi-Photo-Printer-Treiber auf Raspberry Pi OS (Trixie).
# Aufruf:  sudo ./install.sh [BENUTZER_DER_PHOTOBOOTH_APP]
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen."; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
BOOTH_USER="${1:-${SUDO_USER:-}}"

echo "==> Pakete installieren"
apt-get update -qq
apt-get install -y --no-install-recommends bluez python3 python3-pil

echo "==> Code nach /opt/mizink/lib kopieren"
install -d /opt/mizink/lib
rm -rf /opt/mizink/lib/mizink
cp -r "$HERE/mizink" /opt/mizink/lib/mizink
find /opt/mizink/lib -name __pycache__ -prune -exec rm -rf {} +
cp "$HERE/LICENSE.mizink" /opt/mizink/

echo "==> Wrapper-Befehle anlegen"
for pair in "mizink-enqueue:mizink.spool" "mizink:mizink.cli"; do
  name="${pair%%:*}"; mod="${pair##*:}"
  cat > "/usr/local/bin/$name" <<EOF
#!/bin/sh
PYTHONPATH=/opt/mizink/lib MIZINK_CONFIG=\${MIZINK_CONFIG:-/etc/mizink/config.json} exec /usr/bin/python3 -m $mod "\$@"
EOF
  chmod 755 "/usr/local/bin/$name"
done

echo "==> Systembenutzer, Spool, Konfiguration"
id mizink >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin mizink
usermod -aG bluetooth mizink
install -d -m 1777 /var/spool/mizink
chown mizink:mizink /var/spool/mizink
install -d -m 750 -o root -g mizink /etc/mizink
if [ ! -f /etc/mizink/config.json ]; then
  install -m 640 -o root -g mizink "$HERE/config.example.json" /etc/mizink/config.json
  echo "    -> /etc/mizink/config.json angelegt: MAC-Adresse und Token eintragen!"
fi
if [ -n "$BOOTH_USER" ] && id "$BOOTH_USER" >/dev/null 2>&1; then
  usermod -aG mizink "$BOOTH_USER"   # darf dann z.B. 'mizink status' ausführen (config lesen)
fi

echo "==> systemd-Dienst"
install -m 644 "$HERE/systemd/mizink-worker.service" /etc/systemd/system/mizink-worker.service
systemctl daemon-reload
systemctl enable mizink-worker.service >/dev/null

cat <<'MSG'

Fertig. Nächste Schritte:
  1. Drucker koppeln:           sudo ./pair.sh        (siehe README, Schritt 2)
  2. Token+MAC eintragen:       sudo nano /etc/mizink/config.json   (README, Schritt 3)
  3. Dienst starten:            sudo systemctl start mizink-worker
  4. Testdruck:                 mizink-enqueue /pfad/zu/foto.jpg ; journalctl -u mizink-worker -f
MSG
