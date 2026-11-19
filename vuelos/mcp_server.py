"""Servidor MCP para que Claude consulte vuelos y el histórico local.

Requiere:  pip install mcp
Registro:  claude mcp add vuelos -- python -m vuelos.mcp_server   (ejecutado desde esta carpeta)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from vuelos import config, core, db  # noqa: E402
from datetime import date  # noqa: E402

from vuelos.api import SerpApi, Travelpayouts  # noqa: E402

mcp = FastMCP("vuelos", instructions=(
    "Precios de vuelos de la caché de Aviasales (no son cotizaciones en tiempo real) e histórico "
    "local recogido a diario. Orígenes habituales: MAD, ALC, RMU. Para confirmar un precio antes "
    "de comprar, compruébalo en Google Flights o en la web de la aerolínea."))


def _cfg():
    return config.cargar()


def _con(cfg):
    con = db.conectar(cfg.base_datos)
    db.sincronizar(con, cfg.carpeta_datos)
    return con


@mcp.tool()
def buscar_vuelos(origen: str, destino: str, salida: str, regreso: str | None = None,
                  directo: bool = False, max_escalas: int | None = None,
                  aerolinea: str | None = None, precio_max: float | None = None,
                  limite: int = 20) -> list[dict]:
    """Busca los vuelos más baratos. salida/regreso: 'YYYY-MM' (todo el mes) o 'YYYY-MM-DD'.
    Códigos IATA de aeropuerto o ciudad (LON, PAR, IAH, PDX). Sin regreso = solo ida."""
    cfg = _cfg()
    con = _con(cfg)
    vs = core.buscar(Travelpayouts(config.token(), cfg.moneda), origen, destino, salida, regreso,
                     solo_ida=not regreso, directo=directo, max_escalas=max_escalas,
                     aerolinea=aerolinea, precio_max=precio_max, limite=limite, guardar_en=con)
    return [{"salida": v.salida, "regreso": v.regreso, "precio": v.precio, "moneda": cfg.moneda,
             "aerolinea": v.aerolinea, "escalas": v.escalas, "aeropuertos": f"{v.origen}-{v.destino}",
             "duracion_min": v.duracion_min, "enlace": v.url} for v in vs]


@mcp.tool()
def buscar_google_flights(origen: str, destino: str, salida: str, regreso: str | None = None,
                          max_escalas: int | None = None, limite: int = 10) -> dict:
    """Precio real en Google Flights (vía SerpApi; gasta 1 búsqueda del plan). Úsalo para
    transatlánticos (IAH, PDX...) o para confirmar un precio. salida/regreso: 'YYYY-MM-DD'.
    Devuelve opciones, nivel de precio según Google (low/typical/high), rango típico e historial."""
    cfg = _cfg()
    res = SerpApi(config.clave_serpapi(), cfg.moneda).buscar(
        origen.upper(), destino.upper(), salida, regreso, max_escalas)
    if res.vuelos:
        db.guardar(_con(cfg), date.today().isoformat(), origen.upper(), destino.upper(),
                   res.vuelos[:1], cfg.moneda, "google")
    return {
        "opciones": [{"precio": v.precio, "aerolinea": v.aerolinea, "escalas": v.escalas,
                      "duracion_min": v.duracion_min, "aeropuertos": f"{v.origen}-{v.destino}"}
                     for v in res.vuelos[:limite]],
        "moneda": cfg.moneda, "nivel_google": res.nivel, "rango_tipico": res.rango_tipico,
        "historial": res.historial, "url": res.url,
    }


@mcp.tool()
def cuota_serpapi() -> dict:
    """Búsquedas de SerpApi usadas y restantes este mes (consultarlo no gasta)."""
    return SerpApi(config.clave_serpapi(), _cfg().moneda).cuenta()


@mcp.tool()
def historico_ruta(origen: str, destino: str, mes_salida: str | None = None) -> dict:
    """Histórico local de una ruta: evolución del mínimo por día de recogida, mínimos por mes de
    salida y mínimo histórico. mes_salida opcional ('YYYY-MM') para ver un mes concreto."""
    return core.historico(_con(_cfg()), origen, destino, mes_salida)


@mcp.tool()
def mejores_ofertas(origen: str | None = None, destino: str | None = None, limite: int = 15) -> list[dict]:
    """Precios más bajos de la última recogida, comparados con la media histórica de cada ruta."""
    return core.ofertas(_con(_cfg()), origen, destino, limite)


@mcp.tool()
def alertas_precio() -> list[dict]:
    """Rutas cuyo precio actual está por debajo del umbral definido en rutas.toml."""
    cfg = _cfg()
    return core.alertas(_con(cfg), cfg)


@mcp.tool()
def rutas_configuradas() -> dict:
    """Orígenes, destinos y umbrales de alerta definidos en rutas.toml."""
    cfg = _cfg()
    return {"origenes": cfg.origenes, "destinos": cfg.destinos, "alertas": cfg.alertas,
            "meses_adelante": cfg.meses_adelante, "moneda": cfg.moneda,
            "google": {"origenes": cfg.google.fechas_por_origen, "destinos": cfg.google.destinos,
                       "dias_antelacion": cfg.google.dias_antelacion,
                       "dias_estancia": cfg.google.dias_estancia,
                       "frecuencia_dias": cfg.google.frecuencia_dias}}


if __name__ == "__main__":
    mcp.run()
