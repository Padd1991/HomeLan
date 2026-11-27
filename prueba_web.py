"""Pruebas de la web: primera vez, contraseña, sesiones, protecciones, ajustes y textos en los dos idiomas."""
import atexit
import json
import logging
import os
import shutil
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import closing
from http.server import ThreadingHTTPServer

os.environ["PANEL_DATOS"] = tempfile.mkdtemp(prefix="prueba-panel-")
atexit.register(shutil.rmtree, os.environ["PANEL_DATOS"], True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nucleo as N  # noqa: E402
import router  # noqa: E402
import web  # noqa: E402

router.CONF_ANTIGUA = os.path.join(N.DATOS, "no-existe.conf")  # nunca la de verdad

N.preparar()
ROUTER, PC = "00:00:5e:00:53:01", "00:00:5e:00:53:03"
with closing(N.abrir()) as c, c:
    for mac, ip, padre in ((ROUTER, "192.168.0.1", "internet"), (PC, "192.168.0.11", "")):
        c.execute("INSERT INTO equipos(mac,ip,padre,visto_1,visto_n,online,auto) VALUES(?,?,?,0,0,1,1)", (mac, ip, padre))
    N.meta(c, "router_mac", ROUTER)

srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Web)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"
HOST = f"127.0.0.1:{srv.server_port}"
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def pedir(ruta, datos=None, cookie=None, origen=HOST, host=None):
    cab = {}
    cuerpo = None
    if datos is not None:
        cuerpo = json.dumps(datos).encode()
        cab.update({"Content-Type": "application/json", "Origin": f"http://{origen}"})
    if cookie:
        cab["Cookie"] = cookie
    if host:
        cab["Host"] = host
    pet = urllib.request.Request(BASE + ruta, cuerpo, cab)
    try:
        with NO_PROXY.open(pet, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}") if "json" in r.headers.get("Content-Type", "") else r.read(), r.headers.get("Set-Cookie")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), e.headers.get("Set-Cookie")


def cookie_de(set_cookie):
    return set_cookie.split(";")[0]


# Pagina y textos accesibles sin sesion; datos no
assert pedir("/")[0] == 200 and pedir("/i18n/en.json")[0] == 200
s, d, _ = pedir("/api/sesion")
assert s == 200 and d["configurado"] is False and d["autenticado"] is False
assert pedir("/api/estado")[0] == 401

# DNS rebinding: un nombre de Host ajeno no pasa
assert pedir("/api/sesion", host="evil.example.com")[0] == 403
# En el navegador, una pagina que explica que pasa (y no pinta como HTML lo que venga en el Host)
try:
    NO_PROXY.open(urllib.request.Request(BASE + "/", headers={"Host": "panel<b>.lan"}), timeout=10)
    raise AssertionError("deberia ser 403")
except urllib.error.HTTPError as e:
    pagina = e.read().decode()
    assert e.code == 403 and "text/html" in e.headers["Content-Type"] and "PANEL_HOSTS" in pagina
    assert "panel&lt;b&gt;.lan" in pagina and "panel<b>" not in pagina

# Primera vez: hace falta el codigo que sale en el registro; contraseña corta no; luego bien, con sesion
codigo = web.preparar_codigo()
assert re.fullmatch(r"[A-Z2-9]{8}", codigo) and web.preparar_codigo() == codigo  # el mismo hasta que se usa
assert pedir("/api/configurar", {"clave": "una clave larga"})[:2] == (403, {"error": "codigo_malo"})
assert pedir("/api/configurar", {"clave": "una clave larga", "codigo": "AAAA-AAAA"})[1]["error"] == "codigo_malo"
assert pedir("/api/configurar", {"clave": "corta", "codigo": codigo})[1]["error"] == "clave_corta"
web._fallos.clear()
s, d, sc = pedir("/api/configurar", {"clave": "una clave larga", "nombre": "Casa de prueba", "idioma": "en",
                                     "codigo": f" {codigo[:4].lower()}-{codigo[4:]} "})  # como se copie
