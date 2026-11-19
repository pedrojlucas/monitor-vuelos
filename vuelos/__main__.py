"""Uso: python -m vuelos <orden> ...   (python -m vuelos -h para ayuda)"""
import argparse
import sys
from datetime import date

from . import config, core, db
from pathlib import Path

from .api import SerpApi, Travelpayouts


def _cliente(cfg):
    return Travelpayouts(config.token(), cfg.moneda)


def _tabla(filas, columnas):
    if not filas:
        print("(sin resultados)")
        return
    anchos = [max(len(c), *(len(str(f.get(c, ""))) for f in filas)) for c in columnas]
    print("  ".join(c.ljust(a) for c, a in zip(columnas, anchos)))
    for f in filas:
        print("  ".join(str(f.get(c, "") if f.get(c) is not None else "").ljust(a)
                        for c, a in zip(columnas, anchos)))


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="vuelos", description="Rastreador de precios de vuelos")
    sub = p.add_subparsers(dest="orden", required=True)

    b = sub.add_parser("buscar", help="buscar vuelos ahora")
    b.add_argument("origen"); b.add_argument("destino")
    b.add_argument("salida", help="YYYY-MM o YYYY-MM-DD")
    b.add_argument("--regreso", help="YYYY-MM o YYYY-MM-DD (ida y vuelta)")
    b.add_argument("--directo", action="store_true")
    b.add_argument("--max-escalas", type=int)
    b.add_argument("--aerolinea", help="código IATA, p.ej. IB")
    b.add_argument("--precio-max", type=float)
    b.add_argument("-n", "--limite", type=int, default=20)

    rc = sub.add_parser("recoger", help="recogida diaria de todas las rutas de rutas.toml")
    rc.add_argument("--solo-google", action="store_true", help="solo las rutas de Google Flights")
    rc.add_argument("--forzar-google", action="store_true", help="ignorar frecuencia_dias")

    go = sub.add_parser("google", help="búsqueda puntual en Google Flights (gasta 1 búsqueda de SerpApi)")
    go.add_argument("origen"); go.add_argument("destino")
    go.add_argument("salida", help="YYYY-MM-DD")
    go.add_argument("--regreso", help="YYYY-MM-DD")
    go.add_argument("--max-escalas", type=int)
    go.add_argument("-n", "--limite", type=int, default=10)

    sub.add_parser("cuota", help="búsquedas de SerpApi usadas y restantes (no gasta)")

    h = sub.add_parser("historico", help="evolución de precios de una ruta")
    h.add_argument("origen"); h.add_argument("destino")
    h.add_argument("--mes", help="solo salidas de este mes (YYYY-MM)")

    o = sub.add_parser("ofertas", help="mejores precios de la última recogida")
    o.add_argument("--origen"); o.add_argument("--destino")
    o.add_argument("-n", "--limite", type=int, default=15)

    al = sub.add_parser("alertas", help="rutas por debajo del umbral configurado")
    al.add_argument("--nuevas", action="store_true", help="solo alertas nuevas o con precio más bajo que ayer")
    al.add_argument("--markdown", action="store_true", help="salida en Markdown (vacía si no hay alertas)")

    i = sub.add_parser("informe", help="genera informe.html con gráficas")
    i.add_argument("--salida", default="informe.html")

    a = p.parse_args(argv)
    cfg = config.cargar()
    con = db.conectar(cfg.base_datos)
    importados = db.sincronizar(con, cfg.carpeta_datos)
    if importados and not getattr(a, "markdown", False):
        print(f"(importados {importados} días de histórico desde {cfg.carpeta_datos.name}/)")

    if a.orden == "buscar":
        vs = core.buscar(_cliente(cfg), a.origen, a.destino, a.salida, a.regreso,
                         solo_ida=not a.regreso, directo=a.directo, max_escalas=a.max_escalas,
                         aerolinea=a.aerolinea, precio_max=a.precio_max, limite=a.limite,
                         guardar_en=con)
        _tabla([{"salida": v.salida, "regreso": v.regreso, "precio": f"{v.precio:.0f} {cfg.moneda.upper()}",
                 "aerolinea": v.aerolinea, "escalas": v.escalas, "ruta": f"{v.origen}-{v.destino}",
                 "enlace": v.url} for v in vs],
               ["salida", "regreso", "precio", "aerolinea", "escalas", "ruta", "enlace"])
    elif a.orden == "google":
        g = SerpApi(config.clave_serpapi(), cfg.moneda)
        res = g.buscar(a.origen.upper(), a.destino.upper(), a.salida, a.regreso, a.max_escalas)
        if res.vuelos:
            db.guardar(con, date.today().isoformat(), a.origen.upper(), a.destino.upper(),
                       res.vuelos[:1], cfg.moneda, "google")
        _tabla([{"precio": f"{v.precio:.0f} {cfg.moneda.upper()}", "aerolinea": v.aerolinea,
                 "escalas": v.escalas, "duracion": f"{v.duracion_min // 60}h{v.duracion_min % 60:02d}"
                 if v.duracion_min else "", "ruta": f"{v.origen}-{v.destino}"} for v in res.vuelos[:a.limite]],
               ["precio", "aerolinea", "escalas", "duracion", "ruta"])
        if res.nivel or res.rango_tipico:
            print(f"\nNivel según Google: {res.nivel or '-'} · rango típico: {res.rango_tipico or '-'}")
        if res.historial:
            print(f"Historial ({len(res.historial)} días): " +
                  ", ".join(f"{f[5:]} {p:.0f}" for f, p in res.historial[-10:]))
        print(res.url or "")
    elif a.orden == "cuota":
        c = SerpApi(config.clave_serpapi(), cfg.moneda).cuenta()
        for k in ("plan_name", "searches_per_month", "this_month_usage", "plan_searches_left",
                  "total_searches_left"):
            if k in c:
                print(f"{k}: {c[k]}")
    elif a.orden == "recoger":
        dias = set()
        if not a.solo_google:
            print(f"Recogida {date.today()} · {len(cfg.origenes)} orígenes × {len(cfg.destinos)} destinos "
                  f"× {cfg.meses_adelante} meses")
            r = core.recoger(_cliente(cfg), cfg, con)
            dias |= r["dias"]
            print(f"{r['consultas']} consultas, {r['filas']} precios guardados, {len(r['errores'])} errores")
            for e in r["errores"][:20]:
                print("  !", e)
            if r["consultas"] and len(r["errores"]) == r["consultas"]:
                sys.exit("Todas las consultas fallaron: revisa el token o la conexión.")
        clave = config.clave_serpapi(obligatoria=False)
        print(f"\nGoogle Flights (SerpApi) · rutas transatlánticas")
        if not clave:
            print("  omitido: no hay SERPAPI_KEY")
        else:
            rg = core.recoger_google(SerpApi(clave, cfg.moneda), cfg, con, forzar=a.forzar_google)
            dias |= rg["dias"]
            if rg["omitido"]:
                print(f"  omitido: {rg['omitido']}")
            else:
                print(f"{rg['busquedas']} búsquedas (quedaban {rg.get('restantes_antes')}), "
                      f"{rg['historial']} precios históricos añadidos, {len(rg['errores'])} errores")
            for e in rg["errores"]:
                print("  !", e)
            bajos = [x for x in rg["insights"] if x["nivel"] == "low"]
            if bajos:
                m = cfg.moneda.upper()
                lineas = ["\n**Google Flights marca estos precios como BAJOS:**\n",
                          "| Ruta | Fechas | Precio | Rango típico | Aerolínea | |", "|---|---|---:|---|---|---|"]
                for x in bajos:
                    rango = f"{x['rango_tipico'][0]:.0f}–{x['rango_tipico'][1]:.0f} {m}" if x["rango_tipico"] else "–"
                    fechas = x["salida"] + (f" → {x['regreso']}" if x["regreso"] else "")
                    lineas.append(f"| {x['ruta']} | {fechas} | **{x['precio']:.0f} {m}** | {rango} "
                                  f"| {x['aerolinea'] or ''} | [ver]({x['url']}) |")
                Path("google_bajos.md").write_text("\n".join(lineas) + "\n", encoding="utf-8")
        for f in core.exportar(con, cfg, dias):
            print(f"Guardado {f.relative_to(cfg.carpeta_datos.parent)}")
    elif a.orden == "historico":
        r = core.historico(con, a.origen, a.destino, a.mes)
        print(f"Ruta {r['ruta']} — evolución del mínimo por día de observación:")
        _tabla(r["evolucion"], ["observado", "minimo", "medio", "dias"])
        print("\nMínimos por mes de salida (última recogida):")
        _tabla(r["por_mes_salida"], ["mes", "minimo", "medio"])
        if r["minimo_historico"]:
            m = r["minimo_historico"]
            print(f"\nMínimo histórico: {m['precio']} {m['moneda'].upper()} "
                  f"(salida {m['salida']}, visto el {m['observado']}, {m['aerolinea']})")
    elif a.orden == "ofertas":
        _tabla(core.ofertas(con, a.origen, a.destino, a.limite),
               ["origen", "destino", "salida", "precio", "media_ruta", "aerolinea", "escalas"])
    elif a.orden == "alertas":
        lista = core.alertas(con, cfg, nuevas=a.nuevas)
        if a.markdown:
            print(core.alertas_markdown(lista, cfg.moneda), end="")
        else:
            _tabla(lista, ["origen", "destino", "precio", "umbral", "anterior", "salida", "aerolinea", "enlace"])
    elif a.orden == "informe":
        from .informe import generar
        ruta = generar(con, cfg, a.salida)
        print(f"Informe escrito en {ruta}")


if __name__ == "__main__":
    main()
