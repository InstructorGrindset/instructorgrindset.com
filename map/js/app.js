// Career Map entry point.
//
// Load order (Phase 8 performance, see docs/map/ARCHITECTURE.md "Frontend"):
// 1. map-index.json, clusters.geojson and states.json, then the panel and the list view render.
// 2. A 2D picture of the view (preview.js) with "Click (Tap) to explore the map". The first click or tap runs
//    MapLibre (preloaded by the page head) and builds the WebGL map (owner decision 9).
// 3. After the load event and the first paint, when idle: map.js and MapLibre download, sources.json loads when the
//    panel shows source links, and resorts.json is only prefetched (a resort or comparison view loads it at once).
//    After the WebGL map loads: the full data, the recolor control and the state outlines.
import { config, isPhone } from './config.js';
import { isFiltered, loadAll, loadStateOutlines, matchesFilter, NO_FILTER, whenPainted } from './data.js';
import { createRouter } from './router.js';
import { createPanel, officialMapFile } from './panel.js';
import { createSheet } from './sheet.js';
import { createListView } from './listview.js';
import { applyMeta } from './meta.js';
import { h, joinOr, plural } from './ui.js';
import { FILTERS, PASS_LABEL } from './labels.js';
import { drawPreview, addPreviewUnderlay, wantsUnderlay } from './preview.js';

const $ = (id) => document.getElementById(id);

const els = {
  stage: $('map-stage'),
  map: $('map'),
  panel: $('panel'),
  content: $('panel-content'),
  scroll: $('panel-scroll'),
  handle: $('sheet-handle'),
  status: $('panel-status'),
  list: $('list-view'),
  listToggle: $('list-toggle'),
  filter: $('facility-filter'),
  recolor: $('recolor'),
  detailLayers: $('detail-layers'),
  devBadge: $('dev-badge'),
};

// The WebGL map starts on the first click (owner decision 9). MapLibre and the map module download once the page
// has loaded and gone idle, so they never compete with the first paint; a click before then fetches them at once.
let mapModulePromise = null;
const mapModule = () => (mapModulePromise ||= import('./map.js'));
/** Resolves once the MapLibre preload has finished (or failed, or after 10 s). */
function prefetchMap() {
  mapModule().catch(() => { mapModulePromise = null; });
  const l = document.createElement('link');
  l.rel = 'preload'; l.as = 'script'; l.href = MAPLIBRE_URL;
  const done = new Promise((resolve) => { l.onload = resolve; l.onerror = resolve; setTimeout(resolve, 10000); });
  document.head.append(l);
  return done;
}

if (config.isDevData && els.devBadge) {
  els.devBadge.hidden = false;
  els.devBadge.textContent = 'Test fixture data';
}

let mapCtl = null;
const sheet = createSheet(els.panel, els.handle, els.stage, { onChange: () => mapCtl && mapCtl.refit() });

function announce(text) {
  els.status.textContent = '';
  // Next frame so repeated messages are still announced.
  requestAnimationFrame(() => { els.status.textContent = text; });
}

let listView = null;
function setListView(on) {
  if (on && listView) listView.show();
  document.body.classList.toggle('list-mode', on);
  els.listToggle.setAttribute('aria-pressed', String(on));
  els.listToggle.textContent = on ? 'Map view' : 'List view';
  els.list.hidden = !on;
  els.map.hidden = on;
  if (!on && mapCtl) requestAnimationFrame(() => { mapCtl.resize(); mapCtl.refit(); });
}

function panelWidth(view) {
  return view.level === 'compare' ? config.comparePanelWidth : config.desktopPanelWidth;
}

function padding(view) {
  if (isPhone()) return { top: 24, left: 24, right: 24, bottom: sheet.visible() + 16 };
  const w = Math.min(panelWidth(view), els.stage.clientWidth * 0.6);
  return { top: 48, left: 56, right: w + 40, bottom: 40 };
}

function summary(index, view) {
  switch (view.level) {
    case 'state': return `Showing ${index.states.get(view.stateCode).name}.`;
    case 'cluster': return `Showing the ${index.clusters.get(view.clusterId).name} cluster.`;
    case 'resort': return `Showing ${index.resorts.get(view.resortId).name}.`;
    case 'compare': return `Comparing ${view.compare.length} resorts.`;
    default: return 'Showing the national view.';
  }
}

