#!/usr/bin/env bash
# Installs the Mi Portable Photo Printer driver on Raspberry Pi OS (Trixie).
# Usage:  sudo ./install.sh [USER_RUNNING_THE_PHOTOBOOTH_APP]
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo."; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
BOOTH_USER="${1:-${SUDO_USER:-}}"

echo "==> Installing packages"
apt-get update -qq
apt-get install -y --no-install-recommends bluez python3 python3-pil

echo "==> Copying code to /opt/mizink/lib"
install -d /opt/mizink/lib
rm -rf /opt/mizink/lib/mizink
cp -r "$HERE/mizink" /opt/mizink/lib/mizink
find /opt/mizink/lib -name __pycache__ -prune -exec rm -rf {} +
cp "$HERE/LICENSE.mizink" /opt/mizink/

echo "==> Creating wrapper commands"
for pair in "mizink-enqueue:mizink.spool" "mizink:mizink.cli"; do
  name="${pair%%:*}"; mod="${pair##*:}"
  cat > "/usr/local/bin/$name" <<WRAP
#!/bin/sh
PYTHONPATH=/opt/mizink/lib MIZINK_CONFIG=\${MIZINK_CONFIG:-/etc/mizink/config.json} exec /usr/bin/python3 -m $mod "\$@"
WRAP
  chmod 755 "/usr/local/bin/$name"
done

echo "==> System user, spool, configuration"
id mizink >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin mizink
usermod -aG bluetooth mizink
# Spool is group-writable only (mizink + the photobooth user), not world-writable.
install -d -m 2770 -o mizink -g mizink /var/spool/mizink
install -d -m 750 -o root -g mizink /etc/mizink
if [ ! -f /etc/mizink/config.json ]; then
  install -m 640 -o root -g mizink "$HERE/config.example.json" /etc/mizink/config.json
  echo "    -> created /etc/mizink/config.json: enter the MAC address and token!"
fi
if [ -n "$BOOTH_USER" ] && id "$BOOTH_USER" >/dev/null 2>&1; then
  usermod -aG mizink "$BOOTH_USER"   # may enqueue jobs and read the config (mizink status)
  echo "    -> added '$BOOTH_USER' to group 'mizink' (restart the photobooth service / log in again)"
fi

echo "==> systemd service"
install -m 644 "$HERE/systemd/mizink-worker.service" /etc/systemd/system/mizink-worker.service
systemctl daemon-reload
systemctl enable mizink-worker.service >/dev/null

cat <<'MSG'

Done. Next steps:
  1. Pair the printer:        sudo ./pair.sh        (see README, step 2)
  2. Enter MAC + token:       sudo nano /etc/mizink/config.json   (README, step 3)
  3. Start the service:       sudo systemctl start mizink-worker
  4. Restart the photobooth service so it picks up the new group, then test:
                              mizink-enqueue /path/to/photo.jpg ; journalctl -u mizink-worker -f
MSG
