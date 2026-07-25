#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Ejecuta con sudo: sudo $0 [--check]" >&2
    exit 1
fi
if [[ $# -gt 1 || (${1:-} != "" && ${1:-} != "--check") ]]; then
    echo "Uso: sudo $0 [--check]" >&2
    exit 1
fi

check_only=0
if [[ ${1:-} == "--check" ]]; then
    check_only=1
fi

for command in ip awk lpstat lpoptions cupsd systemctl ipptool ss; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Falta el comando requerido: $command" >&2
        exit 1
    fi
done

default_iface="$(ip -4 route show default | awk '/default/ {print $5; exit}')"
if [[ -z "$default_iface" ]]; then
    echo "No fue posible determinar la interfaz de red local." >&2
    exit 1
fi
subnet="$(ip -4 route show dev "$default_iface" scope link |
    awk '/proto kernel/ {print $1; exit}')"
if [[ -z "$subnet" ]]; then
    echo "No fue posible determinar la subred local de $default_iface." >&2
    exit 1
fi

mapfile -t all_queues < <(lpstat -e)
zebra_queues=()
for queue in "${all_queues[@]}"; do
    options="$(lpoptions -p "$queue" 2>/dev/null || true)"
    if [[ "${options,,}" == *"zebra"* ]]; then
        zebra_queues+=("$queue")
    fi
done
if (( ${#zebra_queues[@]} == 0 )); then
    echo "No hay colas Zebra configuradas; no se activará IPP." >&2
    exit 1
fi

validate_queue() {
    local queue="$1"
    local options choices
    options="$(lpoptions -p "$queue")"
    choices="$(lpoptions -p "$queue" -l)"
    if [[ "$options" != *"printer-is-shared=true"* ]]; then
        echo "$queue no está marcada como compartida." >&2
        return 1
    fi
    if ! grep -Eq '^PageSize/.*: .*\*w288h432([[:space:]]|$)' <<<"$choices"; then
        echo "$queue no tiene 4×6 (w288h432) como tamaño seleccionado." >&2
        return 1
    fi
    if ! grep -Eq '^Resolution/.*: .*\*203dpi([[:space:]]|$)' <<<"$choices"; then
        echo "$queue no tiene 203 DPI como resolución seleccionada." >&2
        return 1
    fi
    if ! grep -Eq '^MediaType/.*: .*\*(Thermal|Direct)([[:space:]]|$)' <<<"$choices"; then
        echo "$queue no tiene un método térmico explícito." >&2
        return 1
    fi
}

for queue in "${zebra_queues[@]}"; do
    validate_queue "$queue"
done

ufw_active=0
if command -v ufw >/dev/null 2>&1 &&
   ufw status 2>/dev/null | grep -q '^Status: active'; then
    ufw_active=1
fi

echo "Verificación previa correcta."
echo "Interfaz local: $default_iface"
echo "Subred autorizada: $subnet"
echo "Colas Zebra: ${zebra_queues[*]}"
echo "UFW activo: $([[ $ufw_active -eq 1 ]] && echo sí || echo no)"
echo
echo "Cambios previstos:"
echo "- Respaldar /etc/cups/cupsd.conf."
echo "- Escuchar IPP en el puerto 631 y permitirlo únicamente a @LOCAL."
echo "- Activar DNS-SD y mantener la interfaz web de CUPS deshabilitada."
echo "- Deshabilitar cups-browsed y habilitar CUPS/Avahi."
echo "- Reiniciar CUPS y Avahi."
if (( ufw_active == 1 )); then
    echo "- Autorizar TCP 631 y UDP 5353 solamente desde $subnet."
else
    echo "- No modificar UFW porque está inactivo."
fi
echo "- Validar sintaxis, servicios y atributos IPP 4×6 publicados."

if (( check_only == 1 )); then
    echo
    echo "Modo --check: no se realizaron cambios."
    exit 0
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="/etc/cups/cupsd.conf.skunk-backup.${timestamp}"
config_uid="$(stat -c %u /etc/cups/cupsd.conf)"
config_gid="$(stat -c %g /etc/cups/cupsd.conf)"
config_mode="$(stat -c %a /etc/cups/cupsd.conf)"
cups_enabled="$(systemctl is-enabled cups.service 2>/dev/null || true)"
cups_active="$(systemctl is-active cups.service 2>/dev/null || true)"
avahi_enabled="$(systemctl is-enabled avahi-daemon.service 2>/dev/null || true)"
avahi_active="$(systemctl is-active avahi-daemon.service 2>/dev/null || true)"
cups_browsed_enabled="$(systemctl is-enabled cups-browsed.service 2>/dev/null || true)"
cups_browsed_active="$(systemctl is-active cups-browsed.service 2>/dev/null || true)"
install -m 0600 /etc/cups/cupsd.conf "$backup"
ufw_ipp_added=0
ufw_mdns_added=0
candidate=""

restore_service_state() {
    local unit="$1" enabled="$2" active="$3"
    if [[ "$enabled" == "enabled" ]]; then
        systemctl enable "$unit" >/dev/null 2>&1 || true
    else
        systemctl disable "$unit" >/dev/null 2>&1 || true
    fi
    if [[ "$active" == "active" ]]; then
        systemctl start "$unit" >/dev/null 2>&1 || true
    else
        systemctl stop "$unit" >/dev/null 2>&1 || true
    fi
}

rollback() {
    exit_code=$?
    trap - ERR
    echo "Falló la activación; restaurando la configuración anterior." >&2
    if [[ -n "$candidate" ]]; then
        rm -f "$candidate"
    fi
    install -o "$config_uid" -g "$config_gid" -m "$config_mode" \
        "$backup" /etc/cups/cupsd.conf
    if (( ufw_ipp_added == 1 )); then
        ufw --force delete allow from "$subnet" to any port 631 proto tcp >/dev/null || true
    fi
    if (( ufw_mdns_added == 1 )); then
        ufw --force delete allow from "$subnet" to any port 5353 proto udp >/dev/null || true
    fi
    restore_service_state cups.service "$cups_enabled" "$cups_active"
    restore_service_state avahi-daemon.service "$avahi_enabled" "$avahi_active"
    restore_service_state \
        cups-browsed.service "$cups_browsed_enabled" "$cups_browsed_active"
    exit "$exit_code"
}
trap rollback ERR

# Keep the candidate beside the real CUPS configuration.  cupsd derives its
# ServerRoot from the directory of the file passed with -c and may normalize
# that directory's ownership and mode while validating it.  Using plain
# mktemp here would therefore change /tmp from 1777 root:root to 0755 root:lp.
candidate="$(mktemp /etc/cups/cupsd.conf.skunk-candidate.XXXXXX)"
awk '
    BEGIN { root_location = 0 }
    /^[[:space:]]*<Location[[:space:]]+\/>[[:space:]]*$/ {
        root_location = 1
        print
        next
    }
    root_location && /^[[:space:]]*<\/Location>[[:space:]]*$/ {
        root_location = 0
        print
        next
    }
    root_location && /^[[:space:]]*Allow[[:space:]]+@LOCAL[[:space:]]*$/ {
        next
    }
    root_location && /^[[:space:]]*Order[[:space:]]+allow,deny[[:space:]]*$/ {
        print
        print "  Allow @LOCAL"
        next
    }
    /^[[:space:]]*Listen[[:space:]]+localhost:631[[:space:]]*$/ {
        print "Port 631"
        next
    }
    /^[[:space:]]*Browsing[[:space:]]+/ {
        print "Browsing Yes"
        next
    }
    /^[[:space:]]*WebInterface[[:space:]]+/ {
        print "WebInterface No"
        next
    }
    { print }
' /etc/cups/cupsd.conf >"$candidate"

if ! cupsd -t -c "$candidate"; then
    rm -f "$candidate"
    echo "La configuración CUPS candidata no es válida." >&2
    false
fi
install -o "$config_uid" -g "$config_gid" -m "$config_mode" \
    "$candidate" /etc/cups/cupsd.conf
rm -f "$candidate"

systemctl disable --now cups-browsed.service 2>/dev/null || true
systemctl enable cups.service avahi-daemon.service
systemctl restart cups.service avahi-daemon.service
systemctl is-active --quiet cups.service avahi-daemon.service
if ! ss -H -lnt 'sport = :631' |
    awk '{print $4}' |
    grep -Ev '^(127\.0\.0\.1|\[::1\]):631$' >/dev/null; then
    echo "CUPS no está escuchando IPP fuera de loopback." >&2
    false
fi

if (( ufw_active == 1 )); then
    if ! ufw show added | grep -Fq \
        "ufw allow from $subnet to any port 631 proto tcp"; then
        ufw allow from "$subnet" to any port 631 proto tcp comment "Skunk PC IPP"
        ufw_ipp_added=1
    fi
    if ! ufw show added | grep -Fq \
        "ufw allow from $subnet to any port 5353 proto udp"; then
        ufw allow from "$subnet" to any port 5353 proto udp comment "Skunk PC mDNS"
        ufw_mdns_added=1
    fi
fi

ipp_test="/usr/share/cups/ipptool/get-printer-attributes.test"
if [[ ! -f "$ipp_test" ]]; then
    echo "No existe la prueba IPP estándar: $ipp_test" >&2
    false
fi
for queue in "${zebra_queues[@]}"; do
    attributes="$(mktemp)"
    if ! ipptool -tv "ipp://localhost/printers/$queue" "$ipp_test" >"$attributes"; then
        rm -f "$attributes"
        echo "No se pudieron consultar los atributos IPP de $queue." >&2
        false
    fi
    if ! grep -Ei 'media-default.*(4x6|w288h432)' "$attributes" >/dev/null; then
        echo "$queue no publica 4×6 como media-default." >&2
        grep -Ei 'media-(default|ready|supported)' "$attributes" >&2 || true
        rm -f "$attributes"
        false
    fi
    if grep -Ei 'media-ready' "$attributes" >/dev/null &&
       ! grep -Ei 'media-ready.*(4x6|w288h432)' "$attributes" >/dev/null; then
        echo "$queue anuncia media-ready sin incluir 4×6." >&2
        grep -Ei 'media-(default|ready)' "$attributes" >&2 || true
        rm -f "$attributes"
        false
    fi
    rm -f "$attributes"
done

trap - ERR
echo
echo "Impresión nativa activada para $subnet mediante IPP/mDNS."
echo "Respaldo de CUPS: $backup"
