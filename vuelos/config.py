import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


@dataclass
class ConfigGoogle:
    """Rutas que se vigilan con Google Flights (SerpApi): pocas búsquedas, precio real."""
    destinos: list[str] = field(default_factory=list)
    # origen -> cuántas fechas de 'dias_antelacion' se consultan (más para el origen principal)
    fechas_por_origen: dict[str, int] = field(default_factory=dict)
    dias_antelacion: list[int] = field(default_factory=lambda: [45, 90, 150])
    fechas_salida: list[str] = field(default_factory=list)   # fechas fijas que quieras vigilar
    dias_estancia: int = 14          # 0 = solo ida
    max_escalas: int | None = None
    frecuencia_dias: int = 3
    reserva_busquedas: int = 10      # no gastar las últimas N búsquedas del plan

    @property
    def origenes(self) -> list[str]:
        return list(self.fechas_por_origen)


@dataclass
class Viaje:
    """Viaje concreto con fechas exactas: se consulta cada día en Google Flights hasta que pasa."""
    nombre: str
    destino: str
    origenes: list[str]
    fechas: list[tuple[str, str | None]]   # (salida, regreso)
    max_escalas: int | None = None
    umbral: float | None = None

    @property
    def clave(self) -> str:
        return self.nombre


@dataclass
class Config:
    moneda: str = "eur"
    meses_adelante: int = 6
    solo_ida: bool = True
    base_datos: Path = RAIZ / "precios.db"
    carpeta_datos: Path = RAIZ / "datos"
    origenes: list[str] = field(default_factory=list)
    destinos: list[str] = field(default_factory=list)
    alertas: dict[str, float] = field(default_factory=dict)
    google: ConfigGoogle = field(default_factory=ConfigGoogle)
    viajes: list[Viaje] = field(default_factory=list)

    def umbral(self, origen: str, destino: str) -> float | None:
        return self.alertas.get(f"{origen}-{destino}", self.alertas.get(f"*-{destino}"))


def cargar(ruta: Path | None = None) -> Config:
    ruta = ruta or RAIZ / "rutas.toml"
    datos = tomllib.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}
    bd = Path(datos.get("base_datos", "precios.db"))
    cd = Path(datos.get("carpeta_datos", "datos"))
    g = datos.get("google", {})
    return Config(
        carpeta_datos=cd if cd.is_absolute() else ruta.parent / cd,
        moneda=datos.get("moneda", "eur").lower(),
        meses_adelante=int(datos.get("meses_adelante", 6)),
        solo_ida=bool(datos.get("solo_ida", True)),
        base_datos=bd if bd.is_absolute() else ruta.parent / bd,
        origenes=[o.upper() for o in datos.get("origenes", [])],
        destinos=[d.upper() for d in datos.get("destinos", [])],
        alertas={k.upper(): float(v) for k, v in datos.get("alertas", {}).items()},
        google=ConfigGoogle(
            destinos=[d.upper() for d in g.get("destinos", [])],
            fechas_por_origen={k.upper(): int(v) for k, v in g.get("fechas_por_origen", {}).items()},
            dias_antelacion=[int(x) for x in g.get("dias_antelacion", [45, 90, 150])],
            fechas_salida=[str(x) for x in g.get("fechas_salida", [])],
            dias_estancia=int(g.get("dias_estancia", 14)),
            max_escalas=g.get("max_escalas"),
            frecuencia_dias=int(g.get("frecuencia_dias", 3)),
            reserva_busquedas=int(g.get("reserva_busquedas", 10)),
        ),
        viajes=[Viaje(
            nombre=v["nombre"], destino=v["destino"].upper(),
            origenes=[o.upper() for o in v.get("origenes", ["MAD"])],
            fechas=[(str(f[0]), str(f[1]) if len(f) > 1 and f[1] else None) for f in v["fechas"]],
            max_escalas=v.get("max_escalas"),
            umbral=float(v["umbral"]) if v.get("umbral") is not None else None,
        ) for v in datos.get("viajes", [])],
    )


def _secreto(var: str, fichero: str) -> str:
    t = os.environ.get(var, "").strip()
    if not t and (RAIZ / fichero).exists():
        t = (RAIZ / fichero).read_text(encoding="utf-8").strip()
    return t


def token() -> str:
    """Token de Travelpayouts: variable TRAVELPAYOUTS_TOKEN o fichero token.txt."""
    t = _secreto("TRAVELPAYOUTS_TOKEN", "token.txt")
    if not t:
        raise SystemExit(
            "Falta el token de Travelpayouts. Créalo en https://www.travelpayouts.com "
            "(Herramientas > API) y guárdalo en token.txt o en la variable TRAVELPAYOUTS_TOKEN."
        )
    return t


def clave_serpapi(obligatoria: bool = True) -> str:
    """Clave de SerpApi: variable SERPAPI_KEY o fichero serpapi_key.txt."""
    t = _secreto("SERPAPI_KEY", "serpapi_key.txt")
    if not t and obligatoria:
        raise SystemExit("Falta la clave de SerpApi (https://serpapi.com/manage-api-key). Guárdala en "
                         "serpapi_key.txt o en la variable SERPAPI_KEY.")
    return t
