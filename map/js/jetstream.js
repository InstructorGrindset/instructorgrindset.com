// Jet stream overlay (docs/map/JETSTREAM.md "UI"): the average jet stream path of the last winters and of the El
// Niño, La Niña and neutral winters, each toggled on its own. app.js loads this module, its stylesheet and
// data/build/jetstream.json the first time the Jet stream box in the Layers panel opens, or with a ?jet= link.
// It owns the box (one checkbox per layer in file order, the note, NOAA's latest reading and both sources), the card
// of the active layer (its states and their ski areas, west to east, and how two paths differ), the legend strip
// items, one line in the state, cluster and resort panels, and the bands and lines on the WebGL map (under the resort
// dots; the lower 48 and Alaska at their true place) and on the 2D picture before the map starts. The most recently
// turned on visible layer is the active one: states under its band get an outline, its ski areas a ring, and the
// card lists them. Layer ids, their number and their branches all come from the file.
import { config, prefersReducedMotion } from './config.js';
import { matchesFilter, schemas } from './data.js';
import { hrefFor } from './router.js';
import { badge } from './panel.js';
import { addPreviewOverlay } from './preview.js';
import { resortRadius } from './camera.js';
import { h, clear, plural, formatIsoDate, safeUrl, fmtInt } from './ui.js';

const SRC = 'jet';
const LABEL_SRC = 'jet-labels';
/** The map layers, bottom to top: the state outlines, then the bands and lines under the resort dots, labels on top. */
export const LAYERS = ['jet-states', 'jet-band', 'jet-edge', 'jet-axis-casing', 'jet-axis', 'jet-label'];
const NEW_TAB = { target: '_blank', rel: 'noopener noreferrer' };
/** Paint per basemap tone: the imagery of SATELLITE needs a stronger band and a darker casing to read. */
const PAINT = {
  map: { band: [0.2, 0.09], edge: [0.7, 0.4], casing: 0.55, states: 0.6 },
  satellite: { band: [0.28, 0.14], edge: [0.9, 0.6], casing: 0.75, states: 0.8 },
};
const paintOf = (tone) => PAINT[tone] || PAINT.map;
/** The band fill fades out between these zooms (cluster and resort views keep only the edges and the line). */
const FADE = [5, 7];
/** A list with at most this many ski areas opens every state; a longer one opens state by state. */
const OPEN_ALL_MAX = 12;
const FONT = "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif";
const PHASE_WORD = { el_nino: 'El Niño', la_nina: 'La Niña', neutral: 'neutral' };

// ── Transforms (pure; tests/map/browser_test.mjs jetStreamChecks imports them) ──

/** "El Niño" and "La Niña" with a no break space, so the name never splits over two lines. */
export function nb(text) { return String(text).replace(/\b(El|La) (Ni[ñn][oa])/g, '$1 $2'); }

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

