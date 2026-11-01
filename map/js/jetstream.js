// Jet stream overlay (docs/map/JETSTREAM.md "UI"): the average 250 hPa jet path of the last winters and of the El
// Niño, La Niña and neutral winters, each toggled on its own. app.js loads this module, its stylesheet and
// data/build/jetstream.json the first time the Jet stream box in the Layers panel opens, or with a ?jet= link.
// It owns the box (one checkbox per layer in file order, the note and both sources), the card of the active layer
// (its states and their resorts, west to east), the legend strip items, and the bands and lines on the WebGL map
// (under the resort dots; the lower 48 and Alaska at their true place) and on the 2D picture before the map starts.
// The most recently turned on visible layer is the active one: states under its band get a tint, its resorts a ring,
// and the card lists them. Layer ids, their number and their branches all come from the file.
import { loadOptional, schemas } from './data.js';
import { hrefFor } from './router.js';
import { badge } from './panel.js';
import { addPreviewOverlay } from './preview.js';
import { resortRadius } from './camera.js';
import { prefersReducedMotion } from './config.js';
import { h, clear, plural, formatIsoDate, safeUrl, fmtInt } from './ui.js';

const SRC = 'jet';
/** The map layers, bottom to top, all under the resort dots. */
export const LAYERS = ['jet-states', 'jet-band', 'jet-edge', 'jet-axis-casing', 'jet-axis', 'jet-label'];
const NEW_TAB = { target: '_blank', rel: 'noopener noreferrer' };
/** Paint per basemap tone: the imagery of SATELLITE needs a stronger band and a darker casing to read. */
const PAINT = {
  map: { band: [0.2, 0.09], edge: [0.8, 0.45], casing: 0.55, states: 0.13 },
  satellite: { band: [0.28, 0.14], edge: [0.95, 0.7], casing: 0.75, states: 0.2 },
};
const paintOf = (tone) => PAINT[tone] || PAINT.map;
/** A list with at most this many resorts opens every state; a longer one opens state by state. */
const OPEN_ALL_MAX = 12;
const FONT = "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif";

// ── Transforms (pure; tests/map/browser_test.mjs jetStreamChecks imports them) ──

/** Split points [lon, ...] into runs of neighbors (a gap of more than 1.5 degrees, or a null value, ends a run). */
export function runs(points, valid = () => true) {
  const out = [];
  let cur = [];
  for (const p of points) {
    if (!valid(p) || (cur.length && Math.abs(p[0] - cur[cur.length - 1][0]) > 1.5)) {
      if (cur.length) out.push(cur);
      cur = valid(p) ? [p] : [];
    } else cur.push(p);
  }
  if (cur.length) out.push(cur);
  return out;
}

const bandValid = (p) => p[1] != null && p[2] != null && p[2] >= p[1];

/** Band polygons of a branch: the south edge west to east, then the north edge back, closed. */
export function bandPolygons(band) {
  return runs(band, bandValid).filter((r) => r.length >= 2).map((r) => {
    const ring = [...r.map((p) => [p[0], p[1]]), ...r.slice().reverse().map((p) => [p[0], p[2]])];
    ring.push(ring[0]);
    return ring;
  });
}

/** The south and north edges of a branch's band as lines (the band's ends stay open). */
export function bandEdges(band) {
  return runs(band, bandValid).filter((r) => r.length >= 2).flatMap((r) => [r.map((p) => [p[0], p[1]]), r.map((p) => [p[0], p[2]])]);
}

/** The axis of a branch as lines of [lon, lat]. */
export function axisLines(axis) {
  return runs(axis, (p) => p[1] != null).filter((r) => r.length >= 2).map((r) => r.map((p) => [p[0], p[1]]));
}

/**
 * The visible layers as one GeoJSON FeatureCollection for the map: per branch its band polygons, band edges and axis
 * lines, each with the layer's color, its stacking order (the active layer last, so on top) and an `active` flag.
 * layers: the file's layers; order: visible ids, the order they were turned on.
 */
