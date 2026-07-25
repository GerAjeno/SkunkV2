# Validación controlada en Bodega

Esta guía corresponde al servidor diagnosticado el 23 de julio de 2026:

- Ubuntu 26.04 LTS, IP `192.168.1.147`.
- Zebra GC420t EPL2, USB `0a5f:00d3`.
- Serie `54J170200124`.
- URI física:
  `usb://Zebra%20Technologies/ZTC%20GC420t%20(EPL)?serial=54J170200124`.
- Cola existente `Planchetta` con URI incorrecta
  `usb://Unknown/Printer?serial=0.0`.
- Etiqueta objetivo: 4×6 pulgadas, 203 DPI, 812×1218 puntos.

## 1. Revisar trabajos antiguos

Antes de corregir la URI:

```bash
lpstat -o Planchetta
```

Si existen únicamente trabajos de prueba antiguos y autorizas descartarlos:

```bash
cancel -a Planchetta
```

La aplicación nueva no cancela trabajos automáticamente.

## 2. Instalar en paralelo

Desde la carpeta descomprimida:

```bash
sudo ./install.sh
systemctl status skunk-admin skunk-worker skunk-api --no-pager
```

El panel anterior permanece en el puerto 8080. Abre la versión nueva en:

```text
http://192.168.1.147:8081
```

## 3. Reparar y probar

1. Inicia sesión en el panel nuevo.
2. Confirma que `Planchetta` se muestra desconectada.
3. Pulsa **Reparar y fijar 4×6**.
4. Pulsa **Prueba**.
5. Carga una imagen y luego un PDF real desde el teléfono.
6. Verifica orientación, márgenes, contraste y avance de una sola etiqueta.

Comprobación por terminal:

```bash
lpstat -v Planchetta
lpoptions -p Planchetta
journalctl -u skunk-worker -u skunk-api -n 100 --no-pager
```

## 4. Activar impresión nativa

Solo después de aprobar la impresión web:

```bash
sudo skunk-activate-native-printing
```

Comprueba en Android o iPhone que aparezca `Planchetta` en el diálogo nativo de
impresión y realiza una etiqueta de prueba.

## 5. Convivencia y reversión

Mientras dure la validación:

- Aplicación anterior: puerto 8080.
- Skunk PC Next: puerto 8081.
- Ambas usan CUPS, pero solo debe enviarse una prueba a la vez.

Para detener la versión nueva sin borrar datos:

```bash
sudo systemctl disable --now skunk-api skunk-worker skunk-admin
```

Para volver a iniciarla:

```bash
sudo systemctl enable --now skunk-admin skunk-worker skunk-api
```

No desactives la aplicación anterior hasta completar las pruebas web y nativas.
