#!/bin/bash
# Instala, actualiza o desinstala el panel de red en Debian o Ubuntu (tambien dentro de un contenedor LXC).
#
#   sudo ./instalar.sh                    instala o actualiza (conserva los datos)
#   sudo PANEL_PUERTO=8080 ./instalar.sh  en otro puerto (por defecto 80, o el que ya tuviera)
#   sudo ./instalar.sh --desinstalar      quita el programa y deja los datos
#   sudo ./instalar.sh --desinstalar --borrar-datos
#
# Installs, updates or removes the network panel on Debian/Ubuntu. Same options as above.
set -euo pipefail
export LC_ALL=C.UTF-8  # evita avisos de idioma al llegar por SSH desde otro sistema

DIR=$(cd "$(dirname "$0")" && pwd)
DESTINO=/opt/panel-red
DATOS=/var/lib/panel-red
UNIDAD=/etc/systemd/system/panel-red.service
ARCHIVOS="panel.py nucleo.py red.py router.py web.py web/index.html web/app.js web/app.css web/i18n/es.json web/i18n/en.json"

fallo() { echo "ERROR: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fallo "hay que ejecutarlo como root (sudo) / run it as root"

if [ "${1:-}" = "--desinstalar" ]; then
  systemctl disable --now panel-red 2>/dev/null || true
  rm -f "$UNIDAD"
  systemctl daemon-reload
  rm -rf "$DESTINO"
  if [ "${2:-}" = "--borrar-datos" ]; then
    rm -rf "$DATOS"
    echo "Panel desinstalado y datos borrados."
  else
    echo "Panel desinstalado. Los datos siguen en $DATOS (bórralos con --desinstalar --borrar-datos)."
  fi
  exit 0
fi

for f in $ARCHIVOS; do [ -f "$DIR/$f" ] || fallo "falta $f junto a este script"; done
command -v apt-get >/dev/null || fallo "este instalador es para Debian o Ubuntu; en otros sistemas usa Docker"
command -v systemctl >/dev/null || fallo "hace falta systemd"

# Puerto: el indicado, o el de una instalacion anterior, o el 80.
ANTERIOR=$(systemctl show -p Environment panel-red 2>/dev/null | grep -o 'PANEL_PUERTO=[0-9]*' | cut -d= -f2 || true)
# Una instalacion anterior sin puerto escrito usa el 80 por defecto: ese puerto es suyo, no de otro programa.
[ -z "$ANTERIOR" ] && [ -f "$UNIDAD" ] && ANTERIOR=80
PUERTO=${PANEL_PUERTO:-${ANTERIOR:-80}}
[[ "$PUERTO" =~ ^[0-9]+$ ]] && [ "$PUERTO" -ge 1 ] && [ "$PUERTO" -le 65535 ] || fallo "puerto no válido: $PUERTO"
if [ "$PUERTO" != "${ANTERIOR:-}" ] && command -v ss >/dev/null && ss -ltnH "sport = :$PUERTO" | grep -q .; then
  fallo "el puerto $PUERTO ya lo usa otro programa; elige otro con PANEL_PUERTO=8080 ./instalar.sh"
fi

echo "== Instalando dependencias (python3, arp-scan, iproute2)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends python3 arp-scan iproute2 >/dev/null
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || fallo "hace falta Python 3.9 o superior"

echo "== Copiando el programa a $DESTINO"
id panelred >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin panelred
install -d -m 755 "$DESTINO" "$DESTINO/web" "$DESTINO/web/i18n"
for f in $ARCHIVOS; do install -m 644 "$DIR/$f" "$DESTINO/$f"; done

cat > "$UNIDAD" <<EOF
[Unit]
Description=HomeLan (panel de red / network panel)
After=network-online.target
Wants=network-online.target

[Service]
User=panelred
Group=panelred
Environment=PANEL_PUERTO=$PUERTO
ExecStart=/usr/bin/python3 -I $DESTINO/panel.py
StateDirectory=panel-red
StateDirectoryMode=0750
Restart=always
RestartSec=5
AmbientCapabilities=CAP_NET_RAW CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_RAW CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
MemoryMax=200M

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable panel-red >/dev/null 2>&1
systemctl restart panel-red

echo "== Comprobando que responde"
for _ in $(seq 1 30); do
  if python3 -c "import urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:$PUERTO/api/sesion', timeout=2)" 2>/dev/null; then
    IP=$(ip -4 route get 1.1.1.1 2>/dev/null | grep -o 'src [0-9.]*' | cut -d' ' -f2 || true)
    URL="http://${IP:-$(hostname)}"
    [ "$PUERTO" = 80 ] || URL="$URL:$PUERTO"
    echo
    echo "Listo / Done: $URL"
    exit 0
  fi
  sleep 1
done
systemctl --no-pager status panel-red | tail -15
fallo "el panel no responde; mira el registro con: journalctl -u panel-red"
