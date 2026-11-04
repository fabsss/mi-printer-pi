#!/usr/bin/env bash
# Pairs the Mi Portable Photo Printer over Bluetooth (one-time).
# Switch the printer on and do NOT leave it connected to your phone.
set -euo pipefail
MAC="${1:-}"

command -v bluetoothctl >/dev/null || { echo "bluez missing: sudo apt install bluez"; exit 1; }
sudo rfkill unblock bluetooth || true
sudo systemctl enable --now bluetooth >/dev/null

if [ -z "$MAC" ]; then
  echo "Scanning for devices for 15 s ..."
  bluetoothctl --timeout 15 scan on | grep -i -E "NEW|printer|mi |xiaomi|hannto" || true
  echo
  bluetoothctl devices
  echo
  read -r -p "Printer MAC address (e.g. F0:13:C1:3F:63:94): " MAC
fi

echo "Pairing with $MAC ..."
bluetoothctl power on
bluetoothctl pair "$MAC" || echo "(pair reported an error - maybe already paired)"
bluetoothctl trust "$MAC"
bluetoothctl info "$MAC" | grep -E "Name|Paired|Trusted|UUID" || true
echo
echo "Set '$MAC' as \"address\" in /etc/mizink/config.json."
