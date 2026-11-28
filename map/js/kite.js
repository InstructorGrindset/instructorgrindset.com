// Snowkite layer (docs/map/SNOWKITE.md "UI"; ROADMAP item 24): zones where a source says people kite on snow, and
// snowkite events, with a report for each zone. app.js loads this module, kite.css and data/build/snowkite.json the
// first time the Snowkite checkbox in the Layers panel is turned on, or after the first paint with a ?kite=1, ?zone=
// or ?kevent= link. It owns the box body (filters, search, the zone list by state), the zone report and event card in
// the side panel, the legend items, and the marks on the WebGL map (a kite glyph per zone colored by its surface, a
// pennant per event venue; under the resort dots, above the basemap, the lower 48 and Alaska at their true place)
// and on the 2D picture before the map starts. Zone and event ids, kinds and surfaces all come from the file.
import { config } from './config.js';
import { schemas } from './data.js';
import { hrefFor } from './router.js';
import { addPreviewOverlay } from './preview.js';
import { dateSpan } from './eventshere.js';
import { h, clear, plural, fmtInt, monthShort, safeUrl } from './ui.js';

const ZONES = 'kite-zones';
const EVENTS = 'kite-events';
export const LAYERS = ['kite-zones-soft', 'kite-zones-halo', 'kite-zones', 'kite-events-halo', 'kite-events'];
const NEW_TAB = { target: '_blank', rel: 'noopener noreferrer' };
const NO_SURFACE = 'none';
const NO_SURFACE_COLOR = '#b8bcc4';
/** A state list opens by itself when it holds this many zones or fewer. */
const OPEN_ALL_MAX = 12;
const EVENT_COLOR = '#ffffff';

// ── Transforms (pure; tests/map/browser_test.mjs kiteChecks imports them) ──

/**
 * Months as words along `order` (November first): [12, 1, 2, 3] is "Dec to Mar", [11, 12, 1, 2, 3, 4] "Nov to Apr",
 * [1, 3] "Jan, Mar", [2] "Feb". Runs of three or more months read "Dec to Mar"; a run of two reads "Dec and Jan".
 */
export function formatMonths(months, order) {
  const set = new Set(months);
  const pos = order.map((m) => set.has(m));
  const runs = [];
  let cur = null;
  order.forEach((m, i) => {
    if (pos[i]) { if (!cur) { cur = [m]; runs.push(cur); } else cur.push(m); } else cur = null;
  });
  return runs.map((r) => (r.length >= 3 ? `${monthShort(r[0])} to ${monthShort(r[r.length - 1])}`
    : r.length === 2 ? `${monthShort(r[0])} and ${monthShort(r[1])}` : monthShort(r[0]))).join(', ');
}

/** The date line of an event: dates, "Runs ..." for a program, or what is known. Matches events.js whenText. */
export function whenText(e, season) {
  if (e.program) return e.usual_timing ? `Runs ${e.usual_timing.value}` : 'Runs all season';
  const ed = e.edition_2026_27;
  if (ed) return ed.end ? dateSpan(ed.start, ed.end) : `Starts ${dateSpan(ed.start)}`;
  const le = e.last_edition;
  const unpublished = `${season || 'This season'} date not yet published.`;
  if (le && le.start) return `Last held ${dateSpan(le.start, le.end)}. ${unpublished}`;
  if (le && le.season) return `Last held in the ${le.season} season. ${unpublished}`;
  return unpublished;
}

/** The cited block behind an event's date line. */
export function whenCite(e) {
  if (e.program) return e.usual_timing || e.season_long;
  return e.edition_2026_27 || e.last_edition || null;
}

/**
 * Does the zone pass the filters? f: { surfaces: Set of surface ids that are on, or null for all; months: Set of
 * month numbers chosen, empty for any; query: lower case text }. A zone with no surface listed shows while every
 * surface is on; a zone with no months shows while no month is chosen. state(code) gives the state name for the search.
 */
export function matchesZone(z, f, state = () => '') {
  if (f.surfaces && f.surfaces.size) {
    const ok = z.surface.length ? z.surface.some((s) => f.surfaces.has(s)) : f.surfaces.has(NO_SURFACE);
    if (!ok) return false;
  } else if (f.surfaces) return false;
  if (f.months && f.months.size && !(z.months && z.months.value.some((m) => f.months.has(m)))) return false;
  const q = (f.query || '').trim().toLowerCase();
  if (q) {
    const hay = `${z.name} ${z.state} ${state(z.state)}`.toLowerCase();
    if (!q.split(/\s+/).every((w) => hay.includes(w))) return false;
  }
  return true;
}

/** Zones grouped by state name (alphabetical), each group's zones by name. */
export function groupByState(zones, stateName) {
  const groups = new Map();
  for (const z of zones) {
    if (!groups.has(z.state)) groups.set(z.state, { code: z.state, name: stateName(z.state), zones: [] });
    groups.get(z.state).zones.push(z);
  }
  const out = [...groups.values()].sort((a, b) => a.name.localeCompare(b.name));
  for (const g of out) g.zones.sort((a, b) => a.name.localeCompare(b.name));
  return out;
}

/** The surface a zone is drawn in: its first listed surface, else none. */
export const primarySurface = (z) => z.surface[0] || NO_SURFACE;

