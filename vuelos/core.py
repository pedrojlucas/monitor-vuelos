"""Lógica común a la línea de comandos y al servidor MCP."""
import sqlite3
from datetime import date, timedelta

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
    resumen["dias"] = {hoy}
    return resumen


def fechas_google(cfg: Config, origen: str, hoy: date | None = None) -> list[tuple[str, str | None]]:
    """(salida, regreso) a consultar para un origen: fechas fijas + N fechas a X días vista."""
    hoy = hoy or date.today()
    g = cfg.google
    salidas = [f for f in g.fechas_salida if f > hoy.isoformat()]
    salidas += [(hoy + timedelta(days=d)).isoformat()
                for d in g.dias_antelacion[:g.fechas_por_origen.get(origen, 0)]]
    out = []
    for s in dict.fromkeys(salidas):
        regreso = ((date.fromisoformat(s) + timedelta(days=g.dias_estancia)).isoformat()
                   if g.dias_estancia else None)
        out.append((s, regreso))
    return out


def recoger_google(cliente, cfg: Config, con: sqlite3.Connection, aviso=print,
                   forzar: bool = False) -> dict:
    """Rutas transatlánticas con Google Flights (SerpApi), con control de frecuencia y de cuota."""
    hoy = date.today()
    resumen = {"busquedas": 0, "filas": 0, "historial": 0, "errores": [], "omitido": None,
               "dias": set(), "insights": []}
    g = cfg.google
    if not g.destinos or not g.fechas_por_origen:
        resumen["omitido"] = "no hay rutas [google] en rutas.toml"
        return resumen
    ultima = con.execute("SELECT MAX(observado) FROM observaciones WHERE fuente='google'").fetchone()[0]
    if not forzar and ultima and (hoy - date.fromisoformat(ultima)).days < g.frecuencia_dias:
        resumen["omitido"] = f"última consulta el {ultima}; se consulta cada {g.frecuencia_dias} días"
        return resumen

    plan = [(o, d, s, r) for o in g.origenes for d in g.destinos for s, r in fechas_google(cfg, o, hoy)]
    try:
        restantes = cliente.busquedas_restantes()
    except ErrorApi as e:
        resumen["errores"].append(f"cuenta: {e}")
        return resumen
    if restantes is not None:
        disponibles = max(0, restantes - g.reserva_busquedas)
        if disponibles < len(plan):   # el orden de fechas_por_origen marca la prioridad
            aviso(f"  cuota: quedan {restantes} búsquedas; se hacen {disponibles} de {len(plan)}")
            plan = plan[:disponibles]
    resumen["restantes_antes"] = restantes

    for o, d, s, r in plan:
        resumen["busquedas"] += 1
        try:
            res = cliente.buscar(o, d, s, r, max_escalas=g.max_escalas)
        except ErrorApi as e:
            resumen["errores"].append(f"{o}-{d} {s}: {e}")
            continue
        if res.vuelos:
            resumen["filas"] += db.guardar(con, hoy.isoformat(), o, d, res.vuelos[:1], cfg.moneda, "google")
            resumen["dias"].add(hoy.isoformat())
        # el historial de Google rellena hacia atrás el precio de esta misma búsqueda
        filas_h = [(f, o, d, s, r or "", p, cfg.moneda, None, None, None, res.url, "google-historial")
                   for f, p in res.historial if f < hoy.isoformat()]
        resumen["historial"] += db.insertar(con, filas_h)
        resumen["dias"].update(f for f, *_ in filas_h)
        mejor = res.vuelos[0] if res.vuelos else None
        resumen["insights"].append({
            "ruta": f"{o}-{d}", "salida": s, "regreso": r, "precio": mejor.precio if mejor else None,
            "aerolinea": mejor.aerolinea if mejor else None, "escalas": mejor.escalas if mejor else None,
            "nivel": res.nivel, "rango_tipico": res.rango_tipico, "url": res.url})
        aviso(f"  {o}-{d} {s}{'→' + r if r else ''}: "
              f"{mejor.precio if mejor else '-'} {cfg.moneda.upper()} ({res.nivel or 'sin nivel'})")
    return resumen


def exportar(con: sqlite3.Connection, cfg: Config, dias) -> list:
    return [p for p in (db.exportar_dia(con, d, cfg.carpeta_datos) for d in sorted(dias)) if p]


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
    filtro = """o.observado=(SELECT MAX(observado) FROM observaciones u
                WHERE u.origen=o.origen AND u.destino=o.destino AND u.fuente!='google-historial')"""
    args = []
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
    ultimo = con.execute("SELECT MAX(observado) FROM observaciones").fetchone()[0]
    hoy = _minimos(con, ultimo)
    out = []
    for clave, f in hoy.items():
        u = cfg.umbral(*clave)
        if u is None or f["precio"] > u:
            continue
        # observación anterior de ESTA ruta (las rutas de Google no se consultan cada día)
        dia_previo = con.execute(
            "SELECT MAX(observado) FROM observaciones WHERE origen=? AND destino=? AND observado<?",
            (*clave, ultimo)).fetchone()[0]
        previo = _minimos(con, dia_previo).get(clave, {}).get("precio")
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
