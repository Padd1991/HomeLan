"""Lo que el panel averigua por si mismo: quien hay en la red (ARP), sus nombres y la bajada (UPnP)."""
import ipaddress
import os
import re
import socket
import struct
import subprocess
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import closing

import nucleo as N

INTERVALO = 60        # segundos entre escaneos
GRACIA = 900          # sin responder este tiempo = desconectado (los moviles dormidos callan un rato)
MUESTREO = 10         # segundos entre lecturas del contador de internet
MAX_HUECO = 30        # con mas tiempo entre lecturas, el contador puede haber dado mas de una vuelta
OLVIDO = 30 * 86400   # sin identificar y sin verse este tiempo: se olvida (moviles con MAC privada que cambia)
MAX_PREFIJO = 22      # redes mas grandes (mas de 1024 direcciones) no se escanean enteras: no daria tiempo
MAX_DIRECCIONES = 2048  # tope para los rangos de PANEL_RED: con 3 reintentos son unos 6000 paquetes, ~20 s
VUELTA = 2 ** 32
MAX_BPS = VUELTA * 8 / MUESTREO * 0.95  # por encima, una vuelta de contador seria indistinguible
log = N.log


# ---------------------------------------------------------------- donde estamos

def ruta_proc(texto):
    """(router, interfaz) leyendo /proc/net/route, por si no esta el comando ip."""
    mejor = None
    for linea in texto.splitlines()[1:]:
        p = linea.split()
        if len(p) >= 7 and p[1] == "00000000" and p[2] != "00000000" and int(p[3], 16) & 2:
            gw = socket.inet_ntoa(struct.pack("<I", int(p[2], 16)))
            if mejor is None or int(p[6]) < mejor[2]:
                mejor = (gw, p[0], int(p[6]))
    return mejor[:2] if mejor else (None, None)


