'use strict';
// Todo el texto que viene de la red se pinta como texto, nunca como HTML.
const $ = (id) => document.getElementById(id);
function h(tag, props, ...hijos) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'style') Object.assign(e.style, v);
    else if (k === 'class') e.className = v;
    else if (k === 'value') e.value = v;
    else e.setAttribute(k, v === true ? '' : v);
  }
  e.append(...hijos.flat().filter((x) => x !== null && x !== undefined && x !== false));
  return e;
}

// ---------------------------------------------------------------- idioma
let T = {}, LANG = 'es';
const t = (k, v) => String(T[k] ?? k).replace(/\{(\w+)\}/g, (_, n) => (v && v[n] !== undefined ? v[n] : ''));
const LOC = () => (LANG === 'es' ? 'es-ES' : 'en-GB');
const guardado = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch { /* sin almacenamiento */ } return null; };
async function cargarIdioma(l) {
  const r = await fetch('i18n/' + l + '.json');
  T = await r.json(); LANG = l;
  document.documentElement.lang = l;
  document.title = t('titulo');
}
const idiomaPreferido = (servidor) => {
  const l = guardado('idioma');
  if (l === 'es' || l === 'en') return l;
  if (servidor === 'es' || servidor === 'en') return servidor;
  return (navigator.language || 'es').toLowerCase().startsWith('es') ? 'es' : 'en';
};

const st = { s: null, d: null, aj: null, q: '', tipo: 'todos', sel: null, dia: 0, quien: null, hist: null, editando: false, msg: '', traf: null, rango: 86400, punto: null, sobre: false, tabla: false, primera: false };
const porMac = (m) => st.d.equipos.find((e) => e.mac === m);
const nodo = (id) => st.d.nodos.find((n) => n.id === id);
const nombre = (e) => e.nombre || e.host || (e.mac === st.d.yo ? t('este_panel') : e.mac === st.d.router_mac ? t('router') : t(e.privada ? 'sin_id_privada' : 'sin_id'));
const nombreDe = (id) => (id === 'internet' ? t('internet') : porMac(id) ? nombre(porMac(id)) : nodo(id) ? nodo(id).nombre : '');
const dos = (n) => String(n).padStart(2, '0');
const hora = (s) => { const x = new Date(s * 1000); return dos(x.getHours()) + ':' + dos(x.getMinutes()); };
const fecha = (s) => new Date(s * 1000).toLocaleDateString(LOC(), { day: 'numeric', month: 'short' });
const cuando = (s) => (new Date(s * 1000).toDateString() === new Date().toDateString() ? hora(s) : fecha(s) + ' ' + hora(s));
const hace = (s) => { const x = Date.now() / 1000 - s; return x < 90 ? t('hace_s', { n: Math.max(0, Math.round(x)) }) : x < 5400 ? t('hace_min', { n: Math.round(x / 60) }) : cuando(s); };
const ipn = (ip) => (ip || '').split('.').reduce((a, b) => a * 256 + +b, 0);
const num = (x, dec) => x.toLocaleString(LOC(), { maximumFractionDigits: dec });
function dur(s) {
  const m = Math.floor(s / 60);
  if (m < 1) return t('dur.menos1');
  const d = Math.floor(m / 1440), hh = Math.floor((m % 1440) / 60), mm = m % 60;
  return [d ? t('dur.d', { n: d }) : '', hh ? t('dur.h', { n: hh }) : '', mm && !d ? t('dur.min', { n: mm }) : ''].filter(Boolean).join(' ');
}
// Puntos de conexion: codigos que manda el servidor (LAN1, wifi5, wifi24-2...).
function etPunto(p) {
  const m = /^wifi(5|24)(?:-(\d))?$/.exec(p || '');
  if (!m) return p || '';
  return t(m[1] === '5' ? 'punto.wifi5' : 'punto.wifi24') + (m[2] ? ' ' + t('punto.red', { n: m[2] }) : '');
}
function colorPunto(p) {
  if (p.startsWith('wifi5')) return '#3987e5';
  if (p.startsWith('wifi24')) return '#d95926';
  return st.d.bocas[p] && porMac(st.d.bocas[p]) ? '#199e70' : '#8fa1a8';
}
const etBoca = (b) => (/^wifi/.test(b || '') ? etPunto(b) : b || '');
const etTipo = (k) => t('tipo.' + k);
const MEDIOS = ['cable', 'wifi', 'virtual', ''];

async function api(ruta, datos) {
  const op = datos === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(datos) };
  let r;
  try { r = await fetch('api/' + ruta, op); } catch { return { error: 'sin_conexion' }; }
  if (r.status === 401 && ruta !== 'entrar') { iniciar(); return { error: 'sin_sesion' }; }
  try { return await r.json(); } catch { return { error: 'no_valido' }; }
}
const errorTxt = (e) => t('error.' + e);

// ---------------------------------------------------------------- cabecera
const SECCIONES = ['resumen', 'mapa', 'equipos', 'historial', 'ajustes'];
function pintarCabecera() {
  const dentro = st.s && st.s.autenticado;
  const otro = LANG === 'es' ? 'en' : 'es';
  $('cab').replaceChildren(...[
    h('b', { class: 'marca' }, (st.d && st.d.nombre) || (st.s && st.s.nombre) || t('titulo')),
    dentro ? h('nav', { 'aria-label': t('secciones') }, SECCIONES.map((s) => h('a', { href: '#' + s, 'aria-current': vista() === s ? 'page' : null }, t('nav.' + s)))) : h('span', { class: 'hueco' }),
    dentro ? h('span', { id: 'escaneo', class: 'm', role: 'status' }) : null,
    h('button', { class: 'pill chico', title: t('cambiar_idioma'), 'aria-label': t('cambiar_idioma'), onclick: async () => { guardado('idioma', otro); await cargarIdioma(otro); if (st.s && st.s.autenticado) repintar(); else iniciar(); } }, otro.toUpperCase()),
    dentro ? h('button', { class: 'pill chico', onclick: async () => { await api('salir', {}); iniciar(); } }, t('salir')) : null].filter(Boolean));
}

