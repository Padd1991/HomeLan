"""Servidor web del panel: pagina, API, contraseña de acceso y ajustes."""
import hashlib
import hmac
import html
import ipaddress
import json
import os
import re
import secrets
import socket
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import nucleo as N
import red
import router as R

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
ESTATICOS = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
             "/app.css": ("app.css", "text/css"), "/i18n/es.json": ("i18n/es.json", "application/json"),
             "/i18n/en.json": ("i18n/en.json", "application/json")}
IDIOMAS = ("es", "en")
SESION_S = 30 * 86400
COOKIE = "panel_sesion"
MIN_CLAVE = 8
TAILSCALE = ipaddress.ip_network("100.64.0.0/10")
_fallos, _fallos_lock = {}, threading.Lock()
# Cada scrypt usa 16 MB: sin limite, unas pocas peticiones a la vez agotarian la memoria del servicio (200 MB).
_scrypt = threading.BoundedSemaphore(2)


# ---------------------------------------------------------------- contraseña y sesiones

def hash_clave(clave, sal=None):
    sal = sal or secrets.token_bytes(16)
    with _scrypt:
        h = hashlib.scrypt(clave.encode(), salt=sal, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${sal.hex()}${h.hex()}"


def clave_correcta(clave, guardado):
    try:
        _, sal, h = guardado.split("$")
        return hmac.compare_digest(hash_clave(clave, bytes.fromhex(sal)).split("$")[2], h)
    except (ValueError, AttributeError):
        return False


def bloqueado(ip):
    with _fallos_lock:
        n, hasta = _fallos.get(ip, (0, 0))
        return hasta > time.time()


def anotar_fallo(ip):
    """Tras 5 fallos seguidos se espera 1 minuto, y el doble con cada fallo mas (hasta 15 min)."""
    with _fallos_lock:
        if len(_fallos) > 1000:  # que no crezca sin fin: se olvidan las IP ya sin bloqueo
            ahora = time.time()
            for k in [k for k, (_, hasta) in _fallos.items() if hasta <= ahora]:
                del _fallos[k]
        n, _ = _fallos.get(ip, (0, 0))
        n += 1
        _fallos[ip] = (n, time.time() + min(900, 60 * 2 ** (n - 5)) if n >= 5 else 0)


ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin 0/O ni 1/I: se copia a mano desde la consola
_codigo = {"v": ""}


def preparar_codigo():
    """Mientras no haya contraseña, ponerla pide un codigo que solo sale en el registro del servicio.

    Asi nadie mas de la red puede adelantarse y quedarse con el panel: hace falta acceso al equipo.
    """
    with closing(N.abrir()) as c:
        if N.meta(c, "clave"):
            return ""
    if not _codigo["v"]:
        _codigo["v"] = "".join(secrets.choice(ALFABETO) for _ in range(8))
    v = _codigo["v"]
    N.log.info("codigo para la primera vez / setup code: %s-%s", v[:4], v[4:])
    return v


def codigo_correcto(dado):
    v = _codigo["v"]
    dado = re.sub(r"[\s-]", "", str(dado or "")).upper()
    return bool(v) and hmac.compare_digest(dado.encode(), v.encode())


def crear_sesion(c):
    token = secrets.token_urlsafe(32)
    c.execute("INSERT INTO web_sesiones VALUES(?,?)", (hashlib.sha256(token.encode()).hexdigest(), int(time.time()) + SESION_S))
    return token


def nombres_propios():
    """Nombres con los que se puede llegar al panel (para la cabecera Host)."""
    h = {"localhost"}
    for n in (socket.gethostname(), socket.getfqdn()):
        if n:
            h.update({n.lower(), n.lower().split(".")[0], n.lower().split(".")[0] + ".local"})
    h.update(x.strip().lower() for x in os.environ.get("PANEL_HOSTS", "").split(",") if x.strip())
    return h


HOSTS = nombres_propios()


def pagina_prohibida(host, red_ok):
    """Pagina 403 en los dos idiomas: por que no se abre y como arreglarlo."""
    if red_ok:  # nombre no reconocido: lo normal es que el router le de un nombre propio (panel-red.lan, .home...)
        h = html.escape(host[:100] or "?")
        es = (f"Este panel no se abre con el nombre <b>{h}</b>. Ábrelo por su IP, o añade ese nombre a la variable "
              f"<code>PANEL_HOSTS</code> (por ejemplo <code>PANEL_HOSTS={h}</code>) y reinicia el panel. Está en el README.")
        en = (f"This panel does not open as <b>{h}</b>. Open it by its IP address, or add that name to the "
              f"<code>PANEL_HOSTS</code> variable (for example <code>PANEL_HOSTS={h}</code>) and restart the panel. See the README.")
    else:
        es = "Este panel solo se abre desde la red de casa (o Tailscale)."
        en = "This panel only opens from the home network (or Tailscale)."
    return ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>HomeLan</title><body style="font:16px/1.5 system-ui,sans-serif;max-width:40em;margin:3em auto;padding:0 1em">'
            f'<h1 style="font-size:1.4em">HomeLan</h1><p lang="es">{es}</p><p lang="en">{en}</p></body>').encode()


