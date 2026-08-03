#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -eq 0 ]]; then
    echo "Ejecuta este script como usuario normal; solicitará sudo cuando sea necesario." >&2
    exit 1
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_dir"

expected_remote="https://github.com/GerAjeno/SkunkV2.git"
actual_remote="$(git remote get-url origin 2>/dev/null || true)"
if [[ "$actual_remote" != "$expected_remote" ]]; then
    echo "El remoto origin no corresponde a $expected_remote." >&2
    exit 1
fi
if [[ "$(git branch --show-current)" != "main" ]]; then
    echo "El actualizador debe ejecutarse desde la rama main." >&2
    exit 1
fi
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "Hay cambios locales versionados. Guárdalos antes de actualizar." >&2
    exit 1
fi

echo "Buscando actualizaciones en origin/main..."
git pull --ff-only origin main
new_commit="$(git rev-parse --short HEAD)"

echo "La fase administrativa realizará un respaldo y reiniciará solo Skunk PC."
sudo -v

# Migra la configuración original de un día de historial al valor
# predeterminado actual de 30 días. Preserva cualquier valor que el
# administrador haya personalizado explícitamente.
if sudo grep -qx 'SKUNK_JOB_RETENTION_HOURS=24' /etc/skunk-pc/skunk.env; then
    sudo sed -i 's/^SKUNK_JOB_RETENTION_HOURS=24$/SKUNK_JOB_RETENTION_HOURS=720/' \
        /etc/skunk-pc/skunk.env
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="/var/backups/skunk-pc/update-${timestamp}"
staging_dir="$(sudo mktemp -d /opt/skunk-pc/app.update.XXXXXX)"
sudo chmod 0755 "$staging_dir"
swapped=0
console_service_existed=0

cleanup() {
    if [[ -n "${staging_dir:-}" && "$staging_dir" == /opt/skunk-pc/app.update.* ]]; then
        sudo rm -rf -- "$staging_dir"
    fi
}

rollback() {
    exit_code=$?
    trap - ERR
    echo "La validación falló; restaurando la aplicación anterior." >&2
    if (( swapped == 1 )) && [[ -d "$backup_dir/app" ]]; then
        sudo systemctl stop skunk-api skunk-worker skunk-admin || true
        sudo mv /opt/skunk-pc/app "$backup_dir/failed-app" || true
        sudo mv "$backup_dir/app" /opt/skunk-pc/app
    fi
    if [[ -d "$backup_dir/services" ]]; then
        sudo cp "$backup_dir/services/"*.service /etc/systemd/system/
        if [[ -f "$backup_dir/skunk-activate-native-printing" ]]; then
            sudo cp "$backup_dir/skunk-activate-native-printing" \
                /usr/local/sbin/skunk-activate-native-printing
        fi
        sudo systemctl daemon-reload
        sudo systemctl restart skunk-admin skunk-worker skunk-api || true
    fi
    if (( console_service_existed == 1 )); then
        sudo cp "$backup_dir/skunk-console.service" \
            /etc/systemd/system/skunk-console.service
        sudo systemctl daemon-reload
        sudo systemctl restart skunk-console.service || true
    else
        sudo systemctl disable --now skunk-console.service 2>/dev/null || true
        sudo rm -f /etc/systemd/system/skunk-console.service
        sudo systemctl enable --now getty@tty1.service || true
    fi
    cleanup
    exit "$exit_code"
}

trap rollback ERR
trap cleanup EXIT

sudo install -d -m 0755 -o root -g root "$backup_dir/services"
sudo cp -a \
    /etc/systemd/system/skunk-admin.service \
    /etc/systemd/system/skunk-worker.service \
    /etc/systemd/system/skunk-api.service \
    "$backup_dir/services/"
if [[ -f /etc/systemd/system/skunk-console.service ]]; then
    sudo cp -a /etc/systemd/system/skunk-console.service \
        "$backup_dir/skunk-console.service"
    console_service_existed=1
fi
if [[ -f /usr/local/sbin/skunk-activate-native-printing ]]; then
    sudo cp -a /usr/local/sbin/skunk-activate-native-printing "$backup_dir/"
fi

sudo install -d -m 0755 -o root -g root "$staging_dir/skunk_pc"
sudo cp -a skunk_pc/. "$staging_dir/skunk_pc/"
sudo install -m 0644 pyproject.toml "$staging_dir/pyproject.toml"
sudo install -m 0644 README.md "$staging_dir/README.md"
sudo chown -R root:root "$staging_dir"

sudo /opt/skunk-pc/venv/bin/pip install "$staging_dir"
sudo install -m 0644 deploy/skunk-admin.service /etc/systemd/system/
sudo install -m 0644 deploy/skunk-worker.service /etc/systemd/system/
sudo install -m 0644 deploy/skunk-api.service /etc/systemd/system/
sudo install -m 0644 deploy/skunk-console.service /etc/systemd/system/
sudo install -m 0755 scripts/activate-native-printing.sh \
    /usr/local/sbin/skunk-activate-native-printing

sudo mv /opt/skunk-pc/app "$backup_dir/app"
sudo mv "$staging_dir" /opt/skunk-pc/app
staging_dir=""
swapped=1

sudo systemctl daemon-reload
if ! command -v cmatrix >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y cmatrix
fi
sudo systemctl enable getty@tty1.service
sudo systemctl enable skunk-console.service
sudo systemctl restart skunk-admin skunk-worker skunk-api skunk-console

port="$(sudo sed -n 's/^SKUNK_PORT=//p' /etc/skunk-pc/skunk.env)"
if [[ ! "$port" =~ ^[0-9]+$ ]]; then
    echo "No se pudo determinar el puerto configurado." >&2
    false
fi
healthy=0
for _attempt in {1..20}; do
    if sudo systemctl is-active --quiet \
           skunk-admin skunk-worker skunk-api skunk-console &&
       curl --fail --silent --output /dev/null \
           "http://127.0.0.1:${port}/login"; then
        healthy=1
        break
    fi
    sleep 1
done
if (( healthy != 1 )); then
    echo "Los servicios no superaron la validación dentro de 20 segundos." >&2
    false
fi

trap - ERR
server_ip="$(hostname -I | awk '{print $1}')"
echo "Skunk PC actualizado correctamente al commit ${new_commit}."
echo "Panel: http://${server_ip}:${port}"
echo "Respaldo: ${backup_dir}"