// ---------------------------------------------------------------- primera vez y acceso
function verBienvenida() {
  pintarCabecera();
  const err = h('p', { class: 'err', role: 'alert' });
  $('vista').replaceChildren(h('section', { class: 'card estrecha' },
    h('h1', {}, t('bienvenida.titulo')), h('p', { class: 's' }, t('bienvenida.texto')),
    h('form', { onsubmit: async (ev) => {
      ev.preventDefault();
      const f = Object.fromEntries(new FormData(ev.target));
      if (f.clave !== f.clave2) { err.textContent = t('error.no_coinciden'); return; }
      const r = await api('configurar', { clave: f.clave, nombre: f.nombre, idioma: LANG });
      if (r.error === 'ya_configurado') { iniciar(); return; }  // otra pestaña se adelanto: a entrar
      if (r.error) { err.textContent = errorTxt(r.error); return; }
      st.primera = true; location.hash = '#ajustes'; iniciar();
    } },
    h('label', {}, t('bienvenida.nombre'), h('input', { name: 'nombre', maxlength: 40, value: t('nombre_defecto') })),
    h('label', {}, t('bienvenida.clave'), h('input', { name: 'clave', type: 'password', minlength: 8, required: true, autocomplete: 'new-password' })),
    h('label', {}, t('bienvenida.clave2'), h('input', { name: 'clave2', type: 'password', minlength: 8, required: true, autocomplete: 'new-password' })),
    h('p', { class: 'm' }, t('bienvenida.nota')),
    h('div', { class: 'pie' }, h('button', { class: 'pill pri' }, t('bienvenida.empezar'))), err)));
}

function verEntrar() {
  pintarCabecera();
  const err = h('p', { class: 'err', role: 'alert' });
  $('vista').replaceChildren(h('section', { class: 'card estrecha' }, h('h1', {}, t('entrar.titulo')),
    h('form', { onsubmit: async (ev) => {
      ev.preventDefault();
      const r = await api('entrar', { clave: new FormData(ev.target).get('clave') });
      if (r.error) { err.textContent = errorTxt(r.error); return; }
      iniciar();
    } },
    h('label', {}, t('entrar.clave'), h('input', { name: 'clave', type: 'password', required: true, autocomplete: 'current-password', autofocus: true })),
    h('div', { class: 'pie' }, h('button', { class: 'pill pri' }, t('entrar.boton'))), err)));
}

// ---------------------------------------------------------------- piezas comunes
function led(e) {
  const c = !e.online ? '' : e.conocido ? ' on' : ' av';
  return h('span', { class: 'led' + c, 'aria-hidden': 'true' });
}
const estadoTxt = (e) => t(!e.online ? 'estado.off' : e.conocido ? 'estado.on' : 'estado.nuevo');
const AVISO = { nuevo: 'av', mac: 'av', conflicto: 'av' };
function textoEvento(v) {
  if (!v.datos) return v.texto || '';
  let d;
  try { d = JSON.parse(v.datos); } catch { return ''; }
  if (v.clase === 'desconecta' && d.dur === undefined) return '';
  const vals = { ...d, dur: d.dur !== undefined ? dur(d.dur) : '', de: v.clase === 'punto' ? etPunto(d.de) : d.de, a: v.clase === 'punto' ? etPunto(d.a) : d.a };
  return t('evtxt.' + v.clase, vals);
}
function evento(v) {
  const e = porMac(v.mac), txt = textoEvento(v);
  return h('div', { class: 'ev' }, h('span', { class: 'm mono' }, cuando(v.ts)),
    h('div', {}, h('span', { class: 'est' }, AVISO[v.clase] ? h('span', { class: 'led av', 'aria-hidden': 'true' }) : null, h('b', {}, t('ev.' + v.clase))), h('br'),
      h('span', { class: 's' }, [e ? nombre(e) : v.mac, v.ip, txt].filter(Boolean).join(' · '))));
}
const mbps = (b) => num(b / 1e6, b / 1e6 < 10 ? 1 : 0);
const tam = (b) => (b >= 1e9 ? num(b / 1e9, 1) + ' GB' : num(b / 1e6, 0) + ' MB');
const RANGOS = [[3600, '1h'], [86400, '24h'], [604800, '7d']];
function escala(max) {
  const paso = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000].find((p) => p * 4 >= max) || 2000;
  return [0, 1, 2, 3, 4].map((i) => i * paso);
}

