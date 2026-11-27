"""Pruebas del lector del router contra un router simulado en 127.0.0.1. No toca la red de casa."""
import hashlib
import atexit
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

os.environ["PANEL_DATOS"] = tempfile.mkdtemp(prefix="prueba-panel-")
atexit.register(shutil.rmtree, os.environ["PANEL_DATOS"], True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nucleo as N  # noqa: E402
import router as R  # noqa: E402

R.CONF_ANTIGUA = os.path.join(N.DATOS, "no-existe.conf")
CLAVE, NUMERO = "clave de prueba=1", "DJ8cggmF"
ESTADO = {"dentro": False, "salidas": 0, "intentos": 0, "bloqueo": 0, "error": "", "solapes": 0}
COOKIE = "_TESTCOOKIESUPPORT=1"


def pares(objeto, filas):
    inst = "".join("<Instance>" + "".join(f"<ParaName>{k}</ParaName><ParaValue>{v}</ParaValue>" for k, v in f.items()) + "</Instance>" for f in filas)
    return f"<{objeto}>{inst}</{objeto}>"


CABLE = "<ajax_response_xml_root><IF_ERRORSTR>SUCC</IF_ERRORSTR>" + pares("OBJ_ACCESSDEV_ID", [
    {"HostName": "", "IPAddress": "192.168.0.40", "MACAddress": "D8:9E:F3:00:00:01", "AliasName": "LAN4"},
    {"HostName": "repetidor", "IPAddress": "192.168.0.17", "MACAddress": "a0:2d:13:00:00:02", "AliasName": "LAN1"},
    {"HostName": "raro", "IPAddress": "192.168.0.9", "MACAddress": "no-es-mac", "AliasName": "LAN2"}]) + "</ajax_response_xml_root>"
WIFI = "<ajax_response_xml_root><IF_ERRORSTR>SUCC</IF_ERRORSTR>" + pares("OBJ_WLAN_AD_ID", [
    {"_InstID": "DEV.WIFI.AP5.AD1", "AliasName": "DEV.WIFI.AP5", "RSSI": "-32", "TxRate": "433000", "RxRate": "24000",
     "CurrentMode": "11ac", "LinkTime": "496386", "IPAddress": "192.168.0.12", "MACAddress": "90:ca:fa:00:00:03", "HostName": ""},
    {"_InstID": "DEV.WIFI.AP2.AD1", "AliasName": "DEV.WIFI.AP2", "RSSI": "x", "MACAddress": "14:c1:9f:00:00:04", "HostName": "esp"}]) \
    + pares("OBJ_WLANAP_ID", [{"Alias": "SSID1"}]) + "</ajax_response_xml_root>"


class Falso(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def enviar(self, texto, tipo="text/xml"):
        b = texto.encode()
        self.send_response(200)
        if self.path == "/":
            self.send_header("Set-Cookie", COOKIE + "; PATH=/; HttpOnly; SameSite=strict")
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        tag = parse_qs(urlparse(self.path).query).get("_tag", [""])[0]
        if tag == "login_entry":
            return self.enviar(json.dumps({"lockingTime": ESTADO["bloqueo"], "loginErrMsg": ESTADO["error"], "promptMsg": "", "sess_token": "T0"}), "application/json")
        if tag == "login_token":
            return self.enviar(f"<ajax_response_xml_root>{NUMERO}</ajax_response_xml_root>")
        if self.path == "/":
            # Como el real: tras una clave mala la portada sigue siendo el login, con su numero de sesion igualmente.
            esc = "".join("\\x%02x" % ord(ch) for ch in ("showCommonPage" if ESTADO["dentro"] else "showloginPage"))
            return self.enviar(f'var x;\n_sessionTmpToken = "\\x41\\x42\\x43";\nvar NowStatus = "{esc}";\n', "text/html")
        if not ESTADO["dentro"]:
            return self.enviar("<ajax_response_xml_root><IF_ERRORSTR>SessionTimeout</IF_ERRORSTR></ajax_response_xml_root>")
        if tag == "localNetStatus":
            return self.enviar("<script></script>", "text/html")
        return self.enviar({"accessdev_landevs_lua.lua": CABLE, "wlan_client_stat_lua.lua": WIFI}[tag])

    def do_POST(self):
        tag = parse_qs(urlparse(self.path).query)["_tag"][0]
        d = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        if tag == "login_entry":
            ESTADO["intentos"] += 1
            ESTADO["solapes"] += ESTADO["dentro"]  # alguien entra con otra sesion aun abierta
            ok = d["Password"][0] == hashlib.sha256((CLAVE + NUMERO).encode()).hexdigest() and d["Username"][0] == "user"
            ESTADO["dentro"] = ok and COOKIE in self.headers.get("Cookie", "")  # sin la cookie de la portada no vale
            ESTADO["error"] = "" if ok else "Usuario o contraseña incorrecta"
            return self.enviar(json.dumps({"login_need_refresh": True, "sess_token": "T1"}), "application/json")  # siempre, como el real
        if tag == "logout_entry":
            assert d["_sessionTOKEN"][0] == "ABC" and d["IF_LogOff"][0] == "1"
            ESTADO["dentro"] = False
            ESTADO["salidas"] += 1
            return self.enviar("{}", "application/json")


srv = ThreadingHTTPServer(("127.0.0.1", 0), Falso)
threading.Thread(target=srv.serve_forever, daemon=True).start()
IP = f"127.0.0.1:{srv.server_port}"
BUENA = {"tipo": "zte_f6600p", "usuario": "user", "clave": CLAVE}

# Entra, lee, interpreta y cierra la sesion
datos = R.leer_router(BUENA, IP)
assert (ESTADO["dentro"], ESTADO["salidas"], ESTADO["intentos"]) == (False, 1, 1), ESTADO
assert datos["d8:9e:f3:00:00:01"]["punto"] == "LAN4" and datos["a0:2d:13:00:00:02"]["punto"] == "LAN1"
assert datos["90:ca:fa:00:00:03"] == {"punto": "wifi5", "ip": "192.168.0.12", "host": "", "rssi": -32, "tx": 433000,
                                      "rx": 24000, "modo": "11ac", "enlace": 496386}, datos["90:ca:fa:00:00:03"]
assert datos["14:c1:9f:00:00:04"]["punto"] == "wifi24-2" and datos["14:c1:9f:00:00:04"]["rssi"] is None
assert "no-es-mac" not in datos and len(datos) == 4

# La web y la vigilancia a la vez: una sesion detras de otra, nunca dos abiertas en el router
hilos = [threading.Thread(target=R.leer_router, args=(BUENA, IP)) for _ in range(4)]
for h in hilos:
    h.start()
for h in hilos:
    h.join()
assert ESTADO["solapes"] == 0 and ESTADO["salidas"] == 5 and not ESTADO["dentro"], ESTADO
ESTADO["intentos"] = 1

# Clave mala: el router dice "recarga" igual, pero se detecta y es un solo intento
try:
    R.leer_router({**BUENA, "clave": "mala"}, IP)
    raise AssertionError("debia fallar")
except R.ErrorClave as ex:
    assert "incorrecta" in str(ex)
assert ESTADO["intentos"] == 2

# Router bloqueado: ni siquiera se envia la clave
ESTADO["bloqueo"] = 60
try:
    R.leer_router(BUENA, IP)
    raise AssertionError("debia fallar")
except R.Ocupado:
    pass
assert ESTADO["intentos"] == 2
ESTADO["bloqueo"] = 0

# Credenciales: archivo solo para el panel, importacion de la primera version y borrado
assert R.leer_credenciales() is None
antigua = os.path.join(N.DATOS, "antigua.conf")
with open(antigua, "w") as f:
    f.write("# comentario\nusuario=user\nclave= a=b \n")
R.CONF_ANTIGUA = antigua
assert R.leer_credenciales() == {"tipo": "zte_f6600p", "usuario": "user", "clave": " a=b "}
if os.name != "nt":  # Windows no tiene permisos de Unix
    assert stat.S_IMODE(os.stat(R.ruta_credenciales()).st_mode) == 0o600
R.guardar_credenciales(None)
R.CONF_ANTIGUA = os.path.join(N.DATOS, "no-existe.conf")
assert R.leer_credenciales() is None

# Volcado en el panel: colocacion automatica segun lo que hay en cada boca
N.preparar()
ROUTER, REP, NUEVO, FIJO, WIFI_N = "00:00:5e:00:53:01", "00:00:5e:00:53:06", "00:11:22:33:44:55", "00:11:22:33:44:66", "00:11:22:33:44:77"
with closing(N.abrir()) as c, c:
    for mac, auto, padre in ((ROUTER, 0, "internet"), (REP, 1, ""), (NUEVO, 1, ""), (FIJO, 0, "switch"), (WIFI_N, 1, "")):
        c.execute("INSERT INTO equipos(mac,ip,padre,auto,visto_1,visto_n,online) VALUES(?,?,?,?,0,0,1)", (mac, "1.1.1.1", padre, auto))
    c.execute("INSERT INTO nodos VALUES('sw','Switch',?, '')", (ROUTER,))
    N.meta(c, "router_mac", ROUTER)
    N.meta_json(c, "bocas", valor={"LAN1": REP, "LAN4": "sw"})
    t = 1_000_000
    R.aplicar_router(c, {NUEVO: {"punto": "LAN1", "ip": "x", "host": ""}, REP: {"punto": "LAN1", "ip": "z", "host": ""},
                         FIJO: {"punto": "wifi5", "ip": "y", "host": "", "rssi": -50}, WIFI_N: {"punto": "wifi24", "ip": "w", "host": "esp"}}, t)
    e = {r["mac"]: dict(r) for r in c.execute("SELECT * FROM equipos")}
    assert (e[NUEVO]["padre"], e[NUEVO]["medio"], e[NUEVO]["boca"]) == (REP, "", "LAN1"), e[NUEVO]   # detras del repetidor
    assert (e[REP]["padre"], e[REP]["medio"]) == (ROUTER, "cable")                                     # el propio repetidor
    assert e[FIJO]["padre"] == "switch" and e[FIJO]["r_rssi"] == -50                                   # lo puesto a mano no se toca
    assert (e[WIFI_N]["padre"], e[WIFI_N]["medio"], e[WIFI_N]["boca"], e[WIFI_N]["host"]) == (ROUTER, "wifi", "wifi24", "esp")
    assert not c.execute("SELECT 1 FROM eventos").fetchone()                                           # primera lectura: sin avisos
    R.aplicar_router(c, {NUEVO: {"punto": "LAN1", "ip": "x", "host": ""}}, t + 300)
    assert c.execute("SELECT COUNT(*) n FROM puntos WHERE mac=?", (NUEVO,)).fetchone()["n"] == 1
    R.aplicar_router(c, {NUEVO: {"punto": "LAN4", "ip": "x", "host": ""}}, t + 600)
    ev = c.execute("SELECT * FROM eventos").fetchall()
    assert len(ev) == 1 and ev[0]["clase"] == "punto" and json.loads(ev[0]["datos"]) == {"de": "LAN1", "a": "LAN4"}
    assert tuple(c.execute("SELECT padre, medio FROM equipos WHERE mac=?", (NUEVO,)).fetchone()) == ("sw", "cable")
    p = c.execute("SELECT * FROM puntos WHERE mac=? ORDER BY id", (NUEVO,)).fetchall()
    assert [x["punto"] for x in p] == ["LAN1", "LAN4"] and p[0]["abierta"] == 0 and p[0]["fin"] == t + 300
    R.aplicar_router(c, {NUEVO: {"punto": "LAN2", "ip": "x", "host": ""}}, t + 9000)   # tras mucho sin datos: sin aviso inventado
    assert len(c.execute("SELECT * FROM eventos").fetchall()) == 1
    # Cambiar lo que hay en una boca recoloca a los automaticos
    N.meta_json(c, "bocas", valor={"LAN2": "sw"})
    R.recolocar(c)
    assert c.execute("SELECT padre FROM equipos WHERE mac=?", (NUEVO,)).fetchone()["padre"] == "sw"

# Probar y guardar desde la web: una clave mala nunca se guarda
with closing(N.abrir()) as c, c:
    N.meta(c, "router_ip", IP)
assert R.probar_y_guardar({**BUENA, "clave": "mala"}) == "clave" and R.leer_credenciales() is None
assert R.probar_y_guardar(BUENA) == "" and R.leer_credenciales() == BUENA and R.ESTADO["estado"] == "ok"
assert R._despertar == {"ya": True, "ok": True}  # la vigilancia deja su espera larga y sigue a ritmo normal
srv.shutdown()
print("Pruebas del router correctas")
