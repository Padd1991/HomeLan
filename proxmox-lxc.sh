#!/bin/bash
# Crea en Proxmox un contenedor LXC con el panel de red ya instalado. Se ejecuta en el Proxmox, como root,
# desde la carpeta del panel. Creates a Proxmox LXC container with the network panel installed.
#
#   ./proxmox-lxc.sh                                   pregunta antes de crear (IP por DHCP)
#   ./proxmox-lxc.sh --ip 192.168.1.5/24 --gw 192.168.1.1
#   ./proxmox-lxc.sh --id 120 --almacen local-lvm --puente vmbr0 --si
set -euo pipefail

DIR=$(cd "$(dirname "$0")" && pwd)
ID="" IP="dhcp" GW="" ALMACEN="" PUENTE="vmbr0" SI=0
fallo() { echo "ERROR: $*" >&2; exit 1; }
while [ $# -gt 0 ]; do
  case "$1" in
    --id) ID=$2; shift 2 ;;
    --ip) IP=$2; shift 2 ;;
    --gw) GW=$2; shift 2 ;;
    --almacen|--storage) ALMACEN=$2; shift 2 ;;
    --puente|--bridge) PUENTE=$2; shift 2 ;;
    --si|--yes) SI=1; shift ;;
    *) fallo "opción desconocida: $1" ;;
  esac
done

[ "$(id -u)" = 0 ] || fallo "hay que ejecutarlo como root en el Proxmox"
command -v pct >/dev/null || fallo "no es un Proxmox (falta pct)"
[ -f "$DIR/instalar.sh" ] || fallo "falta instalar.sh junto a este script"
[ -n "$ID" ] || ID=$(pvesh get /cluster/nextid)
[[ "$ID" =~ ^[0-9]+$ ]] || fallo "id no válido: $ID"
if pct status "$ID" >/dev/null 2>&1 || qm status "$ID" >/dev/null 2>&1; then fallo "el id $ID ya existe"; fi
if [ "$IP" != dhcp ]; then
  [[ "$IP" =~ ^[0-9.]+/[0-9]+$ ]] || fallo "la IP va con máscara, por ejemplo 192.168.1.5/24"
  [ -n "$GW" ] || fallo "con IP fija hace falta --gw (la IP del router)"
fi
ip link show "$PUENTE" >/dev/null 2>&1 || fallo "no existe el puente $PUENTE"
if [ -z "$ALMACEN" ]; then
  ALMACEN=$(pvesm status --content rootdir 2>/dev/null | awk 'NR>1 && $3=="active" {print $1; exit}')
  [ -n "$ALMACEN" ] || fallo "no hay almacenamiento para contenedores; indícalo con --almacen"
fi

# Plantilla Debian: la que ya haya descargada, o la mas reciente disponible.
PLANTILLA=$(pveam list local 2>/dev/null | awk '{print $1}' | grep -oE 'debian-1[2-9]-standard_[^ ]+' | sort -V | tail -1 || true)
if [ -z "$PLANTILLA" ]; then
  pveam update >/dev/null
  PLANTILLA=$(pveam available --section system | awk '{print $2}' | grep -E '^debian-1[2-9]-standard' | sort -V | tail -1)
  [ -n "$PLANTILLA" ] || fallo "no se encuentra una plantilla de Debian"
  NUEVA=1
fi

RED="name=eth0,bridge=$PUENTE,ip=$IP"
[ "$IP" = dhcp ] || RED="$RED,gw=$GW"
echo "Se va a crear / About to create:"
echo "  contenedor $ID 'panel-red' · Debian ($PLANTILLA) · 512 MB · 1 núcleo · 4 GB en $ALMACEN"
echo "  red: $RED · arranca con el Proxmox"
[ "${NUEVA:-0}" = 1 ] && echo "  (antes se descargará la plantilla, unos 120 MB)"
if [ "$SI" != 1 ]; then
  read -rp "¿Continuar? / Continue? [s/N] " r
  [[ "$r" =~ ^[sSyY]$ ]] || { echo "Cancelado."; exit 0; }
fi

[ "${NUEVA:-0}" = 1 ] && pveam download local "$PLANTILLA"
pct create "$ID" "local:vztmpl/$PLANTILLA" --hostname panel-red --unprivileged 1 --features nesting=1 \
  --cores 1 --memory 512 --swap 256 --rootfs "$ALMACEN:4" --net0 "$RED" --onboot 1 --timezone host \
  --description "HomeLan (panel de red / network panel)" >/dev/null
pct start "$ID"

echo "== Esperando a que el contenedor tenga red"
for _ in $(seq 1 60); do
  pct exec "$ID" -- bash -c 'getent hosts deb.debian.org >/dev/null' 2>/dev/null && break
  sleep 2
done
pct exec "$ID" -- bash -c 'getent hosts deb.debian.org >/dev/null' || fallo "el contenedor $ID no tiene salida a internet; revisa la red y vuelve a probar"

echo "== Copiando el panel e instalando"
TMP=$(mktemp --suffix=.tgz)
tar czf "$TMP" -C "$DIR" instalar.sh panel.py nucleo.py red.py router.py web.py web
pct push "$ID" "$TMP" /root/panel-red.tgz
rm -f "$TMP"
pct exec "$ID" -- bash -c 'rm -rf /root/panel-red && mkdir /root/panel-red && tar xzf /root/panel-red.tgz -C /root/panel-red --no-same-owner && rm /root/panel-red.tgz && bash /root/panel-red/instalar.sh'
echo "Contenedor $ID creado. Para actualizar más adelante: copia la carpeta nueva y ejecuta instalar.sh dentro."
