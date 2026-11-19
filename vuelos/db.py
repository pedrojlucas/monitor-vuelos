"""Histórico local en SQLite: una fila por (día de observación, ruta, salida, regreso)."""
import csv
import gzip
import hashlib
import io
import sqlite3
from pathlib import Path

ESQUEMA = """
CREATE TABLE IF NOT EXISTS observaciones (
    observado   TEXT NOT NULL,          -- fecha en que se vio el precio (YYYY-MM-DD)
    origen      TEXT NOT NULL,          -- origen consultado (MAD, ALC, RMU...)
    destino     TEXT NOT NULL,          -- destino consultado (LON, IAH...)
    salida      TEXT NOT NULL,          -- fecha de salida del vuelo
    regreso     TEXT NOT NULL DEFAULT '',
    precio      REAL NOT NULL,
    moneda      TEXT NOT NULL,
    aerolinea   TEXT,
    escalas     INTEGER,
    aeropuertos TEXT,                   -- p.ej. "MAD-LGW"
    enlace      TEXT,
    fuente      TEXT NOT NULL DEFAULT 'travelpayouts',  -- travelpayouts | google | google-historial
    PRIMARY KEY (observado, origen, destino, salida, regreso)
);
CREATE INDEX IF NOT EXISTS ix_ruta ON observaciones(origen, destino, salida);
"""

COLUMNAS = ["observado", "origen", "destino", "salida", "regreso", "precio", "moneda",
            "aerolinea", "escalas", "aeropuertos", "enlace", "fuente"]

_UPSERT = f"""INSERT INTO observaciones ({','.join(COLUMNAS)}) VALUES ({','.join('?' * len(COLUMNAS))})
   ON CONFLICT DO UPDATE SET precio=excluded.precio, aerolinea=excluded.aerolinea,
     escalas=excluded.escalas, aeropuertos=excluded.aeropuertos, enlace=excluded.enlace,
     fuente=excluded.fuente
   WHERE excluded.precio < observaciones.precio"""


def conectar(ruta: Path) -> sqlite3.Connection:
    con = sqlite3.connect(ruta)
    con.row_factory = sqlite3.Row
    con.executescript(ESQUEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(observaciones)")}
    if "fuente" not in cols:   # bases creadas con la versión anterior
        con.execute("ALTER TABLE observaciones ADD COLUMN fuente TEXT NOT NULL DEFAULT 'travelpayouts'")
    cols_imp = {r[1] for r in con.execute("PRAGMA table_info(importados)")}
    if cols_imp and "huella" not in cols_imp:
        con.execute("DROP TABLE importados")   # se reimporta todo; la inserción es idempotente
    con.execute("CREATE TABLE IF NOT EXISTS importados (fichero TEXT PRIMARY KEY, huella TEXT)")
    con.commit()
    return con


def insertar(con: sqlite3.Connection, filas) -> int:
    """filas: tuplas en el orden de COLUMNAS."""
    filas = list(filas)
    con.executemany(_UPSERT, filas)
    con.commit()
    return len(filas)


def guardar(con: sqlite3.Connection, observado: str, origen: str, destino: str,
            vuelos, moneda: str, fuente: str = "travelpayouts") -> int:
    """Guarda vuelos; si ya hay dato para ese día/salida/regreso, conserva el más barato."""
    return insertar(con, (
        (observado, origen, destino, v.salida, v.regreso or "", v.precio, moneda,
         v.aerolinea, v.escalas, f"{v.origen}-{v.destino}", v.url, fuente) for v in vuelos))


# --- Ficheros CSV diarios: formato versionable en git (la base SQLite se reconstruye a partir de ellos)

def fichero_dia(carpeta: Path, observado: str) -> Path:
    return Path(carpeta) / observado[:4] / f"{observado}.csv.gz"


def _huella(ruta: Path) -> str:
    return hashlib.sha1(ruta.read_bytes()).hexdigest()


def exportar_dia(con: sqlite3.Connection, observado: str, carpeta: Path) -> Path | None:
    """Escribe (o reescribe) el CSV del día con todo lo que hay en la base para esa fecha."""
    filas = con.execute(f"SELECT {','.join(COLUMNAS)} FROM observaciones WHERE observado=? "
                        "ORDER BY origen, destino, salida, regreso", (observado,)).fetchall()
    if not filas:
        return None
    ruta = fichero_dia(carpeta, observado)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 para que el mismo contenido produzca el mismo fichero (diffs limpios en git)
    with open(ruta, "wb") as bruto, gzip.GzipFile(fileobj=bruto, mode="wb", mtime=0) as gz, \
            io.TextIOWrapper(gz, encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNAS)
        w.writerows(tuple(r) for r in filas)
    con.execute("INSERT OR REPLACE INTO importados VALUES (?, ?)", (ruta.name, _huella(ruta)))
    con.commit()
    return ruta


def sincronizar(con: sqlite3.Connection, carpeta: Path) -> int:
    """Importa los CSV diarios nuevos o modificados. Devuelve cuántos ficheros importó."""
    carpeta = Path(carpeta)
    if not carpeta.exists():
        return 0
    hechos = dict(con.execute("SELECT fichero, huella FROM importados").fetchall())
    n = 0
    for ruta in sorted(carpeta.glob("*/*.csv.gz")):
        huella = _huella(ruta)
        if hechos.get(ruta.name) == huella:
            continue
        with gzip.open(ruta, "rt", encoding="utf-8", newline="") as f:
            filas = []
            for d in csv.DictReader(f):   # por nombre: admite CSV antiguos sin columna "fuente"
                filas.append((d["observado"], d["origen"], d["destino"], d["salida"], d.get("regreso", ""),
                              float(d["precio"]), d["moneda"], d.get("aerolinea") or None,
                              int(d["escalas"]) if d.get("escalas") else None, d.get("aeropuertos") or None,
                              d.get("enlace") or None, d.get("fuente") or "travelpayouts"))
            con.executemany(_UPSERT, filas)
        con.execute("INSERT OR REPLACE INTO importados VALUES (?, ?)", (ruta.name, huella))
        n += 1
    con.commit()
    return n