// ---------------------------------------------------------------- resumen
function grafico() {
  const T2 = st.traf;
  const botones = h('div', { class: 'barra' }, RANGOS.map(([s, k]) => h('button', { class: 'pill', 'aria-pressed': String(s === st.rango), onclick: () => { st.rango = s; st.traf = null; st.punto = null; repintar(); cargarTraf(); } }, t('rango.' + k))));
  const cab = h('div', { class: 'cabg' }, h('h2', {}, t('graf.titulo.' + RANGOS.find((x) => x[0] === st.rango)[1])), botones);
  const av = st.d.bajada.aviso ? h('p', { class: 's est' }, h('span', { class: 'led av', 'aria-hidden': 'true' }), t('bajada.' + st.d.bajada.aviso)) : null;
  if (!T2) return h('section', { class: 'card' }, cab, av, h('p', { class: 's' }, t('cargando')));
  if (!T2.puntos.length) return h('section', { class: 'card' }, cab, av, h('p', { class: 's' }, t('graf.vacio')));
  const P = T2.puntos, durR = T2.hasta - T2.desde, W = 1000, H = 220;
  const ejes = escala(Math.max(...P.map((p) => p[1])) / 1e6), max = ejes[4] * 1e6;
  const x = (s) => (s + T2.paso / 2 - T2.desde) / durR * W, y = (b) => H - b / max * H;
  const tramos = [];
  P.forEach((p, i) => { if (!i || p[0] - P[i - 1][0] > T2.paso) tramos.push([]); tramos[tramos.length - 1].push(p); });
  const svgNS = 'http://www.w3.org/2000/svg', svg = document.createElementNS(svgNS, 'svg');
  const at = (e, o) => { Object.entries(o).forEach(([k, v]) => e.setAttribute(k, v)); return e; };
  at(svg, { viewBox: '0 0 ' + W + ' ' + H, preserveAspectRatio: 'none', role: 'img', 'aria-label': t('graf.aria', { max: mbps(Math.max(...P.map((p) => p[1]))) }) });
  tramos.forEach((tr) => {
    const pts = tr.length > 1 ? tr.map((p) => [x(p[0]), y(p[1])]) : [[x(tr[0][0]) - W * T2.paso / durR / 2, y(tr[0][1])], [x(tr[0][0]) + W * T2.paso / durR / 2, y(tr[0][1])]];
    const l = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join('');
    svg.append(at(document.createElementNS(svgNS, 'path'), { d: l + 'L' + pts[pts.length - 1][0].toFixed(1) + ' ' + H + 'L' + pts[0][0].toFixed(1) + ' ' + H + 'Z', fill: '#3987e5', 'fill-opacity': '.1' }),
      at(document.createElementNS(svgNS, 'path'), { d: l, fill: 'none', stroke: '#3987e5', 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round', 'vector-effect': 'non-scaling-stroke' }));
  });
  const et = (s) => (st.rango > 86400 ? cuando(s) : hora(s));
  const guia = h('div', { class: 'guia', hidden: true }), tip = h('div', { class: 'tip', hidden: true });
  const poner = (i) => {
    st.punto = i;
    guia.hidden = tip.hidden = i === null;
    if (i === null) return;
    const p = P[i], izq = x(p[0]) / W * 100;
    guia.style.left = izq + '%';
    tip.style.left = izq > 60 ? 'auto' : 'calc(' + izq + '% + 10px)';
    tip.style.right = izq > 60 ? 'calc(' + (100 - izq) + '% + 10px)' : 'auto';
    tip.replaceChildren(h('div', { class: 'm mono' }, et(p[0]) + ' – ' + hora(p[0] + T2.paso)), h('div', {}, t('graf.media') + ' ', h('b', {}, mbps(p[1]) + ' Mbps')), h('div', {}, t('graf.pico') + ' ', h('b', {}, mbps(p[2]) + ' Mbps')));
  };
  const plot = h('div', { class: 'plot', tabindex: 0, 'aria-label': t('graf.teclas'),
    onpointerenter: () => { st.sobre = true; }, onpointerleave: () => { st.sobre = false; poner(null); }, onblur: () => { st.sobre = false; poner(null); },
    onpointermove: (ev) => {
      const b = ev.currentTarget.getBoundingClientRect(), s = T2.desde + (ev.clientX - b.left) / b.width * durR - T2.paso / 2;
      let m = 0; P.forEach((p, i) => { if (Math.abs(p[0] - s) < Math.abs(P[m][0] - s)) m = i; });
      poner(m);
    },
    onkeydown: (ev) => {
      const d = { ArrowLeft: -1, ArrowRight: 1 }[ev.key];
      if (!d) return;
      ev.preventDefault(); st.sobre = true;
      poner(st.punto === null ? (d < 0 ? P.length - 1 : 0) : Math.min(P.length - 1, Math.max(0, st.punto + d)));
    } },
    h('div', { class: 'rej', 'aria-hidden': 'true' }, ejes.map(() => h('span'))), svg, guia, tip);
  const marcas = [0, 1, 2, 3, 4].map((i) => T2.desde + durR * i / 4);
  const pico = P.reduce((a, b) => (b[2] > a[2] ? b : a));
  const cada = T2.paso >= 3600 ? t('dur.h', { n: T2.paso / 3600 }) : t('dur.min', { n: T2.paso / 60 });
  const cuandoPico = T2.paso > 60 ? t('graf.entre', { a: cuando(pico[0]), b: hora(pico[0] + T2.paso) }) : cuando(pico[0]);
  const medido = T2.segundos >= 5400 ? t('dur.h', { n: Math.round(T2.segundos / 3600) }) : t('dur.min', { n: Math.round(T2.segundos / 60) });
  const falta = durR - T2.segundos > durR * 0.05 ? ' ' + t('graf.medido', { t: medido }) : '';
  const det = h('details', { open: st.tabla, ontoggle: (ev) => { st.tabla = ev.target.open; } }, h('summary', { class: 's' }, t('ver_tabla')),
    h('div', { class: 'tablag mono' }, P.slice().reverse().map((p) => h('div', {}, h('span', { class: 'm' }, et(p[0])), h('span', {}, t('graf.media') + ' ' + mbps(p[1])), h('span', {}, t('graf.pico') + ' ' + mbps(p[2]))))));
  return h('section', { class: 'card' }, cab, av,
    h('p', { class: 's nota' }, t('graf.nota', { cada, total: tam(T2.bytes), pico: mbps(pico[2]), cuando: cuandoPico }) + falta),
    h('div', { class: 'graf' }, h('div', { class: 'ejey m mono' }, ejes.slice().reverse().map((v) => h('span', {}, num(v, 1)))), plot, h('span'),
      h('div', { class: 'ejex m mono' }, marcas.map((s) => h('span', {}, et(s))))), det);
}

function verResumen() {
  const q = st.d.equipos, on = q.filter((e) => e.online), nuevos = q.filter((e) => !e.conocido);
  const k = (tt, v, s) => h('div', { class: 'card' }, h('div', { class: 's' }, tt), h('div', { class: 'kv' }, String(v)), h('div', { class: 'm' }, s));
  const cuenta = (m) => on.filter((e) => e.medio === m).length;
  const tipos = st.d.tipos.map((tp) => [tp, q.filter((e) => e.tipo === tp && e.online), q.filter((e) => e.tipo === tp)]).filter((x) => x[2].length);
  return [
    nuevos.length > 3 ? h('div', { class: 'aviso azul' }, t('primeros_pasos', { n: nuevos.length }), ' ', h('a', { href: '#equipos' }, t('ir_equipos'))) : null,
    h('section', { class: 'kpis', 'aria-label': t('cifras') },
      k(t('kpi.bajada'), st.d.bajada.bps === null ? '—' : mbps(st.d.bajada.bps) + ' Mbps', st.d.bajada.bps === null ? t('kpi.sin_lectura') : t('kpi.toda_casa')),
      k(t('kpi.conectados'), on.length, t('kpi.de_conocidos', { n: q.length })),
      k(t('kpi.cable'), cuenta('cable'), t('kpi.virtuales', { n: cuenta('virtual') })),
      k(t('kpi.wifi'), cuenta('wifi'), t('kpi.sin_definir', { n: on.filter((e) => !e.medio).length })),
      k(t('kpi.identificar'), nuevos.length, nuevos.length ? t('kpi.abrelos') : t('kpi.todo')),
      k(t('kpi.cambios'), st.d.cambios_hoy, t('kpi.cambios_txt'))),
    grafico(),
    h('div', { class: 'cols' },
      h('section', { class: 'card ancha' }, h('h2', {}, t('avisos')),
        st.d.eventos.length ? st.d.eventos.map(evento) : h('p', { class: 's' }, t('avisos.vacio'))),
      h('section', { class: 'card lado' }, h('h2', {}, t('por_tipo')),
        tipos.map(([tp, a, b]) => h('div', { class: 'f' }, h('span', { class: 's' }, etTipo(tp)), h('span', {}, t('x_de_y', { a: a.length, b: b.length }))))))
  ];
}

// ---------------------------------------------------------------- mapa
// Arbol de izquierda a derecha: cada hoja ocupa una fila y el padre se centra entre sus hijos.
function verMapa() {
  const nodos = new Map([['internet', { id: 'internet', padre: '', n: { nombre: t('internet'), boca: '' }, hijos: [] }]]);
  st.d.nodos.forEach((n) => nodos.set(n.id, { id: n.id, padre: n.padre, n, hijos: [] }));
  st.d.equipos.forEach((e) => nodos.set(e.mac, { id: e.mac, padre: e.padre, e, hijos: [] }));
  const sueltos = [];
  nodos.forEach((x) => {
    if (x.id === 'internet') return;
    const p = nodos.get(x.padre);
    if (p) p.hijos.push(x); else if (x.e) sueltos.push(x);
  });
  const orden = (a, b) => (a.hijos.length ? 1 : 0) - (b.hijos.length ? 1 : 0) || ipn((a.e || {}).ip) - ipn((b.e || {}).ip);
  const W = 204, H = 60, PX = 268, PY = 74;
  let fila = 0, maxX = 0;
  const puestos = [], visto = new Set();
  (function poner(x, nivel) {
    visto.add(x.id);
    x.x = nivel * PX; maxX = Math.max(maxX, x.x);
    x.hijos = x.hijos.filter((y) => !visto.has(y.id));
    x.hijos.sort(orden).forEach((y) => poner(y, nivel + 1));
    x.y = x.hijos.length ? (x.hijos[0].y + x.hijos[x.hijos.length - 1].y) / 2 : PY * fila++;
    puestos.push(x);
  })(nodos.get('internet'), 0);
  const svgNS = 'http://www.w3.org/2000/svg', alto = fila * PY, ancho = maxX + W;
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('width', ancho); svg.setAttribute('height', alto); svg.setAttribute('aria-hidden', 'true');
  puestos.forEach((x) => x.hijos.forEach((y) => {
    const p = document.createElementNS(svgNS, 'path'), x1 = x.x + W, y1 = x.y + H / 2, y2 = y.y + H / 2, mx = x1 + (PX - W) / 2;
    const m = y.e ? y.e.medio : y.n.boca === 'wifi' ? 'wifi' : 'cable';
    p.setAttribute('d', 'M' + x1 + ' ' + y1 + 'H' + mx + 'V' + y2 + 'H' + y.x);
    p.setAttribute('fill', 'none'); p.setAttribute('stroke-width', '2');
    p.setAttribute('stroke', m === 'wifi' ? '#6da7ec' : m === 'virtual' ? '#8fa1a8' : '#5b7079');
    if (m === 'wifi') p.setAttribute('stroke-dasharray', '6 5');
    if (m === 'virtual') p.setAttribute('stroke-dasharray', '2 4');
    svg.append(p);
  }));
  const caja = (x) => {
    const pos = { left: x.x + 'px', top: x.y + 'px' };
    if (!x.e) return h('div', { class: 'n', style: pos }, h('span', {}, h('b', {}, x.n.nombre), h('i', {}, x.id === 'internet' ? t('fibra') : x.n.boca === 'wifi' ? t('medio.wifi') : x.n.boca || t('nodo_sin_ip'))));
    const e = x.e;
    return h('button', { class: 'n' + (e.online ? '' : ' off') + (e.medio === 'virtual' ? ' virt' : ''), style: pos, title: nombre(e) + ' · ' + e.mac, onclick: () => abrirEquipo(e.mac) },
      led(e), h('span', {}, h('b', {}, nombre(e)), h('i', { class: 'mono' }, e.ip + (e.boca ? ' · ' + etBoca(e.boca) : ''))));
  };
  const ley = (c, tt) => h('span', {}, h('i', { class: c }), tt);
  return [
    h('section', { class: 'card' }, h('h2', {}, t('mapa.titulo')),
      h('div', { class: 'ley' }, ley('', t('medio.cable')), ley('w', t('medio.wifi')), ley('v', t('medio.virtual')),
        h('span', {}, h('span', { class: 'led on' }), t('estado.on')), h('span', {}, h('span', { class: 'led' }), t('estado.off')), h('span', {}, h('span', { class: 'led av' }), t('estado.nuevo'))),
      h('p', { class: 'm' }, t('mapa.ayuda'), ' ', h('a', { href: '#ajustes' }, t('nav.ajustes'))),
      h('div', { class: 'mapa' }, h('div', { class: 'lienzo', style: { width: ancho + 'px', height: alto + 'px' } }, svg, puestos.map(caja)))),
    sueltos.length ? h('section', { class: 'card' }, h('h2', {}, t('mapa.sueltos')),
      h('div', { class: 'barra' }, sueltos.map((x) => h('button', { class: 'pill', onclick: () => abrirEquipo(x.e.mac) }, nombre(x.e) + ' · ' + x.e.ip)))) : null
  ];
}

// ---------------------------------------------------------------- dispositivos
function abrirEquipo(mac) { st.sel = mac; st.editando = false; st.msg = ''; st.q = ''; st.tipo = 'todos'; location.hash = '#equipos'; repintar(); }

function opcionesPadre(excluir) {
  return [['', t('sin_definir')], ['internet', t('internet')]].concat(
    st.d.nodos.filter((n) => n.id !== excluir).map((n) => [n.id, n.nombre + ' (' + t('nodo') + ')']),
    st.d.equipos.filter((x) => x.mac !== excluir).sort((a, b) => nombre(a).localeCompare(nombre(b))).map((x) => [x.mac, nombre(x) + ' · ' + x.ip]));
}
const sel = (n, ops, v) => h('select', { name: n }, ops.map(([a, b]) => h('option', { value: a, selected: a === v }, b)));

function ficha(e) {
  const f = (a, b, c) => h('div', { class: 'f' }, h('span', { class: 'm' }, a), h('span', { class: c || '' }, b || '—'));
  if (st.editando) return formulario(e);
  const ro = e.r_ts ? [etPunto(e.r_punto), e.r_rssi != null ? e.r_rssi + ' dBm' : '', e.r_tx ? t('velocidad', { tx: Math.round(e.r_tx / 1000), rx: Math.round((e.r_rx || 0) / 1000) }) : '', e.r_modo, hace(e.r_ts)].filter(Boolean).join(' · ') : null;
  return h('aside', { class: 'card lado' }, h('h2', { style: { fontSize: '20px' } }, nombre(e)),
    f(t('f.estado'), h('span', { class: 'est' }, led(e), estadoTxt(e))), f('IP', e.ip, 'mono'), f('MAC', e.mac + (e.privada ? ' ' + t('privada') : ''), 'mono'),
    f(t('f.fabricante'), e.fabricante), f(t('f.host'), e.host), f(t('f.tipo'), etTipo(e.tipo)), f(t('f.cuelga'), nombreDe(e.padre) || t('sin_definir')),
    f(t('f.conexion'), (e.medio === 'wifi' && /^wifi/.test(e.boca || '') ? etBoca(e.boca) : t('medio.' + (e.medio || 'nada')) + (e.boca ? ' · ' + etBoca(e.boca) : '')) + (e.auto && e.r_ts ? ' ' + t('segun_router') : '')),
    ro ? f(t('f.router'), ro) : null,
    f(t('f.desde'), cuando(e.visto_1)), f(t('f.ultima'), cuando(e.visto_n)),
    h('div', { class: 'pie' }, h('button', { class: 'pill pri', onclick: () => { st.editando = true; st.msg = ''; repintar(); } }, t(e.conocido ? 'editar' : 'identificar')),
      h('span', { class: 's', role: 'status' }, st.msg)));
}

function formulario(e) {
  const err = h('span', { class: 'err', role: 'status' });
  return h('aside', { class: 'card lado' }, h('h2', {}, t('editar_x', { x: nombre(e) })),
    h('form', { onsubmit: async (ev) => {
      ev.preventDefault();
      const d = Object.fromEntries(new FormData(ev.target)); d.mac = e.mac;
      const r = await api('equipo', d);
      if (r.error) { err.textContent = errorTxt(r.error); return; }
      st.editando = false; st.msg = t('guardado'); await cargar(); repintar();
    } },
    h('label', {}, t('f.nombre'), h('input', { name: 'nombre', maxlength: 40, value: e.nombre, placeholder: e.host || t('ej_nombre') })),
    h('label', {}, t('f.tipo'), sel('tipo', st.d.tipos.map((tp) => [tp, etTipo(tp)]), e.tipo)),
    h('label', {}, t('f.cuelga'), sel('padre', opcionesPadre(e.mac), e.padre)),
    h('label', {}, t('f.conexion'), sel('medio', MEDIOS.map((m) => [m, t('medio.' + (m || 'nada'))]), e.medio)),
    h('label', {}, t('f.boca'), h('input', { name: 'boca', maxlength: 40, value: etBoca(e.boca), placeholder: t('ej_boca') })),
    h('div', { class: 'pie' }, h('button', { class: 'pill pri' }, t('guardar')),
      h('button', { class: 'pill', type: 'button', onclick: () => { st.editando = false; repintar(); } }, t('cancelar')), err)));
}

function filas() {
  const q = st.q.trim().toLowerCase();
  return st.d.equipos.filter((e) => (st.tipo === 'todos' || (st.tipo === 'nuevos' ? !e.conocido : e.tipo === st.tipo)) && (!q || [nombre(e), e.host, e.ip, e.mac, e.fabricante].join(' ').toLowerCase().includes(q)))
    .sort((a, b) => ipn(a.ip) - ipn(b.ip))
    .map((e) => h('button', { class: 'g row', 'aria-pressed': String(e.mac === st.sel), onclick: () => { st.sel = e.mac; st.editando = false; st.msg = ''; repintar(); } },
      h('span', {}, nombre(e)), h('span', { class: 's' }, etTipo(e.tipo)), h('span', { class: 'mono' }, e.ip), h('span', { class: 'mono s' }, e.mac),
      h('span', { class: 's' }, nombreDe(e.padre) || t('sin_definir')), h('span', { class: 'est s' }, led(e), estadoTxt(e))));
}

function verEquipos() {
  if (!porMac(st.sel)) st.sel = ((st.d.equipos.find((e) => !e.conocido) || st.d.equipos[0]) || {}).mac;
  const cuerpo = h('div', {});
  const pon = () => { const f = filas(); cuerpo.replaceChildren(...(f.length ? f : [h('p', { class: 's' }, t('nada_coincide'))])); };
  pon();
  const e = porMac(st.sel), nuevos = st.d.equipos.filter((x) => !x.conocido).length;
  const filtros = ['todos'].concat(nuevos ? ['nuevos'] : [], st.d.tipos);
  return [
    h('div', { class: 'barra' },
      h('input', { type: 'search', 'aria-label': t('buscar'), placeholder: t('buscar'), value: st.q, oninput: (ev) => { st.q = ev.target.value; pon(); } }),
      filtros.map((tp) => h('button', { class: 'pill', 'aria-pressed': String(tp === st.tipo), onclick: () => { st.tipo = tp; repintar(); } }, tp === 'todos' ? t('todos') : tp === 'nuevos' ? t('por_identificar', { n: nuevos }) : etTipo(tp)))),
    h('div', { class: 'cols' },
      h('section', { class: 'card ancha' }, h('h2', {}, t('nav.equipos')), h('div', { class: 'tabla' },
        h('div', { class: 'g cab' }, ['f.nombre', 'f.tipo', 'IP', 'MAC', 'f.cuelga', 'f.estado'].map((x) => h('span', {}, t(x)))), cuerpo)),
      e ? ficha(e) : null)
  ];
}

// ---------------------------------------------------------------- historial
function limites() {
  const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() - st.dia);
  const f = new Date(d); f.setDate(f.getDate() + 1);
  return [Math.floor(d / 1000), Math.floor(f / 1000)];
}

function verHistorial() {
  const [a, b] = limites(), H = st.hist;
  const dia = st.dia === 0 ? t('hoy') : st.dia === 1 ? t('ayer') : fecha(a);
  const mover = (n) => { st.dia = Math.max(0, st.dia + n); st.hist = null; repintar(); cargarHist(); };
  const barra = h('div', { class: 'barra' },
    h('button', { class: 'pill', disabled: !!(H && H.primero && H.primero >= a), onclick: () => mover(1) }, t('dia_anterior')), h('b', { style: { minWidth: '90px', textAlign: 'center' } }, dia),
    h('button', { class: 'pill', disabled: st.dia === 0, onclick: () => mover(-1) }, t('dia_siguiente')));
  if (!H) return [barra, h('section', { class: 'card' }, h('p', { class: 's' }, t('cargando')))];
  const macs = [...new Set(H.sesiones.map((s) => s.mac))].map(porMac).filter(Boolean).sort((x, y) => (x.medio === 'wifi' ? 0 : 1) - (y.medio === 'wifi' ? 0 : 1) || ipn(x.ip) - ipn(y.ip));
  const pct = (s) => (Math.min(Math.max(s, a), b) - a) / (b - a) * 100;
  const ley = (c, tt) => h('span', {}, h('span', { class: 'sw', style: { background: c } }), tt);
  const ev = H.eventos.filter((v) => !st.quien || v.mac === st.quien);
  const hayPuntos = (H.puntos || []).length > 0;
  return [barra,
    h('section', { class: 'card' }, h('h2', {}, t('hist.titulo')),
      h('div', { class: 'ley' }, hayPuntos ? [ley('#3987e5', t('punto.wifi5')), ley('#d95926', t('punto.wifi24')), ley('#8fa1a8', t('hist.cable_router')), ley('#199e70', t('hist.por_otro'))] : null, ley('#3a4b53', t(hayPuntos ? 'hist.sin_router' : 'hist.conectado'))),
      macs.length ? h('div', { class: 'tabla' }, macs.map((e) => h('div', { class: 'gr' },
        h('button', { class: 'who', 'aria-pressed': String(e.mac === st.quien), title: nombre(e) + ' · ' + e.mac, onclick: () => { st.quien = st.quien === e.mac ? null : e.mac; repintar(); } }, nombre(e)),
        h('div', { class: 'trk' }, H.sesiones.filter((s) => s.mac === e.mac).map((s) => h('div', { class: 'bar base', title: nombre(e) + ' · ' + s.ip + ' · ' + cuando(s.inicio) + ' – ' + (s.abierta ? t('ahora') : cuando(s.fin)), style: { left: pct(s.inicio) + '%', width: Math.max(0, pct(s.fin) - pct(s.inicio)) + '%' } })),
          (H.puntos || []).filter((p) => p.mac === e.mac).map((p) => h('div', { class: 'bar', title: nombre(e) + ' · ' + etPunto(p.punto) + ' · ' + cuando(p.inicio) + ' – ' + (p.abierta ? t('ahora') : cuando(p.fin)), style: { left: pct(p.inicio) + '%', width: Math.max(0, pct(p.fin) - pct(p.inicio)) + '%', background: colorPunto(p.punto) } }))))),
        h('div', { class: 'gr' }, h('span'), h('div', { class: 'eje mono' }, ['00:00', '04:00', '08:00', '12:00', '16:00', '20:00', '24:00'].map((x) => h('span', {}, x)))))
        : h('p', { class: 's' }, t('hist.vacio'))),
    h('section', { class: 'card' }, h('h2', {}, t('registro') + (st.quien && porMac(st.quien) ? ': ' + nombre(porMac(st.quien)) : '')),
      st.quien ? h('button', { class: 'pill', onclick: () => { st.quien = null; repintar(); } }, t('ver_todos')) : null,
      ev.length ? h('div', { class: 'tabla' }, h('div', { class: 'lg cab' }, ['hora', 'f.equipo', 'IP', 'MAC', 'que_paso'].map((x) => h('span', {}, t(x)))),
        ev.map((v) => { const txt = textoEvento(v); return h('div', { class: 'lg' }, h('span', { class: 'mono s' }, hora(v.ts)), h('span', {}, porMac(v.mac) ? nombre(porMac(v.mac)) : '—'), h('span', { class: 'mono' }, v.ip), h('span', { class: 'mono s' }, v.mac), h('span', {}, t('ev.' + v.clase) + (txt ? ' · ' + txt : ''))); }))
        : h('p', { class: 's' }, t('sin_cambios')))];
}

// ---------------------------------------------------------------- ajustes
function verAjustes() {
  const A = st.aj;
  if (!A) { cargarAjustes(); return [h('section', { class: 'card' }, h('p', { class: 's' }, t('cargando')))]; }
  const msg = () => h('span', { class: 's', role: 'status' });
  const enviar = async (ruta, datos, salida, ok) => {
    salida.textContent = t('guardando'); salida.className = 's';
    const r = await api(ruta, datos);
    salida.textContent = r.error ? errorTxt(r.error) : ok || t('guardado');
    salida.className = r.error ? 'err' : 's';
    if (!r.error) { await cargar(); await cargarAjustes(); }
    return r;
  };
  const datosDe = (f) => Object.fromEntries(new FormData(f));
  // General
  const g = msg(), fg = h('form', { onsubmit: (ev) => { ev.preventDefault(); enviar('ajustes', datosDe(fg), g); } },
    h('label', {}, t('aj.nombre'), h('input', { name: 'nombre', maxlength: 40, value: A.nombre })),
    h('label', {}, t('aj.idioma_def'), sel('idioma', [['es', 'Español'], ['en', 'English']], A.idioma || LANG)),
    h('div', { class: 'pie' }, h('button', { class: 'pill pri' }, t('guardar')), g));
  // Contraseña del panel
  const c = msg(), fc = h('form', { onsubmit: async (ev) => {
    ev.preventDefault();
    const d = datosDe(fc);
    if (d.nueva !== d.nueva2) { c.textContent = t('error.no_coinciden'); c.className = 'err'; return; }
    const r = await enviar('clave', { actual: d.actual, nueva: d.nueva }, c);
    if (!r.error) fc.reset();
  } },
  h('label', {}, t('aj.clave_actual'), h('input', { name: 'actual', type: 'password', required: true, autocomplete: 'current-password' })),
  h('label', {}, t('aj.clave_nueva'), h('input', { name: 'nueva', type: 'password', minlength: 8, required: true, autocomplete: 'new-password' })),
  h('label', {}, t('aj.clave_nueva2'), h('input', { name: 'nueva2', type: 'password', minlength: 8, required: true, autocomplete: 'new-password' })),
  h('div', { class: 'pie' }, h('button', { class: 'pill pri' }, t('aj.cambiar_clave')), c));
  // Router
  const r = msg(), ro = st.d.router;
  const estadoRouter = ro.estado === 'ok' ? t('aj.router_ok', { n: ro.msg, cuando: hace(ro.ts) }) : t('aj.router_' + ro.estado, { msg: ro.msg });
  const fr = h('form', { onsubmit: async (ev) => {
    ev.preventDefault();
    const d = datosDe(fr);
    const x = await enviar('router', d, r, d.tipo ? t('aj.router_guardado') : t('guardado'));
    if (!x.error) fr.querySelector('[name=clave]').value = '';
  } },
  h('p', { class: 'm' }, t('aj.router_ayuda', { ip: A.router_ip || '?' })),
  h('label', {}, t('aj.modelo'), sel('tipo', [['', t('aj.ninguno')]].concat(Object.entries(A.modelos)), A.router.tipo)),
  h('label', {}, t('aj.usuario'), h('input', { name: 'usuario', maxlength: 60, value: A.router.usuario, autocomplete: 'off' })),
  h('label', {}, t('aj.clave_router'), h('input', { name: 'clave', type: 'password', autocomplete: 'off', placeholder: A.router.tipo ? t('aj.clave_otra_vez') : '' })),
  h('p', { class: 's est' }, h('span', { class: 'led ' + (ro.estado === 'ok' ? 'on' : ro.estado === 'sin_configurar' ? '' : 'av'), 'aria-hidden': 'true' }), estadoRouter),
  h('div', { class: 'pie' }, h('button', { class: 'pill pri' }, t('aj.probar_guardar')), r));
  // Que hay en cada boca del router
  const destinos = [['', t('aj.directo')]].concat(opcionesPadre('').filter(([id]) => id && id !== 'internet' && id !== st.d.router_mac));
  const b = msg(), fb = h('form', { onsubmit: (ev) => { ev.preventDefault(); enviar('bocas', datosDe(fb), b); } },
    h('p', { class: 'm' }, t('aj.bocas_ayuda')),
    ['wifi'].concat(A.bocas_vistas).map((k) => h('label', {}, k === 'wifi' ? t('aj.wifi_router') : t('aj.boca', { b: k }), sel(k, destinos, A.bocas[k] || ''))),
    h('div', { class: 'pie' }, h('button', { class: 'pill pri' }, t('guardar')), b));
  // Nodos sin IP (switches, puntos de acceso...)
  const filaNodo = (n) => {
    const s = msg();
    const f = h('form', { class: 'nodo', onsubmit: (ev) => { ev.preventDefault(); enviar('nodo', { ...datosDe(f), id: n.id || '' }, s); } },
      h('label', {}, t('f.nombre'), h('input', { name: 'nombre', maxlength: 40, value: n.nombre || '', required: true, placeholder: t('ej_nodo') })),
      h('label', {}, t('f.cuelga'), sel('padre', opcionesPadre(n.id).filter(([id]) => id), n.padre || st.d.router_mac)),
      h('label', {}, t('aj.tipo_nodo'), sel('boca', [['', t('aj.nodo_cable')], ['wifi', t('aj.nodo_wifi')]], n.boca || '')),
      h('div', { class: 'pie' }, h('button', { class: 'pill pri' }, n.id ? t('guardar') : t('anadir')),
        n.id ? h('button', { class: 'pill', type: 'button', onclick: () => enviar('nodo', { id: n.id, borrar: true }, s) }, t('borrar')) : null, s));
    return f;
  };
  return [
    st.primera ? h('div', { class: 'aviso azul' }, t('aj.primera')) : null,
    h('div', { class: 'cols' },
      h('section', { class: 'card ancha' }, h('h2', {}, t('aj.router')), fr),
      h('section', { class: 'card lado' }, h('h2', {}, t('aj.general')), fg)),
    h('div', { class: 'cols' },
      h('section', { class: 'card ancha' }, h('h2', {}, t('aj.nodos')), h('p', { class: 'm' }, t('aj.nodos_ayuda')), st.d.nodos.map(filaNodo), h('h3', {}, t('aj.nuevo_nodo')), filaNodo({})),
      h('section', { class: 'card lado' }, h('h2', {}, t('aj.bocas')), fb)),
    h('section', { class: 'card estrecha2' }, h('h2', {}, t('aj.clave')), fc)
  ];
}

// ---------------------------------------------------------------- arranque y refresco
const VISTAS = { resumen: verResumen, mapa: verMapa, equipos: verEquipos, historial: verHistorial, ajustes: verAjustes };
const vista = () => (VISTAS[location.hash.slice(1)] ? location.hash.slice(1) : 'resumen');

function repintar() {
  if (!st.d) return;
  pintarCabecera();
  const s = st.d.ahora - st.d.ultimo, viejo = !st.d.ultimo || s > 240;
  $('escaneo').textContent = !st.d.ultimo ? t('esperando_escaneo') : t('ultimo_escaneo', { cuando: s < 90 ? t('hace_s', { n: Math.max(0, s) }) : t('hace_min', { n: Math.round(s / 60) }) });
  const ro = st.d.router;
  const rav = ro.estado === 'clave' ? t('aviso.router_clave') : ro.estado === 'error' ? t('aviso.router_error', { msg: ro.msg }) : '';
  const av = st.d.error || (viejo && st.d.ultimo ? t('aviso.sin_escaneos') : '') || rav;
  $('vista').replaceChildren(...[av ? h('div', { class: 'aviso' }, t('aviso.atrasados', { motivo: av }), ' ', rav ? h('a', { href: '#ajustes' }, t('nav.ajustes')) : null) : null, ...VISTAS[vista()]()].filter(Boolean));
}

async function cargarHist() {
  const [a, b] = limites();
  const d = await api('historial?desde=' + a + '&hasta=' + b);
  if (!d.error && limites()[0] === a) { st.hist = d; if (vista() === 'historial') repintar(); }
}
async function cargarTraf() {
  const rango = st.rango, b = Math.ceil(Date.now() / 60000) * 60, a = b - rango;
  const d = await api('trafico?desde=' + a + '&hasta=' + b);
  if (!d.error && st.rango === rango) { st.traf = d; if (vista() === 'resumen' && !st.sobre) repintar(); }
}
async function cargarAjustes() {
  const d = await api('ajustes');
  if (!d.error) { st.aj = d; if (vista() === 'ajustes') repintar(); }
}
async function cargar() {
  const d = await api('estado');
  if (d.error) { if ($('escaneo')) $('escaneo').textContent = t('sin_conexion'); return; }
  st.d = d;
  // No repintar mientras se escribe ni en ajustes: se perderia lo escrito.
  const act = document.activeElement;
  if (!st.editando && !st.sobre && vista() !== 'ajustes' && !(act && act.matches('input,select'))) repintar();
}

let reloj = null;
async function iniciar() {
  clearInterval(reloj);
  let s;
  try { s = await (await fetch('api/sesion')).json(); } catch { s = null; }
  await cargarIdioma(idiomaPreferido(s && s.idioma));
  if (!s) { $('vista').replaceChildren(h('div', { class: 'aviso' }, t('sin_conexion'))); return; }
  st.s = s;
  if (!s.configurado) return verBienvenida();
  if (!s.autenticado) return verEntrar();
  await cargar();
  repintar();
  if (vista() === 'resumen') cargarTraf();
  if (vista() === 'historial') cargarHist();
  reloj = setInterval(() => { cargar(); if (vista() === 'resumen') cargarTraf(); if (vista() === 'historial' && st.dia === 0) cargarHist(); }, 15000);
}

window.addEventListener('hashchange', () => {
  if (!st.s || !st.s.autenticado) return;
  st.editando = false; st.sobre = false; st.msg = '';
  if (vista() !== 'ajustes') st.primera = false; else st.aj = null;
  repintar();
  if (vista() === 'resumen') cargarTraf();
  if (vista() === 'historial') cargarHist();
});
iniciar();
