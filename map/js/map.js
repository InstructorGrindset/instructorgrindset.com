// MapLibre map: state outlines, cluster polygons, resort circles.
import { config, prefersReducedMotion } from './config.js';
import { h } from './ui.js';

const C = config.basemap.colors;

/** Circle radius by size_rank (1..5), scaled with zoom. */
function radius(extra = 0) {
  const rank = ['coalesce', ['get', 'size_rank'], 1];
  const at = (base, step) => ['+', base + extra, ['*', rank, step]];
  return ['interpolate', ['linear'], ['zoom'], 3, at(1.6, 0.9), 7, at(3, 1.5), 11, at(5, 2.2)];
}

const fs = (key) => ['boolean', ['feature-state', key], false];

const IS_SKI_AREA = ['==', ['coalesce', ['get', 'facility_type'], 'ski_area'], 'ski_area'];

// Indoor facilities draw as rounded squares, training centers as diamonds.
// Inline SVG rasterized into map images, so no glyph or sprite server is needed.
const SHAPES = {
  indoor: '<rect x="7" y="7" width="26" height="26" rx="6"/>',
  training_center: '<path d="M20 3 L37 20 L20 37 L3 20 Z"/>',
};
const TIER_STYLE = {
  A: { fill: C.tierA, stroke: '#fff4dc' },
  B: { fill: C.tierB, stroke: C.background },
};

// Sequential ramp for recolor modes (one hue, dark to light reads low to high on the dark map),
// validated as an ordinal ramp against the state fill. Grey marks resorts without data.
export const RAMP = ['#1c5cab', '#2a78d6', '#5598e7', '#86b6ef', '#cde2fb'];
export const NO_DATA = '#4a5568';
const RECOLOR_STROKE = { A: '#e0a03a', B: C.background };

function svgImage(id, shape, fill, stroke, width) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40"><g fill="${fill}" stroke="${stroke}" stroke-width="${width}" stroke-linejoin="round">${shape}</g></svg>`;
  return new Promise((resolve) => {
    const img = new Image(40, 40);
    img.onload = () => resolve({ id, img });
    img.onerror = () => resolve(null);
    img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  });
}

/** Default images (tier colors) plus one per recolor class (c0..c4, cnull) with a tier stroke. */
function facilityImages() {
  const out = [];
  for (const [type, shape] of Object.entries(SHAPES)) {
    for (const [tier, st] of Object.entries(TIER_STYLE)) {
      out.push(svgImage(`fac-${type}-${tier}`, shape, st.fill, st.stroke, 3));
      RAMP.forEach((color, k) => out.push(svgImage(`fac-${type}-${tier}-c${k}`, shape, color, RECOLOR_STROKE[tier], tier === 'A' ? 4 : 3)));
      out.push(svgImage(`fac-${type}-${tier}-cnull`, shape, NO_DATA, RECOLOR_STROKE[tier], tier === 'A' ? 4 : 3));
    }
  }
  return Promise.all(out).then((list) => list.filter(Boolean));
}

/** Equal interval class breaks (up to 4 inner stops) over the non null values of a property. */
export function classBreaks(values, roundTo = 1) {
  const v = values.filter((x) => typeof x === 'number' && Number.isFinite(x));
  if (!v.length) return null;
  const min = Math.min(...v); const max = Math.max(...v);
  const stops = [];
  if (max > min) {
    for (let i = 1; i < RAMP.length; i++) {
      const t = Math.round((min + (i * (max - min)) / RAMP.length) / roundTo) * roundTo;
      if (t > min && t <= max && (!stops.length || t > stops[stops.length - 1])) stops.push(t);
    }
  }
  return { min, max, stops, count: v.length };
}

const DEFAULT_PAINT = {
  'circle-color': ['case', ['==', ['get', 'tier'], 'A'], C.tierA, C.tierB],
  'circle-stroke-color': ['case', fs('hover'), C.ring, ['==', ['get', 'tier'], 'A'], '#fff4dc', C.background],
  'circle-stroke-width': ['case', fs('hover'), 2.5, ['==', ['get', 'tier'], 'A'], 1.5, 1],
};
const DEFAULT_ICON = ['concat', 'fac-', ['get', 'facility_type'], '-', ['get', 'tier']];

const FACILITY_LAYER = {
  id: 'resorts-facility', type: 'symbol', source: 'resorts',
  filter: ['!', IS_SKI_AREA],
  layout: {
    'icon-image': DEFAULT_ICON,
    'icon-size': ['interpolate', ['linear'], ['zoom'], 3, 0.6, 7, 0.8, 11, 1.05],
    'icon-allow-overlap': true,
    'icon-ignore-placement': true,
    'symbol-sort-key': ['get', 'tier_a'],
  },
  paint: { 'icon-opacity': ['case', fs('dim'), 0.3, 1] },
};

