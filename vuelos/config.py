import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


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

    def umbral(self, origen: str, destino: str) -> float | None:
        return self.alertas.get(f"{origen}-{destino}", self.alertas.get(f"*-{destino}"))


def cargar(ruta: Path | None = None) -> Config:
    ruta = ruta or RAIZ / "rutas.toml"
    datos = tomllib.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}
    bd = Path(datos.get("base_datos", "precios.db"))
    cd = Path(datos.get("carpeta_datos", "datos"))
    return Config(
        carpeta_datos=cd if cd.is_absolute() else ruta.parent / cd,
        moneda=datos.get("moneda", "eur").lower(),
        meses_adelante=int(datos.get("meses_adelante", 6)),
        solo_ida=bool(datos.get("solo_ida", True)),
        base_datos=bd if bd.is_absolute() else ruta.parent / bd,
        origenes=[o.upper() for o in datos.get("origenes", [])],
        destinos=[d.upper() for d in datos.get("destinos", [])],
        alertas={k.upper(): float(v) for k, v in datos.get("alertas", {}).items()},
    )


def token() -> str:
    """Token de Travelpayouts: variable TRAVELPAYOUTS_TOKEN o fichero token.txt."""
    t = os.environ.get("TRAVELPAYOUTS_TOKEN", "").strip()
    if not t and (RAIZ / "token.txt").exists():
        t = (RAIZ / "token.txt").read_text(encoding="utf-8").strip()
    if not t:
        raise SystemExit(
            "Falta el token de Travelpayouts. Créalo en https://www.travelpayouts.com "
            "(Herramientas > API) y guárdalo en token.txt o en la variable TRAVELPAYOUTS_TOKEN."
        )
    return t