def texto(d, k, maximo=60):
    return re.sub(r"[\x00-\x1f\x7f]", "", str(d.get(k, "") or "")).strip()[:maximo]


# ---------------------------------------------------------------- datos para la web

def estado():
    with closing(N.abrir()) as c:
        eq = [dict(r) for r in c.execute("SELECT * FROM equipos ORDER BY online DESC")]
        for e in eq:
            e["privada"] = N.mac_privada(e["mac"])
            e.pop("host_ts", None)
        ev = [dict(r) for r in c.execute("SELECT * FROM eventos ORDER BY ts DESC, id DESC LIMIT 12")]
        hoy = time.localtime()
        cero = int(time.mktime((hoy.tm_year, hoy.tm_mon, hoy.tm_mday, 0, 0, 0, 0, 0, -1)))
        return {"ahora": int(time.time()), "ultimo": int(N.meta(c, "ultimo") or 0), "error": N.meta(c, "error") or "",
                "equipos": eq, "eventos": ev, "tipos": N.TIPOS,
                "cambios_hoy": c.execute("SELECT COUNT(*) n FROM eventos WHERE ts>=?", (cero,)).fetchone()["n"],
                "router": {"estado": R.ESTADO["estado"], "msg": R.ESTADO["msg"], "ts": int(R.ESTADO["ts"])},
                "bajada": {"bps": red.BAJADA["bps"] if time.time() - red.BAJADA["ts"] < 45 else None, "aviso": red.BAJADA["aviso"]},
                "nodos": [dict(r) for r in c.execute("SELECT * FROM nodos ORDER BY nombre")],
                "bocas": N.meta_json(c, "bocas", {}), "router_mac": N.meta(c, "router_mac") or "",
                "yo": N.meta(c, "yo") or "", "nombre": N.meta(c, "nombre") or ""}


def historial(desde, hasta):
    with closing(N.abrir()) as c:
        s = [dict(r) for r in c.execute("SELECT mac, ip, inicio, fin, abierta FROM sesiones WHERE fin>=? AND inicio<? ORDER BY inicio", (desde, hasta))]
        e = [dict(r) for r in c.execute("SELECT * FROM eventos WHERE ts>=? AND ts<? ORDER BY ts DESC, id DESC LIMIT 1000", (desde, hasta))]
        pu = [dict(r) for r in c.execute("SELECT mac, punto, inicio, fin, abierta FROM puntos WHERE fin>=? AND inicio<? ORDER BY inicio", (desde, hasta))]
        p = c.execute("SELECT MIN(inicio) m FROM sesiones").fetchone()["m"]
        return {"sesiones": s, "eventos": e, "primero": p, "puntos": pu}


def ajustes():
    cred = R.leer_credenciales()
    with closing(N.abrir()) as c:
        vistas = sorted({r["r_punto"] for r in c.execute("SELECT DISTINCT r_punto FROM equipos WHERE r_punto LIKE 'LAN%'")})
        return {"idioma": N.meta(c, "idioma") or "", "nombre": N.meta(c, "nombre") or "",
                "modelos": R.MODELOS, "router_ip": N.meta(c, "router_ip") or "",
                "router": {"tipo": cred["tipo"] if cred else "", "usuario": cred["usuario"] if cred else ""},
                "bocas": N.meta_json(c, "bocas", {}), "bocas_vistas": vistas,
                "escaneo": {"rango": red.ESTADO_ESCANER["rango"], "red": red.ESTADO_ESCANER["red"]}}


