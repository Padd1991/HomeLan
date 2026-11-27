"""Pruebas de presencia, medidor, trafico y migracion. Base de datos en carpeta temporal; no toca la red."""
import atexit
import ipaddress
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

os.environ["PANEL_DATOS"] = tempfile.mkdtemp(prefix="prueba-panel-")
atexit.register(shutil.rmtree, os.environ["PANEL_DATOS"], True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nucleo as N  # noqa: E402
import red  # noqa: E402
import router  # noqa: E402

router.CONF_ANTIGUA = os.path.join(N.DATOS, "no-existe.conf")

red.GRACIA = 300


def memoria():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(N.ESQUEMA)
    N.migrar(c)
    return c


c = memoria()
A, B, NV, RT = "02:00:00:00:00:0a", "00:11:22:33:44:55", "00:11:22:33:44:66", "00:aa:bb:cc:dd:01"
clases = lambda: [r["clase"] for r in c.execute("SELECT clase FROM eventos ORDER BY id")]
t = 1_000_000

# Lectura de arp-scan: ignora basura y duplicados
r = red.leer_arp("192.168.0.9\t00:11:22:33:44:55\tAcme\n192.168.0.9\t00:11:22:33:44:55\tAcme (DUP: 2)\nbasura\nx\ty\tz\n192.168.0.8\tAA:BB:CC:00:00:01\t(Unknown: locally administered)\n")
assert r == [("192.168.0.9", B, "Acme"), ("192.168.0.8", "aa:bb:cc:00:00:01", "")], r

# Primer escaneo: foto inicial sin avisos, todo por identificar; el router se reconoce solo
red.aplicar(c, [("192.168.0.1", RT, "ZTE"), ("192.168.0.2", A, ""), ("192.168.0.3", B, "Acme")], t,
            {RT: {"tipo": "red", "padre": "internet", "medio": "cable"}})
assert clases() == []
e = {r["mac"]: dict(r) for r in c.execute("SELECT * FROM equipos")}
assert e[RT]["tipo"] == "red" and e[RT]["padre"] == "internet" and e[A]["tipo"] == "otro"
assert all(x["conocido"] == 0 and x["auto"] == 1 for x in e.values())

# Equipo nuevo despues: aviso
todos = [("192.168.0.2", A, ""), ("192.168.0.3", B, ""), ("192.168.0.4", NV, "")]
red.aplicar(c, todos, t + 60)
assert clases() == ["nuevo"]

# A falla un par de escaneos: no se da por desconectado
for i in (120, 180):
    red.aplicar(c, todos[1:], t + i)
assert clases() == ["nuevo"]
red.aplicar(c, todos, t + 240)
assert c.execute("SELECT COUNT(*) n FROM sesiones WHERE mac=?", (A,)).fetchone()["n"] == 1

# A se va de verdad: desconexion con la hora real y la duracion en datos (sin texto en un idioma)
for i in range(300, 660, 60):
    red.aplicar(c, todos[1:], t + i)
ev = c.execute("SELECT * FROM eventos ORDER BY id DESC LIMIT 1").fetchone()
assert ev["clase"] == "desconecta" and ev["ts"] == t + 240 and json.loads(ev["datos"]) == {"dur": 240} and ev["texto"] == "", dict(ev)

# A vuelve: conexion y sesion nueva
red.aplicar(c, todos, t + 660)
assert clases()[-1] == "conecta"
assert c.execute("SELECT COUNT(*) n FROM sesiones WHERE mac=?", (A,)).fetchone()["n"] == 2

# B cambia de IP
red.aplicar(c, [todos[0], ("192.168.0.7", B, ""), todos[2]], t + 720)
ev = c.execute("SELECT * FROM eventos ORDER BY id DESC LIMIT 1").fetchone()
assert ev["clase"] == "ip" and json.loads(ev["datos"]) == {"de": "192.168.0.3", "a": "192.168.0.7"}

# Dos equipos en la misma IP: un solo aviso de conflicto
n = len(clases())
for i in (780, 840):
    red.aplicar(c, [todos[0], ("192.168.0.7", B, ""), todos[2], ("192.168.0.4", "00:aa:aa:aa:aa:aa", "")], t + i)
assert clases()[n:].count("conflicto") == 1, clases()[n:]

# Escaner parado 2 horas: no inventa presencia
red.aplicar(c, [todos[0]], t + 8000)
s = c.execute("SELECT * FROM sesiones WHERE mac=? ORDER BY id DESC LIMIT 2", (A,)).fetchall()
assert s[0]["inicio"] == t + 8000 and s[1]["fin"] == t + 840 and s[1]["abierta"] == 0

# Medidor de bajada
TOPE = 2 ** 32 - 1
m = red.Medidor()
assert m.muestra(0, 1000, 10) is None
assert m.muestra(10, 6000, 20) == (5000, 10)
m.muestra(20, TOPE - 999, 30)
assert m.muestra(30, 500, 40) == (1500, 10)
assert m.muestra(40, 200, 5) is None
assert m.muestra(50, 1200, 15) == (1000, 10)
assert m.muestra(100, 9999, 99) is None
m = red.Medidor()
m.muestra(0, TOPE - 1000, 10)
assert m.muestra(10, TOPE, 20) is None and m.aviso == ""
assert m.muestra(20, 4999, 30) == (6000, 20)
for i in range(30, 70, 10):
    m.muestra(i, TOPE, i + 10)
assert m.aviso == "tope" and m.muestra(70, TOPE, 80) is None
m = red.Medidor()
m.muestra(0, 5, 10)
assert m.muestra(10, 5, 12) == (0, 10)
assert m.muestra(20, 5, 500) is None and m.aviso == "parado"
assert m.muestra(30, 1005, 510) == (1000, 10) and m.aviso == ""
# Contador de 64 bits (por encima de 2^32): se mide, no es un tope; y cruzar 2^32 no es una vuelta
m = red.Medidor()
m.muestra(0, 5 * 10 ** 12, 10)
assert m.muestra(10, 5 * 10 ** 12 + 12_500_000, 20) == (12_500_000, 10) and m.aviso == ""
m = red.Medidor()
m.muestra(0, TOPE - 1000, 10)
assert m.muestra(10, TOPE + 4001, 20) == (5001, 10) and m.aviso == ""
assert m.muestra(20, 100, 30) is None  # el router se reinicia: no se inventa una bajada enorme

# Descripcion UPnP: encuentra el contador aunque venga con espacio de nombres y URLBase
xml = b"""<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><URLBase>http://192.168.1.1:5000/</URLBase>
<device><serviceList><service><serviceType>urn:schemas-upnp-org:service:Layer3Forwarding:1</serviceType><controlURL>/l3f</controlURL></service></serviceList>
<deviceList><device><serviceList><service><serviceType>urn:schemas-upnp-org:service:WANCommonInterfaceConfig:1</serviceType>
<controlURL>/ctl/CmnIfCfg</controlURL></service></serviceList></device></deviceList></device></root>"""
assert red.control_wan(xml, "http://192.168.1.1:1900/desc.xml") == ("http://192.168.1.1:5000/ctl/CmnIfCfg", "urn:schemas-upnp-org:service:WANCommonInterfaceConfig:1")
assert red.control_wan(b"<root><device/></root>", "http://x/") is None

# Trafico agregado: media por tramo, pico y sin inventar ceros
N.preparar()
with N.abrir() as d:
    for ts, b in ((6000, 1000), (6010, 3000), (6125, 500)):
        red.apuntar(d, ts, b, 10)
r = red.trafico(5940, 6300)
assert [x[0] for x in r["puntos"]] == [6000, 6120] and r["bytes"] == 4500 and r["puntos"][0][1:] == [1600, 2400], r

# Acumulador: un minuto se escribe entero de una vez, y el minuto en curso se ve igualmente
a = red.Acumulador()
assert a.anadir(7200, 1000, 10) is None and a.anadir(7230, 3000, 10) is None
assert a.pendiente() == (7200, 4000, 20.0, 2400.0)
assert a.anadir(7265, 500, 10) == (7200, 4000, 20.0, 2400.0)   # cambia de minuto: sale el anterior
assert a.cerrar_si_paso(7290) is None and a.cerrar_si_paso(7330) == (7260, 500, 10.0, 400.0)
assert a.pendiente() is None and a.cerrar_si_paso(9999) is None
red.ACUMULADO = red.Acumulador()
red.ACUMULADO.anadir(6130, 1000, 10)                               # aun en memoria, mismo tramo que una fila escrita
r = red.trafico(5940, 6300)
assert r["bytes"] == 5500 and [x[0] for x in r["puntos"]] == [6000, 6120], r
red.ACUMULADO.anadir(6200, 100, 10)                                # cierra 6120 en memoria y abre 6180 (sin escribir)
assert red.trafico(5940, 6300)["bytes"] == 4600

# Migracion desde la primera version: tipos, puntos, bocas y nodos, sin perder nada
v1 = sqlite3.connect(":memory:")
v1.row_factory = sqlite3.Row
v1.executescript("""
CREATE TABLE equipos(mac TEXT PRIMARY KEY, ip TEXT, fabricante TEXT, host TEXT DEFAULT '', nombre TEXT DEFAULT '',
  tipo TEXT DEFAULT 'Sin clasificar', padre TEXT DEFAULT '', medio TEXT DEFAULT '', boca TEXT DEFAULT '',
  visto_1 INTEGER, visto_n INTEGER, online INTEGER DEFAULT 0, conocido INTEGER DEFAULT 0, host_ts INTEGER DEFAULT 0);
CREATE TABLE eventos(id INTEGER PRIMARY KEY, ts INTEGER, clase TEXT, mac TEXT, ip TEXT, texto TEXT);
CREATE TABLE puntos(id INTEGER PRIMARY KEY, mac TEXT, punto TEXT, inicio INTEGER, fin INTEGER, abierta INTEGER DEFAULT 1);
CREATE TABLE meta(clave TEXT PRIMARY KEY, valor TEXT);
INSERT INTO equipos(mac,ip,nombre,tipo,padre,medio,boca) VALUES
 ('00:00:5e:00:53:01','192.168.0.1','Router','Red','internet','cable','Fibra'),
 ('00:00:5e:00:53:02','192.168.0.12','Chromecast','Casa','wifi-router','wifi','5 GHz'),
 ('00:00:5e:00:53:03','192.168.0.11','PC','Ordenador','switch','cable','LAN4'),
 ('00:00:5e:00:53:04','192.168.0.31','ESP','Casa','wifi-router','wifi','2,4 GHz'),
 ('02:00:5e:00:53:05','192.168.0.18','','Sin clasificar','wifi-router','wifi','5 GHz');
INSERT INTO eventos(ts,clase,mac,ip,texto) VALUES (1,'nuevo','x','1.1.1.1','Primera vez en la red');
INSERT INTO puntos(mac,punto,inicio,fin) VALUES ('00:00:5e:00:53:02','WiFi 5 GHz',1,2),('00:00:5e:00:53:04','WiFi 2,4 GHz (red 2)',1,2);
INSERT INTO meta VALUES ('ultimo','5');
""")
v1.executescript(N.ESQUEMA)
N.migrar(v1)
v1.execute("UPDATE equipos SET r_punto='WiFi 2,4 GHz (red 2)' WHERE mac='00:00:5e:00:53:04'")  # dato de la fase 2 antigua
N.meta(v1, "esquema", "1")
N.migrar(v1)
N.migrar(v1)  # repetir no cambia nada
e = {r["mac"]: dict(r) for r in v1.execute("SELECT * FROM equipos")}
assert e["00:00:5e:00:53:01"]["tipo"] == "red" and e["00:00:5e:00:53:02"]["tipo"] == "casa" and e["02:00:5e:00:53:05"]["tipo"] == "otro"
assert e["00:00:5e:00:53:02"]["boca"] == "wifi5" and e["00:00:5e:00:53:04"]["boca"] == "wifi24" and e["00:00:5e:00:53:03"]["boca"] == "LAN4"
assert e["00:00:5e:00:53:04"]["r_punto"] == "wifi24-2"
assert sorted(r["punto"] for r in v1.execute("SELECT punto FROM puntos")) == ["wifi24-2", "wifi5"]
assert all(x["auto"] == 0 for x in e.values()), "lo que ya existia no se mueve solo"
nodos = {r["id"]: dict(r) for r in v1.execute("SELECT * FROM nodos")}
assert set(nodos) == {"switch", "wifi-router"} and nodos["switch"]["padre"] == "00:00:5e:00:53:01" and nodos["wifi-router"]["boca"] == "wifi"
assert N.meta(v1, "router_mac") == "00:00:5e:00:53:01" and N.meta(v1, "ultimo") == "5"
ev = v1.execute("SELECT * FROM eventos").fetchone()
assert ev["clase"] == "nuevo" and json.loads(ev["datos"]) == {} and ev["texto"] == "", "el aviso antiguo pasa a datos"

# Avisos antiguos en español: se convierten a datos; lo que no se entiende se queda como estaba
casos = {("desconecta", "Desconexión tras 1 h 38 min"): {"dur": 5880}, ("desconecta", "Desconexión tras 2 d"): {"dur": 172800},
         ("desconecta", "Desconexión tras menos de 1 min"): {"dur": 30}, ("desconecta", "Desconexión"): {},
         ("ip", "Cambia de 192.168.0.120 a 192.168.0.11"): {"de": "192.168.0.120", "a": "192.168.0.11"},
         ("punto", "Pasa de LAN4 a WiFi 5 GHz"): {"de": "LAN4", "a": "wifi5"},
         ("mac", "En 192.168.0.22 antes la tenía 00:00:5e:00:53:07"): {"ip": "192.168.0.22", "otra": "00:00:5e:00:53:07"},
         ("conflicto", "En 192.168.0.4 responden dos equipos a la vez"): {"ip": "192.168.0.4"},
         ("conecta", "Conexión"): {}, ("raro", "algo"): None, ("ip", "texto inesperado"): None}
for (clase, texto), esperado in casos.items():
    assert N.datos_de_texto(clase, texto) == esperado, (clase, texto, N.datos_de_texto(clase, texto))
assert v1.execute("SELECT COUNT(*) n FROM equipos").fetchone()["n"] == 5

# Ruta por defecto leida de /proc/net/route (sin el comando ip): gana la de menor metrica
ruta = ("Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
        "eth1\t00000000\t0101A8C0\t0003\t0\t0\t200\t00000000\n"
        "eth0\t00000000\t0100A8C0\t0003\t0\t0\t100\t00000000\n"
        "eth0\t0000A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\n")
assert red.ruta_proc(ruta) == ("192.168.0.1", "eth0"), red.ruta_proc(ruta)
assert red.ruta_proc("Iface\tDestination\n") == (None, None)

# Bucles en el arbol
assert N.crea_bucle({"a": "b", "b": ""}, "b", "a") and not N.crea_bucle({"a": "b", "b": ""}, "c", "a")

# mDNS malformado no rompe ni se queda en bucle
for malo in (b"", b"\0" * 12, b"\0\0\0\0\0\1\0\0\0\0\0\0\xc0\x0c\0\1\0\1"):
    try:
        red.ptr_de(malo)
    except (IndexError, red.struct.error, UnicodeError):
        pass

# Version 4: bocas WiFi guardadas con su texto traducido vuelven a ser codigos; lo escrito a mano no se toca
v3 = memoria()
for mac, boca in (("02:00:00:00:01:01", "WiFi 5 GHz"), ("02:00:00:00:01:02", "WiFi 2.4 GHz (network 2)"),
                  ("02:00:00:00:01:03", "WiFi 2,4 GHz"), ("02:00:00:00:01:04", "WiFi del salon")):
    v3.execute("INSERT INTO equipos(mac,ip,medio,boca) VALUES(?,?,'wifi',?)", (mac, "10.0.0.2", boca))
N.meta(v3, "esquema", 3)
N.migrar(v3)
assert [r["boca"] for r in v3.execute("SELECT boca FROM equipos ORDER BY mac")] == ["wifi5", "wifi24-2", "wifi24", "WiFi del salon"]
assert N.meta(v3, "esquema") == "4"

# Olvido: sin identificar y sin verse 30 dias se borra; lo identificado, lo conectado y aquello de lo que algo cuelga, no
o = memoria()
ahora, viejo = 100_000_000, 100_000_000 - red.OLVIDO - 1
for mac, conocido, online, visto, padre in (("02:00:00:00:00:01", 0, 0, viejo, ""),         # se olvida
                                             ("02:00:00:00:00:02", 1, 0, viejo, ""),         # identificado
                                             ("02:00:00:00:00:03", 0, 1, viejo, ""),         # conectado
                                             ("02:00:00:00:00:04", 0, 0, ahora - 3600, ""),  # visto hace poco
                                             ("02:00:00:00:00:05", 0, 0, viejo, ""),         # cuelga algo de el
                                             ("02:00:00:00:00:06", 0, 0, viejo, "02:00:00:00:00:05"),  # se olvida
                                             ("02:00:00:00:00:07", 0, 0, viejo, ""),         # esta en una boca
                                             ("02:00:00:00:00:08", 0, 0, viejo, "internet")):  # el router
    o.execute("INSERT INTO equipos(mac,ip,conocido,online,visto_1,visto_n,padre) VALUES(?,?,?,?,?,?,?)",
              (mac, "10.0.0.1", conocido, online, visto, visto, padre))
    o.execute("INSERT INTO sesiones(mac,ip,inicio,fin,abierta) VALUES(?,?,?,?,0)", (mac, "10.0.0.1", visto, visto))
    N.evento(o, visto, "nuevo", mac, "10.0.0.1")
N.meta_json(o, "bocas", valor={"LAN2": "02:00:00:00:00:07"})
N.meta(o, "router_mac", "02:00:00:00:00:08")
assert red.olvidar(o, ahora) == 2
quedan = {r["mac"][-1] for r in o.execute("SELECT mac FROM equipos")}
assert quedan == set("234578"), quedan
assert {r["mac"][-1] for r in o.execute("SELECT DISTINCT mac FROM sesiones")} == quedan
assert o.execute("SELECT COUNT(*) n FROM eventos").fetchone()["n"] == 8  # los avisos se conservan
assert red.olvidar(o, ahora) == 1  # al dia siguiente, el 5: ya no cuelga nada de el
assert red.olvidar(o, ahora) == 0

# Que se escanea: la red entera si cabe; si es mayor que /22, el /22 de este equipo y el router; o lo de PANEL_RED
IF = ipaddress.IPv4Interface
assert red.objetivos(IF("192.168.1.5/24"), "192.168.1.1") == (["--localnet"], "192.168.1.0/24", False)
assert red.objetivos(None, "192.168.1.1") == (["--localnet"], "", False)
assert red.objetivos(IF("10.0.5.20/16"), "10.0.0.1") == (["10.0.4.0/22", "10.0.0.1"], "10.0.4.0/22, 10.0.0.1", True)
assert red.objetivos(IF("10.0.1.20/16"), "10.0.0.1") == (["10.0.0.0/22"], "10.0.0.0/22", True)  # el router ya esta dentro
assert red.objetivos(IF("10.0.5.20/16"), "10.0.0.1", "10.0.0.0/23, 10.0.8.0/24")[:2] == (["10.0.0.0/23", "10.0.8.0/24"], "10.0.0.0/23, 10.0.8.0/24")
for malo in ("10.0.0.0/16", "basura", " , "):
    try:
        red.objetivos(IF("10.0.5.20/16"), "10.0.0.1", malo)
        raise AssertionError(malo)
    except RuntimeError as ex:
        assert "PANEL_RED" in str(ex)

# DNS inverso: activo salvo PANEL_DNS=0
os.environ["PANEL_DNS"] = "0"
assert not red.dns_inverso_activo()
del os.environ["PANEL_DNS"]
assert red.dns_inverso_activo()

# Reloj hacia atras (Raspberry sin pila al ponerse en hora): una sesion nunca acaba antes de empezar
rl = memoria()
red.aplicar(rl, [("10.0.0.2", A, "")], 50_000)
red.aplicar(rl, [("10.0.0.2", A, "")], 49_000)
s = rl.execute("SELECT inicio, fin FROM sesiones WHERE mac=?", (A,)).fetchone()
assert s["fin"] >= s["inicio"], dict(s)

# Esperar a la hora: sin timesyncd no espera; con el reloj ya en hora tampoco; sin sincronizar, hasta el maximo
hora = tempfile.mkdtemp(dir=N.DATOS)
assert N.esperar_hora(os.path.join(hora, "no-existe"), 60)
inicio = time.monotonic()
N.log.disabled = True  # su aviso de "sigue sin hora" es lo esperado aqui
assert not N.esperar_hora(hora, 0.2) and time.monotonic() - inicio < 5
N.log.disabled = False
open(os.path.join(hora, "synchronized"), "w").close()
assert N.esperar_hora(hora, 60)
print("Pruebas correctas")
