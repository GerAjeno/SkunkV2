#!/usr/bin/env bash
set -Eeuo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root_dir"

default_iso="/mnt/Datos/Descargas/ubuntu-26.04-live-server-amd64.iso"
base_iso="${1:-$default_iso}"
expected_base_sha256="dec49008a71f6098d0bcfc822021f4d042d5f2db279e4d75bdd981304f1ca5d9"

for command in xorriso bsdtar sha256sum md5sum awk sed grep; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Falta el comando de construcción: $command" >&2
        exit 1
    fi
done

if [[ ! -f $base_iso ]]; then
    echo "No existe la ISO base: $base_iso" >&2
    exit 1
fi

actual_base_sha256="$(sha256sum "$base_iso" | awk '{print $1}')"
if [[ $actual_base_sha256 != "$expected_base_sha256" ]]; then
    echo "La ISO base no coincide con Ubuntu Server 26.04 (20260420.1)." >&2
    echo "Esperado: $expected_base_sha256" >&2
    echo "Obtenido: $actual_base_sha256" >&2
    echo "Usa exactamente ubuntu-26.04-live-server-amd64.iso." >&2
    exit 1
fi

python_bin="${PYTHON_BIN:-$root_dir/.venv/bin/python}"
if [[ ! -x $python_bin ]]; then
    echo "No existe $python_bin. Crea .venv e instala el proyecto primero." >&2
    exit 1
fi

version="$("$python_bin" -c \
    'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
deb="$root_dir/dist/skunk-pc_${version}_amd64.deb"
if [[ ! -f $deb ]]; then
    echo "No existe $deb; construyendo primero el paquete Debian..."
    "$root_dir/scripts/build-deb.sh"
fi

output_dir="$root_dir/dist"
output="$output_dir/skunk-pc-server-${version}-ubuntu-26.04-amd64.iso"
checksum_file="$output.sha256"
mkdir -p "$output_dir" "$root_dir/build"

work_dir="$(mktemp -d "$root_dir/build/skunk-pc-iso.XXXXXX")"
cleanup() {
    rm -rf -- "$work_dir"
}
trap cleanup EXIT

original_grub="$work_dir/grub.cfg.original"
original_loopback="$work_dir/loopback.cfg.original"
grub_cfg="$work_dir/grub.cfg"
loopback_cfg="$work_dir/loopback.cfg"
md5_file="$work_dir/md5sum.txt"

bsdtar -xOf "$base_iso" boot/grub/grub.cfg >"$original_grub"
bsdtar -xOf "$base_iso" boot/grub/loopback.cfg >"$original_loopback"
bsdtar -xOf "$base_iso" md5sum.txt >"$work_dir/md5sum.original"

custom_menu="$work_dir/custom-menu.cfg"
printf '%s\n' \
    'menuentry "Instalar Ubuntu Server + Skunk PC (guiado)" {' \
    '	set gfxpayload=keep' \
    '	linux	/casper/vmlinuz autoinstall ds=nocloud\;s=/cdrom/nocloud/ ---' \
    '	initrd	/casper/initrd' \
    '}' \
    >"$custom_menu"

awk -v menu_file="$custom_menu" '
    BEGIN {
        while ((getline line < menu_file) > 0) {
            menu = menu line "\n"
        }
        close(menu_file)
    }
    !inserted && /^menuentry / {
        printf "%s", menu
        inserted = 1
    }
    { print }
' "$original_grub" >"$grub_cfg"

awk -v menu_file="$custom_menu" '
    BEGIN {
        while ((getline line < menu_file) > 0) {
            menu = menu line "\n"
        }
        close(menu_file)
    }
    !inserted && /^menuentry / {
        printf "%s", menu
        inserted = 1
    }
    { print }
' "$original_loopback" >"$loopback_cfg"

awk '
    $2 != "./boot/grub/grub.cfg" &&
    $2 != "./boot/grub/loopback.cfg" &&
    $2 != "./nocloud/user-data" &&
    $2 != "./nocloud/meta-data" &&
    $2 != "./skunk/skunk-pc.deb" &&
    $2 != "./skunk/99-skunk-pc" &&
    $2 != "./skunk/PRIMER-INICIO.txt"
' "$work_dir/md5sum.original" >"$md5_file"

append_md5() {
    local source="$1"
    local destination="$2"
    printf '%s  ./%s\n' "$(md5sum "$source" | awk '{print $1}')" "$destination" \
        >>"$md5_file"
}

append_md5 "$grub_cfg" "boot/grub/grub.cfg"
append_md5 "$loopback_cfg" "boot/grub/loopback.cfg"
append_md5 packaging/iso/user-data "nocloud/user-data"
append_md5 packaging/iso/meta-data "nocloud/meta-data"
append_md5 "$deb" "skunk/skunk-pc.deb"
append_md5 packaging/iso/99-skunk-pc "skunk/99-skunk-pc"
append_md5 packaging/iso/PRIMER-INICIO.txt "skunk/PRIMER-INICIO.txt"

rm -f -- "$output" "$checksum_file"
xorriso \
    -indev "$base_iso" \
    -outdev "$output" \
    -boot_image any replay \
    -volid "SKUNKPC2604AMD64" \
    -padding 0 \
    -map "$grub_cfg" /boot/grub/grub.cfg \
    -map "$loopback_cfg" /boot/grub/loopback.cfg \
    -map packaging/iso/user-data /nocloud/user-data \
    -map packaging/iso/meta-data /nocloud/meta-data \
    -map "$deb" /skunk/skunk-pc.deb \
    -map packaging/iso/99-skunk-pc /skunk/99-skunk-pc \
    -map packaging/iso/PRIMER-INICIO.txt /skunk/PRIMER-INICIO.txt \
    -map "$md5_file" /md5sum.txt \
    -commit

sha256sum "$output" >"$checksum_file"
echo "ISO creada: $output"
echo "SHA256: $(awk '{print $1}' "$checksum_file")"
