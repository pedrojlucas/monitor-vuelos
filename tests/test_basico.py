"""Pruebas sin red: un cliente falso imita las respuestas de Travelpayouts."""
import random
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from vuelos import core, db
from vuelos.api import Vuelo, _a_vuelo
from vuelos.config import Config


class ClienteFalso:
    moneda = "eur"

    def __init__(self, semilla=1):
        self.r = random.Random(semilla)

    def precios_por_fechas(self, origen, destino, salida, regreso=None, solo_ida=True, directo=False):
        base = 520 if destino in ("IAH", "PDX") else 60
        a, m = map(int, salida[:7].split("-"))
        out = []
        for dia in range(1, 29):
            for _ in range(2):
                out.append(_a_vuelo({
                    "origin": origen, "destination": destino, "origin_airport": origen,
                    "destination_airport": destino, "price": base + self.r.randint(-30, 120),
                    "airline": self.r.choice(["IB", "UX", "VY", "FR"]),
                    "departure_at": f"{a:04d}-{m:02d}-{dia:02d}T10:00:00+01:00",
                    "transfers": 0 if directo else self.r.randint(0, 2), "link": "/search/x",
                }))
        return out


class Pruebas(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(origenes=["MAD", "ALC"], destinos=["LON", "IAH"], meses_adelante=2,
                          base_datos=Path(self.tmp.name) / "p.db", alertas={"*-IAH": 10_000},
                          carpeta_datos=Path(self.tmp.name) / "datos")
        self.con = db.conectar(self.cfg.base_datos)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_meses(self):
        self.assertEqual(core.meses_siguientes(3, date(2026, 11, 19)), ["2026-11", "2026-12", "2027-01"])

    def test_minimo_por_dia(self):
        vs = [Vuelo("MAD", "LON", "2027-01-01", None, p, "IB", 0, None, None) for p in (80, 50, 70)]
        self.assertEqual([v.precio for v in core.minimo_por_dia(vs)], [50])

    def test_buscar_filtra_y_guarda(self):
        vs = core.buscar(ClienteFalso(), "mad", "lon", "2027-01", max_escalas=0, precio_max=100,
                         guardar_en=self.con)
        self.assertTrue(all(v.escalas == 0 and v.precio <= 100 for v in vs))
        self.assertEqual(vs, sorted(vs, key=lambda v: v.precio))
        n = self.con.execute("SELECT COUNT(*) FROM observaciones").fetchone()[0]
        self.assertEqual(n, 28)  # un mínimo por día de salida

    def test_recoger_historico_alertas(self):
        r = core.recoger(ClienteFalso(), self.cfg, self.con, aviso=lambda *_: None)
        self.assertEqual(r["consultas"], 2 * 2 * 2)
        self.assertFalse(r["errores"])
        # simula días anteriores de recogida
        cli = ClienteFalso(7)
        for k in range(1, 6):
            obs = (date.today() - timedelta(days=k)).isoformat()
            mes = core.meses_siguientes(2)[1]
            db.guardar(self.con, obs, "MAD", "IAH",
                       core.minimo_por_dia(cli.precios_por_fechas("MAD", "IAH", mes)), "eur")
        h = core.historico(self.con, "MAD", "IAH")
        self.assertEqual(len(h["evolucion"]), 6)
        self.assertIsNotNone(h["minimo_historico"])
        self.assertTrue(h["por_mes_salida"])
        self.assertTrue(core.ofertas(self.con, origen="MAD"))
        al = core.alertas(self.con, self.cfg)
        self.assertEqual({a["destino"] for a in al}, {"IAH"})

    def test_guardar_conserva_el_mas_barato(self):
        hoy = date.today().isoformat()
        caro = Vuelo("MAD", "LON", "2027-02-01", None, 90, "IB", 0, None, None)
        barato = Vuelo("MAD", "LON", "2027-02-01", None, 40, "FR", 0, None, None)
        db.guardar(self.con, hoy, "MAD", "LON", [barato], "eur")
        db.guardar(self.con, hoy, "MAD", "LON", [caro], "eur")
        self.assertEqual(self.con.execute("SELECT precio FROM observaciones").fetchone()[0], 40)

    def test_csv_ida_y_vuelta(self):
        r = core.recoger(ClienteFalso(), self.cfg, self.con, aviso=lambda *_: None)
        self.assertTrue(r["fichero"].exists())
        original = self.con.execute("SELECT COUNT(*), SUM(precio) FROM observaciones").fetchone()
        otra = db.conectar(Path(self.tmp.name) / "otra.db")
        self.assertEqual(db.sincronizar(otra, self.cfg.carpeta_datos), 1)
        self.assertEqual(tuple(otra.execute("SELECT COUNT(*), SUM(precio) FROM observaciones").fetchone()),
                         tuple(original))
        self.assertEqual(db.sincronizar(otra, self.cfg.carpeta_datos), 0)  # no reimporta
        otra.close()

    def test_alertas_nuevas_no_se_repiten(self):
        ayer, hoy = (date.today() - timedelta(days=1)).isoformat(), date.today().isoformat()
        sal = (date.today() + timedelta(days=30)).isoformat()
        v = lambda p: [Vuelo("MAD", "IAH", sal, None, p, "IB", 1, None, None)]
        cfg = Config(alertas={"*-IAH": 450})
        db.guardar(self.con, ayer, "MAD", "IAH", v(420), "eur")
        db.guardar(self.con, hoy, "MAD", "IAH", v(430), "eur")
        self.assertEqual(len(core.alertas(self.con, cfg)), 1)
        self.assertEqual(core.alertas(self.con, cfg, nuevas=True), [])   # ya avisada ayer y no bajó
        self.con.execute("UPDATE observaciones SET precio=400 WHERE observado=?", (hoy,))
        self.assertEqual(len(core.alertas(self.con, cfg, nuevas=True)), 1)  # bajó más: avisa
        self.assertIn("MAD-IAH", core.alertas_markdown(core.alertas(self.con, cfg), "eur"))

    def test_informe(self):
        from vuelos.informe import generar
        core.recoger(ClienteFalso(), self.cfg, self.con, aviso=lambda *_: None)
        ruta = generar(self.con, self.cfg, str(Path(self.tmp.name) / "docs" / "i.html"))  # carpeta aún inexistente
        html = ruta.read_text(encoding="utf-8")
        self.assertIn("MAD-IAH", html)
        self.assertNotIn("__DATOS__", html)


if __name__ == "__main__":
    unittest.main()
