"""Lectura opcional de la web del router, para saber por que boca o banda WiFi entra cada equipo.

Modelos admitidos: ZTE F6600P (Vodafone). Solo lee: entra, pide tablas y cierra la sesion.
La contraseña no viaja en claro: el router da un numero de un solo uso y espera sha256(clave + numero).
"""
import hashlib
import http.cookiejar
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import closing

import nucleo as N

MODELOS = {"zte_f6600p": "ZTE F6600P"}
PERIODO = 300          # segundos entre lecturas
MAX_RESPUESTA = 2_000_000
CONF_ANTIGUA = "/etc/panel-red/router.conf"   # primera version: se importa una vez


class ErrorClave(Exception):
    """Usuario o contraseña rechazados: no se reintenta solo, o el router bloquearia el acceso."""


class Ocupado(Exception):
    """El router no deja entrar ahora (bloqueo temporal...): se reintenta mas tarde."""


class SesionCaducada(Exception):
    pass


# ---------------------------------------------------------------- credenciales

def ruta_credenciales():
    return os.path.join(N.DATOS, "router.json")


def leer_credenciales():
    """{tipo, usuario, clave} o None. Importa la de la primera version si no hay otra."""
    try:
        with open(ruta_credenciales(), encoding="utf-8") as f:
            d = json.load(f)
        if d.get("tipo") in MODELOS and d.get("usuario") and d.get("clave"):
            return d
        return None
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        return None
    try:
        v = {}
        with open(CONF_ANTIGUA, encoding="utf-8") as f:
            for linea in f:
                if "=" in linea and not linea.lstrip().startswith("#"):
                    k, x = linea.rstrip("\r\n").split("=", 1)
                    v[k.strip()] = x
        if v.get("usuario") and v.get("clave"):
            d = {"tipo": "zte_f6600p", "usuario": v["usuario"], "clave": v["clave"]}
            guardar_credenciales(d)
            return d
    except OSError:
        pass
    return None


def guardar_credenciales(d):
    """Escribe el archivo solo legible por el panel; None lo borra."""
    ruta = ruta_credenciales()
    if d is None:
        try:
            os.unlink(ruta)
        except FileNotFoundError:
            pass
        return
    tmp = ruta + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"tipo": d["tipo"], "usuario": d["usuario"], "clave": d["clave"]}, f)
    os.replace(tmp, ruta)


def huella(d):
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest() if d else None


# ---------------------------------------------------------------- ZTE F6600P

def tabla(xml, objeto):
    """Lista de diccionarios de un objeto OBJ_* de la respuesta, que va en pares ParaName/ParaValue."""
    raiz = ET.fromstring(xml)
    err = (raiz.findtext("IF_ERRORSTR") or "").strip()
    if err == "SessionTimeout":
        raise SesionCaducada(err)
    if err not in ("", "SUCC"):
        raise RuntimeError("el router respondió: " + err[:80])
    res = []
    for inst in raiz.findall(f"{objeto}/Instance"):
        d, nombre = {}, None
        for hijo in inst:
            if hijo.tag == "ParaName":
                nombre = (hijo.text or "").strip()
            elif hijo.tag == "ParaValue" and nombre:
                d[nombre] = (hijo.text or "").strip()
                nombre = None
        res.append(d)
    return res