# ---------------------------------------------------------------- cambios desde la web

def guardar_equipo(d):
    mac = str(d.get("mac", "")).lower()
    with closing(N.abrir()) as c, c:
        e = c.execute("SELECT padre, medio, boca, auto FROM equipos WHERE mac=?", (mac,)).fetchone() if N.MAC_RE.match(mac) else None
        if e is None:
            return "no_existe"
        tipo, padre, medio = (str(d.get(k, "")) for k in ("tipo", "padre", "medio"))
        if tipo not in N.TIPOS or medio not in N.MEDIOS:
            return "no_valido"
        padres = N.padres_validos(c)
        if padre and padre not in padres:
            return "padre_no_existe"
        if N.crea_bucle(padres, mac, padre):
            return "bucle"
        boca = texto(d, "boca", 40)
        # Solo deja de colocarse solo si se cambia a mano donde esta; ponerle nombre o tipo no cuenta.
        auto = e["auto"] if (padre, medio, boca) == tuple(e[k] or "" for k in ("padre", "medio", "boca")) else 0
        c.execute("UPDATE equipos SET nombre=?, tipo=?, padre=?, medio=?, boca=?, conocido=1, auto=? WHERE mac=?",
                  (texto(d, "nombre", 40), tipo, padre, medio, boca, auto, mac))
    return ""


def guardar_nodo(d):
    with closing(N.abrir()) as c, c:
        nid = str(d.get("id") or "")
        if d.get("borrar"):
            if not c.execute("SELECT 1 FROM nodos WHERE id=?", (nid,)).fetchone():
                return "no_existe"
            if c.execute("SELECT 1 FROM equipos WHERE padre=? UNION SELECT 1 FROM nodos WHERE padre=?", (nid, nid)).fetchone():
                return "tiene_hijos"
            bocas = {k: v for k, v in N.meta_json(c, "bocas", {}).items() if v != nid}
            N.meta_json(c, "bocas", valor=bocas)
            c.execute("DELETE FROM nodos WHERE id=?", (nid,))
            return ""
        nombre, padre = texto(d, "nombre", 40), str(d.get("padre", ""))
        if not nombre:
            return "falta_nombre"
        padres = N.padres_validos(c)
        if padre not in padres:
            return "padre_no_existe"
        if nid:
            if not c.execute("SELECT 1 FROM nodos WHERE id=?", (nid,)).fetchone():
                return "no_existe"
            if N.crea_bucle(padres, nid, padre):
                return "bucle"
            c.execute("UPDATE nodos SET nombre=?, padre=?, boca=? WHERE id=?", (nombre, padre, texto(d, "boca", 40), nid))
        else:
            c.execute("INSERT INTO nodos VALUES(?,?,?,?)", ("n-" + secrets.token_hex(4), nombre, padre, texto(d, "boca", 40)))
    return ""


def guardar_bocas(d):
    with closing(N.abrir()) as c, c:
        padres = N.padres_validos(c)
        bocas = {}
        for k, v in d.items():
            if not re.fullmatch(r"LAN\d|wifi", str(k)) or not isinstance(v, str):
                return "no_valido"
            if v:
                if v not in padres or v == "internet":
                    return "padre_no_existe"
                bocas[k] = v
        N.meta_json(c, "bocas", valor=bocas)
        R.recolocar(c)
    return ""


def guardar_router(d):
    tipo = str(d.get("tipo", ""))
    if tipo == "":
        R.guardar_credenciales(None)
        R.ESTADO.update(estado="sin_configurar", msg="")
        return ""
    if tipo not in R.MODELOS:
        return "no_valido"
    usuario, clave = texto(d, "usuario", 60), str(d.get("clave", ""))[:200]
    if not usuario or not clave:
        return "faltan_datos"
    return R.probar_y_guardar({"tipo": tipo, "usuario": usuario, "clave": clave})


