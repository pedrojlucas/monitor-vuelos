"""Viajes concretos: consulta diaria de fechas exactas y resumen (docs/viajes.json) para la rutina."""
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from . import db
from .api import ErrorApi
from .config import Config, Viaje


def _clave(viaje: Viaje, origen: str, salida: str, regreso: str | None) -> str:
    return f"{origen}-{viaje.destino} {salida}→{regreso or ''}"


def pendientes(cfg: Config, con: sqlite3.Connection, hoy: date) -> list[tuple]:
    """(viaje, origen, salida, regreso) aún futuros y sin consultar hoy."""
    out = []
    for v in cfg.viajes:
        for o in v.origenes:
            for s, r in v.fechas:
                if s <= hoy.isoformat():
                    continue
                ya = con.execute(
                    """SELECT 1 FROM observaciones WHERE observado=? AND origen=? AND destino=?
                       AND salida=? AND regreso=? AND fuente='google-viaje'""",
                    (hoy.isoformat(), o, v.destino, s, r or "")).fetchone()
                if not ya:
                    out.append((v, o, s, r))
    return out


def recoger(cliente, cfg: Config, con: sqlite3.Connection, aviso=print) -> dict:
    hoy = date.today()
    res = {"busquedas": 0, "errores": [], "dias": set(), "consultas": {}, "omitido": None}
    plan = pendientes(cfg, con, hoy)
    if not plan:
        res["omitido"] = "nada pendiente hoy" if cfg.viajes else "no hay [[viajes]] en rutas.toml"
        return res
    try:
        restantes = cliente.busquedas_restantes()
    except ErrorApi as e:
        res["errores"].append(f"cuenta: {e}")
        return res
    if restantes is not None and restantes - cfg.google.reserva_busquedas < len(plan):
        n = max(0, restantes - cfg.google.reserva_busquedas)
        aviso(f"  cuota: quedan {restantes}; se hacen {n} de {len(plan)}")
        plan = plan[:n]
    for v, o, s, r in plan:
        res["busquedas"] += 1
        try:
            g = cliente.buscar(o, v.destino, s, r, max_escalas=v.max_escalas)
        except ErrorApi as e:
            res["errores"].append(f"{o}-{v.destino} {s}: {e}")
            continue
        if g.vuelos:
            db.guardar(con, hoy.isoformat(), o, v.destino, g.vuelos[:1], cfg.moneda, "google-viaje")
            res["dias"].add(hoy.isoformat())
        filas_h = [(f, o, v.destino, s, r or "", p, cfg.moneda, None, None, None, g.url, "google-historial")
                   for f, p in g.historial if f < hoy.isoformat()]
        db.insertar(con, filas_h)
        res["dias"].update(f for f, *_ in filas_h)
        res["consultas"][_clave(v, o, s, r)] = {
            "nivel": g.nivel, "rango_tipico": list(g.rango_tipico) if g.rango_tipico else None,
            "opciones": [{"precio": x.precio, "aerolinea": x.aerolinea, "escalas": x.escalas,
                          "duracion_min": x.duracion_min, "aeropuertos": f"{x.origen}-{x.destino}"}
                         for x in g.vuelos[:3]],
            "url": g.url}
        mejor = g.vuelos[0] if g.vuelos else None
        aviso(f"  {v.nombre}: {o} {s}→{r}: {mejor.precio if mejor else '-'} {cfg.moneda.upper()} "
              f"({g.nivel or 'sin nivel'})")
    return res


