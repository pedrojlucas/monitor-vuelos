"""Genera un HTML autónomo con la evolución de precios por ruta (Chart.js desde CDN)."""
import json
from pathlib import Path

from . import core

PLANTILLA = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Precios de vuelos</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#fafaf8;--fg:#1d1d1b;--muted:#6b6b66;--card:#fff;--line:#e4e4df;--a:#2a6fdb;--b:#d9822b;--ok:#1f8a4c}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ececea;--muted:#9a9a94;--card:#1f1f1e;--line:#33332f;--a:#6ea2ff;--b:#f0a35a;--ok:#4cc27e}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px}
h1{font-size:22px;margin:0 0 4px}.sub{color:var(--muted);margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:800px){.grid{grid-template-columns:1fr}}
select{font:inherit;padding:6px 8px;border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}.bajo{color:var(--ok);font-weight:600}
.kpi{display:flex;gap:24px;flex-wrap:wrap}.kpi div b{display:block;font-size:22px}.kpi div span{color:var(--muted);font-size:13px}
.wrap{overflow-x:auto}
</style></head><body><main>
<h1>Precios de vuelos</h1>
<p class="sub">Datos recogidos desde __DESDE__ hasta __HASTA__ · moneda __MONEDA__ · fuente: caché de Aviasales (Travelpayouts)</p>
<div class="card"><label>Ruta: <select id="ruta"></select></label>
<div class="kpi" style="margin-top:12px"><div><b id="k-hoy">–</b><span>mínimo actual</span></div>
<div><b id="k-min">–</b><span>mínimo histórico</span></div><div><b id="k-med">–</b><span>media de mínimos</span></div></div></div>
<div class="grid">
<div class="card"><h3>Evolución del mínimo (por día de recogida)</h3><canvas id="g1" height="220"></canvas></div>
<div class="card"><h3>Mínimo por mes de salida (última recogida)</h3><canvas id="g2" height="220"></canvas></div>
</div>
<div class="card"><h3>Mejores precios ahora</h3><div class="wrap"><table id="ofertas"></table></div></div>
</main>
<script>
const D=__DATOS__;const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const sel=document.getElementById('ruta');Object.keys(D.rutas).forEach(r=>sel.add(new Option(r,r)));
let g1,g2;const fmt=v=>v==null?'–':Math.round(v)+' '+D.moneda;
function pintar(){const r=D.rutas[sel.value];if(!r)return;
 const ev=r.evolucion,pm=r.por_mes_salida;
 document.getElementById('k-hoy').textContent=fmt(ev.length?ev[ev.length-1].minimo:null);
 document.getElementById('k-min').textContent=fmt(r.minimo_historico&&r.minimo_historico.precio);
 document.getElementById('k-med').textContent=fmt(ev.length?ev.reduce((s,x)=>s+x.minimo,0)/ev.length:null);
 g1&&g1.destroy();g2&&g2.destroy();Chart.defaults.color=css('--muted');Chart.defaults.borderColor=css('--line');
 g1=new Chart(document.getElementById('g1'),{type:'line',data:{labels:ev.map(x=>x.observado),datasets:[
  {label:'mínimo',data:ev.map(x=>x.minimo),borderColor:css('--a'),backgroundColor:css('--a'),tension:.2,pointRadius:2},
  {label:'media',data:ev.map(x=>x.medio),borderColor:css('--b'),backgroundColor:css('--b'),tension:.2,pointRadius:0,borderDash:[4,4]}]},
  options:{interaction:{mode:'index',intersect:false}}});
 g2=new Chart(document.getElementById('g2'),{type:'bar',data:{labels:pm.map(x=>x.mes),datasets:[
  {label:'mínimo',data:pm.map(x=>x.minimo),backgroundColor:css('--a')}]},options:{plugins:{legend:{display:false}}}});}
sel.onchange=pintar;pintar();
const t=document.getElementById('ofertas');
t.innerHTML='<tr><th>Ruta</th><th>Salida</th><th class="num">Precio</th><th class="num">Media ruta</th><th>Aerolínea</th><th class="num">Escalas</th><th></th></tr>'+
 D.ofertas.map(o=>`<tr><td>${o.origen}-${o.destino}</td><td>${o.salida}</td><td class="num ${o.precio<o.media_ruta*0.85?'bajo':''}">${fmt(o.precio)}</td><td class="num">${fmt(o.media_ruta)}</td><td>${o.aerolinea||''}</td><td class="num">${o.escalas??''}</td><td>${o.enlace?`<a href="${o.enlace}" target="_blank" rel="noopener">ver</a>`:''}</td></tr>`).join('');
</script></body></html>"""


def generar(con, cfg, salida: str = "informe.html") -> Path:
    rutas = {}
    for o, d in con.execute("SELECT DISTINCT origen, destino FROM observaciones ORDER BY origen, destino"):
        rutas[f"{o}-{d}"] = core.historico(con, o, d)
    rango = con.execute("SELECT MIN(observado), MAX(observado) FROM observaciones").fetchone()
    datos = {"moneda": cfg.moneda.upper(), "rutas": rutas, "ofertas": core.ofertas(con, limite=40)}
    html = (PLANTILLA.replace("__DATOS__", json.dumps(datos, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__DESDE__", str(rango[0] or "–")).replace("__HASTA__", str(rango[1] or "–"))
            .replace("__MONEDA__", cfg.moneda.upper()))
    ruta = Path(salida).resolve()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(html, encoding="utf-8")
    return ruta
