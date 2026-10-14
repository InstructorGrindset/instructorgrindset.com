// Trail map dialog with two tabs. "Official map": the resort's own trail map file shown straight from the
// resort's servers (officialmap.js; image viewer or PDF). "Interactive": its own MapLibre map of one resort with
// OpenSkiMap runs colored by North American trail sign (difficulty_us), lifts on top, the base area, a legend and a
// run list; also the fallback when the official file fails. Loaded only when opened; the Interactive tab loads its
// data and MapLibre when first shown. Data: data/build/trailmap/<resort_id>.json and Resort.official_trailmap
// (contract in docs/map/ARCHITECTURE.md, "Trail map" and "Official trail maps").
import { isPhone } from './config.js';
import { loadTrailMap } from './data.js';
import { h, clear, fmtInt, formatIsoDate, safeUrl } from './ui.js';
import { officialTrailmapLink, officialMapFile } from './panel.js';
import { createImageViewer, createPdfViewer, canEmbedPdf } from './officialmap.js';

// Sign colors on the light trail map surface. Black and double black share the ink and differ by width and dash,
// as their signs differ by one or two diamonds.
export const SIGNS = {
  green: { label: 'Green circle', help: 'Easiest', color: '#12804a' },
  blue: { label: 'Blue square', help: 'More difficult', color: '#1b5fc1' },
  black: { label: 'Black diamond', help: 'Most difficult', color: '#14171c' },
  double_black: { label: 'Double black diamond', help: 'Experts only', color: '#14171c' },
  terrain_park: { label: 'Terrain park', help: 'Features and jumps', color: '#d4610a' },
};
const UNRATED = { label: 'No rating in OpenStreetMap', color: '#7c8796' };
const LIFT = '#c2272f';
const SURFACE = '#f3f5f8';
const ORDER = ['green', 'blue', 'black', 'double_black', 'terrain_park'];
const EMPTY = { type: 'FeatureCollection', features: [] };

const sign = (k) => SIGNS[k] || UNRATED;
const colorExpr = ['match', ['coalesce', ['get', 'difficulty_us'], 'none'],
  ...ORDER.flatMap((k) => [k, SIGNS[k].color]), UNRATED.color];
const isArea = ['in', ['geometry-type'], ['literal', ['Polygon', 'MultiPolygon']]];
const isLine = ['in', ['geometry-type'], ['literal', ['LineString', 'MultiLineString']]];
const diff = (k) => ['==', ['get', 'difficulty_us'], k];

/** Inline SVG trail sign for the legend (20 px). */
export function signSvg(k) {
  const c = sign(k).color;
  const shapes = {
    green: `<circle cx="10" cy="10" r="7" fill="${c}"/>`,
    blue: `<rect x="3.5" y="3.5" width="13" height="13" fill="${c}"/>`,
    black: `<path d="M10 2 L18 10 L10 18 L2 10 Z" fill="${c}"/>`,
    double_black: `<path d="M5.5 4 L10 10 L5.5 16 L1 10 Z M14.5 4 L19 10 L14.5 16 L10 10 Z" fill="${c}"/>`,
    terrain_park: `<rect x="2" y="5" width="16" height="10" rx="2" fill="${c}"/>`,
  };
  const body = shapes[k] || `<line x1="2" y1="10" x2="18" y2="10" stroke="${c}" stroke-width="2.5" stroke-dasharray="2 2.5"/>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">${body}</svg>`;
}

function svgEl(markup) {
  const span = h('span', { class: 'sign', 'aria-hidden': 'true' });
  span.innerHTML = markup; // static markup from signSvg, never data
  return span;
}

