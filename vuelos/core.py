"""Lógica común a la línea de comandos y al servidor MCP."""
import sqlite3
from datetime import date

from . import db
from .api import ErrorApi, Vuelo
from .config import Config


def meses_siguientes(n: int, hoy: date | None = None) -> list[str]:
    hoy = hoy or date.today()
    a, m = hoy.year, hoy.month
    salida = []
    for _ in range(n):
        salida.append(f"{a:04d}-{m:02d}")
        m += 1
        if m > 12:
            a, m = a + 1, 1
    return salida


def minimo_por_dia(vuelos: list[Vuelo]) -> list[Vuelo]:
    mejor: dict[tuple, Vuelo] = {}
    for v in vuelos:
        k = (v.salida, v.regreso)
        if k not in mejor or v.precio < mejor[k].precio:
            mejor[k] = v
    return sorted(mejor.values(), key=lambda v: (v.salida, v.precio))


def buscar(cliente, origen: str, destino: str, salida: str, regreso: str | None = None,
           solo_ida: bool = True, directo: bool = False, max_escalas: int | None = None,
           aerolinea: str | None = None, precio_max: float | None = None,
           limite: int = 20, guardar_en: sqlite3.Connection | None = None) -> list[Vuelo]:
    """Busca y filtra. Si se pasa conexión, la búsqueda también alimenta el histórico."""
    vuelos = cliente.precios_por_fechas(origen.upper(), destino.upper(), salida, regreso,
                                        solo_ida=solo_ida, directo=directo)
    if guardar_en is not None and vuelos:
        db.guardar(guardar_en, date.today().isoformat(), origen.upper(), destino.upper(),
                   minimo_por_dia(vuelos), cliente.moneda)
    if max_escalas is not None:
        vuelos = [v for v in vuelos if v.escalas <= max_escalas]
    if aerolinea:
        vuelos = [v for v in vuelos if v.aerolinea.upper() == aerolinea.upper()]
    if precio_max is not None:
        vuelos = [v for v in vuelos if v.precio <= precio_max]
    return sorted(vuelos, key=lambda v: v.precio)[:limite]


def recoger(cliente, cfg: Config, con: sqlite3.Connection, aviso=print) -> dict:
    """Recogida diaria: todos los orígenes × destinos × meses configurados."""
    hoy = date.today().isoformat()
    resumen = {"filas": 0, "consultas": 0, "errores": []}
    for o in cfg.origenes:
        for d in cfg.destinos:
            for mes in meses_siguientes(cfg.meses_adelante):
                resumen["consultas"] += 1
                try:
                    vuelos = cliente.precios_por_fechas(o, d, mes, solo_ida=cfg.solo_ida)
                except ErrorApi as e:
                    resumen["errores"].append(f"{o}-{d} {mes}: {e}")
                    continue
                resumen["filas"] += db.guardar(con, hoy, o, d, minimo_por_dia(vuelos), cfg.moneda)
            aviso(f"  {o}-{d} ok")
    resumen["fichero"] = db.exportar_dia(con, hoy, cfg.carpeta_datos)
    return resumen


def historico(con: sqlite3.Connection, origen: str, destino: str,
              mes_salida: str | None = None) -> dict:
    """Evolución del precio mínimo por día de observación y mínimos por mes de salida."""
    filtro, args = "origen=? AND destino=?", [origen.upper(), destino.upper()]
    if mes_salida:
        filtro += " AND salida LIKE ?"
        args.append(f"{mes_salida}%")
    evolucion = con.execute(
        f"""SELECT observado, MIN(precio) AS minimo, ROUND(AVG(precio),2) AS medio, COUNT(*) AS dias
            FROM observaciones WHERE {filtro} GROUP BY observado ORDER BY observado""", args
    ).fetchall()
    por_mes = con.execute(
        f"""SELECT substr(salida,1,7) AS mes, MIN(precio) AS minimo, ROUND(AVG(precio),2) AS medio
            FROM observaciones WHERE {filtro} AND observado=(SELECT MAX(observado)
              FROM observaciones WHERE {filtro})
            GROUP BY mes ORDER BY mes""", args + args
    ).fetchall()
    record = con.execute(
        f"""SELECT * FROM observaciones WHERE {filtro} ORDER BY precio, observado LIMIT 1""", args
    ).fetchone()
    return {
        "ruta": f"{origen.upper()}-{destino.upper()}",
        "evolucion": [dict(r) for r in evolucion],
        "por_mes_salida": [dict(r) for r in por_mes],
        "minimo_historico": dict(record) if record else None,
    }