/** The band of a branch at a longitude, [south, north] (linear between its points), or null outside it. */
export function bandAt(band, lon) {
  for (let i = 0; i + 1 < band.length; i++) {
    const a = band[i]; const b = band[i + 1];
    if (lon < a[0] || lon > b[0] || b[0] - a[0] > 1.5 || !bandValid(a) || !bandValid(b)) continue;
    const t = b[0] === a[0] ? 0 : (lon - a[0]) / (b[0] - a[0]);
    return [a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  }
  const one = band.find((p) => p[0] === lon && bandValid(p));
  return one ? [one[1], one[2]] : null;
}

/**
 * Where a layer's bands lie at a longitude against a span of latitude: 'south', 'north', 'between' (bands on both
 * sides), 'over' (a band touches the span) or null (no band reaches that longitude). count: the bands that reach it.
 */
export function bandRelation(layer, lon, latS, latN) {
  const sides = new Set();
  let count = 0;
  for (const b of layer.branches) {
    const at = bandAt(b.band, lon);
    if (!at) continue;
    count += 1;
    sides.add(at[1] < latS ? 'south' : at[0] > latN ? 'north' : 'over');
  }
  if (!count) return { side: null, count };
  if (sides.has('over')) return { side: 'over', count };
  return { side: sides.size > 1 ? 'between' : [...sides][0], count };
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
      const props = { layer: l.id, branch: b.id, color: l.color, order: rank, active: id === active };
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

/**
 * The map label of a branch. With one path on it names the branch only ("Northern branch", "Jet stream"); with several
 * it names the layer, and the branch when the layer has two or more ("La Niña, Northern branch").
 */
export function lineLabel(l, b, several) {
  if (!several) return nb(b.label);
  return nb(l.branches.length > 1 ? `${l.label}, ${b.label}` : l.label);
}

/**
 * Link ids against the file: a known id stays; an id of a strength the file now groups ("el_nino_strong") maps to the
 * layer that holds it ("el_nino_moderate_strong"); others drop. Returns { ids, lost }.
 */
export function resolveIds(wanted, layers) {
  const known = new Set(layers.map((l) => l.id));
  const ids = [];
  let lost = 0;
  for (const id of wanted) {
    let got = known.has(id) ? id : null;
    const m = got ? null : /^(.+)_(weak|moderate|strong)$/.exec(id);
    if (m) {
      const l = layers.find((x) => x.phase === m[1] && (x.strength === m[2] || (x.strengths || []).includes(m[2])));
      got = l ? l.id : null;
    }
    if (!got) lost += 1;
    else if (!ids.includes(got)) ids.push(got);
  }
  return { ids, lost };
}

/**
 * The card's list for a layer: its states west to east, each with its ski areas west to east (the file's order).
 * resortOf(id) gives the slim record (a resort missing from the map is skipped); keep(r) is the Type and Show only
 * filters; a resort whose state the file leaves out of its states list shows under its own state, at the end.
 */
export function stateGroups(layer, resortOf, stateName = (code) => code, keep = () => true) {
  const branchLabel = new Map(layer.branches.map((b) => [b.id, b.label]));
  const groups = new Map(layer.overflies.states.map((s) => [s.code, {
    code: s.code, name: s.name, branches: s.branches.map((b) => branchLabel.get(b) || b), resorts: [], total: 0,
  }]));
  for (const x of layer.overflies.resorts) {
    const r = resortOf(x.id);
    if (!r) continue;
    const code = x.state || r.state;
    const branch = x.branch ? branchLabel.get(x.branch) || x.branch : null;
    if (!groups.has(code)) groups.set(code, { code, name: stateName(code), branches: branch ? [branch] : [], resorts: [], total: 0 });
    const g = groups.get(code);
    g.total += 1;
    if (keep(r)) g.resorts.push({ id: r.id, name: r.name, branch, record: r });
  }
  return [...groups.values()];
}

/**
 * The states under `a` and under none of `others`, with how many of a's ski areas (in `groups`) lie in them.
 */
export function onlyUnder(a, others, groups) {
  const elsewhere = new Set(others.flatMap((l) => l.overflies.states.map((s) => s.code)));
  const only = groups.filter((g) => !elsewhere.has(g.code));
  return { names: only.map((g) => g.name), skiAreas: only.reduce((n, g) => n + g.resorts.length, 0) };
}

// ── Data ──

/** jetstream.json: 'missing' for a 404, 'failed' for any other error (a retry may help), else the raw file. */
async function fetchFile() {
  let res;
  try { res = await fetch(`${config.dataBase}jetstream.json`, { credentials: 'same-origin' }); } catch { return { status: 'failed' }; }
  if (res.status === 404) return { status: 'missing' };
  if (!res.ok) return { status: 'failed' };
  try { return { status: 'ok', raw: await res.json() }; } catch { return { status: 'failed' }; }
}

/** The file validated (Zod, per layer and per branch). Resolves to the model, or { error: 'missing' | 'failed' }. */
async function loadModel(index) {
  const [got, sch] = await Promise.all([fetchFile(), schemas(), index.loadSources().catch(() => null)]);
  if (got.status !== 'ok') return { error: got.status, layers: [] };
  const r = sch.JetStreamFile.safeParse(got.raw);
  if (!r.success) {
    console.warn('[map] jetstream.json failed validation', r.error.issues.slice(0, 5));
    return { error: 'failed', layers: [] };
  }
  const seen = new Set();
  const layers = r.data.layers.filter((l) => !seen.has(l.id) && seen.add(l.id));
  const given = Array.isArray(got.raw.layers) ? got.raw.layers.length : 0;
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
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, c.width / 2, c.height / 2 + ratio / 2);
  const d = ctx.getImageData(0, 0, c.width, c.height);
  return { width: c.width, height: c.height, data: new Uint8Array(d.data.buffer) };
}

/**
 * The jet stream layer. opts: { index, box (the Jet stream details in the Layers panel), legend (the legend strip),
 * announce(text), initial (layer ids from the address bar, the active one last), setLayers(ids) (the address bar
 * state), onChange() (the address bar and the 2D picture follow), tone ('map' or 'satellite'), filter() (the Type and
 * Show only filters), current() (the view), panelRoot (the panel content) }.
 */
export function createJetStream(opts) {
  const { index, box } = opts;
  const body = box.querySelector('.jet-body');
  const summary = box.querySelector('summary');
  let model = null;
  let loading = null;
  let order = [];          // visible layer ids in the order they were turned on; the active one is last
  let mapCtl = null;
  let mapUp = false;       // the WebGL map's data layers are in
  let mapReady = false;    // the jet layers are on it
  let drawn = null;        // the FeatureCollection on the map
  let tone = opts.tone || 'map';
  let linked = opts.initial || [];
  const inputs = new Map(); // layer id -> checkbox
  const onCount = h('span', { class: 'jet-on', hidden: true });
  if (summary) summary.append(onCount);

  const layerOf = (id) => model && model.byId && model.byId.get(id);
  const activeLayer = () => layerOf(order[order.length - 1]) || null;
  const visibleLayers = () => (model && model.layers ? model.layers.filter((l) => order.includes(l.id)) : []);
  const resortOf = (id) => index.resorts.get(id) || null;
  const stateName = (code) => (index.states.get(code) || {}).name || code;
  const keep = (r) => matchesFilter(r, opts.filter ? opts.filter() : null);
  const swatch = (color) => h('span', { class: 'jet-swatch', style: `--c:${color}`, 'aria-hidden': 'true' });
  const winters = (l) => plural(l.seasons.length, 'winter', 'winters');
  /** The baseline's winters run in a row, so it shows their span; an ENSO layer shows how many it averages. */
  const span = (l) => (l.kind === 'baseline' && l.season_range ? l.season_range : winters(l));
  const name = (l) => nb(l.label);
  /** A resort's page, in a state its cluster spans (as a click on the map picks it). */
  const resortHref = (r) => {
    const c = index.clusters.get(r.cluster_id);
    const st = c && !c.states.includes(r.state) ? c.states[0] : r.state;
    return hrefFor({ level: 'resort', stateCode: st, clusterId: r.cluster_id, resortId: r.id });
  };
  const eventsMode = () => document.body.classList.contains('events-mode');

  function sourceLink(ref) {
    const s = index.sources.get(ref.source_id);
    if (!s) return h('span', null, ref.source_id);
    const url = safeUrl(s.url);
    return url ? h('a', { href: url, ...NEW_TAB, class: 'src-link' }, s.title || ref.source_id) : h('span', null, s.title || ref.source_id);
  }

  function shortSource(ref) {
    const s = index.sources.get(ref.source_id);
    const url = s && safeUrl(s.url);
    return url ? h('a', { href: url, ...NEW_TAB, class: 'src-link', title: s.title || ref.source_id }, 'Source') : sourceLink(ref);
  }

  // ── The box: a checkbox per layer, the note, the latest reading, the card, the sources ──
  const card = h('section', { class: 'jet-card', id: 'jet-card', 'aria-labelledby': 'jet-card-title', hidden: true });
  let nowButton = null;
  function option(l) {
    const id = `jet-layer-${l.id}`;
    // The visible label shows the name over its winters; the name reads them as one phrase ("El Niño, 13 winters").
    const input = h('input', { type: 'checkbox', id, dataset: { jet: l.id }, 'aria-label': `${l.label}, ${span(l)}` });
    inputs.set(l.id, input);
    return h('label', { class: 'lp-opt lp-checkbox jet-opt', for: id }, input, swatch(l.color),
      h('span', { class: 'jet-opt-text' }, h('span', { class: 'lp-text' }, name(l)), h('span', { class: 'jet-count' }, span(l))));
  }
  /** NOAA's latest reading (the file's `current`) and a button that shows the path it matches. */
  function currentBlock(f) {
    const c = f.current;
    if (!c) return null;
    const value = `${c.value > 0 ? '+' : c.value < 0 ? '−' : ''}${Math.abs(c.value).toFixed(2)}`;
    const phase = PHASE_WORD[c.phase] || nb(String(c.phase).replace(/_/g, ' '));
    const word = c.phase === 'neutral' || !c.strength ? phase : `${c.strength} ${phase}`;
    const target = c.layer ? layerOf(c.layer) : null;
    nowButton = target ? h('button', { type: 'button', class: 'jet-now-btn', dataset: { jet: target.id } }, `Show the ${name(target)} path`) : null;
    if (nowButton) nowButton.addEventListener('click', () => (order.includes(target.id) ? activate(target.id) : set(target.id, true)));
    return h('div', { class: 'jet-now' },
      h('p', { class: 'jet-note' }, `NOAA's latest reading, ${c.months}: ${value}, ${word}${c.preliminary ? ' (preliminary)' : ''}.`),
      // The full source title is in the Sources line below; here a short link keeps the block small.
      h('p', { class: 'fact-meta jet-meta' }, badge(c.confidence), ` Read on ${formatIsoDate(c.as_of)} · `, shortSource(c)),
      nowButton);
  }
  function build() {
    clear(body);
    box.removeAttribute('aria-busy');
    if (model.error) {
      if (model.error === 'missing') {
        body.append(h('p', { class: 'jet-note' }, 'The jet stream paths arrive with the next data build.'));
        return;
      }
      const again = h('button', { type: 'button', class: 'jet-retry' }, 'Try again');
      again.addEventListener('click', () => api.retry());
      body.append(h('p', { class: 'jet-note', role: 'status' }, 'The jet stream paths could not load. Try again.'), again);
      return;
    }
    const f = model.file;
    const fieldset = h('fieldset', { class: 'lp-group jet-layers' }, h('legend', { class: 'lp-legend' }, 'Paths'), model.layers.map(option));
    fieldset.addEventListener('change', (e) => {
      const input = e.target.closest('input[data-jet]');
      if (input) set(input.dataset.jet, input.checked);
    });
    const height = f.level_hpa === 250 ? 'about 10 km up (250 hPa)' : `at ${fmtInt(f.level_hpa)} hPa`;
    body.append(fieldset,
      h('p', { class: 'jet-note' }, `Average jet stream ${height}, ${f.season_label}.`),
      currentBlock(f),
      card,
      h('p', { class: 'jet-src' }, 'Sources: ', sourceLink(f.sources.winds), ', ', sourceLink(f.sources.enso), '.'),
      f.method ? h('details', { class: 'jet-how' }, h('summary', null, 'How the paths are made'), h('p', null, f.method)) : null);
  }

  // The card: the title names the band, a "List for" choice when several paths are on, then the active layer's list.
  // The list is rebuilt only when the active layer or the filters change, so states the reader opened stay open.
  const title = h('h2', { class: 'jet-card-title', id: 'jet-card-title' });
  const pickSlot = h('div', { class: 'jet-pick-slot' });
  const compareSlot = h('div', { class: 'jet-compare' });
  const listSlot = h('div', { class: 'jet-list' });
  let listFor = null;
  card.append(title, pickSlot, compareSlot, listSlot);
  function renderCard() {
    const act = activeLayer();
    card.hidden = !act;
    if (nowButton) nowButton.hidden = Boolean(act && act.id === nowButton.dataset.jet);
    if (!act) { clear(pickSlot); clear(compareSlot); clear(listSlot); listFor = null; return; }
    const visible = visibleLayers();
    card.style.setProperty('--c', act.color);
    title.textContent = `Under the ${name(act)} band`;
    if (visible.length > 1) {
      // Radios rather than a select: long names ("Moderate and strong El Niño") wrap instead of being cut.
      const group = h('fieldset', { class: 'jet-pick' }, h('legend', { class: 'jet-pick-label' }, 'List for'),
        visible.map((l) => h('label', { class: 'lp-opt lp-radio jet-pick-opt', for: `jet-active-${l.id}` },
          h('input', { type: 'radio', name: 'jet-active', id: `jet-active-${l.id}`, value: l.id, checked: l.id === act.id }),
          swatch(l.color), h('span', { class: 'lp-text' }, name(l)))));
      group.addEventListener('change', (e) => { if (e.target.name === 'jet-active') activate(e.target.value); });
      pickSlot.replaceChildren(group);
    } else pickSlot.replaceChildren(h('p', { class: 'jet-one' }, swatch(act.color), h('span', null, span(act))));
    renderCompare(act, visible);
    const key = `${act.id}|${JSON.stringify(opts.filter ? opts.filter() : null)}`;
    if (listFor === key) return;
    listFor = key;
    listSlot.replaceChildren(...stateList(act).flat().filter(Boolean));
  }
  /** Two or more paths: the states under the active band only, and (with two) under the other one only. */
  function renderCompare(act, visible) {
    if (visible.length < 2) { clear(compareSlot); return; }
    const others = visible.filter((l) => l !== act);
    const line = (a, rest) => {
      const { names, skiAreas } = onlyUnder(a, rest, stateGroups(a, resortOf, stateName, keep));
      const restName = rest.length === 1 ? `the ${name(rest[0])} band` : 'the other bands';
      return h('p', { class: 'jet-diff' }, swatch(a.color), h('span', null, names.length
        ? `Under ${name(a)} only: ${names.join(', ')}${skiAreas ? ` (${plural(skiAreas, 'ski area', 'ski areas')})` : ''}.`
        : `Every state under the ${name(a)} band also sits under ${restName}.`));
    };
    compareSlot.replaceChildren(...[line(act, others), others.length === 1 ? line(others[0], [act]) : null].filter(Boolean));
  }
  function stateList(act) {
    const groups = stateGroups(act, resortOf, stateName, keep);
    const total = groups.reduce((n, g) => n + g.total, 0);
    const shown = groups.reduce((n, g) => n + g.resorts.length, 0);
    const withSki = groups.filter((g) => g.resorts.length);
    const rest = groups.filter((g) => !g.resorts.length);
    // Branch names only when the listed states lie under different branches.
    const mixed = new Set(withSki.map((g) => [...new Set(g.branches)].join('|'))).size > 1;
    const open = shown <= OPEN_ALL_MAX;
    let sum;
    if (!groups.length) sum = 'This band crosses no state or ski area on the map.';
    else if (!total) sum = `${plural(groups.length, 'state', 'states')}, west to east. This band crosses no ski areas.`;
    else {
      sum = `${plural(groups.length, 'state', 'states')} and ${plural(total, 'ski area', 'ski areas')}, west to east.`;
      if (shown < total) sum += ` ${fmtInt(shown)} of ${fmtInt(total)} shown by your filters.`;
    }
    let all = null;
    if (!open && withSki.length > 1) {
      // The label says what a press does; no pressed state, which would read as "Close every state, pressed".
      all = h('button', { type: 'button', class: 'jet-all' }, 'Open every state');
      all.addEventListener('click', () => {
        const on = all.textContent === 'Open every state';
        for (const d of listSlot.querySelectorAll('details.jet-state-box')) d.open = on;
        all.textContent = on ? 'Close every state' : 'Open every state';
      });
    }
    const ensoRef = model.file.sources.enso;
    const branchKey = (g) => [...new Set(g.branches)].join(' and ');
    const stateItem = (g) => h('li', { class: 'jet-state' },
      h('details', { class: 'jet-state-box', open },
        h('summary', { class: 'jet-state-name' }, h('span', { class: 'jet-state-label' }, g.name),
          h('span', { class: 'jet-n' }, plural(g.resorts.length, 'ski area', 'ski areas'))),
        h('ul', { class: 'jet-resorts' }, g.resorts.map((x) => h('li', null,
          h('a', { href: resortHref(x.record), 'data-nav': '' }, x.name),
          // A state under two branches names the branch of each ski area.
          mixed && new Set(g.resorts.map((y) => y.branch)).size > 1 && x.branch ? h('span', { class: 'jet-branch' }, x.branch) : null)))));
    // When the list mixes branches, the states group under a heading per branch (in the order they first appear).
    let lists = null;
    if (withSki.length && mixed) {
      const keys = [...new Set(withSki.map(branchKey))];
      lists = keys.map((k) => h('div', { class: 'jet-branch-group' }, h('h3', { class: 'jet-branch-head' }, k),
        h('ol', { class: 'jet-states' }, withSki.filter((g) => branchKey(g) === k).map(stateItem))));
    } else if (withSki.length) lists = h('ol', { class: 'jet-states' }, withSki.map(stateItem));
    return [
      h('p', { class: 'jet-sum' }, sum),
      all,
      lists,
      rest.length ? h('p', { class: 'jet-also' }, `${withSki.length ? 'Also under the band' : 'Under the band'}: ${rest.map((g) => g.name).join(', ')}.`) : null,
      h('p', { class: 'fact-meta jet-meta' }, badge(act.confidence), ` Computed from data read on ${formatIsoDate(act.as_of)} · `, sourceLink(act)),
      act.kind === 'baseline' ? null : h('p', { class: 'fact-meta jet-meta' }, badge(ensoRef.confidence),
        ` El Niño and La Niña years read on ${formatIsoDate(ensoRef.as_of)} · `, sourceLink(ensoRef)),
      h('details', { class: 'jet-winters' }, h('summary', null, `${winters(act)} in this average`), h('p', null, act.seasons.join(', '))),
    ];
  }

  // ── Legend strip: a line per visible layer and the ring, in a row of their own (first on phones) ──
  const legendItems = opts.legend ? h('ul', { class: 'ml-items ml-jet' }) : null;
  const legendRow = legendItems ? h('div', { class: 'ml-jet-row', hidden: true }, legendItems) : null;
  if (legendRow) opts.legend.append(legendRow);
  function renderLegend() {
    if (!legendItems) return;
    const act = activeLayer();
    legendRow.hidden = !act;
    opts.legend.classList.toggle('has-jet', Boolean(act));
    // Narrow desktops: the layers panel stops above the extra legend row (jetstream.css).
    if (opts.legend.parentElement) opts.legend.parentElement.classList.toggle('jet-legend', Boolean(act));
    if (!act) { clear(legendItems); return; }
    legendItems.replaceChildren(h('li', { class: 'ml-jet-title' }, 'Jet stream'),
      ...visibleLayers().map((l) => h('li', { class: l === act ? 'is-active' : null },
        h('span', { class: 'ml-jet-line', style: `--c:${l.color}`, 'aria-hidden': 'true' }), name(l))),
      // EVENTS mode hides the rings with the resort dots, and this item with them (jetstream.css).
      h('li', { class: 'ml-jet-ring-item' }, h('span', { class: 'ml-jet-ring', style: `--c:${act.color}`, 'aria-hidden': 'true' }), 'Ringed: under the band'));
  }

  // ── Panel line: what the paths on mean for the state, cluster or resort in view ──
  function placePhrase(view) {
    if (view.level === 'resort') { const r = resortOf(view.resortId); return r && { kind: 'resort', name: r.name, lon: r.lon, latS: r.lat, latN: r.lat, ids: [r.id] }; }
    if (view.level === 'cluster' || view.level === 'compare') {
      const c = index.clusters.get(view.clusterId);
      if (!c || !c.bbox) return null;
      return { kind: 'cluster', name: `the ${c.name} cluster`, lon: (c.bbox[0][0] + c.bbox[1][0]) / 2, latS: c.bbox[0][1], latN: c.bbox[1][1], ids: c.resort_ids, cluster: c };
    }
    if (view.level === 'state') {
      const s = index.states.get(view.stateCode);
      if (!s || !s.bbox) return null;
      return { kind: 'state', code: s.code, name: s.name, lon: (s.bbox[0][0] + s.bbox[1][0]) / 2, latS: s.bbox[0][1], latN: s.bbox[1][1] };
    }
    return null;
  }
  /** One sentence for a layer and a place ("Stevens Pass sits under the La Niña band, northern branch."). */
  function sentence(l, p) {
    const L = name(l);
    const branchOf = (x) => (l.branches.length > 1 && x.branch ? (l.branches.find((b) => b.id === x.branch) || {}).label : null);
    if (p.kind === 'resort') {
      const x = l.overflies.resorts.find((y) => y.id === p.ids[0]);
      if (x) { const b = branchOf(x); return `${p.name} sits under the ${L} band${b ? `, ${b.toLowerCase()}` : ''}.`; }
    } else if (p.kind === 'state') {
      if (l.overflies.states.some((s) => s.code === p.code)) {
        const n = l.overflies.resorts.filter((x) => (x.state || (resortOf(x.id) || {}).state) === p.code && resortOf(x.id)).length;
        return n ? `The ${L} band crosses ${p.name}: ${plural(n, 'ski area', 'ski areas')}.` : `The ${L} band crosses ${p.name}.`;
      }
    } else {
      const ids = new Set(p.ids);
      const n = l.overflies.resorts.filter((x) => ids.has(x.id)).length;
      // The lists count ski areas only, so the cluster's total does too.
      const total = p.ids.map(resortOf).filter((r) => r && (r.facility_type || 'ski_area') === 'ski_area').length;
      if (n && n >= total) return total === 1 ? `The ski area in ${p.name} sits under the ${L} band.` : `All ${fmtInt(total)} ski areas in ${p.name} sit under the ${L} band.`;
      if (n) return `${fmtInt(n)} of ${fmtInt(total)} ski areas in ${p.name} sit under the ${L} band.`;
    }
    const { side, count } = bandRelation(l, p.lon, p.latS, p.latN);
    const bands = count > 1 ? `${L} bands pass` : `${L} band passes`;
    const Name = p.name.charAt(0).toUpperCase() + p.name.slice(1);
    if (side === 'south' || side === 'north') return `The ${bands} ${side} of ${p.name}.`;
    if (side === 'between') return `${Name} sits between the ${L} bands.`;
    if (side === 'over') return p.kind === 'state' ? `The ${L} band touches ${p.name}.` : `${Name} sits at the edge of the ${L} band.`;
    return `The ${L} band runs far from ${p.name}.`;
  }
  function panelNote(view = opts.current ? opts.current() : null) {
    const root = opts.panelRoot;
    if (!root) return;
    for (const old of root.querySelectorAll('.jet-panel-note')) old.remove();
    const p = view && model && !model.error && order.length ? placePhrase(view) : null;
    if (!p) return;
    const anchor = root.querySelector('.tier-row') || root.querySelector('#panel-heading');
    if (!anchor) return;
    const layers = [activeLayer(), ...visibleLayers().filter((l) => l !== activeLayer())];
    const note = h('div', { class: 'jet-panel-note' },
      layers.map((l) => h('p', null, swatch(l.color), h('span', null, sentence(l, p)))));
    const after = anchor.nextElementSibling && anchor.nextElementSibling.classList.contains('intro') ? anchor.nextElementSibling : anchor;
    after.after(note);
  }

  // ── WebGL map ──
  function fadeOpacity(on, off) {
    const active = ['boolean', ['get', 'active'], false];
    return ['interpolate', ['linear'], ['zoom'], FADE[0], ['case', active, on, off], FADE[1], 0];
  }
  function addLayers() {
    const map = mapCtl.map;
    if (map.getSource(SRC)) return;
    const f = model.file;
    const credit = [f.sources.winds, f.sources.enso].map((ref) => {
      const s = index.sources.get(ref.source_id);
      const url = s && safeUrl(s.url);
      const t = (s && s.title) || ref.source_id;
      return url ? `<a href="${url.replace(/"/g, '&quot;')}" target="_blank" rel="noopener">${escapeHtml(t)}</a>` : escapeHtml(t);
    }).join(', ');
    const empty = { type: 'FeatureCollection', features: [] };
    map.addSource(SRC, { type: 'geojson', data: empty, attribution: `Jet stream: ${credit}` });
    map.addSource(LABEL_SRC, { type: 'geojson', data: empty });
    const before = map.getLayer('resorts-glow') ? 'resorts-glow' : undefined;
    const kind = (k) => ['==', ['get', 'kind'], k];
    const active = ['boolean', ['get', 'active'], false];
    const sort = ['get', 'order'];
    // The states under the active band: a thin outline in its color, under the selected state's green line.
    map.addLayer({ id: 'jet-states', type: 'line', source: 'states', filter: ['in', ['get', 'code'], ['literal', []]],
      paint: { 'line-color': '#ffffff', 'line-width': ['interpolate', ['linear'], ['zoom'], 3, 0.9, 7, 1.6], 'line-opacity': 0.6 } },
    map.getLayer('states-line-selected') ? 'states-line-selected' : before);
    map.addLayer({ id: 'jet-band', type: 'fill', source: SRC, filter: kind('band'), layout: { 'fill-sort-key': sort },
      paint: { 'fill-color': ['get', 'color'], 'fill-opacity': fadeOpacity(0.2, 0.09), 'fill-antialias': false } }, before);
    // Band edges: thin and solid, unlike the dashed outlines of the catch all clusters.
    map.addLayer({ id: 'jet-edge', type: 'line', source: SRC, filter: kind('edge'), layout: { 'line-sort-key': sort, 'line-join': 'round' },
      paint: { 'line-color': ['get', 'color'], 'line-width': 0.8, 'line-opacity': ['case', active, 0.7, 0.4] } }, before);
    map.addLayer({ id: 'jet-axis-casing', type: 'line', source: SRC, filter: kind('axis'), layout: { 'line-sort-key': sort, 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': '#000000', 'line-width': ['case', active, 6.5, 5], 'line-blur': 1.5, 'line-opacity': 0.55 } }, before);
    map.addLayer({ id: 'jet-axis', type: 'line', source: SRC, filter: kind('axis'), layout: { 'line-sort-key': sort, 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': ['get', 'color'], 'line-width': ['case', active, 3, 2.2] } }, before);
    // Labels above the resort dots: one per branch, inside the open map area (placeLabels after every move).
    map.addLayer({ id: 'jet-label', type: 'symbol', source: LABEL_SRC,
      layout: { 'icon-image': ['get', 'image'], 'icon-rotate': ['get', 'rot'], 'icon-rotation-alignment': 'viewport',
        'icon-allow-overlap': false, 'icon-padding': 4, 'symbol-sort-key': ['get', 'rank'] } });
    map.on('moveend', placeLabels);
    map.on('resize', placeLabels);
    applyTone();
  }
  function applyTone() {
    if (!mapCtl || !mapCtl.map.getLayer('jet-band')) return;
    const map = mapCtl.map;
    const p = paintOf(tone);
    const active = ['boolean', ['get', 'active'], false];
    map.setPaintProperty('jet-band', 'fill-opacity', fadeOpacity(p.band[0], p.band[1]));
    map.setPaintProperty('jet-edge', 'line-opacity', ['case', active, p.edge[0], p.edge[1]]);
    map.setPaintProperty('jet-axis-casing', 'line-opacity', p.casing);
    map.setPaintProperty('jet-states', 'line-opacity', p.states);
  }
  /**
   * One label per visible branch, at a point of its axis inside the open map area (the map minus the panels and the
   * legend), turned along the line. Each label takes the first spot along its line whose box clears the labels placed
   * before it, the active layer first.
   */
  const labelSize = new Map(); // image id -> [width, height] in css px
  function placeLabels() {
    if (!mapReady) return;
    const map = mapCtl.map;
    const src = map.getSource(LABEL_SRC);
    if (!src) return;
    const canvas = map.getCanvas();
    const pad = mapCtl.openPadding || { top: 0, right: 0, bottom: 0, left: 0 };
    const x0 = pad.left + 24; const x1 = canvas.clientWidth - pad.right - 24;
    const y0 = pad.top + 16; const y1 = canvas.clientHeight - pad.bottom - 16;
    const several = order.length > 1;
    const spots = [0.5, 0.3, 0.7, 0.15, 0.85, 0.4, 0.6, 0.22, 0.78];
    const placed = []; // boxes [xa, ya, xb, yb]
    const features = [];
    for (const id of [...order].reverse()) {
      const l = layerOf(id);
      for (const b of l ? l.branches : []) {
        const image = `jet-label-${l.id}-${b.id}-${several ? 'l' : 's'}`;
        if (!map.hasImage(image)) {
          const img = labelImage(lineLabel(l, b, several), l.color);
          map.addImage(image, img, { pixelRatio: 2 });
          labelSize.set(image, [img.width / 2, img.height / 2]);
        }
        const [w, hgt] = labelSize.get(image) || [120, 17];
        const pts = axisLines(b.axis).flat().map(([lon, lat]) => { const q = map.project([lon, lat]); return { lon, lat, x: q.x, y: q.y }; });
        const inside = pts.map((q, i) => i).filter((i) => pts[i].x - w / 2 >= x0 && pts[i].x + w / 2 <= x1 && pts[i].y >= y0 && pts[i].y <= y1);
        if (!inside.length) continue;
        let pick = null;
        for (const f of spots) {
          const i = inside[Math.min(inside.length - 1, Math.floor(inside.length * f))];
          const a = pts[Math.max(0, i - 2)]; const c = pts[Math.min(pts.length - 1, i + 2)];
          let rot = (Math.atan2(c.y - a.y, c.x - a.x) * 180) / Math.PI;
          if (rot > 90) rot -= 180; else if (rot < -90) rot += 180;
          const t = (rot * Math.PI) / 180;
          const bw = Math.abs(w * Math.cos(t)) + Math.abs(hgt * Math.sin(t)) + 8;
          const bh = Math.abs(w * Math.sin(t)) + Math.abs(hgt * Math.cos(t)) + 8;
          const box = [pts[i].x - bw / 2, pts[i].y - bh / 2, pts[i].x + bw / 2, pts[i].y + bh / 2];
          const clear = placed.every((q) => box[2] < q[0] || box[0] > q[2] || box[3] < q[1] || box[1] > q[3]);
          if (!pick || clear) pick = { i, rot, box };
          if (clear) break;
        }
        placed.push(pick.box);
        features.push({ type: 'Feature', geometry: { type: 'Point', coordinates: [pts[pick.i].lon, pts[pick.i].lat] },
          properties: { image, rot: Math.round(pick.rot * 10) / 10, rank: features.length, layer: l.id, branch: b.id } });
      }
    }
    src.setData({ type: 'FeatureCollection', features });
  }
  function refreshMap() {
    if (!mapReady) return;
    const map = mapCtl.map;
    drawn = jetFeatures(model.layers, order);
    map.getSource(SRC).setData(drawn);
    const act = activeLayer();
    map.setFilter('jet-states', ['in', ['get', 'code'], ['literal', act ? act.overflies.states.map((s) => s.code) : []]]);
    if (act) map.setPaintProperty('jet-states', 'line-color', act.color);
    // Hidden while nothing is on, so the map's attribution lists the sources only while a path shows.
    for (const id of LAYERS) map.setLayoutProperty(id, 'visibility', order.length ? 'visible' : 'none');
    mapCtl.setResortRing(act ? act.overflies.resorts.map((x) => x.id) : [], act ? act.color : null);
    placeLabels();
  }
  function setupMap() {
    if (mapReady || !mapUp || !model || model.error || !model.layers.length) return;
    try {
      addLayers();
      mapReady = true;
      applyTone();
      refreshMap();
    } catch (err) { console.warn('[map] jet stream map', err.message); }
  }

  // ── 2D picture before the WebGL map starts ──
  function drawPreview(svg, keepFn = null, { rings = true } = {}) {
    if (!model || !model.layers || !model.layers.length || !order.length || !svg) return;
    const p = paintOf(tone);
    const act = activeLayer();
    addPreviewOverlay(svg, (px, cam) => {
      const fade = cam.zoom <= FADE[0] ? 1 : cam.zoom >= FADE[1] ? 0 : (FADE[1] - cam.zoom) / (FADE[1] - FADE[0]);
      const under = cam.el('g', { fill: 'none', 'stroke-linejoin': 'round', 'stroke-linecap': 'round' });
      const path = (pts, close) => pts.map(([lon, lat], i) => { const [x, y] = px(lon, lat); return `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`; }).join('') + (close ? 'Z' : '');
      for (const id of order) {
        const l = layerOf(id);
        if (!l) continue;
        const on = l === act;
        for (const b of l.branches) {
          if (fade > 0) for (const ring of bandPolygons(b.band)) under.append(cam.el('path', { d: path(ring, true), fill: l.color, 'fill-opacity': ((on ? p.band[0] : p.band[1]) * fade).toFixed(3), stroke: 'none' }));
          for (const line of bandEdges(b.band)) under.append(cam.el('path', { d: path(line), stroke: l.color, 'stroke-opacity': on ? p.edge[0] : p.edge[1], 'stroke-width': 0.8 }));
          for (const line of axisLines(b.axis)) {
            under.append(cam.el('path', { d: path(line), stroke: '#000000', 'stroke-opacity': p.casing, 'stroke-width': on ? 6.5 : 5 }));
            under.append(cam.el('path', { d: path(line), stroke: l.color, 'stroke-width': on ? 3 : 2.2 }));
          }
        }
      }
      // The rings as on the map: a dark gap, then the ring. EVENTS mode draws no resort dots, so no rings either.
      const over = cam.el('g', { fill: 'none' });
      for (const x of act && rings ? act.overflies.resorts : []) {
        const r = resortOf(x.id);
        if (!r || (keepFn && !keepFn(r))) continue;
        const [cx, cy] = px(r.lon, r.lat);
        if (cx < -10 || cy < -10 || cx > cam.w + 10 || cy > cam.hgt + 10) continue;
        const base = resortRadius(r.size_rank || 1, cam.zoom);
        over.append(cam.el('circle', { cx: cx.toFixed(1), cy: cy.toFixed(1), r: (base + 1.8).toFixed(1), stroke: '#000000', 'stroke-opacity': 0.9, 'stroke-width': 1.4 }),
          cam.el('circle', { cx: cx.toFixed(1), cy: cy.toFixed(1), r: (base + 3.3).toFixed(1), stroke: act.color, 'stroke-width': 1.6 }));
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
    panelNote();
    opts.setLayers(order);
    opts.onChange();
    if (say) opts.announce(say);
  }
  function describe(l) {
    const groups = stateGroups(l, resortOf, stateName);
    const n = groups.reduce((k, g) => k + g.total, 0);
    return `${plural(groups.length, 'state', 'states')} and ${plural(n, 'ski area', 'ski areas')}`;
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
  function reveal({ toBox = false } = {}) {
    const scroller = box.closest('.layers-panel');
    if (!scroller || !box.open || scroller.scrollHeight <= scroller.clientHeight) return;
    const head = scroller.querySelector('.lp-sheet-head');
    const top = box.offsetTop - (head && head.offsetHeight ? head.offsetHeight : 0);
    const want = toBox || card.hidden ? top : Math.min(top, card.offsetTop - scroller.clientHeight * 0.45);
    if (want > scroller.scrollTop || toBox) scroller.scrollTo({ top: Math.max(0, want), behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
  }
  /** Make a visible layer the active one (the card's List for choice). */
  function activate(id) {
    const l = layerOf(id);
    if (!l || !order.includes(id) || order[order.length - 1] === id) return;
    order = toggled(order, id, true);
    sync({ say: `The list shows ${l.label}: ${describe(l)}.` });
    const radio = card.querySelector(`input[name="jet-active"][value="${CSS.escape(id)}"]`);
    if (radio) radio.focus();
  }

  /** Load (or load again after a failure). A failure keeps the link's paths for the next try. */
  function load() {
    if (loading) return loading;
    box.setAttribute('aria-busy', 'true');
    if (!body.firstChild || (model && model.error)) body.replaceChildren(h('p', { class: 'jet-note' }, 'Loading the jet stream paths.'));
    loading = loadModel(index).catch((err) => {
      console.warn('[map] jet stream', err.message);
      return { error: 'failed', layers: [] };
    }).then((m) => {
      model = m;
      loading = null;
      build();
      if (m.error === 'failed') return api;
      const wanted = linked;
      const { ids, lost } = resolveIds(wanted, m.layers);
      linked = [];
      order = ids;
      // A link's paths the build lacks drop out of the address bar, said once (a missing file says so in the box).
      if (order.length || lost) sync({ say: lost && !m.error ? 'A path in this link is no longer available.' : null });
      setupMap();
      if (order.length) reveal();
      return api;
    });
    return loading;
  }

  const api = {
    ready: load(),
    get loaded() { return Boolean(model); },
    /** The last load failed (not a missing file); opening the box or Try again loads again. */
    get failed() { return Boolean(model && model.error === 'failed'); },
    get model() { return model; },
    /** Visible layer ids, the order they were turned on (the active one last). */
    get visible() { return [...order]; },
    get active() { return order[order.length - 1] || null; },
    /** The bands, edges and axes on the WebGL map (null before it runs). */
    get features() { return drawn; },
    set,
    activate,
    drawPreview,
    panelNote,
    reveal,
    retry() { return load(); },
    /** The Type and Show only filters changed: the card follows them (the rings follow in map.js). */
    refilter() { if (model && !model.error) renderCard(); },
    /** The bands and lines on the WebGL map, once its data layers are in. */
    attachMap(ctl) {
      if (mapCtl === ctl) return;
      mapCtl = ctl;
      ctl.whenReady(() => { mapUp = true; setupMap(); reveal(); });
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