export function jetFeatures(layers, order) {
  const byId = new Map(layers.map((l) => [l.id, l]));
  const active = order[order.length - 1];
  const features = [];
  order.forEach((id, rank) => {
    const l = byId.get(id);
    if (!l) return;
    for (const b of l.branches) {
      const props = { layer: l.id, branch: b.id, color: l.color, order: rank, active: id === active, key: labelKey(l, b) };
      for (const ring of bandPolygons(b.band)) features.push({ type: 'Feature', properties: { ...props, kind: 'band' }, geometry: { type: 'Polygon', coordinates: [ring] } });
      for (const line of bandEdges(b.band)) features.push({ type: 'Feature', properties: { ...props, kind: 'edge' }, geometry: { type: 'LineString', coordinates: line } });
      for (const line of axisLines(b.axis)) features.push({ type: 'Feature', properties: { ...props, kind: 'axis' }, geometry: { type: 'LineString', coordinates: line } });
    }
  });
  return { type: 'FeatureCollection', features };
}

/** The visible ids after turning `id` on (it becomes the active one, last) or off. */
export function toggled(order, id, on) {
  const rest = order.filter((x) => x !== id);
  return on ? [...rest, id] : rest;
}

const labelKey = (l, b) => `${l.id}-${b.id}`;
/** The map label of a branch: the layer, and the branch when the layer has two ("La Niña, Northern branch"). */
export function lineLabel(l, b) { return l.branches.length > 1 ? `${l.label}, ${b.label}` : l.label; }

/**
 * The card's list for a layer: its states west to east, each with its resorts west to east (the file's order), the
 * branches named when the layer has two. resortOf(id) gives the slim record; a resort missing from the map is skipped.
 */
export function stateGroups(layer, resortOf, stateName = (code) => code) {
  const branchLabel = new Map(layer.branches.map((b) => [b.id, b.label]));
  const groups = new Map(layer.overflies.states.map((s) => [s.code, {
    code: s.code, name: s.name, branches: s.branches.map((b) => branchLabel.get(b) || b), resorts: [],
  }]));
  for (const x of layer.overflies.resorts) {
    const r = resortOf(x.id);
    if (!r) continue;
    const code = x.state || r.state;
    // A resort whose state the file leaves out of its states list still shows, under its own state, at the end.
    if (!groups.has(code)) groups.set(code, { code, name: stateName(code), branches: x.branch ? [branchLabel.get(x.branch) || x.branch] : [], resorts: [] });
    groups.get(code).resorts.push({ id: r.id, name: r.name, branch: x.branch ? branchLabel.get(x.branch) || x.branch : null, record: r });
  }
  return [...groups.values()];
}

// ── Data ──

/** jetstream.json validated (Zod, per layer and per branch). Resolves to the model, or { error }. */
async function loadModel(index) {
  const [raw, sch] = await Promise.all([loadOptional('jetstream.json'), schemas(), index.loadSources().catch(() => null)]);
  if (!raw) return { error: 'missing', layers: [] };
  const r = sch.JetStreamFile.safeParse(raw);
  if (!r.success) {
    console.warn('[map] jetstream.json failed validation', r.error.issues.slice(0, 5));
    return { error: 'invalid', layers: [] };
  }
  const seen = new Set();
  const layers = r.data.layers.filter((l) => !seen.has(l.id) && seen.add(l.id));
  const given = Array.isArray(raw.layers) ? raw.layers.length : 0;
  if (layers.length < given) console.warn(`[map] jetstream.json: ${given - layers.length} of ${given} paths skipped`);
  return { file: r.data, layers, byId: new Map(layers.map((l) => [l.id, l])) };
}

/** A canvas drawn map label (the style has no glyph server): the text in the layer color on a dark tag. */
function labelImage(text, color) {
  const ratio = 2;
  const c = document.createElement('canvas');
  const ctx = c.getContext('2d', { willReadFrequently: true });
  const font = `700 ${10.5 * ratio}px ${FONT}`;
  ctx.font = font;
  const pad = 5 * ratio;
  c.width = Math.ceil(ctx.measureText(text).width + 2 * pad);
  c.height = 17 * ratio;
  ctx.font = font;
  ctx.fillStyle = 'rgba(10,10,10,0.9)';
  ctx.fillRect(0, 0, c.width, c.height);
  ctx.fillStyle = color;
  ctx.fillRect(0, 0, 2 * ratio, c.height);
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, c.width / 2 + ratio, c.height / 2 + ratio / 2);
  const d = ctx.getImageData(0, 0, c.width, c.height);
  return { width: c.width, height: c.height, data: new Uint8Array(d.data.buffer) };
}

