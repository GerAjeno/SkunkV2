#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Ejecuta con sudo: sudo ./install.sh" >&2
    exit 1
fi

source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
app_root="/opt/skunk-pc"
app_dir="${app_root}/app"
venv_dir="${app_root}/venv"
config_dir="/etc/skunk-pc"
data_dir="/var/lib/skunk-pc"
service_user="skunkpc"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
    cups cups-client cups-filters avahi-daemon avahi-utils \
    python3 python3-venv python3-pip poppler-utils ghostscript \
    fonts-dejavu-core

if ! getent group "$service_user" >/dev/null; then
    groupadd --system "$service_user"
fi
if ! id "$service_user" >/dev/null 2>&1; then
    useradd --system --gid "$service_user" --home-dir "$data_dir" \
        --shell /usr/sbin/nologin "$service_user"
fi
usermod -aG lp "$service_user"

install -d -m 0755 -o root -g root "$app_root" "$app_dir"
install -d -m 0750 -o root -g "$service_user" "$config_dir"
install -d -m 0750 -o "$service_user" -g "$service_user" \
    "$data_dir" "$data_dir/uploads" "$data_dir/output"

cp -a "${source_dir}/skunk_pc" "$app_dir/"
install -m 0644 "${source_dir}/pyproject.toml" "$app_dir/pyproject.toml"
install -m 0644 "${source_dir}/README.md" "$app_dir/README.md"

if [[ ! -x "${venv_dir}/bin/python" ]]; then
    python3 -m venv "$venv_dir"
fi
"${venv_dir}/bin/pip" install --upgrade pip
"${venv_dir}/bin/pip" install "$app_dir"

password="${SKUNK_ADMIN_PASSWORD:-}"
if [[ -z "$password" ]]; then
    if [[ ! -t 0 ]]; then
        echo "Define SKUNK_ADMIN_PASSWORD para una instalación no interactiva." >&2
        exit 1
    fi
    while true; do
        read -r -s -p "Contraseña del panel Skunk PC (mínimo 10 caracteres): " password
        echo
        read -r -s -p "Repite la contraseña: " confirmation
        echo
        if [[ "$password" != "$confirmation" ]]; then
            echo "Las contraseñas no coinciden."
        elif [[ ${#password} -lt 10 ]]; then
            echo "La contraseña es demasiado corta."
        else
            break
        fi
    done
fi

password_hash="$(
    printf '%s' "$password" |
    PYTHONPATH="$app_dir" "${venv_dir}/bin/python" -c \
        'import sys; from skunk_pc.auth import hash_password; print(hash_password(sys.stdin.read()))'
)"
unset password confirmation
session_secret="$("${venv_dir}/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')"

cat > "${config_dir}/skunk.env" <<EOF
SKUNK_HOST=0.0.0.0
SKUNK_PORT=8081
SKUNK_DATA_DIR=${data_dir}
SKUNK_SESSION_SECRET=${session_secret}
SKUNK_PASSWORD_HASH=${password_hash}
SKUNK_MAX_UPLOAD_MB=30
SKUNK_JOB_RETENTION_HOURS=24
SKUNK_ADMIN_SOCKET=/run/skunk-pc/admin.sock
EOF
chown root:"$service_user" "${config_dir}/skunk.env"
chmod 0640 "${config_dir}/skunk.env"

install -m 0644 "${source_dir}/deploy/skunk-admin.service" /etc/systemd/system/
install -m 0644 "${source_dir}/deploy/skunk-api.service" /etc/systemd/system/
install -m 0644 "${source_dir}/deploy/skunk-worker.service" /etc/systemd/system/
install -m 0755 "${source_dir}/scripts/activate-native-printing.sh" \
    /usr/local/sbin/skunk-activate-native-printing

systemctl daemon-reload
systemctl enable --now skunk-admin.service skunk-worker.service skunk-api.service

server_ip="$(hostname -I | awk '{print $1}')"
echo
echo "Skunk PC instalado en modo de prueba."
echo "Panel nuevo: http://${server_ip}:8081"
echo "El sistema anterior no fue detenido ni reemplazado."
echo "Para activar la configuración nativa segura: sudo skunk-activate-native-printing"