function buildStyle(data, base) {
  return {
    version: 8,
    sources: {
      runs: { type: 'geojson', data: data.runs },
      lifts: { type: 'geojson', data: data.lifts },
      base: { type: 'geojson', data: base ? { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: { type: 'Point', coordinates: [base.lon, base.lat] }, properties: { name: 'Base area' } }] } : EMPTY },
    },
    layers: [
      { id: 'tm-bg', type: 'background', paint: { 'background-color': SURFACE } },
      { id: 'tm-run-area', type: 'fill', source: 'runs', filter: isArea, paint: { 'fill-color': colorExpr, 'fill-opacity': 0.16 } },
      { id: 'tm-run-area-line', type: 'line', source: 'runs', filter: isArea, paint: { 'line-color': colorExpr, 'line-width': 1, 'line-opacity': 0.7 } },
      {
        id: 'tm-run', type: 'line', source: 'runs', filter: ['all', isLine, ['!', ['in', ['coalesce', ['get', 'difficulty_us'], 'none'], ['literal', ['double_black', 'none']]]]],
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': colorExpr, 'line-width': ['interpolate', ['linear'], ['zoom'], 12, 1.6, 15, 3.2] },
      },
      {
        id: 'tm-run-double', type: 'line', source: 'runs', filter: ['all', isLine, diff('double_black')],
        layout: { 'line-join': 'round' },
        paint: { 'line-color': SIGNS.double_black.color, 'line-width': ['interpolate', ['linear'], ['zoom'], 12, 2.4, 15, 4.4], 'line-dasharray': [2.2, 1.1] },
      },
      {
        id: 'tm-run-unrated', type: 'line', source: 'runs', filter: ['all', isLine, ['==', ['get', 'difficulty_us'], null]],
        paint: { 'line-color': UNRATED.color, 'line-width': ['interpolate', ['linear'], ['zoom'], 12, 1.2, 15, 2.4], 'line-dasharray': [1, 1.5] },
      },
      { id: 'tm-run-hover', type: 'line', source: 'runs', filter: ['==', ['id'], -1], paint: { 'line-color': '#f2b705', 'line-width': 6, 'line-opacity': 0.55 } },
      { id: 'tm-lift-casing', type: 'line', source: 'lifts', paint: { 'line-color': '#ffffff', 'line-width': 4 } },
      { id: 'tm-lift', type: 'line', source: 'lifts', paint: { 'line-color': LIFT, 'line-width': 1.8 } },
      { id: 'tm-base', type: 'circle', source: 'base', paint: { 'circle-radius': 6, 'circle-color': '#0a0e14', 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2.5 } },
    ],
  };
}

function runList(runs) {
  const groups = new Map();
  for (const f of runs.features) {
    const k = f.properties.difficulty_us || 'none';
    if (!groups.has(k)) groups.set(k, { named: new Set(), unnamed: 0 });
    const g = groups.get(k);
    if (f.properties.name) g.named.add(f.properties.name); else g.unnamed += 1;
  }
  const keys = [...ORDER, 'none'].filter((k) => groups.has(k));
  return h('details', { class: 'trailmap-runs' },
    h('summary', null, `Runs by trail sign (${fmtInt(runs.features.length)} mapped)`),
    keys.map((k) => {
      const g = groups.get(k);
      const names = [...g.named].sort((a, b) => a.localeCompare(b, 'en', { numeric: true }));
      return h('div', { class: 'run-group' },
        h('p', { class: 'sub' }, svgEl(signSvg(k === 'none' ? null : k)), `${sign(k === 'none' ? null : k).label} (${fmtInt(names.length + g.unnamed)})`),
        h('p', { class: 'run-names' }, names.join(', '), g.unnamed ? `${names.length ? '. ' : ''}${fmtInt(g.unnamed)} without a name.` : ''));
    }));
}

/**
 * The dialog controller. open(resort, trigger) shows the trail map of a resort (resort: full record with
 * id, name, lat, lon, trailmap_available, official_trailmap); the same resort reopens on its last tab and view.
 */