/**
 * Runs MapLibre (preloaded by the page head) once the panel has painted, so its parse and the WebGL setup
 * never delay the first paint. Resolves with window.maplibregl.
 */
const MAPLIBRE_URL = '/map/vendor/maplibre-gl/maplibre-gl.js';
let mapLibrePromise = null;
function whenMapLibre() {
  if (window.maplibregl) return Promise.resolve(window.maplibregl);
  if (mapLibrePromise) return mapLibrePromise;
  mapLibrePromise = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = MAPLIBRE_URL;
    const done = () => (window.maplibregl ? resolve(window.maplibregl) : reject(new Error('MapLibre failed to load')));
    s.onload = done;
    s.onerror = done;
    afterFirstPaint(() => document.head.append(s));
  });
  mapLibrePromise.catch(() => { mapLibrePromise = null; });
  return mapLibrePromise;
}

/** Run fn once the browser has painted content (first-contentful-paint), or after 1.5 s at the latest. */
function afterFirstPaint(fn) {
  let fired = false;
  const go = () => { if (!fired) { fired = true; setTimeout(fn, 0); } };
  try {
    if (performance.getEntriesByName('first-contentful-paint').length) { go(); return; }
    const po = new PerformanceObserver((list) => {
      if (list.getEntries().some((e) => e.name === 'first-contentful-paint')) { po.disconnect(); go(); }
    });
    po.observe({ type: 'paint', buffered: true });
  } catch { /* no paint timing: fall back to the timer */ }
  setTimeout(go, 1500);
}

/** A new task, so long startup work splits into short tasks and the browser can paint in between. */
const nextTask = () => new Promise((resolve) => { setTimeout(resolve, 0); });

const idle = (fn) => (window.requestIdleCallback ? window.requestIdleCallback(fn, { timeout: 1500 }) : setTimeout(fn, 200));