/** GeoJSON Points of the zones that have a map point, for the zone layers. */
export function zoneFeatures(zones, labels) {
  return {
    type: 'FeatureCollection',
    features: zones.filter((z) => z.location).map((z) => ({
      type: 'Feature', geometry: { type: 'Point', coordinates: [z.location.lon, z.location.lat] },
      properties: {
        id: z.id, name: z.name, surface: primarySurface(z), region: z.location.precision === 'region',
        tip: `${z.name}: ${labels.kind(z)}, ${z.state}. Snowkite zone${z.location.precision === 'region' ? ', approximate location' : ''}.`,
      },
    })),
  };
}

/** GeoJSON Points of the events at their venues. */
export function eventFeatures(events, season) {
  return {
    type: 'FeatureCollection',
    features: events.map((e) => ({
      type: 'Feature', geometry: { type: 'Point', coordinates: [e.venue.location.lon, e.venue.location.lat] },
      properties: { id: e.id, name: e.name, tip: `${e.name}: ${whenText(e, season)}` },
    })),
  };
}

// ── Data ──

async function loadModel() {
  let res;
  try { res = await fetch(`${config.dataBase}snowkite.json`, { credentials: 'same-origin' }); } catch { return { error: 'failed' }; }
  if (res.status === 404) return { error: 'missing' };
  if (!res.ok) return { error: 'failed' };
  let raw;
  try { raw = await res.json(); } catch { return { error: 'failed' }; }
  const sch = await schemas();
  const r = sch.SnowkiteFile.safeParse(raw);
  if (!r.success) { console.warn('[map] snowkite.json failed validation', r.error.issues.slice(0, 5)); return { error: 'failed' }; }
  const file = r.data;
  const seen = new Set();
  const zones = file.zones.filter((z) => !seen.has(z.id) && seen.add(z.id));
  const evSeen = new Set();
  const events = file.events.filter((e) => !evSeen.has(e.id) && evSeen.add(e.id));
  const given = Array.isArray(raw.zones) ? raw.zones.length : 0;
  if (zones.length < given) console.warn(`[map] snowkite.json: ${given - zones.length} of ${given} zones skipped`);
  const eventById = new Map(events.map((e) => [e.id, e]));
  const zoneById = new Map(zones.map((z) => [z.id, z]));
  const zoneOfEvent = new Map();
  for (const z of zones) for (const id of z.events) if (eventById.has(id) && !zoneOfEvent.has(id)) zoneOfEvent.set(id, z);
  return {
    file, zones, events, eventById, zoneById, zoneOfEvent,
    kindLabel: new Map(file.kinds.map((k) => [k.id, k.label])),
    surface: new Map(file.surfaces.map((s) => [s.id, s])),
    season: file.season,
  };
}

// ── Canvas drawn marks (the style has no glyph or sprite server) ──

/** A kite: a diamond in the surface color with spars, a dark outline and a short tail. 30 px at ratio 2. */
function kiteImage(color) {
  const ratio = 2;
  const w = 15; const hgt = 17;
  const c = document.createElement('canvas');
  c.width = w * ratio; c.height = hgt * ratio;
  const ctx = c.getContext('2d', { willReadFrequently: true });
  ctx.scale(ratio, ratio);
  const body = () => { ctx.beginPath(); ctx.moveTo(7.5, 1); ctx.lineTo(13.3, 6.2); ctx.lineTo(7.5, 12.2); ctx.lineTo(1.7, 6.2); ctx.closePath(); };
  ctx.lineJoin = 'round'; ctx.lineCap = 'round';
  // The tail first, dark under light, so it reads on any basemap.
  ctx.beginPath(); ctx.moveTo(7.5, 12.2); ctx.bezierCurveTo(5, 13.4, 10, 14.4, 7.5, 16);
  ctx.strokeStyle = '#0a0a0a'; ctx.lineWidth = 2.6; ctx.stroke();
  ctx.strokeStyle = color; ctx.lineWidth = 1.2; ctx.stroke();
  body(); ctx.fillStyle = color; ctx.fill(); ctx.strokeStyle = '#0a0a0a'; ctx.lineWidth = 1.4; ctx.stroke();
  ctx.beginPath(); ctx.moveTo(7.5, 1); ctx.lineTo(7.5, 12.2); ctx.moveTo(1.7, 6.2); ctx.lineTo(13.3, 6.2);
  ctx.strokeStyle = 'rgba(10,10,10,0.55)'; ctx.lineWidth = 0.8; ctx.stroke();
  const d = ctx.getImageData(0, 0, c.width, c.height);
  return { width: c.width, height: c.height, data: new Uint8Array(d.data.buffer) };
}