function buildStyle(index) {
  return {
    version: 8,
    sources: {
      states: { type: 'geojson', data: index.geo.usStates, promoteId: 'code' },
      clusters: { type: 'geojson', data: index.geo.clusters, promoteId: 'id' },
      resorts: { type: 'geojson', data: index.geo.resorts, promoteId: 'id' },
    },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': C.background } },
      {
        id: 'states-fill', type: 'fill', source: 'states',
        paint: {
          'fill-color': ['case', fs('selected'), C.stateFillSelected,
            ['all', fs('hover'), ['boolean', ['get', 'has_resorts'], false]], '#1c2533',
            ['boolean', ['get', 'has_resorts'], false], C.stateFillActive, C.stateFill],
        },
      },
      {
        id: 'states-line', type: 'line', source: 'states',
        paint: { 'line-color': C.stateLine, 'line-width': ['interpolate', ['linear'], ['zoom'], 3, 0.6, 8, 1.4] },
      },
      {
        id: 'clusters-fill', type: 'fill', source: 'clusters',
        paint: {
          'fill-color': C.cluster,
          // Fallback clusters (other-xx) carry no fill so named clusters read clearly.
          'fill-opacity': ['case', ['boolean', ['get', 'fallback'], false],
            ['case', fs('selected'), 0.05, fs('hover'), 0.05, 0],
            ['case', fs('selected'), 0.1, fs('hover'), 0.16, fs('focus'), 0.12, fs('dim'), 0.03, 0.08]],
        },
      },
      {
        id: 'clusters-line-fallback', type: 'line', source: 'clusters', minzoom: 5,
        filter: ['==', ['get', 'fallback'], true],
        paint: {
          'line-color': C.cluster,
          'line-opacity': ['case', fs('selected'), 0.7, fs('hover'), 0.5, fs('focus'), 0.3, fs('dim'), 0.08, 0.15],
          'line-width': ['case', fs('selected'), 1.6, 1],
          'line-dasharray': [2, 3],
        },
      },
      {
        id: 'clusters-line', type: 'line', source: 'clusters',
        filter: ['!=', ['get', 'fallback'], true],
        paint: {
          'line-color': C.cluster,
          'line-opacity': ['case', fs('selected'), 1, fs('hover'), 0.9, fs('focus'), 0.8, fs('dim'), 0.2, 0.5],
          'line-width': ['case', fs('selected'), 2.2, fs('focus'), 1.4, 1],
        },
      },
      {
        id: 'states-line-selected', type: 'line', source: 'states', filter: ['==', ['get', 'code'], ''],
        paint: { 'line-color': C.stateLineSelected, 'line-width': 1.6, 'line-opacity': 0.8 },
      },
      {
        id: 'resorts-halo', type: 'circle', source: 'resorts', filter: ['==', ['get', 'id'], ''],
        paint: {
          'circle-radius': radius(5), 'circle-color': 'rgba(0,0,0,0)',
          'circle-stroke-color': C.ring, 'circle-stroke-width': 2,
        },
      },
      {
        id: 'resorts', type: 'circle', source: 'resorts', filter: IS_SKI_AREA,
        layout: { 'circle-sort-key': ['get', 'tier_a'] },
        paint: {
          'circle-radius': radius(),
          ...DEFAULT_PAINT,
          'circle-opacity': ['case', fs('dim'), 0.3, ['==', ['get', 'tier'], 'A'], 1, 0.85],
          'circle-stroke-opacity': ['case', fs('dim'), 0.3, 1],
        },
      },
    ],
  };
}