# ---------------------------------------------------------------- HTTP

class Web(BaseHTTPRequestHandler):
    server_version, sys_version, timeout = "panel", "", 20

    def log_message(self, *a):
        pass

    def responder(self, cod, cuerpo, tipo="application/json", cabeceras=()):
        self._respondido = True
        if not isinstance(cuerpo, bytes):
            cuerpo = json.dumps(cuerpo, ensure_ascii=False).encode()
        self.send_response(cod)
        self.send_header("Content-Type", tipo + "; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        for k, v in cabeceras:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(cuerpo)

    def ip(self):
        return self.client_address[0]

    def permitido(self):
        """Solo desde la red local (o Tailscale) y con un nombre de Host propio: frena el DNS rebinding."""
        host = self.headers.get("Host", "").lower()
        host = host[1:host.find("]")] if host.startswith("[") else host.rsplit(":", 1)[0]
        try:
            ipaddress.ip_address(host)
            host_ok = True
        except ValueError:
            host_ok = host in HOSTS
        cli = ipaddress.ip_address(self.ip())
        red_ok = cli.is_private or cli.is_loopback or cli in TAILSCALE
        if host_ok and red_ok:
            return True
        if self.command == "GET" and not urlparse(self.path).path.startswith("/api/"):
            # Quien abre la pagina en el navegador ve que pasa, no un JSON pelado.
            self.responder(403, pagina_prohibida(host, red_ok), "text/html")
        else:
            self.responder(403, {"error": "no_permitido"})
        return False

    def token(self):
        for trozo in self.headers.get("Cookie", "").split(";"):
            k, _, v = trozo.strip().partition("=")
            if k == COOKIE and v:
                return v
        return None

    def sesion_valida(self):
        t = self.token()
        if not t:
            return False
        with closing(N.abrir()) as c:
            r = c.execute("SELECT caduca FROM web_sesiones WHERE token=?", (hashlib.sha256(t.encode()).hexdigest(),)).fetchone()
        return bool(r and r["caduca"] > time.time())

    def cookie(self, token, borrar=False):
        return ("Set-Cookie", f"{COOKIE}={'' if borrar else token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={0 if borrar else SESION_S}")

    def do_GET(self):
        self._atender(self._get)

    def do_POST(self):
        self._atender(self._post)

    def _atender(self, f):
        """Un error inesperado responde 500 en vez de cortar la conexion sin decir nada."""
        self._respondido = False
        try:
            f()
        except ConnectionError:
            pass  # el navegador se fue a mitad
        except Exception:
            N.log.exception("web: %s %s", self.command, urlparse(self.path).path)
            if not self._respondido:
                self.responder(500, {"error": "interno"})

    def _get(self):
        if not self.permitido():
            return
        u = urlparse(self.path)
        try:
            if u.path in ESTATICOS:
                f, t = ESTATICOS[u.path]
                with open(os.path.join(WEB, f), "rb") as fh:
                    return self.responder(200, fh.read(), t)
            if u.path == "/api/sesion":
                with closing(N.abrir()) as c:
                    conf = bool(N.meta(c, "clave"))
                    return self.responder(200, {"configurado": conf, "autenticado": conf and self.sesion_valida(),
                                                "idioma": N.meta(c, "idioma") or "", "nombre": N.meta(c, "nombre") or ""})
            if not self.sesion_valida():
                return self.responder(401, {"error": "sin_sesion"})
            q = parse_qs(u.query)
            if u.path == "/api/estado":
                return self.responder(200, estado())
            if u.path == "/api/ajustes":
                return self.responder(200, ajustes())
            if u.path in ("/api/trafico", "/api/historial"):
                desde, hasta = int(q["desde"][0]), int(q["hasta"][0])
                if not 0 < hasta - desde <= 32 * 86400:
                    raise ValueError
                return self.responder(200, red.trafico(desde, hasta) if u.path == "/api/trafico" else historial(desde, hasta))
            self.responder(404, {"error": "no_existe"})
        except (KeyError, ValueError, OverflowError):
            self.responder(400, {"error": "no_valido"})

    def _post(self):
        if not self.permitido():
            return
        origen = urlparse(self.headers.get("Origin", "")).netloc.lower()
        if origen != self.headers.get("Host", "").lower() or not self.headers.get("Content-Type", "").startswith("application/json"):
            return self.responder(403, {"error": "no_permitido"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if not 0 < n <= 8192:
                raise ValueError
            d = json.loads(self.rfile.read(n))
            if not isinstance(d, dict):
                raise ValueError
        except ValueError:
            return self.responder(400, {"error": "no_valido"})
        ruta = urlparse(self.path).path
        if ruta in ("/api/configurar", "/api/entrar"):
            return self.acceso(ruta, d)
        if not self.sesion_valida():
            return self.responder(401, {"error": "sin_sesion"})
        if ruta == "/api/salir":
            with closing(N.abrir()) as c, c:
                c.execute("DELETE FROM web_sesiones WHERE token=?", (hashlib.sha256((self.token() or "").encode()).hexdigest(),))
            return self.responder(200, {"error": ""}, cabeceras=[self.cookie("", True)])
        if ruta == "/api/clave":
            return self.cambiar_clave(d)
        acciones = {"/api/equipo": guardar_equipo, "/api/nodo": guardar_nodo, "/api/bocas": guardar_bocas,
                    "/api/router": guardar_router, "/api/ajustes": self.guardar_ajustes}
        if ruta not in acciones:
            return self.responder(404, {"error": "no_existe"})
        err = acciones[ruta](d)
        self.responder(400 if err else 200, {"error": err})

    def guardar_ajustes(self, d):
        with closing(N.abrir()) as c, c:
            if "idioma" in d:
                if d["idioma"] not in IDIOMAS:
                    return "no_valido"
                N.meta(c, "idioma", d["idioma"])
            if "nombre" in d:
                N.meta(c, "nombre", texto(d, "nombre", 40))
        return ""

    def acceso(self, ruta, d):
        ip = self.ip()
        if bloqueado(ip):
            return self.responder(429, {"error": "espera"})
        clave = str(d.get("clave", ""))
        with closing(N.abrir()) as c, c:
            guardada = N.meta(c, "clave")
            if ruta == "/api/configurar":
                # Solo la primera vez: despues hace falta la contraseña actual para cambiarla.
                if guardada:
                    return self.responder(403, {"error": "ya_configurado"})
                if not codigo_correcto(d.get("codigo")):
                    anotar_fallo(ip)
                    return self.responder(403, {"error": "codigo_malo"})
                if len(clave) < MIN_CLAVE:
                    return self.responder(400, {"error": "clave_corta"})
                N.meta(c, "clave", hash_clave(clave))
                _codigo["v"] = ""
                if d.get("idioma") in IDIOMAS:
                    N.meta(c, "idioma", d["idioma"])
                N.meta(c, "nombre", texto(d, "nombre", 40))
            elif not guardada or not clave_correcta(clave, guardada):
                anotar_fallo(ip)
                return self.responder(401, {"error": "clave_mala"})
            with _fallos_lock:
                _fallos.pop(ip, None)
            t = crear_sesion(c)
        self.responder(200, {"error": ""}, cabeceras=[self.cookie(t)])

    def cambiar_clave(self, d):
        ip = self.ip()
        if bloqueado(ip):
            return self.responder(429, {"error": "espera"})
        nueva = str(d.get("nueva", ""))
        with closing(N.abrir()) as c, c:
            if not clave_correcta(str(d.get("actual", "")), N.meta(c, "clave")):
                anotar_fallo(ip)
                return self.responder(400, {"error": "clave_mala"})
            if len(nueva) < MIN_CLAVE:
                return self.responder(400, {"error": "clave_corta"})
            N.meta(c, "clave", hash_clave(nueva))
            # Las demas sesiones abiertas dejan de valer.
            c.execute("DELETE FROM web_sesiones WHERE token<>?", (hashlib.sha256((self.token() or "").encode()).hexdigest(),))
        self.responder(200, {"error": ""})


def servir(puerto):
    preparar_codigo()
    ThreadingHTTPServer(("0.0.0.0", puerto), Web).serve_forever()