def ruta_defecto():
    """(IP del router, interfaz) de la ruta por defecto. PANEL_IFACE fuerza la interfaz."""
    gw = dev = None
    try:
        sal = subprocess.run(["ip", "-4", "route", "show", "default"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"default via (\S+) dev (\S+)", sal)
        if m:
            gw, dev = m.group(1), m.group(2)
    except (OSError, subprocess.SubprocessError):
        pass
    if not gw:
        try:
            with open("/proc/net/route") as f:
                gw, dev = ruta_proc(f.read())
        except (OSError, ValueError):
            pass
    return gw, os.environ.get("PANEL_IFACE") or dev


def direccion(dev):
    """IP y mascara de esta maquina en esa interfaz (IPv4Interface), o None si no se sabe."""
    try:
        sal = subprocess.run(["ip", "-4", "-o", "addr", "show", dev], capture_output=True, text=True, timeout=5).stdout
        return ipaddress.IPv4Interface(re.search(r"inet ([\d.]+/\d+)", sal).group(1))
    except (OSError, AttributeError, ValueError, subprocess.SubprocessError):
        return None


def propio(dev, gw=None):
    """IP y MAC de esta maquina en esa interfaz (arp-scan no se ve a si mismo)."""
    try:
        with open(f"/sys/class/net/{dev}/address") as f:
            mac = f.read().strip().lower()
    except (OSError, TypeError):
        return None
    d = direccion(dev)
    if d:
        return str(d.ip), mac
    if gw:  # sin el comando ip: la IP de origen con la que se llega al router
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((gw, 9))
                return s.getsockname()[0], mac
        except OSError:
            pass
    return None


# ---------------------------------------------------------------- escaneo y presencia

def leer_arp(salida):
    """Convierte la salida de arp-scan en [(ip, mac, fabricante)], sin repetidos."""
    vistos, res = set(), []
    for linea in salida.splitlines():
        p = linea.split("\t")
        if len(p) < 2:
            continue
        ip, mac = p[0].strip(), p[1].strip().lower()
        try:
            ipaddress.IPv4Address(ip)
        except ValueError:
            continue
        if not N.MAC_RE.match(mac) or (ip, mac) in vistos:
            continue
        vistos.add((ip, mac))
        fab = re.sub(r"\s*\(DUP: \d+\)$", "", p[2].strip() if len(p) > 2 else "")
        res.append((ip, mac, "" if fab.startswith("(Unknown") else fab[:60]))
    return res


def objetivos(iface, gw, pedido=""):
    """Que escanea arp-scan: (argumentos, rango que se muestra en Ajustes, si se ha recortado la red).

    Una red mayor de /22 no da tiempo a escanearla entera en cada pasada (un /16 son 65 000 direcciones):
    se escanea el /22 de este equipo, mas el router si queda fuera. PANEL_RED elige los rangos a mano.
    """
    if pedido:
        try:
            redes = [ipaddress.IPv4Network(x.strip(), strict=False) for x in pedido.split(",") if x.strip()]
        except ValueError as ex:
            raise RuntimeError(f"PANEL_RED no valido: {ex}") from None
        if not redes or sum(r.num_addresses for r in redes) > MAX_DIRECCIONES:
            raise RuntimeError(f"PANEL_RED: entre 1 y {MAX_DIRECCIONES} direcciones en total")
        return [str(r) for r in redes], ", ".join(str(r) for r in redes), False
    if iface is None:
        return ["--localnet"], "", False
    if iface.network.prefixlen >= MAX_PREFIJO:
        return ["--localnet"], str(iface.network), False
    trozo = ipaddress.IPv4Network(f"{iface.ip}/{MAX_PREFIJO}", strict=False)
    extra = [gw] if gw and ipaddress.IPv4Address(gw) not in trozo else []
    return [str(trozo)] + extra, ", ".join([str(trozo)] + extra), True


def escanear(dev, gw=None):
    iface = direccion(dev)
    args, rango, recortado = objetivos(iface, gw, os.environ.get("PANEL_RED", ""))
    if recortado and ESTADO_ESCANER["rango"] != rango:
        log.warning("la red %s es mayor que /%s: solo se escanea %s (se cambia con PANEL_RED)", iface.network, MAX_PREFIJO, rango)
    ESTADO_ESCANER.update(rango=rango, red=str(iface.network) if recortado else "")
    r = subprocess.run(["arp-scan", f"--interface={dev}", "--plain", "--retry=3", *args],
                       capture_output=True, text=True, timeout=50)
    res = leer_arp(r.stdout)
    if r.returncode != 0 or not res:
        raise RuntimeError((r.stderr.strip() or "arp-scan: 0")[:200])
    return res


def aplicar(c, encontrados, ahora, defectos=None):
    """Vuelca un escaneo: altas, cambios, sesiones y bajas.

    defectos: {mac: {campo: valor}} para equipos que se reconocen solos al darse de alta (router, este panel).
    """
    defectos = defectos or {}
    primera = N.meta(c, "ultimo") is None
    por_mac = {}
    for ip, mac, fab in encontrados:
        por_mac.setdefault(mac, []).append((ip, fab))
    ips_antes = {r["ip"]: r["mac"] for r in c.execute("SELECT ip, mac FROM equipos WHERE online=1")}
    for mac, lista in por_mac.items():
        e = c.execute("SELECT * FROM equipos WHERE mac=?", (mac,)).fetchone()
        ips = [x[0] for x in lista]
        ip = e["ip"] if e and e["ip"] in ips else ips[0]
        fab = next((x[1] for x in lista if x[1]), "")
        if e is None:
            d = {"nombre": "", "tipo": "otro", "padre": "", "medio": "", "boca": "", "host": "", **defectos.get(mac, {})}
            c.execute("INSERT INTO equipos(mac,ip,fabricante,host,nombre,tipo,padre,medio,boca,visto_1,visto_n,online,conocido,auto)"
                      " VALUES(?,?,?,?,?,?,?,?,?,?,?,1,0,1)",
                      (mac, ip, d.get("fabricante") or fab, d["host"], d["nombre"], d["tipo"], d["padre"], d["medio"], d["boca"], ahora, ahora))
            c.execute("INSERT INTO sesiones(mac,ip,inicio,fin) VALUES(?,?,?,?)", (mac, ip, ahora, ahora))
            if not primera:  # el primer escaneo es la foto inicial: sin avisos
                N.evento(c, ahora, "nuevo", mac, ip)
        else:
            if e["ip"] != ip:
                N.evento(c, ahora, "ip", mac, ip, {"de": e["ip"], "a": ip})
            hueco = ahora - e["visto_n"]
            ab = c.execute("SELECT * FROM sesiones WHERE mac=? AND abierta=1 ORDER BY id DESC LIMIT 1", (mac,)).fetchone()
            if ab and hueco <= GRACIA and ab["ip"] == ip:
                # MAX: si el reloj va hacia atras (una Raspberry sin pila al ponerse en hora), fin nunca queda antes que inicio
                c.execute("UPDATE sesiones SET fin=MAX(?, inicio) WHERE id=?", (ahora, ab["id"]))
            else:
                # Cambio de IP, vuelta tras ausencia o escaner parado: empieza una sesion nueva.
                c.execute("UPDATE sesiones SET abierta=0 WHERE mac=? AND abierta=1", (mac,))
                c.execute("INSERT INTO sesiones(mac,ip,inicio,fin) VALUES(?,?,?,?)", (mac, ip, ahora, ahora))
                if not e["online"]:
                    N.evento(c, ahora, "conecta", mac, ip)
            c.execute("UPDATE equipos SET ip=?, visto_n=?, online=1, fabricante=CASE WHEN fabricante='' THEN ? ELSE fabricante END"
                      " WHERE mac=?", (ip, ahora, fab, mac))
        otra = ips_antes.get(ip)
        if otra and otra != mac:
            a_la_vez = otra in por_mac and ip in [x[0] for x in por_mac[otra]]
            clase = "conflicto" if a_la_vez else "mac"
            if not c.execute("SELECT 1 FROM eventos WHERE clase=? AND ip=? AND ts>?", (clase, ip, ahora - 3600)).fetchone():
                N.evento(c, ahora, clase, mac, ip, {"ip": ip, "otra": otra})
    for e in c.execute("SELECT * FROM equipos WHERE online=1 AND visto_n<?", (ahora - GRACIA,)).fetchall():
        c.execute("UPDATE equipos SET online=0 WHERE mac=?", (e["mac"],))
        ab = c.execute("SELECT * FROM sesiones WHERE mac=? AND abierta=1 ORDER BY id DESC LIMIT 1", (e["mac"],)).fetchone()
        c.execute("UPDATE sesiones SET abierta=0 WHERE mac=? AND abierta=1", (e["mac"],))
        N.evento(c, e["visto_n"], "desconecta", e["mac"], e["ip"], {"dur": ab["fin"] - ab["inicio"]} if ab else None)
    N.meta(c, "ultimo", ahora)
    N.meta(c, "error", "")


def olvidar(c, ahora):
    """Borra los equipos sin identificar que llevan OLVIDO sin verse, salvo si algo cuelga de ellos.

    Sin esto, cada MAC privada nueva de un movil se queda para siempre en la lista. Sus avisos se conservan.
    """
    usados = {r[0] for r in c.execute("SELECT padre FROM equipos UNION SELECT padre FROM nodos")}
    usados |= set(N.meta_json(c, "bocas", {}).values()) | {N.meta(c, "router_mac"), N.meta(c, "yo")}
    viejos = [r["mac"] for r in c.execute("SELECT mac FROM equipos WHERE conocido=0 AND online=0 AND visto_n<?",
                                          (ahora - OLVIDO,)) if r["mac"] not in usados]
    for mac in viejos:
        for tabla in ("equipos", "sesiones", "puntos"):
            c.execute(f"DELETE FROM {tabla} WHERE mac=?", (mac,))
    return len(viejos)


ESTADO_ESCANER = {"gw": None, "dev": None, "rango": "", "red": ""}  # red: la red entera, si no se escanea toda


def bucle():
    ultima_limpieza = 0
    while True:
        t0 = time.time()
        try:
            gw, dev = ruta_defecto()
            if not dev:
                raise RuntimeError("sin ruta por defecto")
            ESTADO_ESCANER.update(gw=gw, dev=dev)
            res = escanear(dev, gw)
            defectos = {}
            yo = propio(dev, gw)
            if yo:
                res.append((yo[0], yo[1], ""))
                defectos[yo[1]] = {"tipo": "servidor", "host": socket.gethostname()[:60]}
            router = next((mac for ip, mac, _ in res if ip == gw), None)
            if router:
                defectos[router] = {"tipo": "red", "padre": "internet", "medio": "cable"}
            with closing(N.abrir()) as c, c:
                if router and N.meta(c, "router_mac") != router:
                    N.meta(c, "router_mac", router)
                N.meta(c, "router_ip", gw or "")
                if yo:
                    N.meta(c, "yo", yo[1])
                aplicar(c, res, int(time.time()), defectos)
                if t0 - ultima_limpieza > 86400:
                    for tabla, col in (("eventos", "ts"), ("sesiones", "fin"), ("trafico", "minuto"), ("puntos", "fin")):
                        extra = " AND abierta=0" if tabla in ("sesiones", "puntos") else ""
                        c.execute(f"DELETE FROM {tabla} WHERE {col}<?{extra}", (t0 - N.RETENCION,))
                    c.execute("DELETE FROM web_sesiones WHERE caduca<?", (int(t0),))
                    n = olvidar(c, int(t0))
                    if n:
                        log.info("olvidados %s equipos sin identificar que no se ven desde hace %s dias", n, OLVIDO // 86400)
                    ultima_limpieza = t0
        except Exception as ex:
            # Un escaneo fallido no debe dar a nadie por desconectado.
            log.warning("escaneo: %s", ex)
            try:
                with closing(N.abrir()) as c, c:
                    N.meta(c, "error", str(ex)[:200] or type(ex).__name__)
            except Exception:
                log.exception("guardando el error")
        time.sleep(max(5, INTERVALO - (time.time() - t0)))


# ---------------------------------------------------------------- nombres (mDNS y DNS inverso)

def mdns_inverso(ip, espera=1.2):
    """Pregunta por mDNS como se llama una IP. Devuelve '' si nadie contesta."""
    nombre = ".".join(reversed(ip.split("."))) + ".in-addr.arpa"
    q = b"".join(bytes([len(p)]) + p.encode() for p in nombre.split(".")) + b"\0"
    paq = struct.pack(">HHHHHH", 0x5244, 0, 1, 0, 0, 0) + q + struct.pack(">HH", 12, 0x8001)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(espera)
        s.sendto(paq, ("224.0.0.251", 5353))
        fin = time.monotonic() + espera
        while time.monotonic() < fin:
            try:
                d, orig = s.recvfrom(4096)
            except OSError:
                return ""
            if orig[0] == ip:
                try:
                    return ptr_de(d)
                except (IndexError, struct.error, UnicodeError):
                    return ""
    return ""


def _nombre(d, i, saltos=0):
    partes, fin = [], None
    while True:
        n = d[i]
        if n == 0:
            return ".".join(partes), fin if fin is not None else i + 1
        if n & 0xC0 == 0xC0:
            saltos += 1
            if saltos > 20:
                raise IndexError("compresion en bucle")
            if fin is None:
                fin = i + 2
            i = struct.unpack(">H", d[i:i + 2])[0] & 0x3FFF
            continue
        partes.append(d[i + 1:i + 1 + n].decode("utf-8", "replace"))
        i += n + 1


def ptr_de(d):
    _, _, qd, an, _, _ = struct.unpack(">HHHHHH", d[:12])
    i = 12
    for _ in range(qd):
        _, i = _nombre(d, i)
        i += 4
    for _ in range(an):
        _, i = _nombre(d, i)
        t, _, _, n = struct.unpack(">HHIH", d[i:i + 10])
        i += 10
        if t == 12:
            return _nombre(d, i)[0].removesuffix(".local")[:60]
        i += n
    return ""


def dns_inverso_activo():
    """El DNS inverso pregunta al servidor DNS del equipo (casi siempre el router), que puede reenviarlo fuera."""
    return os.environ.get("PANEL_DNS", "1").strip().lower() not in ("0", "no", "off", "false")


def buscar_nombres():
    """Completa el nombre de red de los equipos conectados, cada 6 horas como mucho."""
    dns = dns_inverso_activo()
    if not dns:
        log.info("DNS inverso desactivado (PANEL_DNS=0): los nombres solo se buscan por mDNS, dentro de casa")
    while True:
        try:
            with closing(N.abrir()) as c:
                pend = c.execute("SELECT mac, ip FROM equipos WHERE online=1 AND host_ts<?", (int(time.time()) - 21600,)).fetchall()
            for r in pend:
                host = mdns_inverso(r["ip"])
                if not host and dns:
                    try:
                        host = socket.gethostbyaddr(r["ip"])[0].split(".")[0][:60]
                    except OSError:
                        host = ""
                with closing(N.abrir()) as c, c:
                    c.execute("UPDATE equipos SET host=CASE WHEN ?<>'' THEN ? ELSE host END, host_ts=? WHERE mac=?",
                              (host, host, int(time.time()), r["mac"]))
        except Exception:
            log.exception("buscando nombres")
        time.sleep(60)


# ---------------------------------------------------------------- bajada de internet (UPnP)

SERVICIO_WAN = "urn:schemas-upnp-org:service:WANCommonInterfaceConfig:1"
BUSQUEDAS = (SERVICIO_WAN, "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
             "urn:schemas-upnp-org:device:InternetGatewayDevice:2")
SIN_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def control_wan(xml, base):
    """URL de control del contador de internet en la descripcion UPnP de un router, o None."""
    raiz = ET.fromstring(xml)
    ns = {"u": raiz.tag[1:].split("}")[0]} if raiz.tag.startswith("{") else {}
    q = (lambda t: "u:" + t) if ns else (lambda t: t)
    base = (raiz.findtext(q("URLBase"), namespaces=ns) or "").strip() or base
    for s in raiz.iter(("{%s}service" % ns["u"]) if ns else "service"):
        tipo = (s.findtext(q("serviceType"), namespaces=ns) or "").strip()
        ctl = (s.findtext(q("controlURL"), namespaces=ns) or "").strip()
        if tipo.startswith("urn:schemas-upnp-org:service:WANCommonInterfaceConfig:") and ctl:
            return urllib.parse.urljoin(base, ctl), tipo
    return None


def descubrir_upnp(gw, espera=3):
    """Busca por SSDP el contador de internet del router (solo se acepta si lo anuncia el propio router)."""
    sitios = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        s.settimeout(espera)
        for st in BUSQUEDAS:
            s.sendto((f'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\nMX: 2\r\nST: {st}\r\n\r\n').encode(),
                     ("239.255.255.250", 1900))
        fin = time.monotonic() + espera
        while time.monotonic() < fin:
            try:
                d, orig = s.recvfrom(4096)
            except OSError:
                break
            m = re.search(rb"(?im)^location:\s*(\S+)", d)
            if orig[0] == gw and m:
                url = m.group(1).decode("latin-1")
                if urllib.parse.urlparse(url).hostname == gw and url not in sitios:
                    sitios.append(url)
    for url in sitios:
        try:
            with SIN_PROXY.open(url, timeout=5) as r:
                hallado = control_wan(r.read(300_000), url)
            if hallado and urllib.parse.urlparse(hallado[0]).hostname == gw:
                return hallado
        except (OSError, ET.ParseError, ValueError):
            continue
    return None


class Medidor:
    """Convierte las lecturas del contador de 32 bits del router en bytes fiables."""

    def __init__(self):
        self.prev = None
        self.aviso = ""

    def muestra(self, t, b, p):
        """Devuelve (bytes, segundos) desde la ultima lectura buena, o None si no es fiable."""
        if self.prev is None:
            self.prev = (t, b, p)
            return None
        t0, b0, p0 = self.prev
        dt = t - t0
        tope = b == VUELTA - 1
        if tope and dt <= MAX_HUECO:
            return None  # valor tope: suele durar un instante antes de dar la vuelta
        # Hay routers que dan el contador en 64 bits aunque UPnP lo defina de 32: por encima de 2^32 no es un tope.
        ancho = VUELTA if max(b, b0) < VUELTA else 2 ** 64
        d, dp = (b - b0) % ancho, p - p0
        self.prev = (t, b, p)
        if tope:
            self.aviso = "tope"
        elif d == 0 and dp > 50:
            self.aviso = "parado"
        else:
            self.aviso = ""
        if self.aviso or dt <= 0 or dt > MAX_HUECO or dp < 0 or d * 8 / dt > MAX_BPS:
            return None
        return d, dt


def leer_upnp(url, servicio, accion):
    cuerpo = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
              's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
              f'<u:{accion} xmlns:u="{servicio}"/></s:Body></s:Envelope>').encode()
    pet = urllib.request.Request(url, cuerpo, {"Content-Type": "text/xml", "SOAPAction": f'"{servicio}#{accion}"'})
    with SIN_PROXY.open(pet, timeout=4) as r:
        m = re.search(rb"<New\w+[^>]*>(\d{1,20})</New", r.read(8192))
    if not m:
        raise ValueError("respuesta sin contador")
    return int(m.group(1))


BAJADA = {"bps": None, "ts": 0, "aviso": ""}


def apuntar(c, ts, b, seg, pico=None):
    """Suma lecturas al minuto que les toca."""
    c.execute("INSERT INTO trafico VALUES(?,?,?,?) ON CONFLICT(minuto) DO UPDATE SET bytes=bytes+excluded.bytes,"
              " segundos=segundos+excluded.segundos, pico=max(pico,excluded.pico)",
              (int(ts) // 60 * 60, b, seg, b * 8 / seg if pico is None else pico))


class Acumulador:
    """Junta en memoria las lecturas de un minuto y lo escribe de una vez: menos desgaste en tarjetas SD."""

    def __init__(self):
        self._vacio()

    def _vacio(self):
        self.minuto, self.bytes, self.segundos, self.pico = None, 0, 0.0, 0.0

    def anadir(self, ts, b, seg):
        """Suma una lectura. Devuelve el minuto anterior ya cerrado, si lo hay, para escribirlo."""
        cerrado = self.cerrar_si_paso(ts)
        if self.minuto is None:
            self.minuto = int(ts) // 60 * 60
        self.bytes += b
        self.segundos += seg
        self.pico = max(self.pico, b * 8 / seg)
        return cerrado

    def cerrar_si_paso(self, ts):
        if self.minuto is None or int(ts) // 60 * 60 == self.minuto:
            return None
        r = (self.minuto, self.bytes, self.segundos, self.pico) if self.segundos else None
        self._vacio()
        return r

    def pendiente(self):
        return (self.minuto, self.bytes, self.segundos, self.pico) if self.minuto is not None and self.segundos else None


ACUMULADO = Acumulador()


def medir():
    m, upnp, fallos, buscado = Medidor(), None, 0, 0
    while True:
        t0 = time.monotonic()
        try:
            if upnp is None:
                if time.monotonic() - buscado < 600 and buscado:
                    raise LookupError
                buscado = time.monotonic()
                gw = ESTADO_ESCANER["gw"] or ruta_defecto()[0]
                upnp = descubrir_upnp(gw) if gw else None
                if upnp is None:
                    raise LookupError
                log.info("contador de internet: %s", upnp[0])
                m = Medidor()
            b, p = leer_upnp(*upnp, "GetTotalBytesReceived"), leer_upnp(*upnp, "GetTotalPacketsReceived")
            fallos = 0
            r = m.muestra(t0, b, p)
            if r:
                guardar_minuto(ACUMULADO.anadir(time.time(), *r))
                BAJADA.update(bps=r[0] * 8 / r[1], ts=time.time())
            BAJADA["aviso"] = m.aviso
        except LookupError:
            BAJADA["aviso"] = "sin_upnp"
        except Exception as ex:
            fallos += 1
            BAJADA["aviso"] = "sin_respuesta"
            log.warning("bajada: %s", str(ex)[:150])
            if fallos >= 6:  # el router puede haber cambiado: se vuelve a buscar
                upnp, fallos = None, 0
        try:
            guardar_minuto(ACUMULADO.cerrar_si_paso(time.time()))  # sin lecturas nuevas, el minuto se cierra igual
        except Exception:
            log.exception("guardando el trafico")
        time.sleep(max(2, MUESTREO - (time.monotonic() - t0)))


def guardar_minuto(fila):
    if fila:
        with closing(N.abrir()) as c, c:
            apuntar(c, fila[0], fila[1], fila[2], fila[3])


def trafico(desde, hasta):
    """Media y pico por tramo. Los tramos sin lecturas no aparecen: no son un cero."""
    paso = max(60, -(-(hasta - desde) // 288 // 60) * 60)
    with closing(N.abrir()) as c:
        f = [dict(r) for r in c.execute("SELECT minuto/?*? t, SUM(bytes) b, SUM(segundos) s, MAX(pico) p FROM trafico"
                                        " WHERE minuto>=? AND minuto<? GROUP BY 1 ORDER BY 1", (paso, paso, desde, hasta))]
    pend = ACUMULADO.pendiente()  # el minuto en curso aun no esta escrito: se suma desde memoria
    if pend and desde <= pend[0] < hasta:
        t = pend[0] // paso * paso
        if f and f[-1]["t"] == t:
            f[-1].update(b=f[-1]["b"] + pend[1], s=f[-1]["s"] + pend[2], p=max(f[-1]["p"], pend[3]))
        else:
            f.append({"t": t, "b": pend[1], "s": pend[2], "p": pend[3]})
    return {"paso": paso, "puntos": [[r["t"], r["b"] * 8 / r["s"], r["p"]] for r in f if r["s"]],
            "bytes": sum(r["b"] for r in f), "segundos": sum(r["s"] for r in f), "desde": desde, "hasta": hasta}
