// Career Map entry point.
//
// Load order (Phase 8 performance, see docs/map/ARCHITECTURE.md "Frontend"):
// 1. map-index.json, clusters.geojson and states.json, then the panel and the list view render.
// 2. MapLibre (an async script in the page head) arrives; the map module builds the WebGL map.
// 3. After the first map frame: resorts.json, sources.json and definitions.json, the recolor control,
//    then the state outlines. A resort or comparison view asks for step 3 data straight away.
import { config, isPhone } from './config.js';
import { loadAll, loadStateOutlines } from './data.js';
import { createRouter } from './router.js';
import { createPanel } from './panel.js';
import { createSheet } from './sheet.js';
import { createListView } from './listview.js';
import { applyMeta } from './meta.js';
import { h } from './ui.js';
import { FILTERS } from './labels.js';

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

// The map module starts downloading now, alongside the data; it runs once MapLibre is here.
const mapModule = import('./map.js');

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

function setListView(on) {
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

/** Resolves with window.maplibregl once the async script in the page head has run. */
function whenMapLibre() {
  if (window.maplibregl) return Promise.resolve(window.maplibregl);
  const tag = document.querySelector('script[data-maplibre]');
  return new Promise((resolve, reject) => {
    const done = () => (window.maplibregl ? resolve(window.maplibregl) : reject(new Error('MapLibre failed to load')));
    if (!tag) {
      // A route stub built from an older shell: load it here.
      const s = document.createElement('script');
      s.src = '/map/vendor/maplibre-gl/maplibre-gl.js';
      s.onload = done;
      s.onerror = done;
      document.head.append(s);
      return;
    }
    tag.addEventListener('load', done, { once: true });
    tag.addEventListener('error', done, { once: true });
    // The script may have finished between the check above and the listeners.
    if (window.maplibregl) done();
  });
}

const idle = (fn) => (window.requestIdleCallback ? window.requestIdleCallback(fn, { timeout: 1500 }) : setTimeout(fn, 200));

async function main() {
  let index;
  performance.mark('careermap:start');
  try {
    index = await loadAll();
    performance.mark('careermap:data');
  } catch (err) {
    console.warn('[map] data load failed:', err.message);
    const panel = createPanel(els.content, null, { onCompare: () => {} });
    panel.renderError(err.message);
    els.panel.setAttribute('aria-busy', 'false');
    window.__careerMap = { ready: true, panelReady: true, error: err.message };
    return;
  }

  let router;
  let detailControls = null;
  let detailFor = null;
  let detailData = null;
  let filterKey = 'all';

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

  const panel = createPanel(els.content, index, {
    onCompare: (view) => router.go(view),
    onDetail: (resortId, detail) => {
      detailFor = detail ? resortId : null;
      detailData = detail;
      applyDetail();
    },
    onSiteSelect: (id) => mapCtl && mapCtl.selectSite(id),
  });
  const list = createListView(els.list, index);

  router = createRouter(index, (view, { initial, fellBack }) => {
    if (detailFor && (view.level !== 'resort' || view.resortId !== detailFor)) {
      detailFor = null;
      detailData = null;
      applyDetail();
    }
    els.panel.classList.toggle('is-wide', view.level === 'compare');
    els.stage.classList.toggle('is-wide', view.level === 'compare');
    const headingEl = panel.render(view);
    els.scroll.scrollTop = 0;
    applyMeta(index, view);
    list.highlight(view);
    if (mapCtl) {
      if (!isPhone()) mapCtl.resize();
      mapCtl.show(view, { animate: !initial });
    }
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

  // Facility filter: applies to the map and the list view.
  const filterButtons = FILTERS.map((f) => h('button', {
    type: 'button', class: 'filter-btn', 'aria-pressed': String(f.key === 'all'), dataset: { filter: f.key },
    onclick: () => {
      for (const b of filterButtons) b.setAttribute('aria-pressed', String(b === filterButtons[FILTERS.indexOf(f)]));
      filterKey = f.key;
      if (mapCtl) mapCtl.setFilter(f.key);
      list.setFilter(f.key);
      announce(`Showing ${f.key === 'all' ? 'every facility type' : f.label.toLowerCase()}.`);
    },
  }, f.label));
  if (els.filter) {
    els.filter.append(...filterButtons);
    els.filter.hidden = false;
  }

  els.listToggle.addEventListener('click', () => setListView(els.list.hidden));
  els.listToggle.hidden = false;

  router.start();
  performance.mark('careermap:panel');
  els.panel.setAttribute('aria-busy', 'false');
  const state = { index, router, mapCtl: null, panelReady: true, ready: false };
  window.__careerMap = state;

  // Full records and sources: right away when the view needs them (the panel asked already), else after the map.
  const loadDetails = () => index.loadDetails().then(() => panel.fillSources()).catch(() => {
    for (const el of els.content.querySelectorAll('.src-pending')) el.textContent = 'Source record pending';
  });

  try {
    const [maplibregl, { createMap }] = await Promise.all([whenMapLibre(), mapModule]);
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
    els.map.append(h('p', { class: 'map-fallback' }, 'The interactive map needs WebGL. The list view shows every resort.'));
    setListView(true);
    state.ready = true;
    loadDetails();
    return;
  }

  state.mapCtl = mapCtl;
  if (filterKey !== 'all') mapCtl.setFilter(filterKey);
  if (detailFor) applyDetail();
  if (router.current) mapCtl.show(router.current, { animate: false });
  state.ready = true;

  mapCtl.map.once('load', () => {
    performance.mark('careermap:map');
    // Off the critical path: full data, the recolor control, then state outlines.
    loadDetails();
    if (els.recolor) {
      import('./recolor.js').then(({ createRecolor }) => {
        createRecolor(els.recolor, index, { onChange: (r) => mapCtl.setRecolor(r), announce });
      }).catch((err) => console.warn('[map] recolor', err.message));
    }
    idle(() => loadStateOutlines(index).then((fc) => {
      if (fc) mapCtl.setStates(fc);
      state.statesReady = true;
    }));
  });
}

main();