/** A pennant on a pole, white with a dark outline: the mark of an event, unlike any zone or resort mark. */
function pennantImage() {
  const ratio = 2;
  const w = 15; const hgt = 17;
  const c = document.createElement('canvas');
  c.width = w * ratio; c.height = hgt * ratio;
  const ctx = c.getContext('2d', { willReadFrequently: true });
  ctx.scale(ratio, ratio);
  ctx.lineJoin = 'round'; ctx.lineCap = 'round';
  ctx.beginPath(); ctx.moveTo(3.5, 16); ctx.lineTo(3.5, 1.5);
  ctx.strokeStyle = '#0a0a0a'; ctx.lineWidth = 3.4; ctx.stroke();
  ctx.beginPath(); ctx.moveTo(3.5, 1.8); ctx.lineTo(13, 5.6); ctx.lineTo(3.5, 9.4); ctx.closePath();
  ctx.fillStyle = EVENT_COLOR; ctx.fill(); ctx.strokeStyle = '#0a0a0a'; ctx.lineWidth = 1.6; ctx.stroke();
  ctx.beginPath(); ctx.moveTo(3.5, 16); ctx.lineTo(3.5, 3);
  ctx.strokeStyle = EVENT_COLOR; ctx.lineWidth = 1.3; ctx.stroke();
  const d = ctx.getImageData(0, 0, c.width, c.height);
  return { width: c.width, height: c.height, data: new Uint8Array(d.data.buffer) };
}

const fs = (key) => ['boolean', ['feature-state', key], false];
const SIZE = ['interpolate', ['linear'], ['zoom'], 3, 1, 7, 1.2, 11, 1.45];

/**
 * The Snowkite layer. opts: { index, box (the Snowkite details), legend, announce(text), setOn(bool) (the address bar
 * state), onChange() (the address bar and the 2D picture follow), initialOn, tone, current() (the view), go(view),
 * kit() (panel.js building blocks), onSelect(view) }.
 */
