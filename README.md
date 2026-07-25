# Skunk PC Next

Servidor local de impresión para etiquetas Zebra de 4×6 pulgadas. Está diseñado
para ejecutarse en Ubuntu y ofrecer dos formas de impresión:

1. Panel web móvil para subir un PDF, imagen o texto, seleccionar la impresora y
   controlar ajuste, orientación y copias.
2. Impresión nativa por IPP/mDNS desde Android y iPhone.

La web incluye un manifiesto instalable y caché únicamente para sus recursos
estáticos. La instalación como PWA requiere HTTPS; por HTTP local funciona como
web móvil normal. La impresión nativa por IPP no depende de la PWA.

La versión `0.1.0` se instala en el puerto **8081** para convivir con el sistema
anterior mientras se valida con la impresora física.

## Hardware confirmado

La implementación inicial se validará contra:

- Zebra GC420t en modo EPL2.
- Resolución de 203 DPI.
- Etiqueta física de 4×6 pulgadas.
- Matriz exacta de 812×1218 puntos.
- Conexión USB con fabricante, modelo y número de serie estable.

También admite otras Zebra EPL2 o ZPL que CUPS exponga mediante un URI USB o
`socket://IP:9100`.

## Arquitectura

```text
Android/iPhone ─┬─ Web móvil :8081 ─ API ─ SQLite ─ Worker ─ CUPS ─ Zebra USB
                └─ IPP/mDNS :631 ──────────────── CUPS ─ Zebra USB
```

- `skunk-api`: panel web y API sin privilegios.
- `skunk-worker`: conversión y envío de trabajos sin privilegios.
- `skunk-admin`: agente root local, accesible únicamente mediante un socket Unix
  del grupo `skunkpc`. Solo acepta operaciones CUPS validadas.
- CUPS: spooler, publicación IPP y filtro `rastertolabel`.
- SQLite: historial y estados de los trabajos enviados desde la web.

## Formatos web soportados

- PDF, hasta 50 páginas.
- PNG, JPEG, WebP, BMP y TIFF.
- TXT y CSV.

Cada página es:

1. Renderizada a 203 DPI.
2. Rotada automáticamente cuando mejora el aprovechamiento.
3. Ajustada dentro de 812×1218 o recortada para llenar, según la opción elegida.
4. Convertida a monocromo con dithering Floyd–Steinberg.
5. Enviada a CUPS con tamaño `w288h432`, equivalente a 4×6 pulgadas.

Los documentos de Office no están habilitados en esta primera versión. Se
agregarán mediante una conversión aislada después de validar PDF e imágenes.

## Instalación de prueba

No ejecutes el instalador de Antigravity antes de instalar esta versión.

Descomprime el paquete, entra a su carpeta y ejecuta:

```bash
chmod +x install.sh uninstall.sh scripts/activate-native-printing.sh
sudo ./install.sh
```

El instalador:

- Instala CUPS, Avahi, Poppler, Ghostscript y Python.
- Crea el usuario restringido `skunkpc`.
- Solicita una contraseña para el panel.
- Instala tres servicios systemd.
- Inicia la web en `http://IP_DEL_SERVIDOR:8081`.
- No detiene ni reemplaza el servicio antiguo del puerto 8080.
- No modifica todavía `cupsd.conf`.

También puede ejecutarse sin interacción:

```bash
sudo SKUNK_ADMIN_PASSWORD='una-clave-larga-y-segura' ./install.sh
```

## Primera validación en el servidor

Después de instalar:

```bash
systemctl status skunk-admin skunk-worker skunk-api --no-pager
curl -I http://127.0.0.1:8081/login
```

Abre desde el teléfono:

```text
http://192.168.1.147:8081
```

En la tarjeta de `Planchetta`, la aplicación debe mostrar que la URI
`usb://Unknown/Printer?serial=0.0` está desconectada. El botón
**Reparar y fijar 4×6** debe seleccionar la única Zebra USB detectada y
configurar:

```text
usb://Zebra%20Technologies/ZTC%20GC420t%20(EPL)?serial=54J170200124
```

Antes de reparar una cola con trabajos antiguos, revísalos:

```bash
lpstat -o Planchetta
```

No se cancelan automáticamente, porque podrían contener impresiones válidas.
Si son solamente pruebas antiguas y decides eliminarlas:

```bash
cancel -a Planchetta
```

La secuencia completa de validación y reversión está en
[`docs/VALIDACION-BODEGA.md`](docs/VALIDACION-BODEGA.md).

## Activar impresión nativa

Hazlo únicamente después de validar una impresión desde la web nueva:

```bash
sudo skunk-activate-native-printing
```

Este comando:

- Crea un respaldo de `cupsd.conf`.
- Activa compartir impresoras en la red local mediante CUPS.
- Deshabilita `cups-browsed`, evitando que las Lexmark de la red aparezcan como
  colas locales de Skunk PC.
- Mantiene Avahi para mDNS.
- Si UFW está activo, limita IPP y mDNS a la subred local.

La impresora Zebra debe quedar marcada como compartida para que aparezca en el
diálogo de impresión de Android/iPhone.

## Seguridad

Esta versión no:

- Ejecuta la web como root.
- Desactiva AppArmor.
- Cambia a `666` los permisos de todo el bus USB.
- Descomprime respaldos sobre `/`.
- Ejecuta comandos Git desde un watchdog.
- Cancela automáticamente trabajos atascados.
- Publica impresoras descubiertas que no sean Zebra en su panel.

La versión de prueba usa HTTP dentro de la LAN. Antes de exponer el panel fuera
de la red local debe añadirse HTTPS mediante un proxy confiable.

## Desarrollo y pruebas

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest
```

Para arrancar sin autenticación únicamente en desarrollo:

```bash
export SKUNK_DEV_AUTH_BYPASS=1
export SKUNK_DATA_DIR="$PWD/.dev-data"
export SKUNK_PORT=8081
python -m skunk_pc.main
```

Nunca habilites `SKUNK_DEV_AUTH_BYPASS` en el servidor.

## Desinstalación segura

```bash
sudo ./uninstall.sh
```

Detiene los servicios y aparta sus unidades, pero conserva código,
configuración e historial para evitar pérdida accidental de datos.