def ofertas(con: sqlite3.Connection, origen: str | None = None, destino: str | None = None,
            limite: int = 15) -> list[dict]:
    """Mejores precios de la última recogida, con comparación frente a la media histórica."""
    filtro, args = "o.observado=(SELECT MAX(observado) FROM observaciones)", []
    if origen:
        filtro += " AND o.origen=?"; args.append(origen.upper())
    if destino:
        filtro += " AND o.destino=?"; args.append(destino.upper())
    filas = con.execute(
        f"""SELECT o.origen, o.destino, o.salida, o.precio, o.aerolinea, o.escalas, o.enlace,
                   (SELECT ROUND(AVG(precio),2) FROM observaciones h
                    WHERE h.origen=o.origen AND h.destino=o.destino) AS media_ruta
            FROM observaciones o WHERE {filtro} AND o.salida >= date('now')
            ORDER BY o.precio LIMIT ?""", args + [limite]
    ).fetchall()
    return [dict(f) for f in filas]


def _minimos(con: sqlite3.Connection, observado: str | None) -> dict:
    if not observado:
        return {}
    filas = con.execute(
        """SELECT origen, destino, MIN(precio) AS precio, salida, aerolinea, enlace
           FROM observaciones WHERE observado=? AND salida >= ?
           GROUP BY origen, destino""", (observado, observado)
    ).fetchall()
    return {(f["origen"], f["destino"]): dict(f) for f in filas}


def alertas(con: sqlite3.Connection, cfg: Config, nuevas: bool = False) -> list[dict]:
    """Rutas cuyo mínimo de la última recogida está por debajo del umbral configurado.

    nuevas=True: solo las que acaban de bajar del umbral o han bajado aún más respecto a la
    recogida anterior (evita repetir la misma alerta cada día)."""
    dias = [r[0] for r in con.execute(
        "SELECT DISTINCT observado FROM observaciones ORDER BY observado DESC LIMIT 2")]
    hoy = _minimos(con, dias[0] if dias else None)
    antes = _minimos(con, dias[1] if len(dias) > 1 else None)
    out = []
    for clave, f in hoy.items():
        u = cfg.umbral(*clave)
        if u is None or f["precio"] > u:
            continue
        previo = antes.get(clave, {}).get("precio")
        if nuevas and previo is not None and previo <= u and f["precio"] >= previo:
            continue
        out.append({**f, "umbral": u, "anterior": previo})
    return sorted(out, key=lambda x: x["precio"] / x["umbral"])


def alertas_markdown(lista: list[dict], moneda: str) -> str:
    if not lista:
        return ""
    m = moneda.upper()
    lineas = ["| Ruta | Precio | Umbral | Antes | Salida | Aerolínea | |", "|---|---:|---:|---:|---|---|---|"]
    for a in lista:
        antes = f"{a['anterior']:.0f} {m}" if a["anterior"] is not None else "–"
        enlace = f"[ver]({a['enlace']})" if a["enlace"] else ""
        lineas.append(f"| {a['origen']}-{a['destino']} | **{a['precio']:.0f} {m}** | {a['umbral']:.0f} {m} "
                      f"| {antes} | {a['salida']} | {a['aerolinea'] or ''} | {enlace} |")
    lineas.append("\nPrecios de la caché de Aviasales: confírmalos antes de comprar.")
    return "\n".join(lineas)