export function createKite(opts) {
  const { index, box } = opts;
  const body = box.querySelector('.kite-body');
  const toggle = box.querySelector('input[data-kite-toggle]');
  const summaryEl = box.querySelector('summary');
  let model = null;
  let loading = null;
  let on = false;
  let mapCtl = null;
  let mapUp = false;
  let mapReady = false;
  let drawn = { zones: [], events: [] };   // the ids on the map (tests read them)
  const filters = { surfaces: null, months: new Set(), query: '' };   // surfaces null: all on
  const onBadge = h('span', { class: 'kite-on', hidden: true }, 'ON');
  if (summaryEl) summaryEl.append(onBadge);
  const stateName = (code) => (model && model.file.states[code]) || (index.states.get(code) || {}).name || code;
  const kindLabel = (z) => (model && model.kindLabel.get(z.kind)) || z.kind;
  const surfaceLabel = (id) => (model && model.surface.get(id) ? model.surface.get(id).label : id);
  const colorOf = (z) => (model && model.surface.get(primarySurface(z)) ? model.surface.get(primarySurface(z)).color : NO_SURFACE_COLOR);
  const zoneHref = (z) => hrefFor({ level: 'kite', zoneId: z.id });
  const eventHref = (e) => hrefFor({ level: 'kite', kiteEventId: e.id });

  const visibleZones = () => (model && model.zones ? model.zones.filter((z) => matchesZone(z, filters, stateName)) : []);
  const mappedCount = (zs) => zs.filter((z) => z.location).length;

  // ── The box body ──
  const status = h('p', { class: 'kite-status', role: 'status' });
  const surfaceSlot = h('div', { class: 'kite-surfaces' });
  const monthSlot = h('div', { class: 'kite-months' });
  const listSlot = h('div', { class: 'kite-list' });
  const eventSlot = h('div', { class: 'kite-events' });
  const searchInput = h('input', { type: 'search', id: 'kite-search', class: 'kite-search', autocomplete: 'off', spellcheck: 'false', placeholder: 'Zone name or state' });
  const searchBox = h('div', { class: 'kite-search-box' },
    h('label', { for: 'kite-search', class: 'lp-legend kite-search-label' }, 'Find a zone'), searchInput);
  const resetBtn = h('button', { type: 'button', class: 'kite-reset', hidden: true }, 'Show every zone');
  const controls = h('div', { class: 'kite-controls', hidden: true }, searchBox, surfaceSlot, monthSlot, resetBtn, status, listSlot, eventSlot);
  body.append(controls);

  const filtered = () => Boolean(filters.surfaces || filters.months.size || filters.query.trim());

  function buildControls() {
    const present = model.file.surfaces.filter((s) => model.zones.some((z) => z.surface.includes(s.id)));
    const noSurface = model.zones.some((z) => !z.surface.length);
    const opts2 = [...present, ...(noSurface ? [{ id: NO_SURFACE, label: 'Surface not listed', color: NO_SURFACE_COLOR }] : [])];
    const fieldset = h('fieldset', { class: 'lp-group kite-surface-group' }, h('legend', { class: 'lp-legend' }, 'Surface'),
      opts2.map((s) => {
        const id = `kite-surface-${s.id}`;
        const input = h('input', { type: 'checkbox', id, dataset: { surface: s.id }, checked: true });
        return h('label', { class: 'lp-opt lp-checkbox kite-opt', for: id }, input,
          h('span', { class: 'kite-swatch', style: `--c:${s.color}`, 'aria-hidden': 'true' }),
          h('span', { class: 'lp-text' }, s.label), h('span', { class: 'kite-n' }, String(zonesWith(s.id))));
      }));
    fieldset.addEventListener('change', () => {
      const checked = [...fieldset.querySelectorAll('input:checked')].map((i) => i.dataset.surface);
      filters.surfaces = checked.length === opts2.length ? null : new Set(checked);
      refresh({ say: true });
    });
    surfaceSlot.replaceChildren(fieldset);
    const months = model.file.month_order.filter((m) => model.zones.some((z) => z.months && z.months.value.includes(m)));
    const mfs = h('fieldset', { class: 'lp-group kite-month-group' }, h('legend', { class: 'lp-legend' }, 'Months you can kite'),
      h('div', { class: 'kite-chips' }, months.map((m) => {
        const b = h('button', { type: 'button', class: 'kite-chip', 'aria-pressed': 'false', dataset: { month: m } }, monthShort(m).toUpperCase());
        b.addEventListener('click', () => {
          if (filters.months.has(m)) filters.months.delete(m); else filters.months.add(m);
          b.setAttribute('aria-pressed', String(filters.months.has(m)));
          refresh({ say: true });
        });
        return b;
      })));
    monthSlot.replaceChildren(mfs);
    searchInput.addEventListener('input', () => { filters.query = searchInput.value; refresh({ say: true }); });
    searchInput.addEventListener('keydown', (e) => { if (e.key === 'Escape' && searchInput.value) { searchInput.value = ''; filters.query = ''; refresh({ say: true }); } });
    resetBtn.addEventListener('click', () => {
      filters.surfaces = null; filters.months = new Set(); filters.query = ''; searchInput.value = '';
      for (const i of surfaceSlot.querySelectorAll('input')) i.checked = true;
      for (const b of monthSlot.querySelectorAll('button')) b.setAttribute('aria-pressed', 'false');
      refresh({ say: true });
    });
  }
  const zonesWith = (surface) => model.zones.filter((z) => (surface === NO_SURFACE ? !z.surface.length : z.surface.includes(surface))).length;

  function zoneItem(z) {
    const bits = [kindLabel(z)];
    const a = h('a', { href: zoneHref(z), 'data-nav': '', class: 'kite-zone-link' }, z.name);
    return h('li', { class: 'kite-zone', dataset: { zone: z.id } },
      h('span', { class: 'kite-swatch', style: `--c:${colorOf(z)}`, 'aria-hidden': 'true' }), a,
      h('span', { class: 'kite-meta' }, bits.join(', ') + (z.location ? '' : '. No map point yet.')));
  }
  function renderList() {
    const zs = visibleZones();
    const groups = groupByState(zs, stateName);
    const open = zs.length <= OPEN_ALL_MAX || Boolean(filters.query.trim());
    const cur = opts.current ? opts.current() : null;
    const curId = cur && cur.level === 'kite' ? cur.zoneId : null;
    if (!groups.length) { listSlot.replaceChildren(h('p', { class: 'kite-none' }, 'No zone matches these filters.')); return; }
    listSlot.replaceChildren(h('ol', { class: 'kite-states' }, groups.map((g) => h('li', { class: 'kite-state' },
      h('details', { class: 'kite-state-box', open: open || g.zones.some((z) => z.id === curId) },
        h('summary', { class: 'kite-state-name' }, h('span', null, g.name), h('span', { class: 'kite-n' }, plural(g.zones.length, 'zone', 'zones'))),
        h('ul', { class: 'kite-zones' }, g.zones.map(zoneItem)))))));
    highlight(cur);
  }
  function renderEvents() {
    if (!model.events.length) { eventSlot.replaceChildren(); return; }
    eventSlot.replaceChildren(h('details', { class: 'kite-state-box kite-events-box' },
      h('summary', { class: 'kite-state-name' }, h('span', null, 'Snowkite events'), h('span', { class: 'kite-n' }, plural(model.events.length, 'event', 'events'))),
      h('ul', { class: 'kite-zones' }, model.events.map((e) => h('li', { class: 'kite-zone', dataset: { kevent: e.id } },
        h('span', { class: 'kite-flag', 'aria-hidden': 'true' }), h('a', { href: eventHref(e), 'data-nav': '', class: 'kite-zone-link' }, e.name),
        h('span', { class: 'kite-meta' }, `${whenText(e, model.season)} ${e.venue.name}${e.venue.state ? `, ${e.venue.state}` : ''}`))))));
  }
  function highlight(view) {
    for (const a of body.querySelectorAll('[aria-current]')) a.removeAttribute('aria-current');
    if (!view || view.level !== 'kite') return;
    const sel = view.zoneId ? `li[data-zone="${CSS.escape(view.zoneId)}"] a` : view.kiteEventId ? `li[data-kevent="${CSS.escape(view.kiteEventId)}"] a` : null;
    const a = sel ? body.querySelector(sel) : null;
    if (a) a.setAttribute('aria-current', 'page');
  }
  function summaryLine() {
    const all = model.zones.length;
    const zs = visibleZones();
    const noPoint = all - mappedCount(model.zones);
    if (!filtered()) {
      return `${plural(all, 'zone', 'zones')} in ${plural(new Set(model.zones.map((z) => z.state)).size, 'state', 'states')}, ${plural(model.events.length, 'event', 'events')}.${noPoint ? ` ${plural(noPoint, 'zone has', 'zones have')} no map point yet.` : ''}`;
    }
    return `Showing ${fmtInt(zs.length)} of ${plural(all, 'zone', 'zones')}.`;
  }
  function refresh({ say = false } = {}) {
    if (!model || model.error) return;
    status.textContent = summaryLine();
    resetBtn.hidden = !filtered();
    renderList();
    refreshMap();
    opts.onChange();
    if (say) opts.announce(summaryLine());
  }

  function build() {
    box.removeAttribute('aria-busy');
    if (model.error) {
      controls.hidden = true;
      body.querySelector('.kite-note')?.remove();
      if (model.error === 'missing') body.append(h('p', { class: 'kite-note' }, 'The snowkite zones arrive with the next data build.'));
      else {
        const again = h('button', { type: 'button', class: 'kite-reset kite-retry' }, 'Try again');
        again.addEventListener('click', () => api.retry());
        body.append(h('div', { class: 'kite-note' }, h('p', { role: 'status' }, 'The snowkite zones could not load. Try again.'), again));
      }
      return;
    }
    body.querySelector('.kite-note')?.remove();
    buildControls();
    renderEvents();
    status.textContent = summaryLine();
    renderList();
    controls.hidden = !on;
  }

  // ── Zone report and event card (the side panel) ──
  const crumbs = (items) => h('nav', { class: 'crumbs', 'aria-label': 'Breadcrumb' }, h('ol', null, items.map((it, i) => h('li', null,
    i === items.length - 1 ? h('span', { 'aria-current': 'page' }, it.label) : h('a', { href: it.href, 'data-nav': '' }, it.label)))));
  const layerHref = () => hrefFor({ level: 'national' });
  const facts = (...rows) => h('dl', { class: 'facts kite-facts' }, rows.filter(Boolean));
  const link = (url) => { const u = safeUrl(url); return u ? h('a', { href: u, ...NEW_TAB }, url.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')) : url; };

  function eventCard(e, kit, { zone = true } = {}) {
    const cite = whenCite(e);
    const when = whenText(e, model.season);
    const su = e.signup && e.signup.url;
    const rows = [
      h('div', { class: 'fact' }, h('dt', null, 'Dates'), h('dd', null, cite ? kit.sourcedValue({ ...cite, value: when, note: null }, (v) => v)
        : h('span', { class: 'fact-value' }, when))),
      h('div', { class: 'fact' }, h('dt', null, 'Venue'), h('dd', null, h('span', { class: 'fact-value' },
        `${e.venue.name}${e.venue.town ? `, ${e.venue.town}` : ''}${e.venue.state ? `, ${e.venue.state}` : ''}`))),
      e.format ? kit.fact('Format', e.format, (v) => v) : null,
      e.organizer ? kit.fact('Organizer', e.organizer, () => (e.organizer.url && safeUrl(e.organizer.url)
        ? h('a', { href: safeUrl(e.organizer.url), ...NEW_TAB }, e.organizer.name) : e.organizer.name)) : null,
      e.website ? kit.fact('Website', e.website, link) : null,
    ];
    const z = model.zoneOfEvent.get(e.id);
    return h('article', { class: 'event-card kite-event', 'aria-labelledby': `kev-${e.id}` },
      h('h4', { id: `kev-${e.id}`, class: 'kite-event-title' }, h('span', { class: 'kite-flag', 'aria-hidden': 'true' }), e.name),
      facts(...rows),
      su ? h('p', { class: 'kite-signup' }, h('a', { class: 'btn btn-sm', href: safeUrl(su.value) || '#', ...NEW_TAB }, 'Sign up'),
        ' ', kit.badge(su.confidence), h('span', { class: 'fact-meta' }, ' ', kit.sourceLink(su.source_id))) : h('p', { class: 'fact-meta' }, 'Sign up details are not published yet.'),
      !zone && z ? h('p', { class: 'kite-eventzone' }, 'Held at ', h('a', { href: zoneHref(z), 'data-nav': '' }, z.name), '.') : null);
  }

  function zoneReport(z, kit) {
    const order = model.file.month_order;
    const loc = z.location;
    const approx = loc && loc.precision === 'region';
    const precisionWord = { spot: 'A launch or parking point a source names', area: 'The center of the lake or area', region: 'Approximate location: a nearby town or the middle of a broad region' };
    const out = [
      crumbs([{ label: 'United States', href: hrefFor({ level: 'national' }) }, { label: 'Snowkite zones', href: layerHref() }, { label: z.name, href: zoneHref(z) }]),
      h('span', { class: 'eyebrow' }, 'Snowkite zone'),
      h('h2', { id: 'panel-heading', tabindex: '-1' }, z.name),
      h('p', { class: 'kite-tags' },
        h('span', { class: 'tier tier-facility' }, kindLabel(z)),
        ...z.surface.map((s) => h('span', { class: 'tier kite-surface-tag', style: `--c:${model.surface.get(s) ? model.surface.get(s).color : NO_SURFACE_COLOR}` }, surfaceLabel(s))),
        h('span', { class: 'tier tier-b' }, stateName(z.state))),
      !loc ? h('p', { class: 'notice kite-nopoint', role: 'note' }, 'No map point yet. This zone is in the list and has no mark on the map.') : null,
      approx ? h('p', { class: 'notice kite-approx', role: 'note' }, 'Approximate location. The mark shows a nearby town or the middle of a broad region, not a launch point.') : null,
    ];
    const rows = [
      kit.fact('Months', z.months, (v) => formatMonths(v, order)),
      kit.fact('Wind', z.wind, (v) => v),
      kit.fact('Snow and ice', z.snow_ice, (v) => v),
      kit.fact('Terrain', z.terrain, (v) => v),
      z.elevation_ft ? kit.fact('Elevation', z.elevation_ft, (v) => `${fmtInt(v)} ft`) : null,
      loc ? kit.fact('Map point', { ...loc, value: `${loc.lat.toFixed(3)}, ${loc.lon.toFixed(3)}`, note: loc.precision ? precisionWord[loc.precision] : null }, (v) => v) : null,
    ];
    const hazards = z.hazards.length ? z.hazards.map((hz, i) => kit.fact('Hazard', hz, (v) => v)) : [kit.fact('Hazards', null, (v) => v)];
    const access = z.access
      ? [['Road', z.access.road], ['Parking', z.access.parking], ['Fees', z.access.fees], ['Permit', z.access.permit]]
        .filter(([, v]) => v).map(([label, v]) => kit.fact(`Access: ${label.toLowerCase()}`, { ...z.access, value: v, note: null }, (x) => x))
      : [kit.fact('Access', null, (v) => v)];
    out.push(h('section', { class: 'panel-section kite-section' }, h('h3', null, 'Zone facts'),
      facts(...rows, ...hazards, ...access, z.website ? kit.fact('Website', z.website, link) : null)));
    if (z.report) {
      out.push(h('section', { class: 'panel-section kite-section' }, h('h3', null, 'Zone report'),
        h('p', { class: 'kite-summary' }, z.report.summary),
        h('p', { class: 'fact-meta' }, 'Written from: ', z.report.source_ids.flatMap((id, i) => [i ? ', ' : '', kit.sourceLink(id)]), '.')));
    }
    const evs = z.events.map((id) => model.eventById.get(id)).filter(Boolean);
    out.push(h('section', { class: 'panel-section kite-section' }, h('h3', null, 'Events here'),
      evs.length ? evs.map((e) => eventCard(e, kit)) : h('p', { class: 'kite-none' }, 'No snowkite event is listed for this zone yet.')));
    out.push(h('p', { class: 'kite-back' }, h('a', { href: layerHref(), 'data-nav': '' }, 'Back to the snowkite layer')));
    return out;
  }
  function eventView(e, kit) {
    const z = model.zoneOfEvent.get(e.id);
    return [
      crumbs([{ label: 'United States', href: hrefFor({ level: 'national' }) }, { label: 'Snowkite zones', href: layerHref() }, { label: e.name, href: eventHref(e) }]),
      h('span', { class: 'eyebrow' }, 'Snowkite event'),
      h('h2', { id: 'panel-heading', tabindex: '-1' }, e.name),
      h('section', { class: 'panel-section kite-section' }, eventCard(e, kit, { zone: false })),
      z ? h('p', { class: 'kite-eventzone' }, 'Held at the zone ', h('a', { href: zoneHref(z), 'data-nav': '' }, z.name), '.') : null,
      h('p', { class: 'kite-back' }, h('a', { href: layerHref(), 'data-nav': '' }, 'Back to the snowkite layer')),
    ];
  }
  function missingView(kit) {
    return [
      crumbs([{ label: 'United States', href: hrefFor({ level: 'national' }) }, { label: 'Snowkite zones', href: layerHref() }]),
      h('span', { class: 'eyebrow' }, 'Snowkite'),
      h('h2', { id: 'panel-heading', tabindex: '-1' }, 'Snowkite zones and events'),
      h('p', { class: 'notice', role: 'note' }, 'That zone is not in this build. The layer is on, so every zone with a map point shows on the map.'),
      h('p', { class: 'intro' }, status.textContent || ''),
    ];
  }

  function renderPanel(view, kit) {
    if (!model || model.error) return null;
    if (view.zoneId && model.zoneById.has(view.zoneId)) return zoneReport(model.zoneById.get(view.zoneId), kit);
    if (view.kiteEventId && model.eventById.has(view.kiteEventId)) return eventView(model.eventById.get(view.kiteEventId), kit);
    return missingView(kit);
  }
  const selected = (view) => (view && view.level === 'kite' ? (view.zoneId && model.zoneById.get(view.zoneId)) || (view.kiteEventId && model.eventById.get(view.kiteEventId)) || null : null);
  function select(view) {
    highlight(view);
    if (mapReady) {
      const z = view && view.level === 'kite' && view.zoneId && model.zoneById.get(view.zoneId);
      const e = view && view.level === 'kite' && view.kiteEventId && model.eventById.get(view.kiteEventId);
      mapCtl.map.setFilter('kite-zones-halo', ['==', ['get', 'id'], z ? z.id : '']);
      mapCtl.map.setFilter('kite-events-halo', ['==', ['get', 'id'], e ? e.id : '']);
    }
    const s = selected(view);
    if (s) document.title = `${s.name} • Snowkite • Instructor Grindset`;
  }

  // ── Legend strip: the surfaces in use and the event mark ──
  const legendItems = opts.legend ? h('ul', { class: 'ml-items ml-kite' }) : null;
  const legendRow = legendItems ? h('div', { class: 'ml-kite-row', hidden: true }, legendItems) : null;
  if (legendRow) opts.legend.append(legendRow);
  function renderLegend() {
    if (!legendItems) return;
    legendRow.hidden = !on || !model || Boolean(model.error);
    opts.legend.classList.toggle('has-kite', !legendRow.hidden);
    if (opts.legend.parentElement) opts.legend.parentElement.classList.toggle('kite-legend', !legendRow.hidden);
    if (legendRow.hidden) { clear(legendItems); return; }
    const used = model.file.surfaces.filter((s) => model.zones.some((z) => primarySurface(z) === s.id));
    legendItems.replaceChildren(h('li', { class: 'ml-kite-title' }, 'Snowkite'),
      ...used.map((s) => h('li', null, h('span', { class: 'kite-swatch', style: `--c:${s.color}`, 'aria-hidden': 'true' }), s.label)),
      model.events.length ? h('li', null, h('span', { class: 'kite-flag', 'aria-hidden': 'true' }), 'Event') : null);
  }

  // ── WebGL map ──
  function addLayers() {
    const map = mapCtl.map;
    if (map.getSource(ZONES)) return;
    const empty = { type: 'FeatureCollection', features: [] };
    map.addSource(ZONES, { type: 'geojson', data: empty, promoteId: 'id' });
    map.addSource(EVENTS, { type: 'geojson', data: empty, promoteId: 'id' });
    for (const s of [...model.file.surfaces.map((x) => [x.id, x.color]), [NO_SURFACE, NO_SURFACE_COLOR]]) {
      if (!map.hasImage(`kite-${s[0]}`)) map.addImage(`kite-${s[0]}`, kiteImage(s[1]), { pixelRatio: 2 });
    }
    if (!map.hasImage('kite-event')) map.addImage('kite-event', pennantImage(), { pixelRatio: 2 });
    const before = map.getLayer('resorts-glow') ? 'resorts-glow' : undefined;
    // An approximate point (a town standing in for a lake) gets a wide, soft disc under its mark.
    map.addLayer({ id: 'kite-zones-soft', type: 'circle', source: ZONES, filter: ['==', ['get', 'region'], true],
      paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 3, 11, 7, 26, 11, 48], 'circle-color': '#ffffff', 'circle-blur': 1, 'circle-opacity': 0.22 } }, before);
    map.addLayer({ id: 'kite-zones-halo', type: 'circle', source: ZONES, filter: ['==', ['get', 'id'], ''],
      paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 3, 11, 11, 17], 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': config.basemap.colors.ring, 'circle-stroke-width': 2 } }, before);
    map.addLayer({ id: 'kite-zones', type: 'symbol', source: ZONES,
      layout: { 'icon-image': ['concat', 'kite-', ['get', 'surface']], 'icon-size': SIZE, 'icon-allow-overlap': true, 'icon-ignore-placement': true,
        'icon-anchor': 'center', 'symbol-sort-key': ['case', ['get', 'region'], 0, 1] },
      paint: { 'icon-opacity': ['case', ['get', 'region'], 0.8, 1] } }, before);
    map.addLayer({ id: 'kite-events-halo', type: 'circle', source: EVENTS, filter: ['==', ['get', 'id'], ''],
      paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 3, 11, 11, 17], 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': config.basemap.colors.ring, 'circle-stroke-width': 2 } }, before);
    map.addLayer({ id: 'kite-events', type: 'symbol', source: EVENTS,
      layout: { 'icon-image': 'kite-event', 'icon-size': SIZE, 'icon-allow-overlap': true, 'icon-ignore-placement': true, 'icon-anchor': 'bottom-left', 'icon-offset': [-3, 1] } }, before);
  }
  function refreshMap() {
    if (!mapReady) return;
    const map = mapCtl.map;
    const zs = visibleZones();
    map.getSource(ZONES).setData(zoneFeatures(zs, { kind: kindLabel }));
    map.getSource(EVENTS).setData(eventFeatures(model.events, model.season));
    const show = on && !document.body.classList.contains('events-mode');
    for (const id of LAYERS) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', show ? 'visible' : 'none');
    mapCtl.setExtraPick(show ? pick : null);
    drawn = { zones: zs.filter((z) => z.location).map((z) => z.id).sort(), events: model.events.map((e) => e.id).sort() };
  }
  /** The zone or event under the pointer (map.js pick): zones first. */
  function pick(point, boxPx) {
    const map = mapCtl.map;
    const z = map.queryRenderedFeatures(boxPx, { layers: ['kite-zones'] });
    if (z.length) return { kind: 'zone', id: z[0].properties.id, name: z[0].properties.name, tip: z[0].properties.tip };
    const e = map.queryRenderedFeatures(boxPx, { layers: ['kite-events'] });
    if (e.length) return { kind: 'kevent', id: e[0].properties.id, name: e[0].properties.name, tip: e[0].properties.tip };
    return null;
  }
  function setupMap() {
    if (mapReady || !mapUp || !model || model.error) return;
    try {
      addLayers();
      mapReady = true;
      refreshMap();
      select(opts.current ? opts.current() : null);
    } catch (err) { console.warn('[map] snowkite map', err.message); }
  }

  // ── 2D picture before the WebGL map starts ──
  function drawPreview(svg) {
    if (!on || !model || model.error || !svg) return;
    addPreviewOverlay(svg, (px, cam) => {
      const over = cam.el('g', { fill: 'none' });
      const k = Math.min(1.3, 0.8 + (cam.zoom - 3) * 0.06);
      const inside = (x, y) => x > -12 && y > -12 && x < cam.w + 12 && y < cam.hgt + 12;
      for (const z of visibleZones()) {
        if (!z.location) continue;
        const [x, y] = px(z.location.lon, z.location.lat);
        if (!inside(x, y)) continue;
        const col = colorOf(z);
        const s = 7.5 * k;
        if (z.location.precision === 'region') over.append(cam.el('circle', { cx: x.toFixed(1), cy: y.toFixed(1), r: (s * 2).toFixed(1), fill: '#ffffff', 'fill-opacity': 0.2 }));
        over.append(cam.el('path', { d: `M${x.toFixed(1)} ${(y - s).toFixed(1)}L${(x + s * 0.78).toFixed(1)} ${y.toFixed(1)}L${x.toFixed(1)} ${(y + s * 0.95).toFixed(1)}L${(x - s * 0.78).toFixed(1)} ${y.toFixed(1)}Z`, fill: col, stroke: '#0a0a0a', 'stroke-width': 1.3, 'stroke-linejoin': 'round' }));
      }
      for (const e of model.events) {
        const [x, y] = px(e.venue.location.lon, e.venue.location.lat);
        if (!inside(x, y)) continue;
        const s = 8 * k;
        over.append(cam.el('path', { d: `M${x.toFixed(1)} ${(y + 1).toFixed(1)}L${x.toFixed(1)} ${(y - s).toFixed(1)}L${(x + s * 0.95).toFixed(1)} ${(y - s * 0.62).toFixed(1)}L${x.toFixed(1)} ${(y - s * 0.25).toFixed(1)}`, fill: '#ffffff', stroke: '#0a0a0a', 'stroke-width': 1.3, 'stroke-linejoin': 'round' }));
      }
      return { over };
    }, 'kite');
  }

  // ── State changes ──
  function setOn(next, { say = false } = {}) {
    if (next === on) return;
    on = next;
    if (toggle) toggle.checked = on;
    onBadge.hidden = !on;
    controls.hidden = !on || !model || Boolean(model.error);
    box.classList.toggle('is-on', on);
    renderLegend();
    refreshMap();
    opts.setOn(on);
    opts.onChange();
    if (say && model && !model.error) opts.announce(on ? `Snowkite layer on. ${summaryLine()}` : 'Snowkite layer off.');
  }

  function load() {
    if (loading) return loading;
    box.setAttribute('aria-busy', 'true');
    if (!model || model.error) { body.querySelector('.kite-note')?.remove(); body.prepend(h('p', { class: 'kite-note kite-loading' }, 'Loading the snowkite zones.')); }
    loading = loadModel().catch((err) => { console.warn('[map] snowkite', err.message); return { error: 'failed' }; }).then((m) => {
      model = m;
      loading = null;
      body.querySelector('.kite-loading')?.remove();
      build();
      if (m.error) return api;
      index.kite = api.indexHook;
      on = false;
      setOn(Boolean(opts.initialOn));
      setupMap();
      return api;
    });
    return loading;
  }

  const api = {
    ready: load(),
    get loaded() { return Boolean(model); },
    get failed() { return Boolean(model && model.error === 'failed'); },
    get model() { return model; },
    get on() { return on; },
    get drawn() { return drawn; },
    set: setOn,
    renderPanel,
    select,
    drawPreview,
    highlight,
    refresh,
    retry() { return load(); },
    /** The title of a zone or event view for the sub bar. */
    titleOf(view) { const s = model && !model.error ? selected(view) : null; return s ? s.name : null; },
    summary(view) {
      const s = model && !model.error ? selected(view) : null;
      if (!s) return 'Showing the snowkite layer.';
      return view.zoneId ? `Showing the snowkite zone ${s.name}.` : `Showing the snowkite event ${s.name}.`;
    },
    /** For camera.js viewFrame: the point of a zone or event view, or null (a zone with no map point). */
    indexHook: {
      pointOf(view) {
        if (!model || model.error) return null;
        const z = view.zoneId && model.zoneById.get(view.zoneId);
        if (z && z.location) return z.location;
        const e = view.kiteEventId && model.eventById.get(view.kiteEventId);
        return e ? { ...e.venue.location, precision: e.venue.location.precision === 'region' ? 'region' : 'spot' } : null;
      },
    },
    /** Events mode hides the layer with the resorts; leaving it brings it back. */
    syncMode() { refreshMap(); renderLegend(); },
    attachMap(ctl) {
      if (mapCtl === ctl) return;
      mapCtl = ctl;
      ctl.whenReady(() => { mapUp = true; setupMap(); });
    },
    fillSources() {},
  };
  return api;
}