async function main() {
  let index;
  performance.mark('careermap:start');
  try {
    index = await loadAll();
    performance.mark('careermap:data');
    await nextTask();
  } catch (err) {
    console.warn('[map] data load failed:', err.message);
    const panel = createPanel(els.content, null, { onCompare: () => {} });
    panel.renderError(err.message);
    els.panel.setAttribute('aria-busy', 'false');
    window.__careerMap = { ready: true, panelReady: true, error: err.message };
    return;
  }

  let router;
  let onViewChange = () => {};
  let detailControls = null;
  let detailFor = null;
  let detailData = null;
  let filter = NO_FILTER; // the chips' state (data.js matchesFilter)
  let idleReached = false; // the first idle time after the load event and the first paint has come
  let sourcesWanted = false; // the panel showed a source placeholder before then

  async function showDetailControls(groups) {
    if (!detailControls) {
      const { createDetailControls } = await import('./detailcontrols.js');
      detailControls = detailControls || createDetailControls(els.detailLayers, { onToggle: (g, on) => mapCtl && mapCtl.setLayerGroup(g, on) });
    }
    if (detailFor && groups.length) detailControls.show(groups); else detailControls.hide();
  }

  async function applyDetail() {
    if (!mapCtl) return;
    const groups = await mapCtl.setDetail(detailFor ? detailData : null);
    if (detailFor || detailControls) showDetailControls(groups);
  }

  // "Show on the detail map" (Employee housing): start the WebGL map when needed, switch the housing layer on and fit
  // the map to the housing pins. Phones drop the sheet to its peek so the map shows.
  async function showHousing(resortId, n) {
    if (!els.list.hidden) setListView(false);
    const start = window.__careerMap && window.__careerMap.startMap;
    const ctl = mapCtl || (start ? await start() : null);
    if (!ctl) return;   // no WebGL: the list view and its message are up
    ctl.whenReady(() => {
      if (detailFor !== resortId || !detailData) return;
      if (detailControls) detailControls.ensure('housing'); else ctl.setLayerGroup('housing', true);
      if (isPhone()) {
        sheet.set('peek');
        ctl.map.getCanvas().focus({ preventScroll: true });   // the button scrolls away with the sheet
      }
      const shown = ctl.fitHousing() || n;
      const pins = detailData.housing.filter((s) => s.precision === 'building').length;
      const key = pins === 0 ? 'Circles mark neighborhood areas.' : pins === detailData.housing.length ? 'Pins mark listed addresses.'
        : 'Pins mark listed addresses and circles mark neighborhood areas.';
      announce(`Showing ${plural(shown, 'housing site', 'housing sites')} on the detail map. ${key}`);
    });
  }

  // Trail map dialog: module and data load on first open.
  let trail = null;
  let trailFor = null; // resort record whose map control is up (or waiting for the map)
  async function openTrail(r, from) {
    try {
      const [{ createTrailMap }] = await Promise.all([import('./trailmap.js'), index.loadDetails().catch(() => null)]);
      if (!trail) trail = createTrailMap({ getMapLibre: whenMapLibre, sourceTitle: (id) => index.sources.get(id) });
      trail.open(r, from);
      window.__careerMap.trail = trail;
    } catch (err) {
      console.warn('[map] trail map', err.message);
      announce('The trail map could not open. Try reloading the page.');
    }
  }
  function setTrailControl(r) {
    trailFor = r && (r.trailmap_available || officialMapFile(r)) ? r : null;
    if (mapCtl) mapCtl.setTrailControl(trailFor ? (btn) => openTrail(trailFor, btn) : null);
  }

  const panel = createPanel(els.content, index, {
    onTrailMap: (r, btn) => openTrail(r, btn),
    onResort: (r) => setTrailControl(r),
    onCompare: (view) => router.go(view),
    onDetail: (resortId, detail) => {
      detailFor = detail ? resortId : null;
      detailData = detail;
      applyDetail();
    },
    onSiteSelect: (id) => mapCtl && mapCtl.selectSite(id),
    onShowHousing: (resortId, n) => showHousing(resortId, n),
    onSourcesNeeded: () => { if (idleReached) loadSources(); else sourcesWanted = true; },
  });
  const list = createListView(els.list, index);
  listView = list;

  // Source records for the panel's source links, and the full data (resort records too).
  const sourcesFailed = () => {
    for (const el of els.content.querySelectorAll('.src-pending')) el.textContent = 'Source record pending';
  };
  const loadSources = () => index.loadSources().then(() => panel.fillSources()).catch(sourcesFailed);
  const loadDetails = () => index.loadDetails().then(() => panel.fillSources()).catch(sourcesFailed);

  router = createRouter(index, (view, { initial, fellBack }) => {
    if (trailFor && (view.level !== 'resort' || view.resortId !== trailFor.id)) setTrailControl(null);
    if (trail) trail.close();
    if (detailFor && (view.level !== 'resort' || view.resortId !== detailFor)) {
      detailFor = null;
      detailData = null;
      applyDetail();
    }
    els.panel.classList.toggle('is-wide', view.level === 'compare');
    els.stage.classList.toggle('is-wide', view.level === 'compare');
    const headingEl = panel.render(view);
    if (!initial) els.scroll.scrollTop = 0; // a fresh page is at the top already; setting it forces a layout
    applyMeta(index, view);
    list.highlight(view);
    if (mapCtl) {
      if (!isPhone()) mapCtl.resize();
      mapCtl.show(view, { animate: !initial });
    } else onViewChange();
    let msg = summary(index, view);
    if (fellBack) {
      msg = `That link points to a page we could not find, so here is the closest match. ${msg}`;
      els.content.prepend(h('p', { class: 'notice', role: 'note' }, 'That link points to a page we could not find, so here is the closest match.'));
    }
    announce(msg);
    if (!initial) {
      sheet.reveal();
      if (headingEl) headingEl.focus({ preventScroll: true });
    }
  });

  // In-app links: History API navigation, real hrefs for new tabs and crawlers.
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('a[data-nav]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    router.goHref(a.getAttribute('href'));
  });

  // Filter chips (labels.js FILTERS): All, the facility types, Hiring now and the passes, combined with AND. They
  // apply to the map and the list view. hiring.js counts Hiring now after the first paint (or on its first click);
  // a disabled chip only announces its title.
  const onlyIds = (f) => (f.hiring || f.passes.length
    ? index.resortList.filter((r) => matchesFilter(r, { ...f, facility: 'all' })).map((r) => r.id) : null);
  const applyFilter = (next = filter) => {
    filter = next;
    if (mapCtl) mapCtl.setFilter(filter, onlyIds(filter));
    list.setFilter(filter);
  };
  let hiringMod = null;
  const hiring = () => (hiringMod ||= import('./hiring.js').then((m) => { m.markHiring(index, null, hiringBtn); return m; },
    (err) => { hiringMod = null; throw err; }));
  const syncChips = () => {
    for (const b of filterButtons) {
      const k = b.dataset.filter;
      const on = k === 'all' ? !isFiltered(filter) : k === 'hiring' ? filter.hiring
        : b.dataset.group === 'pass' ? filter.passes.includes(k) : filter.facility === k;
      b.setAttribute('aria-pressed', String(on));
    }
  };
  /** "Showing 3 resorts hiring now.", "Showing 37 resorts on the Epic Pass.", "Showing every resort." */
  const filterSummary = (f) => {
    if (!isFiltered(f)) return 'Showing every resort.';
    const n = index.resortList.filter((r) => matchesFilter(r, f)).length;
    const noun = f.facility === 'ski_area' ? ['ski area', 'ski areas']
      : f.facility === 'facility' ? ['indoor or training facility', 'indoor and training facilities'] : ['resort', 'resorts'];
    const passes = f.passes.length ? ` on the ${joinOr(f.passes.map((p) => PASS_LABEL[p]))}` : '';
    return `Showing ${plural(n, ...noun)}${f.hiring ? ' hiring now' : ''}${passes}.`;
  };
  const toggle = (f) => {
    if (f.key === 'all') return NO_FILTER;
    if (f.group === 'facility') return { ...filter, facility: filter.facility === f.key ? 'all' : f.key };
    if (f.key === 'hiring') return { ...filter, hiring: !filter.hiring };
    const passes = filter.passes.includes(f.key) ? filter.passes.filter((p) => p !== f.key) : [...filter.passes, f.key];
    return { ...filter, passes: FILTERS.filter((x) => x.group === 'pass' && passes.includes(x.key)).map((x) => x.key) };
  };
  const select = (f) => {
    applyFilter(toggle(f));
    syncChips();
    announce(filterSummary(filter));
    onViewChange(); // redraws the 2D picture until the WebGL map runs
  };
  // A pass chip with no resort on its pass in this build stays visible and disabled.
  const passCount = (key) => index.resortList.filter((r) => r.passes.includes(key)).length;
  const filterButtons = FILTERS.map((f) => {
    const empty = f.group === 'pass' && !passCount(f.key);
    return h('button', {
      type: 'button', class: 'filter-btn',
      'aria-pressed': String(f.key === 'all'), dataset: { filter: f.key, group: f.group },
      title: empty ? `No resort lists the ${PASS_LABEL[f.key]} yet.` : f.title,
      'aria-disabled': empty ? 'true' : null,
      onclick: (e) => {
        const btn = e.currentTarget;
        if (btn.getAttribute('aria-disabled') && f.key !== 'hiring') { announce(btn.title); return; }
        if (f.key !== 'hiring') select(f);
        else hiring().then(() => (btn.getAttribute('aria-disabled') && !filter.hiring ? announce(btn.title) : select(f)), (err) => console.warn('[map] hiring', err.message));
      },
    }, f.label);
  });
  const hiringBtn = filterButtons.find((b) => b.dataset.filter === 'hiring');
  if (els.filter) {
    els.filter.append(...filterButtons);
    els.filter.hidden = false;
  }

  els.listToggle.addEventListener('click', () => setListView(els.list.hidden));
  els.listToggle.hidden = false;

  // Resort search: search.js loads on first focus and navigates through the router, as a click does. On phones
  // its list stays above the bottom sheet (a fully open sheet drops to half first).
  const searchEl = $('map-search');
  if (searchEl) {
    let searchMod = null;
    const loadSearch = () => {
      searchMod ||= import('./search.js').then(({ createSearch }) => createSearch(searchEl, index, {
        current: () => router.current,
        onSelect: (view) => {
          router.go(view);
          const cur = !els.list.hidden && els.list.querySelector('[aria-current="page"]');
          if (cur) cur.scrollIntoView({ block: 'center' });
        },
        listBottom: () => {
          if (!isPhone()) return window.innerHeight - 16;
          if (sheet.state === 'full') sheet.set('half');
          return els.stage.getBoundingClientRect().bottom - sheet.visible();
        },
      })).catch((err) => { searchMod = null; console.warn('[map] search', err.message); });
      return searchMod;
    };
    const input = $('map-search-input');
    if (!isPhone()) input.placeholder = 'Search resorts and clusters';
    input.addEventListener('focus', loadSearch);
    input.addEventListener('pointerdown', loadSearch, { passive: true });
    searchEl.hidden = false;
  }

  router.start();
  performance.mark('careermap:panel');
  els.panel.setAttribute('aria-busy', 'false');
  await nextTask();

  // The WebGL map starts on the first click or tap over the map (owner decision 9, 2026-10-13). Until then a 2D
  // picture of the current view (preview.js) fills the map area with a button that starts it.
  let preview = null;
  let underlay = null; // the satellite style underlay (underlay.js), once loaded after the first paint
  let underlayStarted = false;
  let mapPrefetched = false;
  const drawFrame = () => {
    if (mapCtl || els.map.hidden || !router.current) return;
    const keep = isFiltered(filter) ? (r) => matchesFilter(r, filter) : null;
    const next = (() => { try { return drawPreview(els.map, index, router.current, padding(router.current), keep, underlay); } catch (err) { console.warn('[map] preview', err.message); return null; } })();
    if (preview) preview.remove();
    preview = next;
    if (preview) els.map.insertBefore(preview, startBtn);
    startUnderlay();
  };
  const dropPreview = () => { if (preview) { preview.remove(); preview = null; } };
  const coarse = window.matchMedia && window.matchMedia('(pointer: coarse)').matches;
  const startLabel = coarse ? 'Tap to explore the map' : 'Click to explore the map';
  const startBtn = h('button', { type: 'button', class: 'btn map-start', id: 'map-start' }, startLabel);
  els.map.append(startBtn);
  els.map.classList.add('map-waiting');
  drawFrame();
  const state = { index, router, mapCtl: null, panelReady: true, ready: true, mapStarted: false };
  window.__careerMap = state;
  onViewChange = () => { if (!mapCtl) drawFrame(); };
  let resizeTimer = null;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (!mapCtl) drawFrame(); }, 150); });

  // Background work waits for the window load event and the first contentful paint, then for idle time, so it
  // never competes with the first paint (a slow first frame used to let 600 KB of it start before the paint).
  const loaded = new Promise((resolve) => {
    if (document.readyState === 'complete') resolve(); else window.addEventListener('load', resolve, { once: true });
  });
  const afterLoad = (fn) => Promise.all([loaded, whenPainted()]).then(() => idle(fn));
  // When idle: source records if the panel shows source links, and only a cache warming download of the rest
  // (resorts.json is 2.6 MB; parsing it is a long task). A view that needs full records loads them (panel.js).
  afterLoad(() => { idleReached = true; if (sourcesWanted) loadSources(); index.prefetchDetails(); });
  afterLoad(() => prefetchMap().then(() => { mapPrefetched = true; startUnderlay(); }));
  // The underlay (470 KB) joins the picture after the first paint at low priority, once the MapLibre download is
  // done so a quick click keeps the bandwidth, and only for a picture that draws it (national to cluster zooms; a
  // resort picture skips it). It is decoded off the main thread first.
  function startUnderlay() {
    if (underlayStarted || !mapPrefetched || mapCtl || !wantsUnderlay(preview)) return;
    underlayStarted = true;
    import('./underlay.js').then((m) => m.loadUnderlay().then(m.decodeUnderlay)).then((u) => {
      underlay = u;
      if (u && preview && !mapCtl) addPreviewUnderlay(preview, u, { fade: true });
    }).catch((err) => console.warn('[map] underlay', err.message));
  }
  // Hiring now: the windows count, then the open roles once jobs/postings.json is in.
  afterLoad(() => hiring().then(async (m) => {
    const file = await m.feedPostings(index);
    const before = index.hiringIds.join();
    if (file) m.markHiring(index, file, hiringBtn);
    if (filter.hiring && index.hiringIds.join() !== before) { applyFilter(); onViewChange(); }
  }).catch((err) => console.warn('[map] hiring', err.message)));

  let starting = null;
  function startMap() {
    if (!starting) starting = createWebGLMap();
    return starting;
  }
  els.map.addEventListener('click', (e) => { if (!state.mapStarted && ((preview && preview.contains(e.target)) || e.target === els.map || e.target === startBtn)) startMap(); });
  state.startMap = startMap;

  async function createWebGLMap() {
    state.mapStarted = true;
    performance.mark('careermap:start-map');
    const hadFocus = document.activeElement === startBtn;
    startBtn.textContent = 'Loading the map';
    startBtn.setAttribute('aria-disabled', 'true');
    els.map.setAttribute('aria-busy', 'true');
    try {
      const [maplibregl, { createMap }] = await Promise.all([whenMapLibre().then((m) => {
        performance.mark('careermap:maplibre');
        // Start the worker threads before the map constructor occupies the main thread.
        if (typeof m.prewarm === 'function') m.prewarm();
        return m;
      }), mapModule()]);
      mapCtl = createMap(els.map, index, maplibregl, {
        getPadding: padding,
        onPick(target, current) {
          const cur = current || { level: 'national' };
          if (target.kind === 'housing') { panel.selectSite(target.id); return; }
          if (target.kind === 'state') {
            if (cur.stateCode === target.id && cur.level === 'state') return;
            router.go({ level: 'state', stateCode: target.id });
          } else if (target.kind === 'cluster') {
            const c = index.clusters.get(target.id);
            const st = cur.stateCode && c.states.includes(cur.stateCode) ? cur.stateCode : c.states[0];
            router.go({ level: 'cluster', stateCode: st, clusterId: c.id });
          } else {
            const r = index.resorts.get(target.id);
            const c = index.clusters.get(r.cluster_id);
            if (!c) return;
            const st = cur.stateCode && c.states.includes(cur.stateCode) ? cur.stateCode
              : (c.states.includes(r.state) ? r.state : c.states[0]);
            router.go({ level: 'resort', stateCode: st, clusterId: c.id, resortId: r.id });
          }
        },
      });
    } catch (err) {
      console.warn('[map] map unavailable, showing list view:', err.message);
      startBtn.remove();
      els.map.classList.remove('map-waiting');
      els.map.removeAttribute('aria-busy');
      els.map.append(h('p', { class: 'map-fallback' }, 'The interactive map needs WebGL. The list view shows every resort.'));
      dropPreview();
      setListView(true);
      return null;
    }

    state.mapCtl = mapCtl;
    if (isFiltered(filter)) mapCtl.setFilter(filter, onlyIds(filter));
    if (detailFor) applyDetail();
    if (trailFor) setTrailControl(trailFor);
    if (router.current) mapCtl.show(router.current, { animate: false });

    mapCtl.whenReady(() => {
      performance.mark('careermap:map');
      dropPreview();
      startBtn.remove();
      els.map.classList.remove('map-waiting');
      els.map.removeAttribute('aria-busy');
      announce('The map is ready. Drag to pan and use the zoom buttons or the plus and minus keys.');
      if (hadFocus || document.activeElement === document.body) mapCtl.map.getCanvas().focus({ preventScroll: true });
      // Off the critical path: full data, the recolor control, then state outlines.
      loadDetails();
      if (els.recolor) {
        import('./recolor.js').then(({ createRecolor }) => createRecolor(els.recolor, index, { onChange: (r) => mapCtl.setRecolor(r), announce }))
          .catch((err) => console.warn('[map] recolor', err.message));
      }
      idle(() => loadStateOutlines(index).then((fc) => {
        if (fc) mapCtl.setStates(fc);
        state.statesReady = true;
      }));
    });
    return mapCtl;
  }
}

main();
