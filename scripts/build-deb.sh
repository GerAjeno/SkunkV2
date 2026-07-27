#!/usr/bin/env bash
set -Eeuo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root_dir"

for command in ar tar sed awk du find sha256sum; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Falta el comando de construcción: $command" >&2
        exit 1
    fi
done

python_bin="${PYTHON_BIN:-$root_dir/.venv/bin/python}"
if [[ ! -x $python_bin ]]; then
    echo "No existe $python_bin. Crea .venv e instala el proyecto primero." >&2
    exit 1
fi

python_version="$("$python_bin" -c \
    'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ $python_version != 3.14 ]]; then
    echo "El paquete para Ubuntu 26.04 debe construirse con Python 3.14." >&2
    exit 1
fi

version="$("$python_bin" -c \
    'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
case "$(uname -m)" in
    x86_64) architecture=amd64 ;;
    *) echo "Arquitectura de construcción no compatible: $(uname -m)" >&2; exit 1 ;;
esac

build_parent="$root_dir/build"
output_dir="$root_dir/dist"
mkdir -p "$build_parent" "$output_dir"
work_dir="$(mktemp -d "$build_parent/skunk-pc-deb.XXXXXX")"
cleanup() {
    rm -rf -- "$work_dir"
}
trap cleanup EXIT

package_root="$work_dir/root"
control_root="$work_dir/control"
wheel_dir="$package_root/usr/share/skunk-pc/wheels"
source_date_epoch="${SOURCE_DATE_EPOCH:-$(git log -1 --format=%ct 2>/dev/null || date +%s)}"
export SOURCE_DATE_EPOCH="$source_date_epoch"
mkdir -p \
    "$package_root/opt/skunk-pc/app" \
    "$package_root/usr/lib/systemd/system" \
    "$package_root/usr/sbin" \
    "$package_root/usr/share/doc/skunk-pc" \
    "$wheel_dir" \
    "$control_root"

"$python_bin" -m pip wheel \
    --disable-pip-version-check \
    --wheel-dir "$wheel_dir" \
    --requirement packaging/debian/requirements.lock
"$python_bin" -m pip wheel \
    --disable-pip-version-check \
    --wheel-dir "$wheel_dir" \
    --requirement packaging/debian/build-requirements.lock

build_venv="$work_dir/build-venv"
"$python_bin" -m venv "$build_venv"
"$build_venv/bin/python" -m pip install \
    --disable-pip-version-check \
    --no-index \
    --find-links "$wheel_dir" \
    --requirement packaging/debian/build-requirements.lock
"$build_venv/bin/python" -m pip wheel \
    --disable-pip-version-check \
    --no-build-isolation \
    --no-deps \
    --wheel-dir "$wheel_dir" \
    "$root_dir"

cp -a skunk_pc "$package_root/opt/skunk-pc/app/"
install -m 0644 pyproject.toml "$package_root/opt/skunk-pc/app/pyproject.toml"
install -m 0644 README.md "$package_root/usr/share/doc/skunk-pc/README.md"
install -m 0644 LICENSE "$package_root/usr/share/doc/skunk-pc/copyright"
install -m 0644 deploy/*.service "$package_root/usr/lib/systemd/system/"
install -m 0755 scripts/activate-native-printing.sh \
    "$package_root/usr/sbin/skunk-activate-native-printing"
install -m 0755 scripts/skunk-setup \
    "$package_root/usr/sbin/skunk-setup"

find "$package_root/opt/skunk-pc/app" \
    -type d -name __pycache__ -prune -exec rm -rf -- {} +
find "$package_root/opt/skunk-pc/app" \
    -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete

installed_size="$(du -sk "$package_root" | awk '{print $1}')"
sed \
    -e "s/@VERSION@/$version/g" \
    -e "s/@INSTALLED_SIZE@/$installed_size/g" \
    packaging/debian/control.in >"$control_root/control"
sed "s/@VERSION@/$version/g" \
    packaging/debian/postinst.in >"$control_root/postinst"
install -m 0755 packaging/debian/prerm "$control_root/prerm"
install -m 0755 packaging/debian/postrm "$control_root/postrm"
chmod 0755 "$control_root/postinst"

tar_options=(
    --sort=name
    "--mtime=@$source_date_epoch"
    --owner=0
    --group=0
    --numeric-owner
)
tar "${tar_options[@]}" -C "$control_root" -cJf "$work_dir/control.tar.xz" .
tar "${tar_options[@]}" -C "$package_root" -cJf "$work_dir/data.tar.xz" .
printf '2.0\n' >"$work_dir/debian-binary"

package="$output_dir/skunk-pc_${version}_${architecture}.deb"
rm -f -- "$package"
(
    cd "$work_dir"
    ar rD "$package" debian-binary control.tar.xz data.tar.xz
)

echo "Paquete creado: $package"
echo "SHA256: $(sha256sum "$package" | awk '{print $1}')"
