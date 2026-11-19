"""Pruebas sin red de la parte de Google Flights (SerpApi)."""
import csv
import gzip
import io
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from vuelos import core, db
from vuelos.api import ErrorApi, _a_resultado_google
from vuelos.config import Config, ConfigGoogle


def respuesta(precio=612, nivel="low", dias_hist=5):
    hoy = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    hist = [[int((hoy - timedelta(days=k)).timestamp()), precio + 10 * k] for k in range(dias_hist, -1, -1)]
    return {
        "search_metadata": {"google_flights_url": "https://www.google.com/travel/flights?x"},
        "best_flights": [{
            "flights": [
                {"departure_airport": {"id": "MAD"}, "arrival_airport": {"id": "DFW"}, "airline": "American"},
                {"departure_airport": {"id": "DFW"}, "arrival_airport": {"id": "IAH"}, "airline": "American"},
            ],
            "layovers": [{"id": "DFW"}], "total_duration": 780, "price": precio,
        }],
        "other_flights": [
            {"flights": [{"departure_airport": {"id": "MAD"}, "arrival_airport": {"id": "IAH"},
                          "airline": "Iberia"}], "total_duration": 660, "price": precio + 150},
            {"flights": [], "price": 1},          # incompleto: se ignora
        ],
        "price_insights": {"lowest_price": precio, "price_level": nivel,
                           "typical_price_range": [700, 950], "price_history": hist},
    }


class SerpFalso:
    def __init__(self, restantes=100, fallar=()):
        self.restantes, self.fallar, self.llamadas = restantes, set(fallar), []

    def busquedas_restantes(self):
        return self.restantes

    def buscar(self, o, d, s, r=None, max_escalas=None):
        self.llamadas.append((o, d, s, r))
        if (o, d) in self.fallar:
            raise ErrorApi("fallo simulado")
        return _a_resultado_google(respuesta(600 if d == "IAH" else 800), o, d, s, r)


