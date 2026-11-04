#!/usr/bin/env bash
# Koppelt den Mi Portable Photo Printer per Bluetooth (einmalig).
# Drucker einschalten (Taste halten bis LED blinkt) und NICHT mit dem Handy verbunden lassen.
set -euo pipefail
MAC="${1:-}"

command -v bluetoothctl >/dev/null || { echo "bluez fehlt: sudo apt install bluez"; exit 1; }
sudo rfkill unblock bluetooth || true
sudo systemctl enable --now bluetooth >/dev/null

if [ -z "$MAC" ]; then
  echo "Suche 15 s nach Geräten ..."
  bluetoothctl --timeout 15 scan on | grep -i -E "NEW|printer|mi |xiaomi|hannto" || true
  echo
  bluetoothctl devices
  echo
  read -r -p "MAC-Adresse des Druckers (z.B. F0:13:C1:3F:63:94): " MAC
fi

echo "Koppeln mit $MAC ..."
bluetoothctl power on
bluetoothctl pair "$MAC" || echo "(pair meldete Fehler - evtl. schon gekoppelt)"
bluetoothctl trust "$MAC"
bluetoothctl info "$MAC" | grep -E "Name|Paired|Trusted|UUID" || true
echo
echo "Trage '$MAC' als \"address\" in /etc/mizink/config.json ein."