export function createMap(container, index, { onPick, getPadding }) {
  if (!window.maplibregl) throw new Error('MapLibre failed to load');
  const maplibregl = window.maplibregl;
  const map = new maplibregl.Map({
    container,
    style: buildStyle(index),
    bounds: config.nationalBounds,
    fitBoundsOptions: { padding: 20 },
    attributionControl: false,
    dragRotate: false,
    pitchWithRotate: false,
    touchPitch: false,
    renderWorldCopies: false,
    fadeDuration: 0,
    minZoom: 1.5,
    maxZoom: 14,
  });
  map.touchZoomRotate.disableRotation();
  map.keyboard.disableRotation();
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-left');
  // Top right keeps attribution clear of the side panel (offset in CSS) and the phone sheet.
  map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: config.basemap.attribution }), 'top-right');
  map.on('error', (e) => console.warn('[map]', e && e.error ? e.error.message : e));

  const tip = h('div', { class: 'map-tip', role: 'presentation', hidden: true });
  container.append(tip);

  let loaded = false;
  let pending = null;
  let current = null;
  const stateKeys = []; // [{source, id}] with feature state set by applyView
  let hovered = null;

  function setFS(source, id, state) {
    map.setFeatureState({ source, id }, state);
    stateKeys.push({ source, id });
  }

  function clearFS() {
    for (const k of stateKeys) map.removeFeatureState(k);
    stateKeys.length = 0;
    hovered = null;
  }

  function applyView(view) {
    clearFS();
    const state = view.stateCode ? index.states.get(view.stateCode) : null;
    const cluster = view.clusterId ? index.clusters.get(view.clusterId) : null;
    if (state) setFS('states', state.code, { selected: true });
    map.setFilter('states-line-selected', ['==', ['get', 'code'], state ? state.code : '']);

    let focusResorts = null;
    if (cluster) {
      for (const c of index.clusters.values()) setFS('clusters', c.id, c.id === cluster.id ? { selected: true } : { dim: true });
      focusResorts = new Set(cluster.resort_ids);
    } else if (state) {
      const ids = new Set(state.cluster_ids);
      for (const c of index.clusters.values()) setFS('clusters', c.id, ids.has(c.id) ? { focus: true } : { dim: true });
      focusResorts = new Set();
      for (const cid of state.cluster_ids) for (const r of index.clusters.get(cid).resort_ids) focusResorts.add(r);
    }
    if (focusResorts) {
      for (const r of index.resorts.values()) if (!focusResorts.has(r.id)) setFS('resorts', r.id, { dim: true });
    }
    map.setFilter('resorts-halo', ['==', ['get', 'id'], view.resortId || '']);
  }

  function clampPadding(p) {
    const w = container.clientWidth; const hgt = container.clientHeight;
    const out = { ...p };
    const fix = (a, b, total) => {
      const over = out[a] + out[b] - (total - 60);
      if (over > 0) { const s = (total - 60) / (out[a] + out[b]); out[a] = Math.max(0, Math.floor(out[a] * s)); out[b] = Math.max(0, Math.floor(out[b] * s)); }
    };
    fix('left', 'right', w);
    fix('top', 'bottom', hgt);
    return out;
  }

  function boundsFor(view) {
    if (view.level === 'resort') {
      const r = index.resorts.get(view.resortId);
      // Frame the resort with its neighbours around it.
      const d = 0.22;
      return { b: [[r.lon - d * 1.3, r.lat - d], [r.lon + d * 1.3, r.lat + d]], maxZoom: 11 };
    }
    if (view.clusterId) return { b: minSpan(index.clusters.get(view.clusterId).bbox, 1.2, 0.8), maxZoom: 9 };
    if (view.stateCode) return { b: index.states.get(view.stateCode).bbox, maxZoom: 7.5 };
    return { b: config.nationalBounds, maxZoom: 5 };
  }

  function fitView(view, animate) {
    const { b, maxZoom } = boundsFor(view);
    if (!b) return;
    map.fitBounds(b, {
      padding: clampPadding(getPadding(view)),
      maxZoom,
      duration: animate && !prefersReducedMotion() ? 900 : 0,
      essential: false,
    });
  }

  // Hit testing: resorts beat clusters beat states.
  function pick(point) {
    const pad = 6;
    const box = [[point.x - pad, point.y - pad], [point.x + pad, point.y + pad]];
    const r = map.queryRenderedFeatures(box, { layers: resortLayers() });
    if (r.length) {
      // Prefer the nearest Tier A, then any.
      const f = r.find((x) => x.properties.tier === 'A') || r[0];
      return { kind: 'resort', id: f.properties.id, name: f.properties.name };
    }
    const c = map.queryRenderedFeatures(point, { layers: ['clusters-fill'] });
    if (c.length) {
      // Smallest cluster wins where polygons overlap.
      const best = c.map((f) => index.clusters.get(f.properties.id)).filter(Boolean)
        .filter((cl) => !cl.fallback || (current && current.stateCode && cl.states.includes(current.stateCode)))
        .sort((a, b) => (a.fallback - b.fallback) || (area(a.bbox) - area(b.bbox)))[0];
      if (best) return { kind: 'cluster', id: best.id, name: best.name };
    }
    const s = map.queryRenderedFeatures(point, { layers: ['states-fill'] });
    if (s.length) {
      const code = s[0].properties.code;
      if (index.states.has(code)) return { kind: 'state', id: code, name: index.states.get(code).name };
    }
    return null;
  }

  function setHover(target) {
    if (hovered && (!target || hovered.kind !== target.kind || hovered.id !== target.id)) {
      map.setFeatureState(hoverKey(hovered), { hover: false });
      hovered = null;
    }
    if (target && !hovered) {
      const key = hoverKey(target);
      map.setFeatureState(key, { hover: true });
      stateKeys.push(key);
      hovered = target;
    }
  }

  map.on('mousemove', (e) => {
    const t = pick(e.point);
    setHover(t);
    map.getCanvas().style.cursor = t ? 'pointer' : '';
    if (t) {
      tip.hidden = false;
      tip.textContent = t.kind === 'resort' ? t.name
        : t.kind === 'cluster' ? `${t.name} cluster` : t.name;
      const x = Math.min(e.point.x + 14, container.clientWidth - tip.offsetWidth - 8);
      tip.style.transform = `translate(${Math.max(4, x)}px, ${Math.max(4, e.point.y - 34)}px)`;
    } else tip.hidden = true;
  });
  map.on('mouseout', () => { setHover(null); tip.hidden = true; });

  map.on('click', (e) => {
    const t = pick(e.point);
    if (t) onPick(t, current);
  });

  let filter = 'all';
  let recolor = null; // { prop, stops } or null for default

  function applyRecolor() {
    if (!map.getLayer('resorts')) return;
    if (!recolor) {
      for (const [k, v] of Object.entries(DEFAULT_PAINT)) map.setPaintProperty('resorts', k, v);
      if (map.getLayer('resorts-facility')) map.setLayoutProperty('resorts-facility', 'icon-image', DEFAULT_ICON);
      return;
    }
    const val = ['get', recolor.prop];
    const isNull = ['==', val, null];
    const colorStep = ['step', ['to-number', val], RAMP[0]];
    const classStep = ['step', ['to-number', val], 'c0'];
    recolor.stops.forEach((t, i) => { colorStep.push(t, RAMP[i + 1]); classStep.push(t, `c${i + 1}`); });
    const color = recolor.stops.length ? colorStep : RAMP[2];
    const cls = recolor.stops.length ? classStep : 'c2';
    map.setPaintProperty('resorts', 'circle-color', ['case', isNull, NO_DATA, color]);
    map.setPaintProperty('resorts', 'circle-stroke-color', ['case', fs('hover'), C.ring, ['==', ['get', 'tier'], 'A'], RECOLOR_STROKE.A, RECOLOR_STROKE.B]);
    map.setPaintProperty('resorts', 'circle-stroke-width', ['case', fs('hover'), 2.5, ['==', ['get', 'tier'], 'A'], 2.2, 1]);
    if (map.getLayer('resorts-facility')) {
      map.setLayoutProperty('resorts-facility', 'icon-image',
        ['concat', 'fac-', ['get', 'facility_type'], '-', ['get', 'tier'], '-', ['case', isNull, 'cnull', cls]]);
    }
  }
  function resortLayers() { return ['resorts', 'resorts-facility'].filter((id) => map.getLayer(id)); }
  function applyFilter() {
    if (map.getLayer('resorts')) map.setLayoutProperty('resorts', 'visibility', filter === 'facility' ? 'none' : 'visible');
    if (map.getLayer('resorts-facility')) map.setLayoutProperty('resorts-facility', 'visibility', filter === 'ski_area' ? 'none' : 'visible');
  }

  map.on('load', async () => {
    for (const { id, img } of await facilityImages()) if (!map.hasImage(id)) map.addImage(id, img, { pixelRatio: 2 });
    map.addLayer(FACILITY_LAYER);
    applyFilter();
    applyRecolor();
    loaded = true;
    // Start the compact attribution collapsed; the (i) button opens it.
    const attrib = container.querySelector('.maplibregl-ctrl-attrib');
    if (attrib) attrib.classList.remove('maplibregl-compact-show');
    if (pending) { const v = pending; pending = null; applyView(v.view); fitView(v.view, false); }
  });

  return {
    map,
    show(view, { animate = true } = {}) {
      current = view;
      if (!loaded) { pending = { view }; return; }
      applyView(view);
      fitView(view, animate);
    },
    refit() { if (loaded && current) fitView(current, false); },
    /** null for default colors, or { prop, stops } from classBreaks. */
    setRecolor(next) { recolor = next; if (loaded) applyRecolor(); },
    /** 'all' | 'ski_area' | 'facility' */
    setFilter(next) { filter = next; if (loaded) applyFilter(); },
    resize() { map.resize(); },
  };
}

function hoverKey(t) {
  return { source: t.kind === 'resort' ? 'resorts' : t.kind === 'cluster' ? 'clusters' : 'states', id: t.id };
}

/** Grow a bbox around its center to at least spanX by spanY degrees. */
function minSpan(b, spanX, spanY) {
  if (!b) return b;
  const cx = (b[0][0] + b[1][0]) / 2; const cy = (b[0][1] + b[1][1]) / 2;
  const hx = Math.max((b[1][0] - b[0][0]) / 2, spanX / 2);
  const hy = Math.max((b[1][1] - b[0][1]) / 2, spanY / 2);
  return [[cx - hx, cy - hy], [cx + hx, cy + hy]];
}

function area(b) { return b ? (b[1][0] - b[0][0]) * (b[1][1] - b[0][1]) : Infinity; }