assert s == 200 and "HttpOnly" in sc and "SameSite=Strict" in sc
ck = cookie_de(sc)
assert pedir("/api/configurar", {"clave": "otra clave larga", "codigo": codigo})[1]["error"] == "ya_configurado"
assert web.preparar_codigo() == "" and not web.codigo_correcto(codigo)  # con contraseña ya no hay codigo
s, d, _ = pedir("/api/sesion", cookie=ck)
assert d["autenticado"] and d["idioma"] == "en" and d["nombre"] == "Casa de prueba"
s, d, _ = pedir("/api/estado", cookie=ck)
assert s == 200 and len(d["equipos"]) == 2 and d["router_mac"] == ROUTER

# Peticiones que cambian algo: sin Origin propio o sin JSON, nada
assert pedir("/api/equipo", {"mac": PC}, cookie=ck, origen="evil.example.com")[0] == 403

# Editar un equipo: tipos con codigo, bucles impedidos, deja de ser automatico
assert pedir("/api/equipo", {"mac": PC, "nombre": "PC", "tipo": "Ordenador", "padre": "", "medio": "cable"}, cookie=ck)[1]["error"] == "no_valido"
assert pedir("/api/equipo", {"mac": PC, "nombre": "PC", "tipo": "ordenador", "padre": ROUTER, "medio": "cable"}, cookie=ck)[1]["error"] == ""
assert pedir("/api/equipo", {"mac": ROUTER, "nombre": "R", "tipo": "red", "padre": PC, "medio": "cable"}, cookie=ck)[1]["error"] == "bucle"
with closing(N.abrir()) as c:
    assert tuple(c.execute("SELECT tipo, auto, conocido FROM equipos WHERE mac=?", (PC,)).fetchone()) == ("ordenador", 0, 1)

# Ponerle nombre no apaga la colocacion automatica; cambiar a mano donde esta, si
MOVIL = "00:00:5e:00:53:04"
with closing(N.abrir()) as c, c:
    c.execute("INSERT INTO equipos(mac,ip,padre,medio,boca,visto_1,visto_n,online,auto) VALUES(?,?,?,?,?,0,0,1,1)",
              (MOVIL, "192.168.0.12", ROUTER, "wifi", "wifi5"))
auto = lambda: N.abrir().execute("SELECT auto FROM equipos WHERE mac=?", (MOVIL,)).fetchone()["auto"]
movil = {"mac": MOVIL, "nombre": "Movil", "tipo": "movil", "padre": ROUTER, "medio": "wifi", "boca": "wifi5"}
assert pedir("/api/equipo", movil, cookie=ck)[1]["error"] == "" and auto() == 1
assert pedir("/api/equipo", {**movil, "boca": "salon"}, cookie=ck)[1]["error"] == "" and auto() == 0

# Nodos sin IP y bocas
assert pedir("/api/nodo", {"nombre": "Switch", "padre": ROUTER, "boca": ""}, cookie=ck)[1]["error"] == ""
nid = pedir("/api/estado", cookie=ck)[1]["nodos"][0]["id"]
assert pedir("/api/equipo", {"mac": PC, "nombre": "PC", "tipo": "ordenador", "padre": nid, "medio": "cable"}, cookie=ck)[1]["error"] == ""
assert pedir("/api/nodo", {"id": nid, "borrar": True}, cookie=ck)[1]["error"] == "tiene_hijos"
assert pedir("/api/bocas", {"LAN4": nid, "wifi": ""}, cookie=ck)[1]["error"] == ""
assert pedir("/api/bocas", {"LAN4": "no-existe"}, cookie=ck)[1]["error"] == "padre_no_existe"
assert pedir("/api/bocas", {"<script>": ""}, cookie=ck)[1]["error"] == "no_valido"
assert pedir("/api/bocas", {"LAN1": ["x"]}, cookie=ck)[:2] == (400, {"error": "no_valido"})

# Un fallo inesperado responde 500 con un error que la web sabe mostrar, en vez de cortar la conexion
original, web.guardar_nodo = web.guardar_nodo, lambda d: 1 / 0
logging.disable(logging.CRITICAL)
assert pedir("/api/nodo", {"nombre": "x"}, cookie=ck)[:2] == (500, {"error": "interno"})
logging.disable(logging.NOTSET)
web.guardar_nodo = original
s, d, _ = pedir("/api/ajustes", cookie=ck)
assert d["bocas"] == {"LAN4": nid} and "zte_f6600p" in d["modelos"] and d["router"]["tipo"] == ""
assert d["escaneo"] == {"rango": "", "red": ""}

