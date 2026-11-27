"""Base de datos y utilidades comunes del panel de red."""
import json
import logging
import os
import re
import sqlite3
from contextlib import closing

DATOS = (os.environ.get("PANEL_DATOS") or os.environ.get("STATE_DIRECTORY")
         or os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos"))
BD = os.path.join(DATOS, "red.db")
RETENCION = 400 * 86400
MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
TIPOS = ["red", "servidor", "ordenador", "movil", "casa", "otro"]
MEDIOS = ["cable", "wifi", "virtual", ""]
VERSION_ESQUEMA = 3

log = logging.getLogger("panel")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS equipos(
  mac TEXT PRIMARY KEY, ip TEXT, fabricante TEXT DEFAULT '', host TEXT DEFAULT '', nombre TEXT DEFAULT '',
  tipo TEXT DEFAULT 'otro', padre TEXT DEFAULT '', medio TEXT DEFAULT '', boca TEXT DEFAULT '',
  visto_1 INTEGER, visto_n INTEGER, online INTEGER DEFAULT 0, conocido INTEGER DEFAULT 0, host_ts INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS sesiones(
  id INTEGER PRIMARY KEY, mac TEXT, ip TEXT, inicio INTEGER, fin INTEGER, abierta INTEGER DEFAULT 1);
CREATE INDEX IF NOT EXISTS s_mac ON sesiones(mac, inicio);
CREATE INDEX IF NOT EXISTS s_fin ON sesiones(fin);
CREATE TABLE IF NOT EXISTS eventos(
  id INTEGER PRIMARY KEY, ts INTEGER, clase TEXT, mac TEXT, ip TEXT, texto TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS e_ts ON eventos(ts);
CREATE TABLE IF NOT EXISTS trafico(
  minuto INTEGER PRIMARY KEY, bytes INTEGER, segundos REAL, pico REAL);
CREATE TABLE IF NOT EXISTS puntos(
  id INTEGER PRIMARY KEY, mac TEXT, punto TEXT, inicio INTEGER, fin INTEGER, abierta INTEGER DEFAULT 1);
CREATE INDEX IF NOT EXISTS p_mac ON puntos(mac, inicio);
CREATE INDEX IF NOT EXISTS p_fin ON puntos(fin);
CREATE TABLE IF NOT EXISTS nodos(
  id TEXT PRIMARY KEY, nombre TEXT, padre TEXT DEFAULT '', boca TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS web_sesiones(token TEXT PRIMARY KEY, caduca INTEGER);
CREATE TABLE IF NOT EXISTS meta(clave TEXT PRIMARY KEY, valor TEXT);
"""

# Columnas añadidas despues de la primera version: se crean al arrancar sobre una base antigua.
COLUMNAS = {
    # auto=1: el panel coloca el equipo en el mapa. Los que ya existian al migrar se quedan donde estan (0).
    "equipos": {"auto": "INTEGER DEFAULT 0", "r_punto": "TEXT DEFAULT ''", "r_rssi": "INTEGER", "r_tx": "INTEGER",
                "r_rx": "INTEGER", "r_modo": "TEXT DEFAULT ''", "r_ts": "INTEGER DEFAULT 0"},
    "eventos": {"datos": "TEXT"},
}
TIPOS_ANTIGUOS = {"Red": "red", "Servidor": "servidor", "Ordenador": "ordenador", "Móvil": "movil",
                  "Casa": "casa", "Sin clasificar": "otro"}
NODOS_ANTIGUOS = {"switch": ("Switch", ""), "wifi-router": ("WiFi del router", "wifi")}


def abrir():
    c = sqlite3.connect(BD, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA synchronous=NORMAL")  # con WAL es seguro y escribe mucho menos en disco (tarjetas SD)
    return c


def meta(c, clave, valor=None):
    if valor is None:
        r = c.execute("SELECT valor FROM meta WHERE clave=?", (clave,)).fetchone()
        return r["valor"] if r else None
    c.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (clave, str(valor)))


def meta_json(c, clave, defecto=None, valor=...):
    if valor is ...:
        try:
            v = meta(c, clave)
            return json.loads(v) if v else defecto
        except ValueError:
            return defecto
    meta(c, clave, json.dumps(valor, ensure_ascii=False))


def evento(c, ts, clase, mac, ip, datos=None):
    """Los textos se componen en la web, en el idioma de quien mira: aqui solo se guardan datos."""
    c.execute("INSERT INTO eventos(ts,clase,mac,ip,texto,datos) VALUES(?,?,?,?,'',?)",
              (ts, clase, mac, ip, json.dumps(datos) if datos else None))


def mac_privada(mac):
    return bool(int(mac[:2], 16) & 2)


def punto_codigo(v):
    """Convierte los nombres de punto de la primera version ('WiFi 5 GHz (red 2)') en codigos ('wifi5-2')."""
    m = re.fullmatch(r"WiFi (5|2,4) GHz(?: \(red (\d)\))?", v or "")
    if not m:
        return v
    return ("wifi5" if m.group(1) == "5" else "wifi24") + (f"-{m.group(2)}" if m.group(2) else "")


def datos_de_texto(clase, texto):
    """Datos de un aviso de la primera version, que se guardaba como frase en español. None si no se entiende."""
    t = (texto or "").strip()
    if clase in ("nuevo", "conecta"):
        return {}
    if clase == "desconecta":
        if t.endswith("menos de 1 min"):
            return {"dur": 30}
        m = re.fullmatch(r"Desconexión(?: tras(?: (\d+) d)?(?: (\d+) h)?(?: (\d+) min)?)?", t)
        if not m:
            return None
        d, h, mi = (int(x or 0) for x in m.groups())
        return {"dur": d * 86400 + h * 3600 + mi * 60} if any(m.groups()) else {}
    patrones = {
        "ip": (r"Cambia de (\S+) a (\S+)", ("de", "a")),
        "punto": (r"Pasa de (.+) a (.+)", ("de", "a")),
        "mac": (r"En (\S+) antes la tenía (\S+)", ("ip", "otra")),
        "conflicto": (r"En (\S+) responden dos equipos a la vez", ("ip",)),
    }
    if clase not in patrones:
        return None
    m = re.fullmatch(patrones[clase][0], t)
    if not m:
        return None
    d = dict(zip(patrones[clase][1], m.groups()))
    if clase == "punto":
        d = {k: punto_codigo(v) for k, v in d.items()}
    return d


def columnas(c, tabla):
    return {r["name"] for r in c.execute(f"PRAGMA table_info({tabla})")}


def migrar(c):
    """Pone al dia una base de datos de una version anterior sin perder nada."""
    for tabla, cols in COLUMNAS.items():
        hay = columnas(c, tabla)
        for col, tipo in cols.items():
            if col not in hay:
                c.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}")
    try:
        version = int(meta(c, "esquema") or 1)
    except ValueError:
        version = 1
    if version < 2:
        migrar_v2(c)
    if version < 3:
        # Avisos antiguos: de frase en español a datos, para que se lean en cualquier idioma.
        for e in c.execute("SELECT id, clase, texto FROM eventos WHERE datos IS NULL").fetchall():
            d = datos_de_texto(e["clase"], e["texto"])
            if d is not None:
                c.execute("UPDATE eventos SET datos=?, texto='' WHERE id=?", (json.dumps(d), e["id"]))
    meta(c, "esquema", VERSION_ESQUEMA)


def migrar_v2(c):
    for viejo, nuevo in TIPOS_ANTIGUOS.items():
        c.execute("UPDATE equipos SET tipo=? WHERE tipo=?", (nuevo, viejo))
    c.execute(f"UPDATE equipos SET tipo='otro' WHERE tipo NOT IN ({','.join('?' * len(TIPOS))})", TIPOS)
    for tabla, col in (("puntos", "punto"), ("equipos", "r_punto")):
        for (v,) in c.execute(f"SELECT DISTINCT {col} FROM {tabla}").fetchall():
            if punto_codigo(v) != v:
                c.execute(f"UPDATE {tabla} SET {col}=? WHERE {col}=?", (punto_codigo(v), v))
    for viejo, nuevo in (("5 GHz", "wifi5"), ("2,4 GHz", "wifi24")):
        c.execute("UPDATE equipos SET boca=? WHERE boca=? AND medio='wifi'", (nuevo, viejo))
    router = meta(c, "router_mac")
    if not router:
        r = c.execute("SELECT mac FROM equipos WHERE padre='internet' LIMIT 1").fetchone()
        if r:
            router = r["mac"]
            meta(c, "router_mac", router)
    nodos = {r["id"] for r in c.execute("SELECT id FROM nodos")}
    for (p,) in c.execute("SELECT DISTINCT padre FROM equipos").fetchall():
        if p and p != "internet" and not MAC_RE.match(p) and p not in nodos:
            nombre, boca = NODOS_ANTIGUOS.get(p, (p, ""))
            c.execute("INSERT INTO nodos(id,nombre,padre,boca) VALUES(?,?,?,?)", (p, nombre, router or "", boca))


def preparar():
    os.makedirs(DATOS, mode=0o750, exist_ok=True)
    with closing(abrir()) as c, c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(ESQUEMA)
        migrar(c)
    for sufijo in ("", "-wal", "-shm"):
        try:
            os.chmod(BD + sufijo, 0o600)
        except OSError:
            pass


def padres_validos(c):
    """Todo aquello de lo que puede colgar algo: equipos, nodos sin IP e internet. {id: su padre}."""
    p = {r["mac"]: r["padre"] for r in c.execute("SELECT mac, padre FROM equipos")}
    p.update({r["id"]: r["padre"] for r in c.execute("SELECT id, padre FROM nodos")})
    p["internet"] = ""
    return p


def crea_bucle(padres, hijo, padre):
    p, pasos = padre, 0
    while p:
        if p == hijo or pasos > 200:
            return True
        p, pasos = padres.get(p, ""), pasos + 1
    return False