def resumen(cfg: Config, con: sqlite3.Connection, consultas: dict, previo: dict | None = None) -> dict:
    """Estado de cada viaje. Conserva nivel/opciones del resumen anterior si hoy no se consultó."""
    previo = previo or {}
    hoy = date.today()
    hace7 = (hoy - timedelta(days=7)).isoformat()
    out = {"generado": hoy.isoformat(), "moneda": cfg.moneda.upper(),
           "nota": "Precios de Google Flights (vía SerpApi), ida y vuelta, 1 adulto, turista, SIN maleta "
                   "facturada. 'evolucion' incluye el historial que da Google para esas mismas fechas.",
           "viajes": []}
    prev_viajes = {v["nombre"]: v for v in previo.get("viajes", [])}
    for v in cfg.viajes:
        prev_opc = {o["clave"]: o for o in prev_viajes.get(v.nombre, {}).get("opciones", [])}
        opciones = []
        for o in v.origenes:
            for s, r in v.fechas:
                k = _clave(v, o, s, r)
                serie = [dict(x) for x in con.execute(
                    """SELECT observado AS fecha, MIN(precio) AS precio FROM observaciones
                       WHERE origen=? AND destino=? AND salida=? AND regreso=?
                       GROUP BY observado ORDER BY observado""", (o, v.destino, s, r or ""))]
                ultimo = serie[-1] if serie else None
                anterior7 = next((x for x in reversed(serie) if x["fecha"] <= hace7), None)
                nuevo = consultas.get(k) or {}
                viejo = prev_opc.get(k, {})
                opciones.append({
                    "clave": k, "origen": o, "destino": v.destino, "salida": s, "regreso": r,
                    "precio_actual": ultimo["precio"] if ultimo else None,
                    "visto_el": ultimo["fecha"] if ultimo else None,
                    "precio_hace_7_dias": anterior7["precio"] if anterior7 else None,
                    "minimo_visto": min((x["precio"] for x in serie), default=None),
                    "nivel_google": nuevo.get("nivel", viejo.get("nivel_google")),
                    "rango_tipico": nuevo.get("rango_tipico", viejo.get("rango_tipico")),
                    "mejores_opciones": nuevo.get("opciones", viejo.get("mejores_opciones", [])),
                    "url_google_flights": nuevo.get("url", viejo.get("url_google_flights")),
                    "evolucion": serie[-60:],
                    "terminado": s <= hoy.isoformat(),
                })
        validas = [x for x in opciones if x["precio_actual"] is not None and not x["terminado"]]
        mejor = min(validas, key=lambda x: x["precio_actual"]) if validas else None
        out["viajes"].append({
            "nombre": v.nombre, "destino": v.destino, "umbral": v.umbral, "max_escalas": v.max_escalas,
            "mejor": {k: mejor[k] for k in ("clave", "precio_actual", "nivel_google")} if mejor else None,
            "opciones": opciones})
    return out


def alertas(nuevo: dict, previo: dict | None) -> list[dict]:
    """Avisa si una opción baja del umbral (o baja más) o si Google pasa a marcarla como baja."""
    prev = {o["clave"]: o for v in (previo or {}).get("viajes", []) for o in v["opciones"]}
    out = []
    for v in nuevo["viajes"]:
        for o in v["opciones"]:
            p = o["precio_actual"]
            if p is None or o["terminado"] or o["visto_el"] != nuevo["generado"]:
                continue
            antes = prev.get(o["clave"], {})
            pp, pn = antes.get("precio_actual"), antes.get("nivel_google")
            motivo = None
            if v["umbral"] is not None and p <= v["umbral"] and (pp is None or pp > v["umbral"] or p < pp):
                motivo = f"por debajo de {v['umbral']:.0f}"
            elif o["nivel_google"] == "low" and pn != "low":
                motivo = "Google lo marca como precio bajo"
            if motivo:
                out.append({"viaje": v["nombre"], "opcion": o, "anterior": pp, "motivo": motivo})
    return out


def alertas_markdown(lista: list[dict], moneda: str) -> str:
    if not lista:
        return ""
    m = moneda.upper()
    filas = ["\n**Viajes vigilados:**\n", "| Viaje | Fechas | Precio | Antes | Rango típico | Motivo | |",
             "|---|---|---:|---:|---|---|---|"]
    for a in lista:
        o = a["opcion"]
        rango = f"{o['rango_tipico'][0]:.0f}–{o['rango_tipico'][1]:.0f}" if o["rango_tipico"] else "–"
        antes = f"{a['anterior']:.0f}" if a["anterior"] is not None else "–"
        filas.append(f"| {a['viaje']} ({o['origen']}) | {o['salida']} → {o['regreso'] or ''} "
                     f"| **{o['precio_actual']:.0f} {m}** | {antes} | {rango} | {a['motivo']} "
                     f"| [ver]({o['url_google_flights']}) |")
    return "\n".join(filas) + "\n"


def leer(ruta: Path) -> dict:
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def escribir(ruta: Path, datos: dict) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