class PruebasGoogle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(
            base_datos=Path(self.tmp.name) / "p.db", carpeta_datos=Path(self.tmp.name) / "datos",
            alertas={"*-IAH": 650},
            google=ConfigGoogle(destinos=["IAH", "PDX"], fechas_por_origen={"MAD": 3, "ALC": 1},
                                dias_antelacion=[45, 90, 150], dias_estancia=14, frecuencia_dias=3,
                                reserva_busquedas=10))
        self.con = db.conectar(self.cfg.base_datos)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_parseo(self):
        r = _a_resultado_google(respuesta(), "MAD", "IAH", "2027-03-01", "2027-03-15")
        self.assertEqual([v.precio for v in r.vuelos], [612, 762])
        self.assertEqual(r.vuelos[0].aerolinea, "American")      # sin duplicados
        self.assertEqual(r.vuelos[0].escalas, 1)
        self.assertEqual(r.vuelos[0].destino, "IAH")
        self.assertEqual(r.vuelos[0].url, "https://www.google.com/travel/flights?x")
        self.assertEqual(r.nivel, "low")
        self.assertEqual(r.rango_tipico, (700, 950))
        self.assertEqual(len(r.historial), 6)

    def test_fechas(self):
        hoy = date(2026, 11, 19)
        self.cfg.google.fechas_salida = ["2026-01-01", "2027-07-10"]   # la pasada se ignora
        f = core.fechas_google(self.cfg, "MAD", hoy)
        self.assertEqual(f[0], ("2027-07-10", "2027-07-24"))
        self.assertEqual(len(f), 4)
        self.assertEqual(len(core.fechas_google(self.cfg, "ALC", hoy)), 2)
        self.cfg.google.dias_estancia = 0
        self.assertIsNone(core.fechas_google(self.cfg, "MAD", hoy)[1][1])

    def test_recoger_guarda_hoy_e_historial(self):
        cli = SerpFalso()
        r = core.recoger_google(cli, self.cfg, self.con, aviso=lambda *_: None)
        self.assertEqual(r["busquedas"], (3 + 1) * 2)
        self.assertEqual(r["filas"], 8)
        self.assertEqual(r["historial"], 8 * 5)    # 5 días anteriores por búsqueda
        self.assertIn(date.today().isoformat(), r["dias"])
        hoy = self.con.execute("SELECT COUNT(*) FROM observaciones WHERE fuente='google'").fetchone()[0]
        self.assertEqual(hoy, 8)
        # exporta todos los días tocados y otra base los reimporta igual
        ficheros = core.exportar(self.con, self.cfg, r["dias"])
        self.assertEqual(len(ficheros), 6)
        otra = db.conectar(Path(self.tmp.name) / "otra.db")
        db.sincronizar(otra, self.cfg.carpeta_datos)
        q = "SELECT COUNT(*), SUM(precio) FROM observaciones"
        self.assertEqual(tuple(otra.execute(q).fetchone()), tuple(self.con.execute(q).fetchone()))
        otra.close()

    def test_frecuencia(self):
        core.recoger_google(SerpFalso(), self.cfg, self.con, aviso=lambda *_: None)
        cli = SerpFalso()
        r = core.recoger_google(cli, self.cfg, self.con, aviso=lambda *_: None)
        self.assertIsNotNone(r["omitido"])
        self.assertEqual(cli.llamadas, [])
        r = core.recoger_google(cli, self.cfg, self.con, aviso=lambda *_: None, forzar=True)
        self.assertEqual(r["busquedas"], 8)

    def test_cuota_recorta_por_prioridad(self):
        cli = SerpFalso(restantes=15)    # 15 - reserva 10 = 5 búsquedas
        r = core.recoger_google(cli, self.cfg, self.con, aviso=lambda *_: None)
        self.assertEqual(r["busquedas"], 5)
        self.assertTrue(all(o == "MAD" for o, *_ in cli.llamadas))   # MAD va primero
        cli = SerpFalso(restantes=5)
        r = core.recoger_google(cli, self.cfg, self.con, aviso=lambda *_: None, forzar=True)
        self.assertEqual(r["busquedas"], 0)

    def test_errores_no_paran(self):
        r = core.recoger_google(SerpFalso(fallar={("MAD", "PDX")}), self.cfg, self.con, aviso=lambda *_: None)
        self.assertEqual(len(r["errores"]), 3)
        self.assertEqual(r["filas"], 5)

    def test_alerta_y_ofertas(self):
        core.recoger_google(SerpFalso(), self.cfg, self.con, aviso=lambda *_: None)
        al = core.alertas(self.con, self.cfg, nuevas=True)
        self.assertEqual({(a["origen"], a["destino"]) for a in al}, {("MAD", "IAH"), ("ALC", "IAH")})
        self.assertTrue(all(a["anterior"] is not None for a in al))   # viene del historial de Google
        of = core.ofertas(self.con, destino="IAH")
        self.assertTrue(of and all(o["precio"] == 600 for o in of))

    def test_csv_antiguo_sin_fuente(self):
        carpeta = self.cfg.carpeta_datos / "2026"
        carpeta.mkdir(parents=True)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["observado", "origen", "destino", "salida", "regreso", "precio", "moneda",
                    "aerolinea", "escalas", "aeropuertos", "enlace"])
        w.writerow(["2026-11-19", "MAD", "LON", "2026-12-17", "", "15.0", "eur", "FR", "0", "MAD-STN", ""])
        with gzip.open(carpeta / "2026-11-19.csv.gz", "wt", encoding="utf-8", newline="") as f:
            f.write(buf.getvalue())
        self.assertEqual(db.sincronizar(self.con, self.cfg.carpeta_datos), 1)
        fila = self.con.execute("SELECT fuente, precio FROM observaciones").fetchone()
        self.assertEqual(tuple(fila), ("travelpayouts", 15.0))

    def test_migracion_base_antigua(self):
        ruta = Path(self.tmp.name) / "vieja.db"
        import sqlite3
        v = sqlite3.connect(ruta)
        v.executescript("""CREATE TABLE observaciones (observado TEXT, origen TEXT, destino TEXT,
            salida TEXT, regreso TEXT NOT NULL DEFAULT '', precio REAL, moneda TEXT, aerolinea TEXT,
            escalas INTEGER, aeropuertos TEXT, enlace TEXT,
            PRIMARY KEY (observado, origen, destino, salida, regreso));
            CREATE TABLE importados (fichero TEXT PRIMARY KEY);
            INSERT INTO observaciones VALUES ('2026-11-19','MAD','LON','2026-12-17','',15,'eur','FR',0,'','');""")
        v.commit(); v.close()
        con = db.conectar(ruta)
        self.assertEqual(con.execute("SELECT fuente FROM observaciones").fetchone()[0], "travelpayouts")
        con.close()


if __name__ == "__main__":
    unittest.main()
