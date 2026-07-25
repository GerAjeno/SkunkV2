#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Ejecuta con sudo: sudo ./uninstall.sh" >&2
    exit 1
fi

systemctl disable --now skunk-api.service skunk-worker.service skunk-admin.service 2>/dev/null || true
for unit in skunk-api.service skunk-worker.service skunk-admin.service; do
    if [[ -f "/etc/systemd/system/${unit}" ]]; then
        mv "/etc/systemd/system/${unit}" "/etc/systemd/system/${unit}.removed"
    fi
done
systemctl daemon-reload

echo "Servicios de Skunk PC detenidos."
echo "Por seguridad, no se eliminaron automáticamente:"
echo "  /opt/skunk-pc"
echo "  /etc/skunk-pc"
echo "  /var/lib/skunk-pc"
echo "Puedes respaldarlos y eliminarlos manualmente si ya no los necesitas."
