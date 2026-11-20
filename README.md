# Rastreador de precios de vuelos

Busca vuelos desde MAD, ALC y RMU hacia Europa, y desde MAD y ALC hacia Houston (IAH) y Portland
(PDX). Guarda los precios para ir creando tu propio histórico. Usa dos fuentes:

| Rutas | Fuente | Frecuencia | Coste |
|---|---|---|---|
| Europa (desde MAD, ALC, RMU) | Aviasales Data API (Travelpayouts): caché de precios que otros usuarios han encontrado hace poco | diaria | gratis |
| Transatlánticas (MAD/ALC → IAH, PDX) | Google Flights vía SerpApi: precio real | cada 3 días (configurable) | 1 búsqueda del plan por consulta |

- La caché de Travelpayouts no tiene datos transatlánticos desde Madrid ni desde Alicante (lo
  comprobó el workflow de diagnóstico), por eso esas rutas van por Google Flights.
- Cada búsqueda en Google trae, además, el **historial de unos 60 días** de esa misma búsqueda,
  así que el histórico transatlántico no empieza de cero.
- Ninguna fuente gratuita da años de precios hacia atrás: el resto se acumula día a día.
- Antes de comprar, confirma el precio en la web de la aerolínea.

## Puesta en marcha (una sola vez)

1. **Travelpayouts:** regístrate en <https://www.travelpayouts.com> (es gratis) y copia tu token
   desde *Herramientas → API*.
2. **SerpApi:** regístrate en <https://serpapi.com> y copia tu clave desde
   <https://serpapi.com/manage-api-key>.
3. **En local** (opcional): guarda el token en `token.txt` y la clave en `serpapi_key.txt`. Los dos
   ficheros están en `.gitignore`, así que nunca se suben al repositorio.
4. **Rutas y alertas:** edita `rutas.toml`.

## Uso

```
python -m vuelos buscar MAD LON 2027-02 --max-escalas 0 --precio-max 60    # Europa (gratis)
python -m vuelos google MAD IAH 2027-03-10 --regreso 2027-03-24             # Google (1 búsqueda)
python -m vuelos cuota                   # búsquedas de SerpApi usadas y restantes (no gasta)
python -m vuelos recoger                 # recogida completa (Europa + Google si toca)
python -m vuelos recoger --solo-google --forzar-google
python -m vuelos historico MAD IAH
python -m vuelos ofertas --destino PDX
python -m vuelos alertas
python -m vuelos informe                 # crea informe.html con las gráficas
```

## Consumo de SerpApi

Por defecto (sección `[google]` de `rutas.toml`):

- **Madrid:** 3 fechas de salida (a 45, 90 y 150 días vista).
- **Alicante:** 1 fecha (a 45 días).
- **Estancia:** ida y vuelta de 14 días.

Son 4 fechas × 2 destinos = **8 búsquedas por consulta**. Con una consulta cada 3 días salen
**unas 80 búsquedas al mes**.

- Antes de cada consulta se mira la cuota que queda, y nunca se gastan las últimas 10 búsquedas
  (`reserva_busquedas`).
- Si no hay cuota para todo, se recorta empezando por lo último de la lista (Alicante), así que
  Madrid tiene prioridad.
- Para vigilar las fechas de un viaje concreto, añádelas en `fechas_salida`.

## Viajes vigilados (fechas exactas)

Además del histórico general, en `rutas.toml` puedes definir viajes concretos con `[[viajes]]`:
destino, orígenes, parejas de fechas (ida, vuelta), máximo de escalas y precio umbral.

- **Cuándo se consultan:** cada día en Google Flights, hasta que pasa la fecha de ida.
- **Prioridad:** tienen preferencia sobre el histórico general cuando escasea la cuota de SerpApi.
- **Resumen:** se publica en `docs/viajes.json`, con el precio actual, el de hace 7 días, el mínimo
  visto, el nivel según Google, el rango típico, las 3 mejores opciones y la evolución.
  Es lo que lee la rutina semanal:
  <https://raw.githubusercontent.com/pedrojlucas/monitor-vuelos/main/docs/viajes.json>
- **Alertas:** se abre una issue si una opción baja del umbral (o baja más), o si Google la pasa a
  marcar como precio bajo.
- **En local:** `python -m vuelos viajes` muestra el estado (no gasta búsquedas).

Los precios no incluyen la maleta facturada.

## Recogida en GitHub Actions (sin tener el PC encendido)

El workflow `.github/workflows/recoger.yml` se ejecuta cada día a las 05:17 UTC. En cada ejecución:

1. recoge los precios de Europa y, si toca, consulta las rutas de Google;
2. guarda cada día en `datos/AAAA/AAAA-MM-DD.csv.gz` y hace un commit (es el histórico permanente);
3. regenera el informe en `docs/index.html` (publicado con GitHub Pages si lo activas);
4. si alguna ruta baja de su umbral, o Google marca un precio como **bajo**, **abre una issue**,
   y GitHub te avisa por correo.

Secrets necesarios (**Settings → Secrets and variables → Actions**):

- `TRAVELPAYOUTS_TOKEN`
- `SERPAPI_KEY`. Sin esta clave, la parte de Google se omite y Europa sigue funcionando.

El workflow manual **"Diagnóstico de cobertura de la API"** prueba qué rutas tienen datos en
Travelpayouts.

En local, cada orden importa automáticamente los CSV nuevos o modificados de `datos/`, así que
basta con hacer `git pull` para tener el histórico al día.

### Alternativa local

`recoger.bat` hace lo mismo con el Programador de tareas de Windows, pero el PC tiene que estar
encendido.

## Uso desde Claude (MCP)

```
pip install mcp
claude mcp add vuelos -- python "RUTA\vuelos\mcp_server.py"
```

Herramientas que expone:

- `buscar_vuelos`
- `buscar_google_flights`
- `cuota_serpapi`
- `historico_ruta`
- `mejores_ofertas`
- `alertas_precio`
- `rutas_configuradas`

## Pruebas

`python -m unittest discover -s tests -t .` (no usan la red: clientes falsos imitan las dos APIs).
