"""Cliente mínimo de la Aviasales Data API (Travelpayouts). Sin dependencias externas.

Los precios son los encontrados por usuarios de Aviasales en los últimos días (caché),
no una cotización en tiempo real: sirven para tendencias e histórico. Antes de comprar,
confirma el precio en la web de la aerolínea o en Google Flights.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

BASE = "https://api.travelpayouts.com"


@dataclass
class Vuelo:
    origen: str
    destino: str
    salida: str            # YYYY-MM-DD
    regreso: str | None
    precio: float
    aerolinea: str
    escalas: int
    duracion_min: int | None
    enlace: str | None

    @property
    def url(self) -> str | None:
        return f"https://www.aviasales.com{self.enlace}" if self.enlace else None


class ErrorApi(Exception):
    pass


class Travelpayouts:
    def __init__(self, token: str, moneda: str = "eur", pausa: float = 0.25):
        self.token = token
        self.moneda = moneda
        self.pausa = pausa

    def _get(self, ruta: str, params: dict) -> dict:
        params = {k: v for k, v in params.items() if v is not None}
        url = f"{BASE}{ruta}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={
            "X-Access-Token": self.token,
            "Accept-Encoding": "identity",
            "User-Agent": "rastreador-vuelos/1.0",
        })
        for intento in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    datos = json.load(r)
                time.sleep(self.pausa)
                if datos.get("success") is False:
                    raise ErrorApi(datos.get("error") or "respuesta sin éxito")
                return datos
            except urllib.error.HTTPError as e:
                if e.code == 429 and intento < 2:   # límite de peticiones
                    time.sleep(5 * (intento + 1))
                    continue
                raise ErrorApi(f"HTTP {e.code}: {e.read()[:200]!r}") from e
        raise ErrorApi("demasiados reintentos")

    def precios_por_fechas(self, origen: str, destino: str, salida: str,
                           regreso: str | None = None, solo_ida: bool = True,
                           directo: bool = False, limite: int = 1000) -> list[Vuelo]:
        """salida/regreso: 'YYYY-MM' (todo el mes) o 'YYYY-MM-DD'."""
        datos = self._get("/aviasales/v3/prices_for_dates", {
            "origin": origen, "destination": destino,
            "departure_at": salida, "return_at": regreso,
            "one_way": str(solo_ida and not regreso).lower(),
            "direct": str(directo).lower(),
            "currency": self.moneda, "sorting": "price", "limit": limite,
        })
        return [_a_vuelo(x) for x in datos.get("data", [])]


def _a_vuelo(x: dict) -> Vuelo:
    return Vuelo(
        origen=x.get("origin_airport") or x.get("origin"),
        destino=x.get("destination_airport") or x.get("destination"),
        salida=(x.get("departure_at") or "")[:10],
        regreso=(x.get("return_at") or "")[:10] or None,
        precio=float(x["price"]),
        aerolinea=x.get("airline", ""),
        escalas=int(x.get("transfers", 0) or 0),
        duracion_min=x.get("duration_to") or x.get("duration"),
        enlace=x.get("link"),
    )
