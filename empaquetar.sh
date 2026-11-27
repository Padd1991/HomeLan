#!/bin/bash
# Genera el paquete para compartir (dist/panel-red-FECHA-COMMIT.tar.gz) con lo ultimo que esta en git.
# Solo entra lo necesario: lo marcado con export-ignore en .gitattributes se queda fuera.
set -euo pipefail
cd "$(dirname "$0")"
if [ -n "$(git status --porcelain)" ]; then
  echo "Hay cambios sin guardar en git: el paquete saldría sin ellos. Haz commit antes." >&2
  exit 1
fi
mkdir -p dist
SALIDA="dist/panel-red-$(date +%Y%m%d)-$(git rev-parse --short HEAD).tar.gz"
git archive --format=tar.gz --prefix=panel-red/ -o "$SALIDA" HEAD
echo "$SALIDA"
tar tzvf "$SALIDA" | awk '{print "  " $1 "  " $6}'
