"""Pruebas sin red de los viajes vigilados."""
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tests.test_google import SerpFalso
from vuelos import core, db, viajes
from vuelos.api import _a_resultado_google
from vuelos.config import Config, ConfigGoogle, Viaje
from tests.test_google import respuesta


def futuro(d):
    return (date.today() + timedelta(days=d)).isoformat()


class SerpPrecio(SerpFalso):
    def __init__(self, precio, nivel="typical", **kw):
        super().__init__(**kw)
        self.precio, self.nivel = precio, nivel

    def buscar(self, o, d, s, r=None, max_escalas=None):
        self.llamadas.append((o, d, s, r))
        return _a_resultado_google(respuesta(self.precio, self.nivel), o, d, s, r)


class PruebasViajes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.viaje = Viaje("Portland", "PDX", ["MAD"],
                           [(futuro(100), futuro(106)), (futuro(107), futuro(113)),
                            ((date.today() - timedelta(days=1)).isoformat(), futuro(5))],  # ya pasado
                           max_escalas=1, umbral=700)
        self.cfg = Config(base_datos=Path(self.tmp.name) / "p.db", carpeta_datos=Path(self.tmp.name) / "datos",
                          google=ConfigGoogle(destinos=["PDX"], fechas_por_origen={"MAD": 1}),
                          viajes=[self.viaje])
        self.con = db.conectar(self.cfg.base_datos)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_solo_fechas_futuras_y_una_vez_al_dia(self):
        cli = SerpPrecio(800)
        r = viajes.recoger(cli, self.cfg, self.con, aviso=lambda *_: None)
        self.assertEqual(r["busquedas"], 2)
        r = viajes.recoger(cli, self.cfg, self.con, aviso=lambda *_: None)
        self.assertEqual(r["busquedas"], 0)
        self.assertIsNotNone(r["omitido"])

    def test_no_bloquea_el_historico_general(self):
        viajes.recoger(SerpPrecio(800), self.cfg, self.con, aviso=lambda *_: None)
        rg = core.recoger_google(SerpPrecio(800), self.cfg, self.con, aviso=lambda *_: None)
        self.assertIsNone(rg["omitido"])
        self.assertEqual(rg["busquedas"], 1)

    def test_cuota_reserva(self):
        r = viajes.recoger(SerpPrecio(800, restantes=11), self.cfg, self.con, aviso=lambda *_: None)
        self.assertEqual(r["busquedas"], 1)

    def test_resumen_y_alertas(self):
        r1 = viajes.recoger(SerpPrecio(800), self.cfg, self.con, aviso=lambda *_: None)
        res1 = viajes.resumen(self.cfg, self.con, r1["consultas"], {})
        v = res1["viajes"][0]
        self.assertEqual(len(v["opciones"]), 3)
        o = v["opciones"][0]
        self.assertEqual(o["precio_actual"], 800)
        self.assertEqual(o["minimo_visto"], 800)       # historial: 800 + 10k, el mínimo es hoy
        self.assertEqual(o["precio_hace_7_dias"], None)  # el historial falso solo tiene 5 días
        self.assertEqual(o["mejores_opciones"][0]["aerolinea"], "American")
        self.assertTrue(v["opciones"][2]["terminado"])
        self.assertEqual(v["mejor"]["precio_actual"], 800)
        self.assertEqual(viajes.alertas(res1, {}), [])   # 800 > 700 y nivel normal

        # al día siguiente (simulado) baja a 680 y Google lo marca como bajo
        self.con.execute("DELETE FROM observaciones WHERE fuente='google-viaje'")
        r2 = viajes.recoger(SerpPrecio(680, "low"), self.cfg, self.con, aviso=lambda *_: None)
        res2 = viajes.resumen(self.cfg, self.con, r2["consultas"], res1)
        al = viajes.alertas(res2, res1)
        self.assertEqual(len(al), 2)
        self.assertIn("700", al[0]["motivo"])
        self.assertIn("Portland", viajes.alertas_markdown(al, "eur"))
        # misma situación otra vez: no se repite
        self.assertEqual(viajes.alertas(res2, res2), [])

    def test_resumen_conserva_nivel_si_no_se_consulta(self):
        r1 = viajes.recoger(SerpPrecio(800, "high"), self.cfg, self.con, aviso=lambda *_: None)
        res1 = viajes.resumen(self.cfg, self.con, r1["consultas"], {})
        res2 = viajes.resumen(self.cfg, self.con, {}, res1)
        self.assertEqual(res2["viajes"][0]["opciones"][0]["nivel_google"], "high")

    def test_json(self):
        ruta = Path(self.tmp.name) / "docs" / "viajes.json"
        self.assertEqual(viajes.leer(ruta), {})
        viajes.escribir(ruta, {"a": "ñ"})
        self.assertEqual(viajes.leer(ruta), {"a": "ñ"})


if __name__ == "__main__":
    unittest.main()