/**
 * The jet stream layer. opts: { index, box (the Jet stream details in the Layers panel), legend (the legend strip),
 * announce(text), initial (layer ids from the address bar, the active one last), setLayers(ids) (the address bar
 * state), onChange() (the address bar and the 2D picture follow), tone ('map' or 'satellite') }.
 */
export function createJetStream(opts) {
  const { index, box } = opts;
  const body = box.querySelector('.jet-body');
  const summary = box.querySelector('summary');
  let model = null;
  let order = [];          // visible layer ids in the order they were turned on; the active one is last
  let mapCtl = null;
  let mapReady = false;
  let drawn = null;        // the FeatureCollection on the map
  let tone = opts.tone || 'map';
  const inputs = new Map(); // layer id -> checkbox
  const onCount = h('span', { class: 'jet-on', hidden: true });
  if (summary) summary.append(onCount);
  if (!body.firstChild) body.append(h('p', { class: 'jet-note' }, 'Loading the jet stream paths.'));
  box.setAttribute('aria-busy', 'true');

  const layerOf = (id) => model && model.byId && model.byId.get(id);
  const activeLayer = () => layerOf(order[order.length - 1]) || null;
  const resortOf = (id) => index.resorts.get(id) || null;
  const stateName = (code) => (index.states.get(code) || {}).name || code;
  const swatch = (color) => h('span', { class: 'jet-swatch', style: `--c:${color}`, 'aria-hidden': 'true' });
  const winters = (l) => plural(l.seasons.length, 'winter', 'winters');
  /** A resort's page, in a state its cluster spans (as a click on the map picks it). */
  const resortHref = (r) => {
    const c = index.clusters.get(r.cluster_id);
    const st = c && !c.states.includes(r.state) ? c.states[0] : r.state;
    return hrefFor({ level: 'resort', stateCode: st, clusterId: r.cluster_id, resortId: r.id });
  };

  function sourceLink(ref) {
    const s = index.sources.get(ref.source_id);
    if (!s) return h('span', null, ref.source_id);
    const url = safeUrl(s.url);
    return url ? h('a', { href: url, ...NEW_TAB, class: 'src-link' }, s.title || ref.source_id) : h('span', null, s.title || ref.source_id);
  }

  // ── The box: a checkbox per layer, the note, the sources and the card ──
  const card = h('section', { class: 'jet-card', id: 'jet-card', 'aria-labelledby': 'jet-card-title', hidden: true });
  function option(l) {
    const id = `jet-layer-${l.id}`;
    // The visible label shows the name over the count; the name reads them as one phrase ("El Niño, 13 winters").
    const input = h('input', { type: 'checkbox', id, dataset: { jet: l.id }, 'aria-label': `${l.label}, ${winters(l)}` });
    inputs.set(l.id, input);
    return h('label', { class: 'lp-opt lp-checkbox jet-opt', for: id }, input, swatch(l.color),
      h('span', { class: 'jet-opt-text' }, h('span', { class: 'lp-text' }, l.label), h('span', { class: 'jet-count' }, winters(l))));
  }
  function build() {
    clear(body);
    box.removeAttribute('aria-busy');
    if (model.error) {
      body.append(h('p', { class: 'jet-note' }, model.error === 'missing'
        ? 'The jet stream paths arrive with the next data build.' : 'The jet stream paths could not load. Try reloading the page.'));
      return;
    }
    const f = model.file;
    const fieldset = h('fieldset', { class: 'lp-group jet-layers' }, h('legend', { class: 'lp-legend' }, 'Paths'), model.layers.map(option));
    fieldset.addEventListener('change', (e) => {
      const input = e.target.closest('input[data-jet]');
      if (input) set(input.dataset.jet, input.checked);
    });
    body.append(fieldset,
      h('p', { class: 'jet-note' }, `Average ${fmtInt(f.level_hpa)} hPa jet, ${f.season_label}.`),
      card,
      h('p', { class: 'jet-src' }, 'Sources: ', sourceLink(f.sources.winds), ', ', sourceLink(f.sources.enso), '.'),
      f.method ? h('details', { class: 'jet-how' }, h('summary', null, 'How the paths are made'), h('p', null, f.method)) : null);
  }

  // The card: a title, the selector (several layers on) or the layer's name, then the list of the active layer. The
  // list is rebuilt only when the active layer changes, so states the reader opened stay open.
  const pickSlot = h('div', { class: 'jet-pick-slot' });
  const listSlot = h('div', { class: 'jet-list' });
  let listFor = null;
  card.append(h('h2', { class: 'jet-card-title', id: 'jet-card-title' }, 'Under the band'), pickSlot, listSlot);
  function renderCard() {
    const act = activeLayer();
    card.hidden = !act;
    if (!act) { clear(pickSlot); clear(listSlot); listFor = null; return; }
    const visible = model.layers.filter((l) => order.includes(l.id));
    card.style.setProperty('--c', act.color);
    if (visible.length > 1) {
      const select = h('select', { id: 'jet-active', class: 'jet-pick' },
        visible.map((l) => h('option', { value: l.id, selected: l.id === act.id }, `${l.label}, ${winters(l)}`)));
      select.addEventListener('change', () => activate(select.value));
      pickSlot.replaceChildren(h('div', { class: 'jet-pick-row' }, h('label', { for: 'jet-active', class: 'jet-pick-label' }, 'List for'), select));
    } else pickSlot.replaceChildren(h('p', { class: 'jet-one' }, swatch(act.color), h('span', null, `${act.label}, ${winters(act)}`)));
    if (listFor === act.id) return;
    listFor = act.id;
    listSlot.replaceChildren(...stateList(act).filter(Boolean));
  }
  function stateList(act) {
    const groups = stateGroups(act, resortOf, stateName);
    const nResorts = groups.reduce((n, g) => n + g.resorts.length, 0);
    const two = act.branches.length > 1;
    const branchText = (labels) => [...new Set(labels)].join(' and ');
    // Short lists open in full; long ones (the last ten winters cross about a hundred resorts) open by state.
    const open = nResorts <= OPEN_ALL_MAX;
    const withResorts = groups.filter((g) => g.resorts.length).length;
    const sum = !groups.length ? 'This band crosses no state or ski area on the map.'
      : nResorts ? `${plural(groups.length, 'state', 'states')} and ${plural(nResorts, 'resort', 'resorts')}, west to east.`
        : `${plural(groups.length, 'state', 'states')}, west to east. This band crosses no ski areas.`;
    let all = null;
    if (!open && withResorts > 1) {
      all = h('button', { type: 'button', class: 'jet-all', 'aria-pressed': 'false' }, 'Open every state');
      all.addEventListener('click', () => {
        const on = all.getAttribute('aria-pressed') !== 'true';
        for (const d of listSlot.querySelectorAll('details.jet-state-box')) d.open = on;
        all.setAttribute('aria-pressed', String(on));
        all.textContent = on ? 'Close every state' : 'Open every state';
      });
    }
    const ensoRef = model.file.sources.enso;
    return [
      h('p', { class: 'jet-sum' }, sum),
      all,
      groups.length ? h('ol', { class: 'jet-states' }, groups.map((g) => {
        const tag = two && g.branches.length ? h('span', { class: 'jet-branch' }, branchText(g.branches)) : null;
        if (!g.resorts.length) {
          return h('li', { class: 'jet-state' }, h('p', { class: 'jet-state-name' }, h('span', { class: 'jet-state-label' }, g.name), tag,
            h('span', { class: 'jet-n' }, '0 resorts')));
        }
        const mixed = new Set(g.resorts.map((x) => x.branch)).size > 1;
        return h('li', { class: 'jet-state' }, h('details', { class: 'jet-state-box', open },
          h('summary', { class: 'jet-state-name' }, h('span', { class: 'jet-state-label' }, g.name), tag,
            h('span', { class: 'jet-n' }, plural(g.resorts.length, 'resort', 'resorts'))),
          h('ul', { class: 'jet-resorts' }, g.resorts.map((x) => h('li', null,
            h('a', { href: resortHref(x.record), 'data-nav': '' }, x.name),
            two && mixed && x.branch ? h('span', { class: 'jet-branch' }, x.branch) : null)))));
      })) : null,
      h('p', { class: 'fact-meta jet-meta' }, badge(act.confidence), ` Path last verified ${formatIsoDate(act.as_of)} · `, sourceLink(act)),
      act.kind === 'baseline' ? null : h('p', { class: 'fact-meta jet-meta' }, badge(ensoRef.confidence),
        ` Winter classes last verified ${formatIsoDate(ensoRef.as_of)} · `, sourceLink(ensoRef)),
      h('details', { class: 'jet-winters' }, h('summary', null, `${winters(act)} in this average`), h('p', null, act.seasons.join(', '))),
    ];
  }

  // ── Legend strip: a line per visible layer, and the ring of the active one ──
  const legendItems = opts.legend ? h('ul', { class: 'ml-items ml-jet', hidden: true }) : null;
  if (legendItems) opts.legend.append(legendItems);
  function renderLegend() {
    if (!legendItems) return;
    const act = activeLayer();
    legendItems.hidden = !act;
    opts.legend.classList.toggle('has-jet', Boolean(act));
    // Narrow desktops: the layers panel stops above the extra legend row (jetstream.css).
    if (opts.legend.parentElement) opts.legend.parentElement.classList.toggle('jet-legend', Boolean(act));
    if (!act) { clear(legendItems); return; }
    legendItems.replaceChildren(h('li', { class: 'ml-jet-title' }, 'Jet stream'),
      ...model.layers.filter((l) => order.includes(l.id)).map((l) => h('li', null, h('span', { class: 'ml-jet-line', style: `--c:${l.color}`, 'aria-hidden': 'true' }), l.label)),
      h('li', null, h('span', { class: 'ml-jet-ring', style: `--c:${act.color}`, 'aria-hidden': 'true' }), `Under the ${act.label} band`));
  }

  // ── WebGL map ──
  function addLayers() {
    const map = mapCtl.map;
    if (map.getSource(SRC)) return;
    const f = model.file;
    const credit = [f.sources.winds, f.sources.enso].map((ref) => {
      const s = index.sources.get(ref.source_id);
      const url = s && safeUrl(s.url);
      const title = (s && s.title) || ref.source_id;
      return url ? `<a href="${url.replace(/"/g, '&quot;')}" target="_blank" rel="noopener">${escapeHtml(title)}</a>` : escapeHtml(title);
    }).join(', ');
    map.addSource(SRC, { type: 'geojson', data: { type: 'FeatureCollection', features: [] }, attribution: `Jet stream: ${credit}` });
    const before = map.getLayer('resorts-glow') ? 'resorts-glow' : undefined;
    const kind = (k) => ['==', ['get', 'kind'], k];
    const active = ['boolean', ['get', 'active'], false];
    const sort = ['get', 'order'];
    map.addLayer({ id: 'jet-states', type: 'fill', source: 'states', filter: ['in', ['get', 'code'], ['literal', []]],
      paint: { 'fill-color': '#ffffff', 'fill-opacity': 0.13 } }, map.getLayer('clusters-fill') ? 'clusters-fill' : before);
    map.addLayer({ id: 'jet-band', type: 'fill', source: SRC, filter: kind('band'), layout: { 'fill-sort-key': sort },
      paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.2, 'fill-antialias': false } }, before);
    map.addLayer({ id: 'jet-edge', type: 'line', source: SRC, filter: kind('edge'), layout: { 'line-sort-key': sort, 'line-join': 'round' },
      paint: { 'line-color': ['get', 'color'], 'line-width': 1, 'line-dasharray': [3, 3], 'line-opacity': 0.8 } }, before);
    map.addLayer({ id: 'jet-axis-casing', type: 'line', source: SRC, filter: kind('axis'), layout: { 'line-sort-key': sort, 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': '#000000', 'line-width': ['case', active, 6.5, 5], 'line-blur': 1.5, 'line-opacity': 0.55 } }, before);
    map.addLayer({ id: 'jet-axis', type: 'line', source: SRC, filter: kind('axis'), layout: { 'line-sort-key': sort, 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': ['get', 'color'], 'line-width': ['case', active, 3, 2.2] } }, before);
    map.addLayer({ id: 'jet-label', type: 'symbol', source: SRC, filter: kind('axis'),
      layout: { 'symbol-placement': 'line', 'icon-image': ['concat', 'jet-label-', ['get', 'key']],
        'icon-rotation-alignment': 'map', 'icon-keep-upright': true, 'icon-padding': 6, 'symbol-sort-key': ['-', 0, sort] } }, before);
    applyTone();
  }
  function applyTone() {
    if (!mapCtl || !mapCtl.map.getLayer('jet-band')) return;
    const map = mapCtl.map;
    const p = paintOf(tone);
    const active = ['boolean', ['get', 'active'], false];
    map.setPaintProperty('jet-band', 'fill-opacity', ['case', active, p.band[0], p.band[1]]);
    map.setPaintProperty('jet-edge', 'line-opacity', ['case', active, p.edge[0], p.edge[1]]);
    map.setPaintProperty('jet-axis-casing', 'line-opacity', p.casing);
    map.setPaintProperty('jet-states', 'fill-opacity', p.states);
  }
  function refreshMap() {
    if (!mapReady) return;
    const map = mapCtl.map;
    for (const id of order) {
      const l = layerOf(id);
      for (const b of l ? l.branches : []) {
        const key = `jet-label-${labelKey(l, b)}`;
        if (!map.hasImage(key)) map.addImage(key, labelImage(lineLabel(l, b), l.color), { pixelRatio: 2 });
      }
    }
    drawn = jetFeatures(model.layers, order);
    map.getSource(SRC).setData(drawn);
    const act = activeLayer();
    map.setFilter('jet-states', ['in', ['get', 'code'], ['literal', act ? act.overflies.states.map((s) => s.code) : []]]);
    if (act) map.setPaintProperty('jet-states', 'fill-color', act.color);
    // Hidden while nothing is on, so the map's attribution lists the sources only while a path shows.
    for (const id of LAYERS) map.setLayoutProperty(id, 'visibility', order.length ? 'visible' : 'none');
    mapCtl.setResortRing(act ? act.overflies.resorts.map((x) => x.id) : [], act ? act.color : null);
  }

  // ── 2D picture before the WebGL map starts ──
  function drawPreview(svg, keep = null) {
    if (!model || !model.layers.length || !order.length || !svg) return;
    const p = paintOf(tone);
    const act = activeLayer();
    addPreviewOverlay(svg, (px, cam) => {
      const under = cam.el('g', { fill: 'none', 'stroke-linejoin': 'round', 'stroke-linecap': 'round' });
      const path = (pts, close) => pts.map(([lon, lat], i) => { const [x, y] = px(lon, lat); return `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`; }).join('') + (close ? 'Z' : '');
      for (const id of order) {
        const l = layerOf(id);
        if (!l) continue;
        const on = l === act;
        for (const b of l.branches) {
          for (const ring of bandPolygons(b.band)) under.append(cam.el('path', { d: path(ring, true), fill: l.color, 'fill-opacity': on ? p.band[0] : p.band[1], stroke: 'none' }));
          for (const line of bandEdges(b.band)) under.append(cam.el('path', { d: path(line), stroke: l.color, 'stroke-opacity': on ? p.edge[0] : p.edge[1], 'stroke-width': 1, 'stroke-dasharray': '3 3' }));
          for (const line of axisLines(b.axis)) {
            under.append(cam.el('path', { d: path(line), stroke: '#000000', 'stroke-opacity': p.casing, 'stroke-width': on ? 6.5 : 5 }));
            under.append(cam.el('path', { d: path(line), stroke: l.color, 'stroke-width': on ? 3 : 2.2 }));
          }
        }
      }
      const over = cam.el('g', { fill: 'none', stroke: act ? act.color : '#ffffff', 'stroke-width': 1.6 });
      for (const x of act ? act.overflies.resorts : []) {
        const r = resortOf(x.id);
        if (!r || (keep && !keep(r))) continue;
        const [cx, cy] = px(r.lon, r.lat);
        if (cx < -10 || cy < -10 || cx > cam.w + 10 || cy > cam.hgt + 10) continue;
        over.append(cam.el('circle', { cx: cx.toFixed(1), cy: cy.toFixed(1), r: (resortRadius(r.size_rank || 1, cam.zoom) + 1.7).toFixed(1) }));
      }
      return { under, over };
    });
  }

  // ── State changes ──
  function sync({ say = null } = {}) {
    for (const [id, input] of inputs) input.checked = order.includes(id);
    onCount.hidden = !order.length;
    onCount.textContent = order.length ? `${order.length} on` : '';
    renderCard();
    renderLegend();
    refreshMap();
    opts.setLayers(order);
    opts.onChange();
    if (say) opts.announce(say);
  }
  function describe(l) {
    const groups = stateGroups(l, resortOf, stateName);
    const n = groups.reduce((k, g) => k + g.resorts.length, 0);
    return `${plural(groups.length, 'state', 'states')} and ${plural(n, 'resort', 'resorts')}`;
  }
  /** Turn a layer on (it becomes the active one) or off (the most recent other visible layer takes over). */
  function set(id, on) {
    const l = layerOf(id);
    if (!l) return;
    const wasActive = order[order.length - 1] === id;
    order = toggled(order, id, on);
    const act = activeLayer();
    let say = on ? `${l.label} path on. Its band covers ${describe(l)}.` : `${l.label} path off.`;
    if (!on && wasActive && act) say += ` The list shows ${act.label}.`;
    sync({ say });
    if (on) reveal();
  }
  /** Scroll the Layers panel (or the phone sheet) so the card's top shows, keeping the box's title in view. */
  function reveal() {
    const scroller = box.closest('.layers-panel');
    if (!scroller || card.hidden || scroller.scrollHeight <= scroller.clientHeight) return;
    const head = scroller.querySelector('.lp-sheet-head');
    const top = box.offsetTop - (head && head.offsetHeight ? head.offsetHeight : 0);
    const want = Math.min(top, card.offsetTop - scroller.clientHeight * 0.45);
    if (want > scroller.scrollTop) scroller.scrollTo({ top: want, behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
  }
  /** Make a visible layer the active one (the card's selector). */
  function activate(id) {
    const l = layerOf(id);
    if (!l || !order.includes(id) || order[order.length - 1] === id) return;
    order = toggled(order, id, true);
    sync({ say: `The list shows ${l.label}: ${describe(l)}.` });
    const select = card.querySelector('#jet-active');
    if (select) select.focus();
  }

  const ready = loadModel(index).catch((err) => {
    console.warn('[map] jet stream', err.message);
    return { error: 'invalid', layers: [] };
  }).then((m) => {
    model = m;
    build();
    const wanted = opts.initial || [];
    order = wanted.filter((id) => model.byId && model.byId.has(id));
    // A link with ids this build does not have keeps the rest (the address bar drops the others).
    if (order.length || wanted.length) sync();
    return api;
  });

  const api = {
    ready,
    get loaded() { return Boolean(model); },
    get model() { return model; },
    /** Visible layer ids, the order they were turned on (the active one last). */
    get visible() { return [...order]; },
    get active() { return order[order.length - 1] || null; },
    /** The bands, edges and axes on the WebGL map (null before it runs). */
    get features() { return drawn; },
    set,
    activate,
    drawPreview,
    /** The bands and lines on the WebGL map, once its data layers are in. */
    attachMap(ctl) {
      if (mapCtl === ctl) return;
      mapCtl = ctl;
      ctl.whenReady(() => ready.then(() => {
        if (!model.layers.length) return;
        addLayers();
        mapReady = true;
        applyTone();
        refreshMap();
      }).catch((err) => console.warn('[map] jet stream map', err.message)));
    },
    /** MAP or SATELLITE: the band, edge and casing strength. */
    setTone(next) {
      tone = next;
      if (mapReady) applyTone();
    },
  };
  return api;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
