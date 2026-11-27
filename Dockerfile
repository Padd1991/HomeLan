# HomeLan: panel de red / network panel
# Necesita la red del equipo (network_mode: host) para ver los dispositivos por ARP y el router por UPnP.
FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends arp-scan iproute2 tzdata libcap2-bin \
 && setcap cap_net_raw+ep "$(command -v arp-scan)" \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --home-dir /datos --shell /usr/sbin/nologin panel \
 && install -d -o panel -g panel -m 700 /datos

COPY panel.py nucleo.py red.py router.py web.py /app/
COPY web /app/web

ENV PANEL_DATOS=/datos \
    PANEL_PUERTO=8080 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER panel
VOLUME /datos
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD ["python3", "-c", "import os,urllib.request as u;u.build_opener(u.ProxyHandler({})).open('http://127.0.0.1:%s/api/sesion'%os.environ['PANEL_PUERTO'],timeout=4)"]
CMD ["python3", "-I", "/app/panel.py"]
