# Skunk PC Next

Servidor local de impresión para etiquetas Zebra de 4×6 pulgadas. Está diseñado
para ejecutarse en Ubuntu y ofrecer dos formas de impresión:

1. Panel web móvil para subir un PDF, imagen o texto, seleccionar la impresora y
   controlar ajuste, orientación y copias.
2. Impresión nativa por IPP/mDNS desde Android y iPhone.

La web incluye un manifiesto instalable y caché únicamente para sus recursos
estáticos. La instalación como PWA requiere HTTPS; por HTTP local funciona como
web móvil normal. La impresión nativa por IPP no depende de la PWA.

El instalador usa el puerto **8081** de forma predeterminada para convivir con
un sistema anterior. En un servidor nuevo puede seleccionarse otro puerto
mediante `SKUNK_PORT`.

## Hardware compatible

La implementación ha sido validada con impresoras Zebra térmicas de escritorio,
incluidas TLP2844 y GC420t en modo EPL2, con:

- Resolución de 203 DPI.
- Etiqueta física de 4×6 pulgadas.
- Matriz exacta de 812×1218 puntos.
- Impresión térmica directa o transferencia térmica con ribbon.
- Conexión USB identificada por fabricante, modelo y número de serie.

También admite modelos Zebra equivalentes, como GK888, cuando CUPS dispone de
un controlador EPL2 o ZPL compatible. Las impresoras de red pueden agregarse
mediante una dirección `socket` en el puerto 9100.

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
- SQLite: historial unificado de trabajos web y trabajos nativos recibidos por
  CUPS.

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

## Paquete Debian recomendado

El método recomendado para servidores nuevos es el paquete `.deb` para Ubuntu
26.04 LTS en arquitectura AMD64. Incluye la aplicación y todas sus dependencias
Python en un repositorio interno de wheels, por lo que la fase Python no
necesita conectarse a Internet durante la instalación.

Instala el paquete con `apt` para que también resuelva las dependencias del
sistema:

```bash
sudo apt install ./skunk-pc_VERSION_amd64.deb
sudo skunk-setup
```

`skunk-setup`:

- Solicita una contraseña de al menos diez caracteres.
- Genera un secreto de sesión nuevo.
- Crea `/etc/skunk-pc/skunk.env` con permisos restringidos.
- Usa el puerto 8080 de forma predeterminada; puede cambiarse con
  `sudo skunk-setup --port PUERTO`.
- Activa y valida los cuatro servicios.
- Nunca sobrescribe una configuración existente.

Los datos y el historial se almacenan en `/var/lib/skunk-pc`. Una actualización
del paquete conserva tanto ese directorio como `/etc/skunk-pc/skunk.env` y
reinicia los servicios con la nueva versión.

