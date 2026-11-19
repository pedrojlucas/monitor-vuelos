"""Histórico local en SQLite: una fila por (día de observación, ruta, día de salida)."""
import csv
import gzip
import io
import sqlite3
from pathlib import Path

ESQUEMA = """
CREATE TABLE IF NOT EXISTS observaciones (
    observado   TEXT NOT NULL,          -- fecha en que se recogió el precio (YYYY-MM-DD)
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
    PRIMARY KEY (observado, origen, destino, salida, regreso)
);
CREATE INDEX IF NOT EXISTS ix_ruta ON observaciones(origen, destino, salida);
CREATE TABLE IF NOT EXISTS importados (fichero TEXT PRIMARY KEY);
"""

COLUMNAS = ["observado", "origen", "destino", "salida", "regreso", "precio", "moneda",
            "aerolinea", "escalas", "aeropuertos", "enlace"]

_UPSERT = """INSERT INTO observaciones VALUES (?,?,?,?,?,?,?,?,?,?,?)
   ON CONFLICT DO UPDATE SET precio=excluded.precio, aerolinea=excluded.aerolinea,
     escalas=excluded.escalas, aeropuertos=excluded.aeropuertos, enlace=excluded.enlace
   WHERE excluded.precio < observaciones.precio"""


def conectar(ruta: Path) -> sqlite3.Connection:
    con = sqlite3.connect(ruta)
    con.row_factory = sqlite3.Row
    con.executescript(ESQUEMA)
    return con


def guardar(con: sqlite3.Connection, observado: str, origen: str, destino: str,
            vuelos, moneda: str) -> int:
    """Guarda el mínimo por día de salida; si ya hay dato ese día, conserva el más barato."""
    filas = 0
    for v in vuelos:
        con.execute(_UPSERT, (observado, origen, destino, v.salida, v.regreso or "", v.precio,
                              moneda, v.aerolinea, v.escalas, f"{v.origen}-{v.destino}", v.url))
        filas += 1
    con.commit()
    return filas


# --- Ficheros CSV diarios: formato versionable en git (la base SQLite se reconstruye a partir de ellos)

def fichero_dia(carpeta: Path, observado: str) -> Path:
    return Path(carpeta) / observado[:4] / f"{observado}.csv.gz"


def exportar_dia(con: sqlite3.Connection, observado: str, carpeta: Path) -> Path | None:
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
    con.execute("INSERT OR IGNORE INTO importados VALUES (?)", (ruta.name,))
    con.commit()
    return ruta


def sincronizar(con: sqlite3.Connection, carpeta: Path) -> int:
    """Importa los CSV diarios que aún no estén en la base. Devuelve cuántos ficheros importó."""
    carpeta = Path(carpeta)
    if not carpeta.exists():
        return 0
    hechos = {r[0] for r in con.execute("SELECT fichero FROM importados")}
    n = 0
    for ruta in sorted(carpeta.glob("*/*.csv.gz")):
        if ruta.name in hechos:
            continue
        with gzip.open(ruta, "rt", encoding="utf-8", newline="") as f:
            r = csv.reader(f)
            next(r, None)
            con.executemany(_UPSERT, (
                (o, a, b, s, rg, float(p), m, al, int(e) if e else None, ae, en or None)
                for o, a, b, s, rg, p, m, al, e, ae, en in r))
        con.execute("INSERT INTO importados VALUES (?)", (ruta.name,))
        n += 1
    con.commit()
    return n
