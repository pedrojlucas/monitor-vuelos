# Rastreador de precios de vuelos

Busca vuelos desde MAD, ALC y RMU hacia Europa, Houston (IAH) y Portland (PDX). Además, guarda
cada día los precios en una base de datos local (`precios.db`) para ir creando tu propio histórico.

- **Fuente:** Aviasales Data API (Travelpayouts), gratuita. Son precios que otros usuarios han
  encontrado en los últimos días (caché), así que sirven para tendencias e histórico, pero no son
  cotizaciones al minuto. Antes de comprar, confirma el precio en Google Flights o en la web de la aerolínea.
- **Histórico:** ninguna fuente gratuita da años de precios hacia atrás. Este proyecto los acumula
  a partir del día en que empiezas a recogerlos.

## Puesta en marcha (una sola vez)

1. **Token:** regístrate en <https://www.travelpayouts.com> (es gratis) y copia tu token de API
   desde *Herramientas → API*. Guárdalo en un fichero `token.txt` en esta carpeta.
2. **Rutas y alertas:** edita `rutas.toml` (orígenes, destinos, meses que se miran hacia delante
   y precios umbral de las alertas).
3. **Prueba:** `python -m vuelos buscar MAD IAH 2027-03`

## Uso

```
python -m vuelos buscar MAD LON 2027-02 --max-escalas 0 --precio-max 60
python -m vuelos buscar ALC PDX 2027-05-10 --regreso 2027-05-24
python -m vuelos recoger                 # recogida completa (unas 400 consultas, 2–3 min)
python -m vuelos historico MAD IAH --mes 2027-06
python -m vuelos ofertas --origen RMU
python -m vuelos alertas
python -m vuelos informe                 # crea informe.html con las gráficas
```

Cada `buscar` también se guarda en el histórico.

## Recogida diaria en GitHub Actions (sin tener el PC encendido)

El workflow `.github/workflows/recoger.yml` se ejecuta cada día a las 05:17 UTC. En cada ejecución:

1. recoge los precios de todas las rutas;
2. guarda el día en `datos/AAAA/AAAA-MM-DD.csv.gz` y hace un commit en el repositorio. Ese es el
   histórico permanente: unos 20–30 MB al año;
3. regenera el informe en `docs/index.html`;
4. si alguna ruta baja de su umbral (o baja más que el día anterior), **abre una issue**, y GitHub
   te avisa por correo.

Configuración (una sola vez):

1. Crea un repositorio en GitHub y sube esta carpeta (`git push`).
2. En el repositorio, ve a **Settings → Secrets and variables → Actions → New repository secret**.
   Crea uno con nombre `TRAVELPAYOUTS_TOKEN` y pega tu token como valor.
3. En **Actions**, abre "Recoger precios de vuelos" y pulsa **Run workflow** para probarlo.
4. Opcional: si el repositorio es público, ve a **Settings → Pages**, elige la rama `main` y la
   carpeta `/docs`. Así el informe queda publicado en `https://TU_USUARIO.github.io/REPO/`.
   Si es privado, Pages requiere un plan de pago; en ese caso ábrelo en local tras un `git pull`.

Coste: en un repositorio público es gratis. En uno privado, cada ejecución gasta unos 3–5 minutos
de los 2.000 minutos gratuitos al mes. Si una ejecución falla (por ejemplo, porque el token
caduca), GitHub te avisa por correo.

En local, cada orden importa automáticamente los CSV nuevos de `datos/` a `precios.db`, así que
basta con hacer `git pull` para tener el histórico al día en tu PC y en el MCP.

### Alternativa local

`recoger.bat` hace lo mismo con el Programador de tareas de Windows, pero el PC tiene que estar
encendido:
`schtasks /Create /SC DAILY /ST 08:00 /TN "Vuelos - recoger precios" /TR "\"RUTA\recoger.bat\""`

## Uso desde Claude (MCP)

```
pip install mcp
claude mcp add vuelos -- python "RUTA\vuelos\mcp_server.py"
```

Herramientas que expone: `buscar_vuelos`, `historico_ruta`, `mejores_ofertas`, `alertas_precio`
y `rutas_configuradas`.

## Pruebas

`python -m unittest discover -s tests -t .` (no usan la red: un cliente falso imita la API).