Para construir el paquete desde el repositorio:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
./scripts/build-deb.sh
```

El artefacto y su suma SHA-256 quedan en `dist/`. Las versiones de sus
dependencias de ejecución y construcción están fijadas en los archivos
`packaging/debian/*requirements.lock`. Debe construirse con Python 3.14 sobre
AMD64, porque contiene wheels binarios dirigidos a Ubuntu 26.04.

Para desinstalar la aplicación conservando configuración e historial:

```bash
sudo apt remove skunk-pc
```

`apt purge` elimina además `/etc/skunk-pc/skunk.env`, pero conserva
deliberadamente `/var/lib/skunk-pc` para evitar pérdida accidental del
historial. Ese directorio solo debe eliminarse después de realizar un respaldo.

Después de instalar mediante `.deb`, las actualizaciones deben realizarse
instalando una versión posterior del paquete. No mezcles esa modalidad con
`update.sh`, ya que el gestor de paquetes debe mantener la propiedad de los
archivos instalados.

## ISO instalable

También puede construirse una ISO AMD64 basada en la imagen oficial de Ubuntu
Server 26.04. La ISO añade una opción guiada al menú de arranque, conserva el
instalador normal y no contiene contraseñas, nombres de impresora, direcciones
IP ni credenciales Wi-Fi.

Primero construye el `.deb` y después la ISO:

```bash
./scripts/build-deb.sh
./scripts/build-iso.sh /ruta/ubuntu-26.04-live-server-amd64.iso
```

El constructor acepta únicamente la edición oficial 20260420.1 cuya suma
SHA-256 está fijada en el script. El artefacto y su archivo `.sha256` quedan en
`dist/`.

Al arrancar selecciona **Instalar Ubuntu Server + Skunk PC (guiado)**. El
instalador solicitará red, almacenamiento, identidad y SSH antes de modificar
el disco. La red debe tener acceso a los repositorios de Ubuntu para resolver
las dependencias del sistema; las dependencias Python ya están incluidas en el
`.deb`.

Después del primer inicio:

```bash
sudo skunk-setup
```

La instalación muestra este recordatorio al iniciar sesión hasta que exista la
configuración de Skunk PC. Después se añaden las impresoras desde la web y se
activa la impresión nativa únicamente tras validar una impresión web.

## Instalación desde el código fuente

Descomprime el paquete, entra a su carpeta y ejecuta:

```bash
chmod +x install.sh uninstall.sh scripts/activate-native-printing.sh
sudo ./install.sh
```

Para instalar en el puerto 8080:

```bash
sudo env SKUNK_PORT=8080 ./install.sh
```

El instalador:

- Instala CUPS, Avahi, Poppler, Ghostscript y Python.
- Crea el usuario restringido `skunkpc`.
- Solicita una contraseña para el panel.
- Instala cuatro servicios systemd: API, worker, agente administrativo y
  monitor de consola.
- Inicia la web en el puerto elegido (8081 de forma predeterminada).
- No detiene ni reemplaza el servicio antiguo del puerto 8080.
- No modifica todavía `cupsd.conf`.

También puede ejecutarse sin interacción:

```bash
sudo SKUNK_ADMIN_PASSWORD='una-clave-larga-y-segura' ./install.sh
```

## Primera validación en el servidor

Después de instalar:

```bash
systemctl status \
  skunk-admin skunk-worker skunk-api skunk-console \
  --no-pager
curl -I http://127.0.0.1:8081/login
```

Abre el panel desde otro equipo usando el nombre local del servidor o la
dirección que le haya asignado la red, seguida del puerto configurado.

Como ejemplo de organización, las colas pueden llamarse
`Etiquetadora_Recepcion` y `Etiquetadora_Despacho`. Los nombres visibles son
libres y no deben depender del modelo, puerto USB o dirección del servidor.

Desde **Añadir impresora**, elige primero el **tipo** (Zebra o Genérica) y la
**conexión** (USB o red). Para una Zebra por USB, selecciona el dispositivo
disponible, el lenguaje compatible y el método de impresión correspondiente;
el dispositivo debe mostrar su número de serie cuando el firmware lo
proporciona, y si indica **S/N no informado**, identifica físicamente la
impresora antes de crear la cola. Para una Zebra de red usa
`socket://IP:9100`.

También puede agregarse cualquier **impresora genérica**, por USB o por red
(`ipp://`, `ipps://`, `socket://host:puerto` o `lpd://host/cola`), usando el
controlador automático de CUPS (IPP Everywhere). Las impresoras genéricas
son solo administrables desde el panel — se pueden probar, diagnosticar,
vaciar su cola, renombrar y eliminar, pero el formulario de impresión web
(subir PDF/imagen) sigue exclusivo para Zebra, porque está construido para
etiquetas 4×6 a 203 DPI.

En cada tarjeta de impresora Zebra están disponibles:

- **Prueba**: imprime una etiqueta de validación.
- **Calibrar**: ejecuta la calibración del medio.
- **Reparar y fijar 4×6**: recupera la conexión y normaliza tamaño, resolución
  y método térmico.
- **Vaciar cola**: cancela los trabajos pendientes de esa impresora.
- **Diagnóstico**: revisa conexión, estado CUPS, cola, resolución y formato.
- **Eliminar**: elimina la cola y su configuración del sistema.

Las impresoras genéricas ofrecen las mismas acciones salvo **Calibrar** (no
aplica), y su botón de reparación se llama **Reparar conexión**: solo
recupera la URI física, sin forzar tamaño de etiqueta ni resolución.

Antes de reparar una cola con trabajos antiguos, revísalos:

```bash
lpstat -o Etiquetadora_Recepcion
```

No se cancelan automáticamente, porque podrían contener impresiones válidas.
Si son solamente pruebas antiguas y decides eliminarlas:

```bash
cancel -a Etiquetadora_Recepcion
```

## Activar impresión nativa

Hazlo únicamente después de validar una impresión desde la web nueva:

```bash
sudo skunk-activate-native-printing --check
sudo skunk-activate-native-printing
```

Este comando:

- Ofrece un modo `--check` que no realiza cambios.
- Crea un respaldo de `cupsd.conf`.
- Activa compartir impresoras en la red local mediante CUPS.
- Deshabilita `cups-browsed`, evitando que las Lexmark de la red aparezcan como
  colas locales de Skunk PC.
- Mantiene Avahi para mDNS.
- Si UFW está activo, limita IPP y mDNS a la subred local.
- Valida 4×6, 203 DPI, método térmico, sintaxis de CUPS y atributos IPP.
- Restaura la configuración anterior si la activación falla.

La impresora Zebra debe quedar marcada como compartida para que aparezca en el
diálogo de impresión de Android/iPhone.

El activador no depende de un rango de red fijo. Detecta la interfaz que posee
la ruta predeterminada, obtiene su subred directamente del sistema y configura
CUPS para clientes locales. Si cambia la red del servidor, vuelve a ejecutar
primero `--check` y después la activación.

## Historial de impresión

La aplicación conserva durante 30 días los trabajos enviados desde la web y
los trabajos nativos recibidos por CUPS. El worker sincroniza CUPS cada 10
segundos y elimina cada hora solamente los registros terminales que superan la
retención; nunca elimina trabajos en cola o en procesamiento.

El panel muestra 30, 50 o 100 trabajos por página y permite filtrar por fecha,
impresora, IP/origen y resultado. La retención puede personalizarse mediante
`SKUNK_JOB_RETENTION_HOURS`.

Cada registro incluye el documento o identificador de trabajo, dispositivo o
dirección de origen, impresora de destino, resultado, páginas y hora en formato
de 24 horas. Si CUPS informa un fallo, el historial conserva el mensaje de
error. Los registros históricos completados no deben confundirse con trabajos
pendientes en la cola.

Al terminar el arranque, `skunk-console.service` abre `btop` automáticamente en
la consola física `tty1`. Se ejecuta como el usuario restringido `skunkpc`, no
como root. Al salir de `btop` con `q`, la consola muestra el inicio de sesión
normal de `tty1`.

El acceso al panel limita cada IP a cinco contraseñas incorrectas dentro de diez
minutos. El quinto fallo bloquea nuevos intentos durante quince minutos y queda
registrado en el journal de `skunk-api`, sin almacenar la contraseña introducida.

## Seguridad

Esta versión no:

- Ejecuta la web como root.
- Desactiva AppArmor.
- Cambia a `666` los permisos de todo el bus USB.
- Descomprime respaldos sobre `/`.
- Ejecuta comandos Git desde un watchdog.
- Cancela automáticamente trabajos atascados.
- Permite imprimir desde el formulario web hacia impresoras que no sean
  Zebra, aunque estén agregadas y administrables desde el panel.

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

## Migración de una imagen de disco a producción

La aplicación no contiene una dirección de red fija y puede funcionar en una
subred diferente. Sin embargo, una imagen completa conserva la configuración
de red y la identidad del equipo original. Si el hardware de producción usa
otra tarjeta Wi-Fi o Ethernet, realiza estos pasos desde una pantalla y teclado
locales.

El equipo original debe permanecer apagado durante la puesta en marcha del
clon.

### 1. Primer arranque y detección de interfaces

Inicia el equipo de producción y sal de `btop` con `q` si necesitas acceder al
inicio de sesión. Comprueba los nombres reales de sus interfaces:

```bash
ip -brief link
```

No supongas que la nueva tarjeta conservará nombres como `wlp6s0` o `eno1`.

### 2. Adaptar Netplan

Respalda la configuración existente:

```bash
sudo cp -a /etc/netplan \
  "/etc/netplan.backup.$(date +%Y%m%d-%H%M%S)"
```

Edita el archivo YAML de `/etc/netplan/` y sustituye la interfaz o coincidencia
de hardware anterior por la interfaz real del equipo de producción. Configura
en ese archivo el SSID y la contraseña de la red de producción. Marca como
`optional: true` cualquier interfaz cableada que normalmente permanezca
desconectada, para que no retrase el arranque.

Valida antes de aplicar:

```bash
sudo netplan generate
sudo netplan try
```

Después confirma la conectividad:

```bash
ip -brief address
ip route
networkctl status --all
```

### 3. Evitar identidades duplicadas

Asigna un nombre exclusivo al servidor de producción:

```bash
sudo hostnamectl hostname NOMBRE-NUEVO
```

Regenera la identidad de la máquina y las claves de host SSH solamente en el
clon:

```bash
sudo truncate -s 0 /etc/machine-id
sudo rm -f /var/lib/dbus/machine-id
sudo systemd-machine-id-setup
sudo rm -f /etc/ssh/ssh_host_*
sudo ssh-keygen -A
sudo systemctl restart ssh
```

Al cambiar las claves SSH, los clientes que conocían la identidad anterior
deberán aceptar la nueva huella después de verificarla localmente.

### 4. Zona horaria y servicios

Configura la zona horaria correspondiente a la ubicación de producción:

```bash
sudo timedatectl set-timezone America/Santiago
timedatectl
```

Comprueba los servicios:

```bash
systemctl is-active \
  cups avahi-daemon skunk-admin skunk-worker skunk-api skunk-console
```

Todos deben responder `active`.

### 5. Reconfigurar la publicación nativa

Con el servidor conectado definitivamente a la nueva red:

```bash
sudo skunk-activate-native-printing --check
sudo skunk-activate-native-printing
```

Verifica que CUPS escuche, que Avahi publique las colas y que no queden trabajos
pendientes:

```bash
ss -lntup
avahi-browse -rt _ipp._tcp
lpstat -t
```

Si UFW estaba activo, revisa sus reglas después de la activación para retirar
manualmente cualquier autorización perteneciente a la red anterior.

### 6. Validar cada impresora

Las colas USB con número de serie estable no dependen del puerto físico. Aun
así, valida cada una desde el panel:

1. Comprueba que el dispositivo aparezca disponible.
2. Ejecuta **Diagnóstico**.
3. Imprime una **Prueba**.
4. Envía una etiqueta desde el panel web.
5. Envía una etiqueta mediante impresión nativa desde un teléfono.
6. Confirma formato 4×6, orientación, calidad, copias e historial.

Si se conectan impresoras nuevas, créalas exclusivamente desde **Añadir
impresora** en el panel. No reutilices una cola de otra impresora solamente
porque ambas compartan modelo.

### 7. Verificación final

```bash
systemctl --failed
lpstat -t
journalctl \
  -u cups -u skunk-api -u skunk-worker -u skunk-admin \
  --since "15 minutes ago" --no-pager
```

Conserva apagado el servidor original hasta completar estas pruebas y confirmar
que no existen nombres de host, colas o identidades duplicadas.

## Actualización desde GitHub

Después de instalar la aplicación, las versiones nuevas se aplican desde el
repositorio con:

```bash
cd <RUTA-DEL-REPOSITORIO>
./update.sh
```

El actualizador exige la rama `main` sin cambios locales, descarga únicamente
un avance rápido desde `origin/main`, crea un respaldo en
`/var/backups/skunk-pc`, actualiza código, dependencias y unidades, reinicia los
cuatro servicios y valida el panel en el puerto configurado. Si la validación
falla, restaura la aplicación y las unidades anteriores.

## Desinstalación segura

```bash
sudo ./uninstall.sh
```

Detiene los servicios y aparta sus unidades, pero conserva código,
configuración e historial para evitar pérdida accidental de datos.