def entero(v, defecto=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return defecto


def desescapar(m):
    """Texto de un literal JS escrito con escapes hexadecimales (asi esconde el router sus variables)."""
    if not m:
        return None
    try:
        return re.sub(r"\\x([0-9a-fA-F]{2})", lambda x: chr(int(x.group(1), 16)), m.group(1))
    except (ValueError, IndexError):
        return None


def interpretar(cable, wifi):
    """Une las dos tablas en {mac: datos}. 'punto' es un codigo: LAN1..LAN4, wifi24, wifi5, wifi5-2..."""
    res = {}
    for d in cable:
        mac = d.get("MACAddress", "").lower()
        boca = d.get("AliasName", "")
        if N.MAC_RE.match(mac) and re.fullmatch(r"LAN\d", boca):
            res[mac] = {"punto": boca, "ip": d.get("IPAddress", ""), "host": d.get("HostName", "")[:60]}
    for d in wifi:
        mac = d.get("MACAddress", "").lower()
        m = re.search(r"AP(\d+)$", d.get("AliasName", ""))
        if not (N.MAC_RE.match(mac) and m):
            continue
        n = int(m.group(1))
        red = (n - 1) % 4 + 1
        punto = ("wifi24" if n <= 4 else "wifi5") + (f"-{red}" if red > 1 else "")
        res[mac] = {"punto": punto, "ip": d.get("IPAddress", ""), "host": d.get("HostName", "")[:60],
                    "rssi": entero(d.get("RSSI")), "tx": entero(d.get("TxRate")), "rx": entero(d.get("RxRate")),
                    "modo": d.get("CurrentMode", "")[:10], "enlace": entero(d.get("LinkTime"))}
    return res


class ZTE:
    def __init__(self, usuario, clave, base):
        self.usuario, self._clave, self.base = usuario, clave, base.rstrip("/")
        self.op = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                              urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.token = None

    def _pedir(self, ruta, datos=None):
        cuerpo = urllib.parse.urlencode(datos).encode() if datos is not None else None
        pet = urllib.request.Request(self.base + ruta, cuerpo, {"X-Requested-With": "XMLHttpRequest", "Referer": self.base + "/"})
        if cuerpo is not None:
            pet.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
        with self.op.open(pet, timeout=15) as r:
            return r.read(MAX_RESPUESTA)

    def entrar(self):
        # Como el navegador: la pagina principal da las cookies sin las que el router no mantiene la sesion.
        self._pedir("/")
        j = json.loads(self._pedir("/?_type=loginData&_tag=login_entry"))
        if entero(j.get("lockingTime"), 0) > 0:
            raise Ocupado(f"bloqueo {j['lockingTime']} s")
        numero = ET.fromstring(self._pedir("/?_type=loginData&_tag=login_token")).text or ""
        clave = hashlib.sha256((self._clave + numero).encode()).hexdigest()
        self._pedir("/?_type=loginData&_tag=login_entry", {
            "action": "login", "Password": clave, "Username": self.usuario, "_sessionTOKEN": j.get("sess_token", "")})
        pag = self._pedir("/").decode("utf-8", "replace")
        self.token = desescapar(re.search(r'_sessionTmpToken\s*=\s*"((?:\\x[0-9a-fA-F]{2})+)"', pag))
        # El router responde igual a una clave buena que a una mala: solo se nota en que sigue en el login.
        if desescapar(re.search(r'var NowStatus\s*=\s*"([^"]*)"', pag)) == "showloginPage":
            self.token = None
            j = json.loads(self._pedir("/?_type=loginData&_tag=login_entry"))
            raise ErrorClave((j.get("loginErrMsg") or "").strip()[:120])
        self._pedir("/?_type=menuView&_tag=localNetStatus&Menu3Location=0")

    def _tabla(self, ruta, objeto):
        try:
            return tabla(self._pedir(ruta), objeto)
        except SesionCaducada:
            raise SesionCaducada("sesión cerrada por el router") from None

    def leer(self):
        cable = self._tabla("/?_type=menuData&_tag=accessdev_landevs_lua.lua", "OBJ_ACCESSDEV_ID")
        wifi = self._tabla("/?_type=menuData&_tag=wlan_client_stat_lua.lua", "OBJ_WLAN_AD_ID")
        return interpretar(cable, wifi)

    def salir(self):
        if self.token:
            try:
                self._pedir("/?_type=loginData&_tag=logout_entry", {"IF_LogOff": "1", "_sessionTOKEN": self.token})
            except OSError:
                pass
            self.token = None

    def __enter__(self):
        try:
            self.entrar()
        except BaseException:
            self.salir()
            raise
        return self

    def __exit__(self, *a):
        self.salir()
        self._clave = None


CLASES = {"zte_f6600p": ZTE}


_en_uso = threading.Lock()  # una sola sesion a la vez: la web y la vigilancia no deben pisarse en el router


def leer_router(cred, ip):
    with _en_uso, CLASES[cred["tipo"]](cred["usuario"], cred["clave"], f"http://{ip}") as r:
        return r.leer()


# ---------------------------------------------------------------- volcado en el panel

def colocar(mac, punto, bocas, router_mac):
    """Donde va en el mapa un equipo segun por donde entra al router: (cuelga de, medio, boca).

    bocas: {'LAN1': mac o nodo} dice que hay enchufado en cada boca (se ajusta en la web).
    """
    if punto.startswith("wifi"):
        return bocas.get("wifi") or router_mac, "wifi", punto
    destino = bocas.get(punto)
    if not destino or destino == mac:
        return router_mac, "cable", punto
    # Detras de otro equipo (switch, repetidor...): el medio final no se sabe si ese equipo da WiFi.
    return destino, "cable" if not N.MAC_RE.match(destino) else "", punto


def aplicar_router(c, datos, ahora):
    """Guarda lo que dice el router de cada equipo, su historial de puntos y los cambios de punto."""
    margen = 3 * PERIODO
    bocas = N.meta_json(c, "bocas", {})
    router_mac = N.meta(c, "router_mac") or ""
    for mac, d in datos.items():
        e = c.execute("SELECT * FROM equipos WHERE mac=?", (mac,)).fetchone()
        if e is None:
            continue  # lo dara de alta el escaner ARP cuando responda
        punto = d["punto"]
        if e["r_punto"] and e["r_punto"] != punto and ahora - (e["r_ts"] or 0) <= margen:
            N.evento(c, ahora, "punto", mac, e["ip"], {"de": e["r_punto"], "a": punto})
        c.execute("UPDATE equipos SET r_punto=?, r_rssi=?, r_tx=?, r_rx=?, r_modo=?, r_ts=?,"
                  " host=CASE WHEN host='' THEN ? ELSE host END WHERE mac=?",
                  (punto, d.get("rssi"), d.get("tx"), d.get("rx"), d.get("modo", ""), ahora, d.get("host", ""), mac))
        ab = c.execute("SELECT * FROM puntos WHERE mac=? AND abierta=1 ORDER BY id DESC LIMIT 1", (mac,)).fetchone()
        if ab and ab["punto"] == punto and ahora - ab["fin"] <= margen:
            c.execute("UPDATE puntos SET fin=MAX(?, inicio) WHERE id=?", (ahora, ab["id"]))  # reloj hacia atras
        else:
            c.execute("UPDATE puntos SET abierta=0 WHERE mac=? AND abierta=1", (mac,))
            c.execute("INSERT INTO puntos(mac,punto,inicio,fin) VALUES(?,?,?,?)", (mac, punto, ahora, ahora))
        if e["auto"] and router_mac and mac != router_mac:
            c.execute("UPDATE equipos SET padre=?, medio=?, boca=? WHERE mac=?", (*colocar(mac, punto, bocas, router_mac), mac))
    c.execute("UPDATE puntos SET abierta=0 WHERE abierta=1 AND fin<?", (ahora - margen,))


def recolocar(c):
    """Vuelve a colocar los equipos automaticos tras cambiar lo que hay en cada boca."""
    bocas, router_mac = N.meta_json(c, "bocas", {}), N.meta(c, "router_mac") or ""
    if not router_mac:
        return
    for e in c.execute("SELECT mac, r_punto FROM equipos WHERE auto=1 AND r_punto<>'' AND mac<>?", (router_mac,)).fetchall():
        c.execute("UPDATE equipos SET padre=?, medio=?, boca=? WHERE mac=?", (*colocar(e["mac"], e["r_punto"], bocas, router_mac), e["mac"]))


ESTADO = {"estado": "sin_configurar", "ts": 0, "msg": ""}
_rechazada = {"huella": None}
_despertar = {"ya": False, "ok": False}  # ok: la web acaba de leer el router con datos nuevos


def probar_y_guardar(cred):
    """Para la web: prueba antes de guardar, asi una clave mala nunca se queda guardada ni se reintenta."""
    with closing(N.abrir()) as c:
        ip = N.meta(c, "router_ip")
    if not ip:
        return "sin_router"
    try:
        datos = leer_router(cred, ip)
    except ErrorClave:
        return "clave"
    except Exception as ex:
        guardar_credenciales(cred)
        ESTADO.update(estado="error", msg=str(ex)[:150])
        _despertar["ya"] = True
        return "no_responde"
    guardar_credenciales(cred)
    with closing(N.abrir()) as c, c:
        aplicar_router(c, datos, int(time.time()))
    ESTADO.update(estado="ok", ts=time.time(), msg=str(len(datos)))
    _rechazada["huella"] = None
    _despertar.update(ok=True, ya=True)  # la vigilancia deja su espera larga y sigue a ritmo normal desde aqui
    return ""


def vigilar():
    espera = PERIODO
    while True:
        if _despertar["ok"]:
            _despertar["ok"] = False
            espera = PERIODO
            _dormir(PERIODO)
            continue
        cred = leer_credenciales()
        if not cred:
            ESTADO.update(estado="sin_configurar", msg="")
            _dormir(60)
            continue
        if _rechazada["huella"] == huella(cred):
            _dormir(60)  # no se reintenta hasta que cambien los datos: el router bloquearia el acceso
            continue
        try:
            with closing(N.abrir()) as c:
                ip = N.meta(c, "router_ip")
            if not ip:  # el escaner aun no ha encontrado el router: se espera sin alargar los reintentos
                _dormir(20)
                continue
            datos = leer_router(cred, ip)
            with closing(N.abrir()) as c, c:
                aplicar_router(c, datos, int(time.time()))
            ESTADO.update(estado="ok", ts=time.time(), msg=str(len(datos)))
            espera = PERIODO
        except ErrorClave as ex:
            _rechazada["huella"] = huella(cred)
            ESTADO.update(estado="clave", msg=str(ex)[:120])
            N.log.warning("router: clave rechazada; no se reintenta hasta cambiarla")
            continue
        except Exception as ex:
            ESTADO.update(estado="error", msg=str(ex)[:150])
            N.log.warning("router: %s", str(ex)[:150])
            espera = min(espera * 2, 3600)
        _dormir(espera)


def _dormir(s):
    fin = time.monotonic() + s
    while time.monotonic() < fin and not _despertar["ya"]:
        time.sleep(1)
    _despertar["ya"] = False
