#!/usr/bin/env python3
"""Panel de red: quien hay en tu red, desde cuando, por donde se conecta y cuanto baja internet.

Solo usa la biblioteca estandar de Python y el programa arp-scan. Lee la red: no configura nada.
Variables de entorno: PANEL_PUERTO (80), PANEL_DATOS (carpeta de datos), PANEL_IFACE (interfaz),
PANEL_HOSTS (nombres extra con los que se abre la web, separados por comas), PANEL_RED (rangos a
escanear, separados por comas) y PANEL_DNS=0 (sin DNS inverso).
"""
import logging
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nucleo as N  # noqa: E402
import red  # noqa: E402
import router as R  # noqa: E402
import web  # noqa: E402

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    os.umask(0o077)  # la base de datos guarda el resumen de la contraseña: solo para el panel
    N.preparar()

    def arrancar():
        N.esperar_hora()  # la web ya responde; lo que guarda horas espera a que el reloj sea fiable
        for hilo in (red.bucle, red.buscar_nombres, red.medir, R.vigilar):
            threading.Thread(target=hilo, daemon=True).start()

    threading.Thread(target=arrancar, daemon=True).start()
    puerto = int(os.environ.get("PANEL_PUERTO", "80"))
    N.log.info("panel en el puerto %s, datos en %s", puerto, N.DATOS)
    web.servir(puerto)
