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
from datetime import datetime, timezone

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
    url_externa: str | None = None     # enlace completo (p.ej. Google Flights)

    @property
    def url(self) -> str | None:
        if self.url_externa:
            return self.url_externa
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


class SerpApi:
    """Google Flights a través de SerpApi: precios en tiempo real + 'price insights'.

    Cada búsqueda consume 1 crédito del plan (las repetidas en la misma hora salen de caché y no
    cuentan). La consulta de la cuenta (/account.json) es gratuita."""
    BASE = "https://serpapi.com"

    def __init__(self, clave: str, moneda: str = "eur", idioma: str = "es", pais: str = "es"):
        self.clave, self.moneda, self.idioma, self.pais = clave, moneda, idioma, pais

    def _get(self, ruta: str, params: dict) -> dict:
        params = {k: v for k, v in {**params, "api_key": self.clave}.items() if v is not None}
        url = f"{self.BASE}{ruta}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": "rastreador-vuelos/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                datos = json.load(r)
        except urllib.error.HTTPError as e:
            try:
                msg = json.load(e).get("error", "")
            except Exception:
                msg = ""
            raise ErrorApi(f"SerpApi HTTP {e.code}: {msg}") from e
        if datos.get("error"):
            raise ErrorApi(f"SerpApi: {datos['error']}")
        return datos

    def cuenta(self) -> dict:
        return self._get("/account.json", {})

    def busquedas_restantes(self) -> int | None:
        c = self.cuenta()
        for k in ("total_searches_left", "plan_searches_left"):
            if c.get(k) is not None:
                return int(c[k])
        return None

    def buscar(self, origen: str, destino: str, salida: str, regreso: str | None = None,
               max_escalas: int | None = None) -> "ResultadoGoogle":
        """salida/regreso: YYYY-MM-DD. Sin regreso = solo ida."""
        paradas = {None: 0, 0: 1, 1: 2, 2: 3}.get(max_escalas, 0)
        d = self._get("/search.json", {
            "engine": "google_flights", "departure_id": origen, "arrival_id": destino,
            "outbound_date": salida, "return_date": regreso, "type": 1 if regreso else 2,
            "currency": self.moneda.upper(), "hl": self.idioma, "gl": self.pais, "stops": paradas,
        })
        return _a_resultado_google(d, origen, destino, salida, regreso)


@dataclass
class ResultadoGoogle:
    vuelos: list[Vuelo]
    nivel: str | None                  # low / typical / high según Google
    rango_tipico: tuple[float, float] | None
    historial: list[tuple[str, float]]  # (fecha, precio) de los últimos ~60 días para esta búsqueda
    url: str | None


def _a_resultado_google(d: dict, origen: str, destino: str, salida: str,
                        regreso: str | None) -> ResultadoGoogle:
    url = (d.get("search_metadata") or {}).get("google_flights_url")
    vuelos = []
    for op in (d.get("best_flights") or []) + (d.get("other_flights") or []):
        tramos = op.get("flights") or []
        if not op.get("price") or not tramos:
            continue
        aerolineas = list(dict.fromkeys(t.get("airline", "") for t in tramos if t.get("airline")))
        vuelos.append(Vuelo(
            origen=(tramos[0].get("departure_airport") or {}).get("id", origen),
            destino=(tramos[-1].get("arrival_airport") or {}).get("id", destino),
            salida=salida, regreso=regreso, precio=float(op["price"]),
            aerolinea=" / ".join(aerolineas), escalas=len(op.get("layovers") or []),
            duracion_min=op.get("total_duration"), enlace=None,
        ))
    pi = d.get("price_insights") or {}
    rango = pi.get("typical_price_range")
    historial = []
    for punto in pi.get("price_history") or []:
        try:
            ts, precio = punto
            historial.append((datetime.fromtimestamp(int(ts), timezone.utc).date().isoformat(), float(precio)))
        except (TypeError, ValueError):
            continue
    for v in vuelos:
        v.url_externa = url
    return ResultadoGoogle(sorted(vuelos, key=lambda v: v.precio), pi.get("price_level"),
                           tuple(rango) if rango and len(rango) == 2 else None, historial, url)


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
