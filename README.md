# HomeLan

[![pruebas](https://github.com/Padd1991/HomeLan/actions/workflows/pruebas.yml/badge.svg)](https://github.com/Padd1991/HomeLan/actions/workflows/pruebas.yml)

Un panel web para ver tu red de casa: qué dispositivos hay, desde cuándo, de qué cuelga cada uno (router, switch, repetidor, WiFi) y cuánto se descarga de internet. Guarda el historial de conexiones y avisa de dispositivos nuevos, cambios de IP y conflictos.

**[English below](#homelan-english)**

- **Solo lee la red; no cambia ninguna configuración.** No envía nada fuera de casa. La única excepción posible son las consultas de nombre (DNS inverso) de tus IP al servidor DNS del equipo, casi siempre el router, que podría reenviarlas fuera; se desactivan con `PANEL_DNS=0` (ver [Opciones](#opciones)).
- Funciona con cualquier router: busca los dispositivos por ARP y, si el router lo ofrece, lee la bajada por UPnP.
- Con un **ZTE F6600P (Vodafone)** puede además leer la web del router para saber en qué boca o banda WiFi está cada dispositivo (opcional; pide la contraseña de esa web).
- En español e inglés.

## Instalación

Necesita un equipo encendido siempre en la red de casa, con Linux: una Raspberry Pi, un mini PC, un NAS o un servidor Proxmox. Ocupa unos 30 MB de memoria.

Descarga el paquete `panel-red-vX.tar.gz` de la última versión en [Releases](../../releases) y descomprímelo con `tar xzf panel-red-*.tar.gz`. También puedes clonar el repositorio con `git clone`. Por dentro el programa se sigue llamando `panel-red` (servicio, carpetas y `panel-red.local`).

### Con Docker (recomendado)

```sh
docker compose up -d --build
```

Después abre `http://IP-DE-ESE-EQUIPO:8080`. La primera vez pide un código de instalación, que sale en el registro: `docker logs panel-red | grep 'setup code'`. El puerto y la zona horaria se cambian en `compose.yaml`.

El panel usa la red del propio equipo (`network_mode: host`): sin eso no puede ver los dispositivos. Por eso funciona en Docker para Linux (también Raspberry Pi y NAS Synology), pero **no en Docker Desktop de Windows o Mac**.

### En Debian, Ubuntu o Raspberry Pi OS

Desde esta carpeta, como root:

```sh
sudo ./instalar.sh                       # puerto 80
sudo PANEL_PUERTO=8080 ./instalar.sh     # otro puerto
```

Al terminar muestra la dirección del panel y el código de instalación que pide la primera vez.

Para actualizar, ejecuta otra vez `instalar.sh` con la versión nueva: conserva los datos. Para quitarlo, usa `sudo ./instalar.sh --desinstalar`; los datos se quedan en `/var/lib/panel-red`, salvo que añadas `--borrar-datos`.

### En una Raspberry Pi, paso a paso

Sirve cualquier Raspberry Pi con red, incluida una Zero 2 W. Mejor con cable de red que por WiFi.

1. Con **Raspberry Pi Imager**, graba **Raspberry Pi OS Lite** en la tarjeta. En la configuración del propio Imager:
   - Nombre del equipo: `panel-red`.
   - Pon un usuario y una contraseña, y activa **SSH**.
   - Configura el WiFi solo si no vas a usar cable.
2. Enciende la Pi y espera un par de minutos. Desde tu ordenador, copia el paquete y entra:

   ```sh
   scp panel-red-*.tar.gz TU_USUARIO@panel-red.local:
   ssh TU_USUARIO@panel-red.local
   ```
3. Ya dentro de la Pi:

   ```sh
   tar xzf panel-red-*.tar.gz && cd panel-red && sudo ./instalar.sh
   ```
4. Abre `http://panel-red.local` en el navegador.

El panel agrupa sus escrituras para no desgastar la tarjeta. Aun así, con tarjetas baratas conviene tener a mano una copia de la carpeta `/var/lib/panel-red`.

### En Proxmox

En el Proxmox, desde esta carpeta, como root:

```sh
./proxmox-lxc.sh                                     # IP por DHCP
./proxmox-lxc.sh --ip 192.168.1.5/24 --gw 192.168.1.1
```

Crea un contenedor LXC Debian (512 MB, 1 núcleo, 4 GB) y lo deja instalado. Pregunta antes de crear nada.

## Primeros pasos

1. Abre el panel: la primera vez pide el código de instalación, un nombre para tu red y una contraseña. El código lo muestra el instalador; si no lo tienes, búscalo en el registro con `journalctl -u panel-red | grep 'setup code'` (o `docker logs panel-red | grep 'setup code'`). Sirve para que nadie más de tu red pueda poner la contraseña antes que tú.
2. En **Dispositivos**, pon nombre y tipo a lo que vaya apareciendo y elige de qué cuelga cada uno. Así se construye el mapa.
3. En **Ajustes**:
   - Añade los switches que no tienen IP.
   - Si tienes un router compatible, pon su contraseña. Se comprueba antes de guardarla.

## Opciones

Se ponen como variables de entorno. Con Docker, en la sección `environment` de `compose.yaml`. Con `instalar.sh`, con `sudo systemctl edit panel-red`, que crea un archivo aparte que se conserva al actualizar:

```ini
[Service]
Environment=PANEL_HOSTS=panel-red.lan
```

Después, `sudo systemctl restart panel-red`.

| Variable | Para qué |
|---|---|
| `PANEL_PUERTO` | Puerto de la web (80; 8080 con Docker). |
| `PANEL_HOSTS` | Nombres extra con los que se abre la web, separados por comas. Hace falta si el router le da un nombre propio (`panel-red.lan`, `panel-red.home`...) y la web dice que «no se abre con el nombre» que usas. |
| `PANEL_RED` | Rangos que se escanean, separados por comas (`192.168.0.0/23`), hasta 2048 direcciones en total. Por defecto, toda la red; si es mayor que /22, solo el /22 de este equipo y el router, porque no daría tiempo a escanearla entera cada minuto. |
| `PANEL_DNS` | `0` para no buscar nombres por DNS inverso (solo por mDNS, que no sale de casa). |
| `PANEL_IFACE` | Interfaz de red que se escanea, si no es la de la ruta por defecto. |
| `PANEL_DATOS` | Carpeta de datos. |

## Seguridad y datos

- La web solo responde desde la red local (o Tailscale), con contraseña, y no se debe abrir a internet.
- Los datos (historial, ajustes y contraseña del router) se guardan en `/var/lib/panel-red`, o en el volumen `datos` con Docker. La contraseña del panel se guarda cifrada (scrypt).
- El historial se guarda 400 días. Los dispositivos sin identificar que llevan 30 días sin aparecer se olvidan (sobre todo móviles con MAC privada que cambia); sus avisos se conservan.

## Pruebas y licencia

Con Python 3.9 o superior, sin instalar nada más: `python3 prueba.py`, `python3 prueba_router.py` y `python3 prueba_web.py`. GitHub las pasa solas en cada cambio, junto con el instalador y la imagen de Docker para PC y Raspberry Pi.

Licencia [MIT](LICENSE).

---

<a id="homelan-english"></a>

# HomeLan (English)

A web panel for your home network: which devices are there, since when, what each one is connected to (router, switch, extender, WiFi) and how much is downloaded from the internet. It keeps a connection history and alerts you about new devices, IP changes and conflicts.

- **Read only: it never changes any setting.** Nothing leaves your home. The only possible exception is name lookups (reverse DNS) of your IPs sent to the machine's DNS server, usually the router, which might forward them; turn them off with `PANEL_DNS=0` (see [Options](#options)).
- Works with any router: it finds devices with ARP and, if the router offers it, reads the download counter over UPnP.
- With a **ZTE F6600P (Vodafone)** it can also read the router's web page to know which port or WiFi band each device uses (optional; needs that page's password).
- Spanish and English.

## Install

You need an always-on Linux machine on your home network: a Raspberry Pi, a mini PC, a NAS or a Proxmox server. It uses about 30 MB of memory.

Download the latest `panel-red-vX.tar.gz` package from [Releases](../../releases) and unpack it with `tar xzf panel-red-*.tar.gz`, or `git clone` the repository. Internally the program is still called `panel-red` (service, folders and `panel-red.local`). The code and its comments are in Spanish.

### Docker (recommended)

```sh
docker compose up -d --build
```

Then open `http://THAT-MACHINE-IP:8080`. The first time it asks for a setup code, which is in the log: `docker logs panel-red | grep 'setup code'`. Change the port and time zone in `compose.yaml`.

The panel uses the host's own network (`network_mode: host`) or it cannot see your devices. It works on Docker for Linux (including Raspberry Pi and Synology NAS), but **not on Docker Desktop for Windows or Mac**.

### Debian, Ubuntu or Raspberry Pi OS

From this folder, as root:

```sh
sudo ./instalar.sh                       # port 80
sudo PANEL_PUERTO=8080 ./instalar.sh     # another port
```

When it finishes it shows the panel's address and the setup code it asks for the first time.

To update, run `instalar.sh` again from the new version: your data is kept. To remove it, use `sudo ./instalar.sh --desinstalar`. Data stays in `/var/lib/panel-red` unless you add `--borrar-datos`.

### Raspberry Pi, step by step

Any Raspberry Pi with networking works, including a Zero 2 W. Wired is better than WiFi.

1. With **Raspberry Pi Imager**, write **Raspberry Pi OS Lite** to the card. In Imager's own settings:
   - Hostname: `panel-red`.
   - Set a user and a password, and enable **SSH**.
   - Set up WiFi only if you won't use a cable.
2. Power the Pi on and wait a couple of minutes. From your computer, copy the package and log in:

   ```sh
   scp panel-red-*.tar.gz YOUR_USER@panel-red.local:
   ssh YOUR_USER@panel-red.local
   ```
3. On the Pi:

   ```sh
   tar xzf panel-red-*.tar.gz && cd panel-red && sudo ./instalar.sh
   ```
4. Open `http://panel-red.local` in your browser.

The panel batches its writes to spare the SD card. Still, with cheap cards keep a copy of `/var/lib/panel-red` around.

### Proxmox

On the Proxmox host, from this folder, as root:

```sh
./proxmox-lxc.sh                                     # DHCP address
./proxmox-lxc.sh --ip 192.168.1.5/24 --gw 192.168.1.1
```

It creates a Debian LXC container (512 MB, 1 core, 4 GB) with the panel installed. It asks before creating anything.

## First steps

1. Open the panel: the first time it asks for the setup code, a name for your network and a password. The installer shows the code; if you don't have it, find it in the log with `journalctl -u panel-red | grep 'setup code'` (or `docker logs panel-red | grep 'setup code'`). It makes sure nobody else on your network can set the password before you.
2. In **Devices**, name and classify devices as they appear, and choose what each one is connected to. That builds the map.
3. In **Settings**:
   - Add switches that have no IP.
   - If you have a supported router, enter its password. It is tested before it is saved.

## Options

They are environment variables. With Docker, in the `environment` section of `compose.yaml`. With `instalar.sh`, with `sudo systemctl edit panel-red`, which creates a separate file that is kept on updates:

```ini
[Service]
Environment=PANEL_HOSTS=panel-red.lan
```

Then `sudo systemctl restart panel-red`.

| Variable | What for |
|---|---|
| `PANEL_PUERTO` | Web port (80; 8080 with Docker). |
| `PANEL_HOSTS` | Extra names the web page opens with, comma separated. Needed if the router gives it its own name (`panel-red.lan`, `panel-red.home`...) and the page says it "does not open as" that name. |
| `PANEL_RED` | Ranges to scan, comma separated (`192.168.0.0/23`), up to 2048 addresses in total. By default, the whole network; if it is larger than /22, only this machine's /22 and the router, because there would be no time to scan all of it every minute. |
| `PANEL_DNS` | `0` to skip reverse DNS name lookups (mDNS only, which stays at home). |
| `PANEL_IFACE` | Network interface to scan, if not the one with the default route. |
| `PANEL_DATOS` | Data folder. |

## Security and data

- The web page only answers from the local network (or Tailscale), needs a password, and should not be exposed to the internet.
- Data (history, settings and router password) lives in `/var/lib/panel-red`, or in the `datos` volume with Docker. The panel password is stored as an scrypt hash.
- History is kept for 400 days. Unidentified devices not seen for 30 days are forgotten (mostly phones with a changing private MAC); their alerts are kept.

## Tests and license

With Python 3.9 or later and nothing else installed: `python3 prueba.py`, `python3 prueba_router.py` and `python3 prueba_web.py`. GitHub runs them on every change, along with the installer and the Docker image for PC and Raspberry Pi.

[MIT](LICENSE) license.