export function createTrailMap({ getMapLibre, sourceTitle }) {
  const title = h('h2', { id: 'trailmap-title', class: 'trailmap-title', tabindex: '-1' });
  const closeBtn = h('button', { type: 'button', class: 'btn btn-sm btn-ghost trailmap-close' }, 'Close');
  const tab = (key, text) => h('button', {
    type: 'button', role: 'tab', id: `trailmap-tab-${key}`, class: 'trailmap-tab', 'aria-controls': `trailmap-panel-${key}`,
    'aria-selected': 'false', tabindex: '-1', dataset: { tab: key },
  }, text);
  const tabs = { official: tab('official', 'Official map'), interactive: tab('interactive', 'Interactive') };
  const tablist = h('div', { class: 'trailmap-tabs', role: 'tablist', 'aria-label': 'Trail map views' }, tabs.official, tabs.interactive);
  // Official tab
  const offStage = h('div', { class: 'tmo-stage' });
  const offStatus = h('p', { class: 'trailmap-status tmo-status', role: 'status' });
  const offHelp = h('p', { class: 'tmo-help', id: 'tmo-help' }, 'Drag or use the arrow keys to move. Scroll, pinch, the plus and minus buttons or keys zoom; 0 resets.');
  const offFoot = h('div', { class: 'tmo-foot' });
  const offPanel = h('div', { class: 'trailmap-panel trailmap-official', role: 'tabpanel', id: 'trailmap-panel-official', 'aria-labelledby': 'trailmap-tab-official', hidden: true },
    h('div', { class: 'tmo-frame' }, offStage, offStatus), offFoot);
  // Interactive tab
  const mapEl = h('div', { class: 'trailmap-map', role: 'region', 'aria-roledescription': 'map' });
  const label = h('div', { class: 'trailmap-label', role: 'status', 'aria-live': 'polite', hidden: true });
  const status = h('p', { class: 'trailmap-status' });
  const note = h('p', { class: 'trailmap-note', role: 'status', hidden: true });
  const side = h('div', { class: 'trailmap-side' });
  const intPanel = h('div', { class: 'trailmap-panel trailmap-body', role: 'tabpanel', id: 'trailmap-panel-interactive', 'aria-labelledby': 'trailmap-tab-interactive', hidden: true },
    h('div', { class: 'trailmap-stage' }, mapEl, label, status), h('div', { class: 'trailmap-side-wrap' }, note, side));
  const dialog = h('dialog', { class: 'trailmap-dialog', 'aria-labelledby': 'trailmap-title' },
    h('div', { class: 'trailmap-head' }, title, tablist, closeBtn),
    h('div', { class: 'trailmap-views' }, offPanel, intPanel));
  document.body.append(dialog);

  // current: { id, resort, map, built, file, viewer, failed, tab }
  let current = null;
  let trigger = null;

  closeBtn.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    label.hidden = true;
    if (trigger && document.contains(trigger)) trigger.focus();
  });
  // Keep Tab inside the dialog (the page behind is inert while it is open).
  dialog.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab') return;
    const items = [...dialog.querySelectorAll('button, [href], summary, [tabindex], canvas[tabindex], object')]
      .filter((el) => el.tabIndex >= 0 && !el.disabled && !el.hidden && el.offsetParent !== null && !el.closest('[hidden]'));
    if (!items.length) return;
    const first = items[0]; const last = items[items.length - 1];
    if (e.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  // A click on the backdrop closes it too.
  dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });
  // Tabs (WAI-ARIA APG, automatic activation): click, or Left, Right, Home and End on the tab list.
  for (const t of Object.values(tabs)) t.addEventListener('click', () => select(t.dataset.tab, { focus: true }));
  tablist.addEventListener('keydown', (e) => {
    const shown = Object.values(tabs).filter((t) => !t.hidden);
    const i = shown.indexOf(document.activeElement);
    if (i < 0) return;
    const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: shown.length - 1 }[e.key];
    if (to == null) return;
    e.preventDefault();
    select(shown[(to + shown.length) % shown.length].dataset.tab, { focus: true });
  });

  function select(key, { focus = false } = {}) {
    if (!current || tabs[key].hidden) return;
    current.tab = key;
    for (const [k, t] of Object.entries(tabs)) {
      const on = k === key;
      t.setAttribute('aria-selected', String(on));
      t.tabIndex = on ? 0 : -1;
    }
    offPanel.hidden = key !== 'official';
    intPanel.hidden = key !== 'interactive';
    if (focus) tabs[key].focus();
    if (key === 'interactive') {
      if (!current.built) {
        current.built = true;
        const resort = current.resort;
        build(resort).catch((err) => { console.warn('[map] trail map', err.message); status.textContent = 'The trail map could not load. Try again later.'; });
      } else if (current.map) {
        // The panel was hidden (0 x 0): measure it again now and once more after layout settles.
        current.map.resize();
        requestAnimationFrame(() => current && current.map && current.map.resize());
      }
    } else if (current.viewer && current.viewer.resize) {
      current.viewer.resize();
      requestAnimationFrame(() => current && current.viewer && current.viewer.resize());
    }
  }

  /** The official file failed (or cannot show here): say so and fall back to the Interactive tab when there is one. */
  function fallback(resort, file, reason) {
    if (!current || current.id !== resort.id || current.failed) return;
    current.failed = true;
    const link = h('a', { href: file.url, target: '_blank', rel: 'noopener noreferrer' }, `Open it on ${resort.name}`);
    clear(offStage);
    offHelp.hidden = true;
    offStatus.hidden = false;
    clear(offStatus);
    offStatus.append(`${reason} `, link, '.');
    if (resort.trailmap_available) {
      clear(note);
      note.append(`${reason} Showing the interactive map. `,
        h('a', { href: file.url, target: '_blank', rel: 'noopener noreferrer' }, `Open the official map on ${resort.name}`), '.');
      note.hidden = false;
      if (current.tab === 'official') select('interactive', { focus: dialog.contains(document.activeElement) && document.activeElement === tabs.official });
    }
  }

  function buildOfficial(resort, file) {
    clear(offStage); clear(offFoot);
    offStatus.hidden = false;
    offStatus.textContent = 'Loading the official trail map';
    offHelp.hidden = file.format === 'pdf';
    const name = resort.name;
    const alt = `Official trail map of ${name}`;
    const o = resort.official_trailmap || {};
    const pageLink = file.page && file.page !== file.url
      ? h('a', { href: file.page, target: '_blank', rel: 'noopener noreferrer' }, `Trail map page on ${name}`) : null;
    const fullLink = h('a', { class: 'tmo-full', href: file.url, target: '_blank', rel: 'noopener noreferrer' }, `View full size on ${name}`);
    offFoot.append(...[ // append() would print a null as text
      h('p', { class: 'tmo-credit' }, h('span', null, `Trail map © ${name}`), pageLink ? [' · ', pageLink] : null, ' · ', fullLink),
      file.format === 'pdf' ? null : offHelp,
      h('p', { class: 'fact-meta' }, [`Shown from ${name}'s own site and unchanged.`, o.season ? ` ${o.season} season.` : null, file.note ? ` ${file.note}` : null].filter(Boolean).join('')),
    ].filter(Boolean));
    const done = () => { offStatus.hidden = true; };
    if (file.format === 'pdf') {
      if (!canEmbedPdf()) { fallback(resort, file, 'This browser opens PDF trail maps in a new tab.'); return null; }
      const v = createPdfViewer({
        url: file.url, label: `${alt} (PDF)`, linkText: `View full size on ${name}`,
        onLoad: done, onError: () => fallback(resort, file, `The official trail map could not load from ${name}.`),
      });
      offStage.append(v.el);
      return v;
    }
    const v = createImageViewer({
      url: file.url, alt, label: `${alt}. Drag, scroll or use the arrow keys to explore it.`, helpId: 'tmo-help',
      onLoad: done, onError: () => fallback(resort, file, `The official trail map could not load from ${name}.`),
    });
    offStage.append(v.el);
    return v;
  }

  function legend(data) {
    const present = new Set(data.runs.features.map((f) => f.properties.difficulty_us || 'none'));
    const item = (svg, text, help) => h('li', null, svgEl(svg), h('span', null, text, help ? h('span', { class: 'legend-help' }, ` ${help}`) : null));
    return h('ul', { class: 'trailmap-legend', 'aria-label': 'Trail map legend' },
      ORDER.map((k) => item(signSvg(k), SIGNS[k].label, SIGNS[k].help)),
      present.has('none') ? item(signSvg(null), UNRATED.label) : null,
      item(`<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><line x1="1" y1="10" x2="19" y2="10" stroke="#fff" stroke-width="4"/><line x1="1" y1="10" x2="19" y2="10" stroke="${LIFT}" stroke-width="2"/></svg>`, 'Lift'),
      item('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="5.5" fill="#0a0e14" stroke="#fff" stroke-width="2"/></svg>', 'Base area'));
  }

  function describe(f, layer) {
    if (layer.startsWith('tm-lift')) return `${f.properties.name || 'Unnamed lift'}. Lift`;
    if (layer === 'tm-base') return 'Base area';
    const s = sign(f.properties.difficulty_us);
    return `${f.properties.name || 'Unnamed run'}. ${s.label}`;
  }

  function wire(map) {
    const layers = ['tm-lift', 'tm-base', 'tm-run', 'tm-run-double', 'tm-run-unrated', 'tm-run-area'];
    const pick = (pt) => {
      const pad = isPhone() ? 10 : 5;
      const hits = map.queryRenderedFeatures([[pt.x - pad, pt.y - pad], [pt.x + pad, pt.y + pad]], { layers });
      // Lifts and the base win over runs; among runs, lines win over areas.
      return hits.find((f) => f.layer.id === 'tm-base') || hits.find((f) => f.layer.id === 'tm-lift')
        || hits.find((f) => f.layer.id !== 'tm-run-area') || hits[0] || null;
    };
    const show = (f, pt) => {
      if (!f) { label.hidden = true; map.setFilter('tm-run-hover', ['==', ['id'], -1]); map.getCanvas().style.cursor = ''; return; }
      label.textContent = describe(f, f.layer.id);
      label.hidden = false;
      const x = Math.min(pt.x + 12, mapEl.clientWidth - label.offsetWidth - 6);
      label.style.transform = `translate(${Math.max(6, x)}px, ${Math.max(6, pt.y - 34)}px)`;
      map.setFilter('tm-run-hover', f.layer.id.startsWith('tm-run') && f.id != null ? ['==', ['id'], f.id] : ['==', ['id'], -1]);
      map.getCanvas().style.cursor = 'pointer';
    };
    map.on('mousemove', (e) => show(pick(e.point), e.point));
    map.on('mouseout', () => show(null));
    map.on('click', (e) => show(pick(e.point), e.point)); // taps on phones
  }

  async function build(resort) {
    clear(side);
    status.textContent = 'Loading the interactive map';
    status.hidden = false;
    const [data, maplibregl] = await Promise.all([loadTrailMap(resort.id), getMapLibre().catch(() => null)]);
    if (!current || current.id !== resort.id) return;
    if (!data) { status.textContent = 'The trail map could not load. Try again later.'; return; }
    side.append(
      h('p', { class: 'trailmap-kind' }, 'Interactive map from OpenSkiMap'),
      officialTrailmapLink(resort),
      h('p', { class: 'trailmap-help', id: 'trailmap-help' }, isPhone() ? 'Tap a run or lift for its name.' : 'Hover or tap a run or lift for its name.'),
      legend(data),
      runList(data.runs),
      h('p', { class: 'fact-meta' }, 'Runs and lifts from OpenSkiMap, drawn from OpenStreetMap. ',
        sourceLinkFor(data.source_id), ` Compiled ${formatIsoDate(data.generated_at)}. The resort's own trail map has the final word on names and ratings.`));
    if (!maplibregl) { status.textContent = 'The trail map needs WebGL. The run list beside it has every mapped run.'; return; }
    // Feature ids let hover highlight one run.
    data.runs.features.forEach((f, i) => { f.id = i + 1; });
    let map;
    try {
      const [w, s, e, n] = data.bounds;
      map = new maplibregl.Map({
        container: mapEl,
        style: buildStyle(data, { lat: resort.lat, lon: resort.lon }),
        bounds: [[w, s], [e, n]],
        fitBoundsOptions: { padding: isPhone() ? 16 : 32 },
        attributionControl: false,
        dragRotate: false,
        pitchWithRotate: false,
        touchPitch: false,
        renderWorldCopies: false,
        fadeDuration: 0,
        maxZoom: 17,
      });
    } catch (err) {
      status.textContent = 'The trail map needs WebGL. The run list beside it has every mapped run.';
      return;
    }
    map.touchZoomRotate.disableRotation();
    map.keyboard.disableRotation();
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-left');
    map.addControl(new maplibregl.AttributionControl({
      compact: true,
      customAttribution: 'Runs and lifts: <a href="https://openskimap.org/" target="_blank" rel="noopener">OpenSkiMap</a>, © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a> (ODbL)',
    }), 'bottom-right');
    map.on('error', (e) => console.warn('[map] trail map', e && e.error ? e.error.message : e));
    map.once('load', () => {
      status.hidden = true;
      map.getCanvas().setAttribute('aria-label', `Trail map of ${resort.name}`);
      // Start the compact attribution collapsed, as on the main map; the (i) button opens it.
      const attrib = mapEl.querySelector('.maplibregl-ctrl-attrib');
      if (attrib) attrib.classList.remove('maplibregl-compact-show');
    });
    wire(map);
    current.map = map;
    current.bounds = data.bounds;
  }

  function sourceLinkFor(id) {
    const s = sourceTitle(id);
    const url = s && safeUrl(s.url);
    return url ? h('a', { href: url, target: '_blank', rel: 'noopener noreferrer', class: 'src-link' }, s.title || id) : h('span', null, (s && s.title) || id);
  }

  return {
    dialog,
    /** Open the trail map of a resort. trigger gets focus back on close. */
    open(resort, from) {
      trigger = from || document.activeElement;
      const file = officialMapFile(resort);
      title.textContent = `${resort.name} trail map`;
      mapEl.setAttribute('aria-label', `Interactive trail map of ${resort.name}. Hover or tap a run or lift for its name. The run list holds the same runs.`);
      closeBtn.setAttribute('aria-label', `Close the ${resort.name} trail map`);
      if (!dialog.open) dialog.showModal();
      if (current && current.id === resort.id) {
        // Same resort: keep its last tab and view.
        select(current.tab);
      } else {
        if (current && current.map) current.map.remove();
        if (current && current.viewer) current.viewer.destroy();
        clear(side); clear(note); note.hidden = true; label.hidden = true;
        current = { id: resort.id, resort, map: null, built: false, file, viewer: null, failed: false, tab: null };
        tabs.official.hidden = !file;
        tabs.interactive.hidden = !resort.trailmap_available;
        tablist.hidden = !(file && resort.trailmap_available);
        if (file) current.viewer = buildOfficial(resort, file);
        select(file && !(current.failed && resort.trailmap_available) ? 'official' : 'interactive');
      }
      closeBtn.focus({ preventScroll: true });
    },
    close() { if (dialog.open) dialog.close(); },
    get map() { return current && current.map; },
    get viewer() { return current && current.viewer; },
    get tab() { return current && current.tab; },
  };
}
