"""Prueba variantes de consulta para ver qué devuelve datos en rutas con poca cobertura.
Uso: python herramientas/diagnostico.py   (necesita TRAVELPAYOUTS_TOKEN)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vuelos import config, core  # noqa: E402
from vuelos.api import ErrorApi, Travelpayouts  # noqa: E402

cli = Travelpayouts(config.token(), "eur")
meses = core.meses_siguientes(6)


def probar(nombre, ruta, params, clave="data"):
    try:
        d = cli._get(ruta, params)
    except ErrorApi as e:
        print(f"{nombre:<60} ERROR {e}")
        return
    datos = d.get(clave) or []
    if isinstance(datos, dict):   # algunos endpoints devuelven {destino: {...}} o {fecha: {...}}
        datos = [x for v in datos.values() for x in (v.values() if isinstance(v, dict) and "price" not in v and "value" not in v else [v])]
    precios = [x.get("price") or x.get("value") for x in datos if isinstance(x, dict)]
    precios = [p for p in precios if p]
    print(f"{nombre:<60} n={len(precios):<4} min={min(precios) if precios else '-'}")


for o in ["MAD", "ALC", "BCN"]:
    for d in ["IAH", "HOU", "PDX"]:
        print(f"--- {o}-{d}")
        for solo_ida in (True, False):
            for mercado in (None, "es", "us"):
                n = 0
                mins = []
                for mes in meses:
                    try:
                        vs = cli.precios_por_fechas(o, d, mes, solo_ida=solo_ida) if mercado is None else [
                            x for x in cli._get("/aviasales/v3/prices_for_dates", {
                                "origin": o, "destination": d, "departure_at": mes,
                                "one_way": str(solo_ida).lower(), "currency": "eur",
                                "sorting": "price", "limit": 1000, "market": mercado}).get("data", [])]
                    except ErrorApi as e:
                        print("   error", e)
                        break
                    n += len(vs)
                    mins += [getattr(v, "precio", None) or v.get("price") for v in vs]
                tipo = "ida" if solo_ida else "ida+vuelta"
                print(f"prices_for_dates {tipo:<10} market={mercado or '-':<3} 6 meses   "
                      f"n={n:<4} min={min(mins) if mins else '-'}")
        probar("prices_for_dates sin fecha (ida+vuelta)", "/aviasales/v3/prices_for_dates",
               {"origin": o, "destination": d, "one_way": "false", "currency": "eur", "limit": 1000})
        probar("prices_for_dates sin fecha (ida)", "/aviasales/v3/prices_for_dates",
               {"origin": o, "destination": d, "one_way": "true", "currency": "eur", "limit": 1000})
        probar("v2/prices/latest año", "/v2/prices/latest",
               {"origin": o, "destination": d, "period_type": "year", "currency": "eur",
                "limit": 1000, "show_to_affiliates": "false"})
        probar("v1/prices/cheap", "/v1/prices/cheap", {"origin": o, "destination": d, "currency": "eur"})
        probar("grouped_prices por mes", "/aviasales/v3/grouped_prices",
               {"origin": o, "destination": d, "group_by": "month", "currency": "eur"})

print("--- MAD a cualquier destino de EE. UU. (v2/prices/latest, sin destino)")
probar("v2/prices/latest MAD-* año", "/v2/prices/latest",
       {"origin": "MAD", "period_type": "year", "currency": "eur", "limit": 1000, "show_to_affiliates": "false"})