# Router: sin datos no; quitarlo si
assert pedir("/api/router", {"tipo": "zte_f6600p", "usuario": "", "clave": ""}, cookie=ck)[1]["error"] == "faltan_datos"
assert pedir("/api/router", {"tipo": "otro"}, cookie=ck)[1]["error"] == "no_valido"
assert pedir("/api/router", {"tipo": ""}, cookie=ck)[1]["error"] == ""

# Cambiar la contraseña: hace falta la actual; las demas sesiones caducan
s, d, sc2 = pedir("/api/entrar", {"clave": "una clave larga"})
ck2 = cookie_de(sc2)
assert pedir("/api/clave", {"actual": "mala", "nueva": "nueva clave larga"}, cookie=ck)[1]["error"] == "clave_mala"
assert pedir("/api/clave", {"actual": "una clave larga", "nueva": "nueva clave larga"}, cookie=ck)[1]["error"] == ""
assert pedir("/api/estado", cookie=ck)[0] == 200 and pedir("/api/estado", cookie=ck2)[0] == 401

# Salir invalida la sesion
assert pedir("/api/salir", {}, cookie=ck)[0] == 200 and pedir("/api/estado", cookie=ck)[0] == 401

# Muchas entradas a la vez: como mucho dos scrypt (16 MB cada uno) en marcha a la vez
real, vivos, cerrojo = web.hashlib.scrypt, {"n": 0, "max": 0}, threading.Lock()


def contado(*a, **k):
    with cerrojo:
        vivos["n"] += 1
        vivos["max"] = max(vivos["max"], vivos["n"])
    try:
        time.sleep(0.05)
        return real(*a, **k)
    finally:
        with cerrojo:
            vivos["n"] -= 1


web.hashlib.scrypt = contado
hilos = [threading.Thread(target=pedir, args=("/api/entrar", {"clave": "nueva clave larga"})) for _ in range(8)]
for h in hilos:
    h.start()
for h in hilos:
    h.join()
web.hashlib.scrypt = real
assert 1 <= vivos["max"] <= 2, vivos

# Fuerza bruta: tras 5 fallos, espera
web._fallos.clear()
for _ in range(5):
    assert pedir("/api/entrar", {"clave": "mala"})[0] == 401
assert pedir("/api/entrar", {"clave": "nueva clave larga"})[0] == 429

# Textos: mismas claves en los dos idiomas, y todas las que usa la pagina existen
base = os.path.dirname(os.path.abspath(__file__))
es = json.load(open(os.path.join(base, "web/i18n/es.json"), encoding="utf-8"))
en = json.load(open(os.path.join(base, "web/i18n/en.json"), encoding="utf-8"))
assert set(es) == set(en), sorted(set(es) ^ set(en))
for k in es:
    assert set(re.findall(r"\{(\w+)\}", es[k])) == set(re.findall(r"\{(\w+)\}", en[k])), k
js = open(os.path.join(base, "web/app.js"), encoding="utf-8").read()
usadas = {k for k in re.findall(r"\bt\('([^']+)'", js) if not k.endswith((".", "_")) and k not in ("IP", "MAC")}
faltan = usadas - set(es)
assert not faltan, sorted(faltan)
dinamicas = ["nav.ajustes", "tipo.movil", "medio.nada", "error.bucle", "error.interno", "error.codigo_malo", "ev.punto", "evtxt.punto", "bajada.sin_upnp", "rango.7d",
             "graf.titulo.7d", "aj.router_clave", "aj.router_error", "aj.router_sin_configurar", "estado.nuevo", "editar", "identificar",
             "sin_id", "sin_id_privada", "hist.sin_router", "hist.conectado", "f.equipo", "hora", "que_paso"]
assert all(k in es for k in dinamicas), [k for k in dinamicas if k not in es]
srv.shutdown()
print("Pruebas de la web correctas")
