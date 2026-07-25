#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Ejecuta con sudo: sudo $0" >&2
    exit 1
fi

default_iface="$(ip route show default | awk '/default/ {print $5; exit}')"
subnet="$(ip -4 route show dev "$default_iface" | awk '/proto kernel/ {print $1; exit}')"

if [[ -z "$default_iface" || -z "$subnet" ]]; then
    echo "No fue posible determinar la red local." >&2
    exit 1
fi

backup="/etc/cups/cupsd.conf.skunk-backup.$(date +%Y%m%d_%H%M%S)"
install -m 0600 /etc/cups/cupsd.conf "$backup"

# CUPS publica únicamente las colas marcadas como compartidas en las redes locales.
cupsctl --share-printers
cupsctl WebInterface=no

systemctl disable --now cups-browsed.service 2>/dev/null || true
systemctl enable --now cups.service avahi-daemon.service
systemctl restart cups.service avahi-daemon.service

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "^Status: active"; then
    ufw allow from "$subnet" to any port 631 proto tcp comment "Skunk PC IPP"
    ufw allow from "$subnet" to any port 5353 proto udp comment "Skunk PC mDNS"
fi

echo "Impresión nativa activada para la red $subnet mediante IPP/mDNS."
echo "Respaldo de CUPS: $backup"

