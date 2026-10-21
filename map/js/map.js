// MapLibre map: state outlines, cluster polygons, resort circles.
// Console look (ROADMAP item 21): a near black base (MAP tone) or full color imagery (SATELLITE tone), resort dots
// with a soft halo under a crisp core, and a green ring on resorts hiring now that pulses unless the viewer asks
// for reduced motion. Zoom, home and trail map buttons sit in one column on the right edge.
import { config, prefersReducedMotion } from './config.js';
import { h } from './ui.js';
import { viewFrame, clampPadding as clampTo, panCenter, panMinZoom, resortRadius } from './camera.js';
import { loadUnderlay, underlayNow } from './underlay.js';
import {
  addDetailLayers, setDetailData, showSiteCommutes, setGroupVisible, detailBounds, groupsWithData, housingBounds,
} from './detailmap.js';

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
  A: { fill: C.tierA, stroke: C.tierAStroke },
  B: { fill: C.tierB, stroke: C.background },
};
const TONES = config.basemap.tone;
const toneOf = (name) => TONES[name] || TONES[config.basemap.defaultTone];

// Trail map control icon: a peak with a run down it (static markup).
const TRAIL_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><path d="M2 17 L9 4 L13 10 L15 7 L19 17 Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M9 6 C8 9, 11 11, 9 16" fill="none" stroke="#44ff88" stroke-width="1.6" stroke-linecap="round"/></svg>';
// Home control icon: a house outline (static markup).
const HOME_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 20 20" aria-hidden="true"><path d="M3 9.5 L10 3.5 L17 9.5 M5 8 V16.5 H8.5 V12 H11.5 V16.5 H15 V8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/></svg>';

// Color modes draw a heat surface (heat.js) under small resort dots. Grey marks resorts without data.
export const NO_DATA = '#8891a0';
const HEAT_DOT = { fill: C.background, strokeA: C.tierA, strokeB: '#e7e9ee' };
// Snowfall isolines: dark lines over the bright upper classes, bolder every major step. Minor lines come in from
// zoom 4.5; labels from mountain range zoom. Labels are canvas drawn images, since the style has no glyph server.
const ISO = { color: C.background, labelMinZoom: 6.5 };
const ISO_LAYERS = ['heat-iso-minor', 'heat-iso-major', 'heat-iso-label'];

/** A small "300 in" label image for an isoline value (2x pixel ratio). */
function isoLabelImage(value) {
  const text = `${value} in`;
  const ratio = 2;
  const font = `600 ${10.5 * ratio}px Inter, system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif`;
  const c = document.createElement('canvas');
  const ctx = c.getContext('2d', { willReadFrequently: true });
  ctx.font = font;
  const pad = 3 * ratio;
  c.width = Math.ceil(ctx.measureText(text).width + 2 * pad);
  c.height = 15 * ratio;
  ctx.font = font;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.lineJoin = 'round';
  ctx.lineWidth = 3 * ratio;
  ctx.strokeStyle = 'rgba(255,255,255,0.88)';
  ctx.strokeText(text, c.width / 2, c.height / 2);
  ctx.fillStyle = '#10151e';
  ctx.fillText(text, c.width / 2, c.height / 2);
  const d = ctx.getImageData(0, 0, c.width, c.height);
  return { width: c.width, height: c.height, data: new Uint8Array(d.data.buffer) };
}

function svgImage(id, shape, fill, stroke, width) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40"><g fill="${fill}" stroke="${stroke}" stroke-width="${width}" stroke-linejoin="round">${shape}</g></svg>`;
  return new Promise((resolve) => {
    const img = new Image(40, 40);
    img.onload = () => resolve({ id, img });
    img.onerror = () => resolve(null);
    img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  });
}

/** Tier colored images for indoor and training markers. */
function facilityImages() {
  const out = [];
  for (const [type, shape] of Object.entries(SHAPES)) {
    for (const [tier, st] of Object.entries(TIER_STYLE)) out.push(svgImage(`fac-${type}-${tier}`, shape, st.fill, st.stroke, 3));
  }
  return Promise.all(out).then((list) => list.filter(Boolean));
}

// Fallback clusters (other-xx) carry no fill so named clusters read clearly.
const CLUSTER_FILL_OPACITY = ['case', ['boolean', ['get', 'fallback'], false],
  ['case', fs('selected'), 0.05, fs('hover'), 0.05, 0],
  ['case', fs('selected'), 0.1, fs('hover'), 0.16, fs('focus'), 0.12, fs('dim'), 0.03, 0.08]];

const IS_A = ['==', ['get', 'tier'], 'A'];
const TIER_COLOR = ['case', IS_A, C.tierA, C.tierB];
const DEFAULT_PAINT = {
  'circle-color': TIER_COLOR,
  'circle-stroke-color': ['case', fs('hover'), C.ring, IS_A, C.tierAStroke, C.background],
  'circle-stroke-width': ['case', fs('hover'), 2.5, IS_A, 1.2, 1],
};
// The soft halo under every dot (ski areas and facilities alike): a wide, fully blurred circle in the tier color.
const rank = ['coalesce', ['get', 'size_rank'], 1];
const GLOW_LAYER = {
  id: 'resorts-glow', type: 'circle', source: 'resorts',
  layout: { 'circle-sort-key': ['get', 'tier_a'] },
  paint: {
    'circle-radius': ['interpolate', ['linear'], ['zoom'], 3, ['+', 4.5, ['*', rank, 1.6]], 7, ['+', 7, ['*', rank, 2.6]], 11, ['+', 11, ['*', rank, 3.8]]],
    'circle-color': TIER_COLOR,
    'circle-blur': 1,
    'circle-opacity': ['case', fs('dim'), 0.08, IS_A, 0.55, 0.42],
    'circle-pitch-alignment': 'map',
  },
};
// Resorts hiring now: a crisp green ring around the dot (filtered to their ids in syncLayers). It never moves; the
// pulse is a CSS animation on a marker over it (syncPulses), left out when the viewer prefers reduced motion.
const HIRING_LAYER = {
  id: 'resorts-hiring', type: 'circle', source: 'resorts', filter: ['in', ['get', 'id'], ['literal', []]],
  paint: {
    'circle-radius': radius(3.2),
    'circle-color': 'rgba(0,0,0,0)',
    'circle-stroke-color': C.hiring,
    'circle-stroke-width': 1.4,
    'circle-stroke-opacity': ['case', fs('dim'), 0.25, 0.95],
  },
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

// Satellite style underlay (underlay.js): image sources under every data layer, at all zooms, the lower 48 and Alaska
// each at its true place. The tone is baked in. The quarter size copies (`-small`) carry national zooms; the full
// images join above them from UNDERLAY_FULL_FROM_ZOOM, so phones that stay national never decode or upload them.
const UNDERLAY_PAINT = { 'raster-opacity': 1, 'raster-fade-duration': 0, 'raster-resampling': 'linear' };
const TONE_KEYS = ['raster-saturation', 'raster-contrast', 'raster-brightness-min', 'raster-brightness-max'];
const UNDERLAY_SMALL = ['underlay-lower48-small', 'underlay-alaska-small'];
const UNDERLAY_FULL_FROM_ZOOM = 4.5;
function underlaySources(u, full) {
  const out = {};
  const r = u ? u.rasters : {};
  const src = (raster) => (full || !raster.small ? raster : raster.small).url;
  const id = (region) => `underlay-${region}${full ? '' : '-small'}`;
  if (r.lower48) out[id('lower48')] = { type: 'image', url: src(r.lower48), coordinates: r.lower48.coordinates };
  if (r.alaska) out[id('alaska')] = { type: 'image', url: src(r.alaska), coordinates: r.alaska.coordinates };
  return out;
}
const underlayLayer = (id, tone) => ({ id, type: 'raster', source: id, paint: { ...UNDERLAY_PAINT, ...toneOf(tone).underlay } });

function stateFillOpacity(on) {
  const stops = config.basemap.state_fill_opacity;
  return on && Array.isArray(stops) && stops.length ? ['interpolate', ['linear'], ['zoom'], ...stops.flat()] : 1;
}

function buildStyle(index, underlay, tone) {
  const img = config.basemap.imagery;
  const ul = underlaySources(underlay, false);
  const translucent = Boolean(img || config.basemap.underlay);
  const t = toneOf(tone);
  return {
    version: 8,
    sources: {
      ...ul,
      ...(img ? { imagery: { type: 'raster', tiles: img.tiles, tileSize: img.tileSize, minzoom: img.minzoom || 0, maxzoom: img.maxzoom, attribution: img.attribution } } : {}),
      states: { type: 'geojson', data: index.geo.usStates, promoteId: 'code' },
      clusters: { type: 'geojson', data: index.geo.clusters, promoteId: 'id' },
      resorts: { type: 'geojson', data: index.geo.resorts, promoteId: 'id' },
    },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': t.background } },
      // The underlay, then the USGS imagery over it from its minzoom, both under every data layer, in the viewer's tone.
      ...UNDERLAY_SMALL.filter((id) => ul[id]).map((id) => underlayLayer(id, tone)),
      ...(img ? [{ id: 'imagery', type: 'raster', source: 'imagery', minzoom: img.minzoom || 0, paint: { ...img.paint, ...t.imagery } }] : []),
      {
        id: 'states-fill', type: 'fill', source: 'states',
        paint: {
          'fill-opacity': stateFillOpacity(translucent),
          'fill-color': ['case', fs('selected'), C.stateFillSelected,
            ['all', fs('hover'), ['boolean', ['get', 'has_resorts'], false]], C.stateFillHover,
            ['boolean', ['get', 'has_resorts'], false], C.stateFillActive, C.stateFill],
        },
      },
      {
        id: 'states-line', type: 'line', source: 'states',
        paint: { 'line-color': C.stateLine, 'line-width': ['interpolate', ['linear'], ['zoom'], 3, 0.5, 8, 1.2] },
      },
      {
        id: 'clusters-fill', type: 'fill', source: 'clusters',
        paint: { 'fill-color': C.cluster, 'fill-opacity': CLUSTER_FILL_OPACITY },
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
      GLOW_LAYER,
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

/** Territories and states with no ski areas, hidden from the map (Hawaii, Puerto Rico, the island territories, DC). */
const HIDDEN_CODES = ['AS', 'GU', 'MP', 'PR', 'VI', 'HI', 'DC'];
const NOT_HIDDEN = ['!', ['in', ['get', 'code'], ['literal', HIDDEN_CODES]]];

const MAX_ZOOM = 14;

export function createMap(container, index, maplibregl, { onPick, getPadding, onHome, tone: startTone }) {
  if (!maplibregl) throw new Error('MapLibre failed to load');
  // The open map area of the current view (the container minus the side panel or the phone sheet), kept by fitView.
  let openPad = { top: 0, right: 0, bottom: 0, left: 0 };
  let map = null;
  let tone = toneOf(startTone) === TONES[startTone] ? startTone : config.basemap.defaultTone;
  /**
   * Pan limit (config.pan, camera.js panCenter), in place of maxBounds: the middle of the open map area stays inside
   * the pan box, so a drag brings Alaska to the middle at any zoom and the map never leaves the US. maxBounds would
   * hold the whole container instead, and the phone's national view reaches far south behind its sheet.
   */
  function constrainCamera(lngLat, zoom) {
    const z = Math.min(MAX_ZOOM, Math.max(map ? map.getMinZoom() : config.pan.minZoom, zoom));
    const tr = map && map.transform;
    const w = tr && tr.width ? tr.width : container.clientWidth;
    const hgt = tr && tr.height ? tr.height : container.clientHeight;
    const c = panCenter(lngLat.lng, lngLat.lat, z, w, hgt, openPad, config.pan.center);
    return { center: c ? new maplibregl.LngLat(c[0], c[1]).wrap() : lngLat, zoom: z };
  }
  map = new maplibregl.Map({
    container,
    // The 2D picture has usually loaded the underlay by now; if not, it joins the style when it arrives.
    style: buildStyle(index, underlayNow(), tone),
    bounds: config.nationalBounds,
    fitBoundsOptions: { padding: 20 },
    attributionControl: false,
    dragRotate: false,
    pitchWithRotate: false,
    touchPitch: false,
    // World copies draw Alaska's islands west of 180 degrees beside it. The pan limit keeps a copy of the map from
    // ever showing next to it.
    renderWorldCopies: true,
    transformConstrain: constrainCamera,
    fadeDuration: 0,
    minZoom: config.pan.minZoom,
    maxZoom: MAX_ZOOM,
  });
  // Zoom out as far as config.pan allows on this screen width (Alaska and the lower 48 together).
  const syncMinZoom = () => { const z = panMinZoom(container.clientWidth, config.pan); if (z != null && z !== map.getMinZoom()) map.setMinZoom(z); };
  syncMinZoom();
  map.on('resize', syncMinZoom);
  performance.mark('careermap:map-created');
  map.once('styledata', () => performance.mark('careermap:style'));
  map.once('render', () => performance.mark('careermap:first-render'));
  map.touchZoomRotate.disableRotation();
  map.keyboard.disableRotation();
  // One column on the right edge of the open map area (offset from the side panel in CSS): zoom in and out, then
  // home (the national view) and, on resort views with a trail map, the trail map button; the attribution under them
  // stays clear of the phone sheet.
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
  const homeBtn = h('button', {
    type: 'button', class: 'map-home', title: 'Back to the national view', 'aria-label': 'Back to the national view',
    onclick: () => { if (!current || current.level === 'national') { if (current) fitView(current, true); } else if (onHome) onHome(); },
  });
  homeBtn.innerHTML = HOME_ICON;
  const extras = h('div', { class: 'maplibregl-ctrl maplibregl-ctrl-group map-extras' }, homeBtn);
  map.addControl({ onAdd: () => extras, onRemove: () => extras.remove() }, 'top-right');
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

  let focusIds = null; // resorts in the focused state or cluster (others dim), or null at the national view
  function applyView(view) {
    clearFS();
    if (view.level === 'events') {
      // EVENTS mode: no state, cluster or resort is selected; events.js marks the selected venue on its own layers.
      map.setFilter('states-line-selected', ['==', ['get', 'code'], '']);
      map.setFilter('resorts-halo', ['==', ['get', 'id'], '']);
      focusIds = null;
      syncPulses();
      return;
    }
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
    focusIds = focusResorts;
    map.setFilter('resorts-halo', ['==', ['get', 'id'], view.resortId || '']);
    syncPulses();
  }

  function clampPadding(p) { return clampTo(p, container.clientWidth, container.clientHeight); }

  function boundsFor(view) {
    if (view.level === 'resort' && detail && detail.resort_id === view.resortId) {
      const db = detailBounds(detail);
      if (db) return { b: db, maxZoom: 13 };
    }
    return viewFrame(index, view);
  }

  function fitView(view, animate) {
    const { b, maxZoom } = boundsFor(view);
    if (!b) return;
    openPad = clampPadding(getPadding(view));
    map.fitBounds(b, {
      padding: openPad,
      maxZoom,
      duration: animate && !prefersReducedMotion() ? 900 : 0,
      essential: false,
    });
  }

  // Hit testing: resorts beat clusters beat states.
  const layersIn = (ids) => ids.filter((id) => map.getLayer(id) && map.getLayoutProperty(id, 'visibility') !== 'none');

  function pick(point) {
    const pad = 6;
    const box = [[point.x - pad, point.y - pad], [point.x + pad, point.y + pad]];
    // EVENTS mode: only event venues answer (events map hooks); states, clusters and resorts stay quiet.
    if (mode === 'events') return modeHooks && modeHooks.pick ? modeHooks.pick(point, box) : null;
    if (detail) {
      const hs = map.queryRenderedFeatures(box, { layers: layersIn(['detail-housing-point', 'detail-housing-area']) });
      if (hs.length) return { kind: 'housing', id: hs[0].properties.id, name: hs[0].properties.name };
      const base = map.queryRenderedFeatures(box, { layers: layersIn(['detail-base']) });
      if (base.length) return { kind: 'base', id: 'base', name: 'Base area', passive: true };
    }
    const r = map.queryRenderedFeatures(box, { layers: resortLayers() });
    if (r.length) {
      // Prefer the nearest Tier A, then any.
      const f = r.find((x) => x.properties.tier === 'A') || r[0];
      return { kind: 'resort', id: f.properties.id, name: f.properties.name };
    }
    if (detail) {
      const lb = [[point.x - 4, point.y - 4], [point.x + 4, point.y + 4]];
      const lift = map.queryRenderedFeatures(lb, { layers: layersIn(['detail-lifts']) });
      if (lift.length) return { kind: 'lift', id: lift[0].id, name: lift[0].properties.name ? `Lift: ${lift[0].properties.name}` : 'Unnamed lift', passive: true };
      const stop = map.queryRenderedFeatures(lb, { layers: layersIn(['detail-transit-stop']) });
      if (stop.length) return { kind: 'stop', id: stop[0].properties.id, name: `Stop: ${stop[0].properties.name}`, passive: true };
      const route = map.queryRenderedFeatures(lb, { layers: layersIn(['detail-transit-line']) });
      if (route.length) {
        // "Route: Intown, Vail Transit. Schedule for May 21 to Dec 6, 2026"
        const p = route[0].properties;
        return { kind: 'route', id: p.id, name: `Route: ${p.name}${p.agency ? `, ${p.agency}` : ''}${p.schedule ? `. ${p.schedule}` : ''}`, passive: true };
      }
    }
    const c = map.queryRenderedFeatures(point, { layers: layersIn(['clusters-fill']) });
    if (c.length) {
      // Smallest cluster wins where polygons overlap.
      const best = c.map((f) => index.clusters.get(f.properties.id)).filter(Boolean)
        .filter((cl) => !cl.fallback || (current && current.stateCode && cl.states.includes(current.stateCode)))
        .sort((a, b) => (a.fallback - b.fallback) || (area(a.bbox) - area(b.bbox)))[0];
      if (best) return { kind: 'cluster', id: best.id, name: best.name };
    }
    const s = map.queryRenderedFeatures(point, { layers: layersIn(['states-fill']) });
    if (s.length) {
      const code = s[0].properties.code;
      if (index.states.has(code)) return { kind: 'state', id: code, name: index.states.get(code).name };
    }
    return null;
  }

  function setHover(rawTarget) {
    // Base area, transit stops and routes have no feature ids; they show a tooltip only.
    const target = rawTarget && ['base', 'stop', 'route'].includes(rawTarget.kind) ? null : rawTarget;
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
    map.getCanvas().style.cursor = t && !t.passive ? 'pointer' : '';
    if (t) {
      tip.hidden = false;
      tip.textContent = t.kind === 'resort' ? resortTip(t)
        : t.kind === 'cluster' ? `${t.name} cluster` : t.tip || t.name;
      const x = Math.min(e.point.x + 14, container.clientWidth - tip.offsetWidth - 8);
      tip.style.transform = `translate(${Math.max(4, x)}px, ${Math.max(4, e.point.y - 34)}px)`;
    } else tip.hidden = true;
  });
  map.on('mouseout', () => { setHover(null); tip.hidden = true; });

  map.on('click', (e) => {
    const t = pick(e.point);
    if (t && !t.passive) onPick(t, current);
  });

  let facility = 'all'; // the Type filter: all | ski_area | facility
  let onlyIds = null;    // resort ids Hiring now and the pass filters keep (null: no such filter)
  let hiringIds = [];    // resorts hiring now (hiring.js), ringed on the map
  let detail = null;
  let selectedSite = null;
  let trailCtl = null;
  let recolor = null; // { key, prop, images, format, attribution, isolines } or null for default
  let heatIds = [];

  function resortTip(t) {
    if (!recolor) return t.name;
    const r = index.resorts.get(t.id);
    const v = r ? r[recolor.prop] : null;
    return v == null ? `${t.name}: no data` : `${t.name}: ${recolor.format(v)}`;
  }

  function clearHeat() {
    for (const id of heatIds) {
      if (map.getLayer(id)) map.removeLayer(id);
      if (map.getSource(id)) map.removeSource(id);
    }
    heatIds = [];
  }

  function applyRecolor() {
    if (!map.getLayer('resorts')) return;
    clearHeat();
    if (map.getLayer('resorts-glow')) map.setLayoutProperty('resorts-glow', 'visibility', recolor ? 'none' : 'visible');
    if (!recolor) {
      for (const [k, v] of Object.entries(DEFAULT_PAINT)) map.setPaintProperty('resorts', k, v);
      map.setPaintProperty('resorts', 'circle-radius', radius());
      map.setPaintProperty('clusters-fill', 'fill-opacity', CLUSTER_FILL_OPACITY);
      if (map.getLayer('resorts-facility')) map.setPaintProperty('resorts-facility', 'icon-opacity', ['case', fs('dim'), 0.3, 1]);
      return;
    }
    // Heat surface under the state lines, cluster outlines and resorts; one image per region.
    const before = map.getLayer('states-line') ? 'states-line' : undefined;
    const raster = (id, url, coordinates, beforeId) => {
      map.addSource(id, { type: 'image', url, coordinates });
      map.addLayer({
        id, type: 'raster', source: id,
        paint: { 'raster-opacity': 1, 'raster-resampling': 'linear', 'raster-fade-duration': 0 },
      }, beforeId);
      heatIds.push(id);
    };
    // Credit for precomputed rasters (snowfall: NOAA normals). Image sources carry no attribution, so an empty
    // GeoJSON source with one invisible layer holds it; MapLibre lists it while the heat map is on.
    if (recolor.attribution && !map.getSource('heat-credit')) {
      map.addSource('heat-credit', { type: 'geojson', data: { type: 'FeatureCollection', features: [] }, attribution: recolor.attribution });
      map.addLayer({ id: 'heat-credit', type: 'fill', source: 'heat-credit', paint: { 'fill-opacity': 0 } }, before);
      heatIds.push('heat-credit');
    }
    recolor.images.forEach((img, i) => raster(`heat-${recolor.key}-${i}`, img.url, img.coordinates, before));
    if (recolor.isolines) {
      const mine = recolor;
      Promise.resolve(recolor.isolines).then((fc) => { if (fc && recolor === mine && map.getLayer('resorts')) addIsolines(fc); })
        .catch((err) => console.warn('[map] isolines', err.message));
    }
    // Cluster tints would shift the heat colors; outlines stay.
    map.setPaintProperty('clusters-fill', 'fill-opacity', 0);
    const isNull = ['==', ['get', recolor.prop], null];
    map.setPaintProperty('resorts', 'circle-radius', ['interpolate', ['linear'], ['zoom'], 3, 2.2, 7, 3.4, 11, 5]);
    map.setPaintProperty('resorts', 'circle-color', ['case', isNull, NO_DATA, HEAT_DOT.fill]);
    map.setPaintProperty('resorts', 'circle-stroke-color', ['case', fs('hover'), C.ring, ['==', ['get', 'tier'], 'A'], HEAT_DOT.strokeA, isNull, C.background, HEAT_DOT.strokeB]);
    map.setPaintProperty('resorts', 'circle-stroke-width', ['case', fs('hover'), 2.5, IS_A, 1.8, 1]);
    if (map.getLayer('resorts-facility')) map.setPaintProperty('resorts-facility', 'icon-opacity', ['case', fs('dim'), 0.3, isNull, 0.6, 1]);
    enforceMode();
  }
  /** Snowfall isolines over the heat surface, the lower 48 and Alaska alike. */
  function addIsolines(fc) {
    if (map.getSource('heat-iso')) return;
    for (const v of new Set(fc.features.map((f) => f.properties.value))) {
      if (!map.hasImage(`iso-${v}`)) map.addImage(`iso-${v}`, isoLabelImage(v), { pixelRatio: 2 });
    }
    map.addSource('heat-iso', { type: 'geojson', data: fc });
    const lines = map.getLayer('states-line') ? 'states-line' : undefined;
    const labels = map.getLayer('states-line-selected') ? 'states-line-selected' : undefined;
    const major = ['boolean', ['get', 'major'], false];
    map.addLayer({
      id: 'heat-iso-minor', type: 'line', source: 'heat-iso', filter: ['!', major], minzoom: 4.5,
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: {
        'line-color': ISO.color,
        'line-opacity': ['interpolate', ['linear'], ['zoom'], 4.5, 0, 6, 0.5, 9, 0.6],
        'line-width': ['interpolate', ['linear'], ['zoom'], 5, 0.4, 8, 0.8, 11, 1.2],
      },
    }, lines);
    map.addLayer({
      id: 'heat-iso-major', type: 'line', source: 'heat-iso', filter: major,
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: {
        'line-color': ISO.color,
        'line-opacity': ['interpolate', ['linear'], ['zoom'], 3, 0.45, 6, 0.8],
        'line-width': ['interpolate', ['linear'], ['zoom'], 3, 0.5, 6, 1.1, 9, 1.8, 11, 2.2],
      },
    }, lines);
    map.addLayer({
      id: 'heat-iso-label', type: 'symbol', source: 'heat-iso', minzoom: ISO.labelMinZoom,
      filter: ['==', ['get', 'label'], true],
      layout: {
        'symbol-placement': 'line', 'symbol-spacing': 280, 'icon-image': ['concat', 'iso-', ['to-string', ['get', 'value']]],
        'icon-rotation-alignment': 'map', 'icon-keep-upright': true, 'icon-size': 1, 'icon-padding': 2,
      },
    }, labels);
    heatIds.push(...ISO_LAYERS, 'heat-iso');
  }
  function resortLayers() { return layersIn(['resorts', 'resorts-facility']); }
  function applyFilter() { syncLayers(); }

  /** Visibility and filters for the facility and resort filters. Territories without ski areas never draw. */
  function syncLayers() {
    const vis = (id, on) => { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none'); };
    if (map.getLayer('states-fill')) {
      map.setFilter('states-fill', NOT_HIDDEN);
      map.setFilter('states-line', NOT_HIDDEN);
    }
    // Hiring now and the pass filters keep only the listed resorts, ski areas and facilities alike.
    const only = onlyIds ? [['in', ['get', 'id'], ['literal', onlyIds]]] : [];
    map.setFilter('resorts', ['all', IS_SKI_AREA, ...only]);
    if (map.getLayer('resorts-facility')) map.setFilter('resorts-facility', ['all', ['!', IS_SKI_AREA], ...only]);
    vis('resorts', facility !== 'facility');
    vis('resorts-facility', facility !== 'ski_area');
    // The halos and the hiring rings follow the same rule as the dots they sit on.
    const type = facility === 'ski_area' ? [IS_SKI_AREA] : facility === 'facility' ? [['!', IS_SKI_AREA]] : [];
    if (map.getLayer('resorts-glow')) map.setFilter('resorts-glow', ['all', ...type, ...only]);
    if (map.getLayer('resorts-hiring')) map.setFilter('resorts-hiring', ['all', ['in', ['get', 'id'], ['literal', hiringIds]], ...type, ...only]);
    enforceMode();
    syncPulses();
  }

  // ── EVENTS mode (ROADMAP item 22): the map shows event venues (events.js, eventsmap.js) instead of resorts. Every
  // resort, cluster and heat layer is hidden while it lasts and comes back as it was; picking goes to the hooks.
  let mode = 'resorts';
  let modeHooks = null; // { pick(point, box) -> { kind: 'venue', id, name, tip } | null }
  const modeHidden = new Set();
  const RESORT_LAYER = (id) => id.startsWith('resorts') || id.startsWith('clusters-') || id.startsWith('heat-');
  function enforceMode() {
    if (mode !== 'events') return;
    const style = map.getStyle();
    for (const l of style ? style.layers : []) {
      if (!RESORT_LAYER(l.id) || map.getLayoutProperty(l.id, 'visibility') === 'none') continue;
      modeHidden.add(l.id);
      map.setLayoutProperty(l.id, 'visibility', 'none');
    }
  }
  function applyMode() {
    if (mode === 'events') enforceMode();
    else {
      for (const id of modeHidden) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', 'visible');
      modeHidden.clear();
      syncLayers(); // the Type filter decides the resort layers again
      if (map.getLayer('resorts-glow')) map.setLayoutProperty('resorts-glow', 'visibility', recolor ? 'none' : 'visible');
    }
    setHover(null);
    tip.hidden = true;
    syncPulses();
  }

  // ── Hiring now pulse: a CSS animated ring (a MapLibre marker, so it follows the map) on each hiring resort the
  // filters and the view keep. Markers are decoration only: hidden from assistive technology and from the pointer.
  const pulses = new Map(); // resort id -> { marker, el, rank }
  const reducedMotion = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
  if (reducedMotion && reducedMotion.addEventListener) reducedMotion.addEventListener('change', () => syncPulses());
  function syncPulses() {
    if (!loaded) return;
    const want = new Set();
    if (!prefersReducedMotion() && mode !== 'events') {
      const only = onlyIds ? new Set(onlyIds) : null;
      for (const id of hiringIds) {
        const r = index.resorts.get(id);
        if (!r || (only && !only.has(id)) || (focusIds && !focusIds.has(id))) continue;
        const ski = (r.facility_type || 'ski_area') === 'ski_area';
        if ((facility === 'ski_area' && !ski) || (facility === 'facility' && ski)) continue;
        want.add(id);
      }
    }
    for (const [id, p] of pulses) if (!want.has(id)) { p.marker.remove(); pulses.delete(id); }
    for (const id of want) {
      if (pulses.has(id)) continue;
      const r = index.resorts.get(id);
      const el = h('span', { class: 'hiring-pulse' }, h('span', { class: 'hiring-pulse-ring' }));
      const marker = new maplibregl.Marker({ element: el, anchor: 'center' }).setLngLat([r.lon, r.lat]).addTo(map);
      // MapLibre labels every marker as a button; this one is a picture of the ring under it.
      el.removeAttribute('aria-label');
      el.setAttribute('role', 'presentation');
      el.setAttribute('aria-hidden', 'true');
      pulses.set(id, { marker, el, rank: r.size_rank || 1 });
    }
    sizePulses();
  }
  function sizePulses() {
    const z = map.getZoom();
    for (const p of pulses.values()) p.el.style.setProperty('--r', `${(resortRadius(p.rank, z) + 3.2).toFixed(1)}px`);
  }
  map.on('zoom', () => { if (pulses.size) sizePulses(); });

  // Ready as soon as the style and the data sources (states, clusters, resorts) are in and drawn. MapLibre's own
  // `load` also waits for every imagery tile, so a slow or failing imagery service must never hold the map back.
  const DATA_SOURCES = ['states', 'clusters', 'resorts'];
  const readyFns = [];
  let styleIn = false;
  let armed = false;
  let readyFired = false;
  const dataIn = () => DATA_SOURCES.every((id) => map.getSource(id) && map.isSourceLoaded(id));
  function checkReady() {
    if (armed || !styleIn || !dataIn()) return;
    armed = true;
    map.off('sourcedata', checkReady);
    map.off('render', checkReady);
    setup();
  }
  map.once('style.load', () => { styleIn = true; checkReady(); });

  // Underlay layers the first style lacks join once the data layers are drawn, under the imagery (or the data), so
  // their decode and texture upload never hold the first frame back: the quarter size copies when the picture had
  // not loaded them (a quick click, a resort page), then the full images from state zooms.
  loadUnderlay().then((u) => { if (u) whenReady(() => addUnderlay(u)); });
  function addUnderlay(u) {
    const before = map.getLayer('imagery') ? 'imagery' : 'states-fill';
    const put = (src) => {
      for (const [id, spec] of Object.entries(src)) {
        if (map.getSource(id)) continue;
        map.addSource(id, spec);
        map.addLayer(underlayLayer(id, tone), before);
      }
    };
    put(underlaySources(u, false));
    const full = () => map.getZoom() >= UNDERLAY_FULL_FROM_ZOOM && (put(underlaySources(u, true)), true);
    if (!full()) { const onZoom = () => { if (full()) map.off('zoom', onZoom); }; map.on('zoom', onZoom); }
  }
  map.on('sourcedata', checkReady);
  map.on('render', checkReady);

  async function setup() {
    for (const { id, img } of await facilityImages()) if (!map.hasImage(id)) map.addImage(id, img, { pixelRatio: 2 });
    map.addLayer(FACILITY_LAYER);
    map.addLayer(HIRING_LAYER);
    addDetailLayers(map);
    if (detail) setDetailData(map, detail);
    applyFilter();
    applyRecolor();
    loaded = true;
    if (mode === 'events') applyMode();
    // Start the compact attribution collapsed; the (i) button opens it.
    const attrib = container.querySelector('.maplibregl-ctrl-attrib');
    if (attrib) attrib.classList.remove('maplibregl-compact-show');
    if (pending) { const v = pending; pending = null; applyView(v.view); fitView(v.view, false); }
    // Fire on the next frame, once the data layers are on screen.
    map.once('render', () => { readyFired = true; for (const fn of readyFns.splice(0)) fn(); });
    map.triggerRepaint();
  }

  /** Run fn once the data layers have rendered (imagery may still be loading). */
  function whenReady(fn) { if (readyFired) fn(); else readyFns.push(fn); }

  return {
    map,
    whenReady,
    /** True once the data layers are drawn; MapLibre's loaded() may stay false while imagery tiles load. */
    get ready() { return readyFired; },
    show(view, { animate = true } = {}) {
      current = view;
      if (!loaded) { pending = { view }; return; }
      applyView(view);
      fitView(view, animate);
    },
    refit() { if (loaded && current) fitView(current, false); },
    /** State outlines arrive after first render. */
    setStates(fc) {
      const apply = () => {
        const src = map.getSource('states'); if (src) src.setData(fc);
        if (current && current.stateCode) applyView(current);
      };
      whenReady(apply);
    },
    /** Resort detail mode. Pass null to leave it. Returns the layer groups that have data. */
    setDetail(next) {
      detail = next;
      selectedSite = null;
      if (!loaded) return groupsWithData(next);
      setDetailData(map, next);
      showSiteCommutes(map, null);
      if (next && current && current.level === 'resort' && current.resortId === next.resort_id) fitView(current, true);
      return groupsWithData(next);
    },
    selectSite(id) {
      if (!loaded) return;
      if (selectedSite) map.setFeatureState({ source: 'detail-housing', id: selectedSite }, { selected: false });
      selectedSite = id;
      if (id) map.setFeatureState({ source: 'detail-housing', id }, { selected: true });
      showSiteCommutes(map, id);
    },
    setLayerGroup(group, on) { if (loaded) setGroupVisible(map, group, on); },
    /** Fit the map to the detail file's housing pins. Returns the number of sites shown (0 when there are none). */
    fitHousing() {
      const b = loaded && detail ? housingBounds(detail) : null;
      if (!b) return 0;
      openPad = clampPadding(getPadding(current || { level: 'resort' }));
      map.fitBounds(b, {
        padding: openPad,
        maxZoom: 14,
        duration: prefersReducedMotion() ? 0 : 900,
        essential: false,
      });
      return detail.housing.length;
    },
    /** Trail map button under home (resort views with a trail map). Pass null to remove it. */
    setTrailControl(onOpen) {
      if (trailCtl) { trailCtl.remove(); trailCtl = null; }
      if (!onOpen) return;
      const btn = h('button', {
        type: 'button', class: 'trailmap-ctrl', 'aria-haspopup': 'dialog', title: 'Open the trail map', 'aria-label': 'Open the trail map',
        onclick: () => onOpen(btn),
      });
      btn.innerHTML = TRAIL_ICON;
      extras.append(btn);
      trailCtl = btn;
    },
    /** 'map' (darkened monochrome) or 'satellite' (full color): the background, underlay and imagery paint. */
    setTone(next) {
      if (!TONES[next] || next === tone) return;
      tone = next;
      const t = toneOf(tone);
      map.setPaintProperty('background', 'background-color', t.background);
      for (const l of map.getStyle().layers) {
        const paint = l.id === 'imagery' ? t.imagery : l.type === 'raster' && l.id.startsWith('underlay-') ? t.underlay : null;
        if (paint) for (const k of TONE_KEYS) map.setPaintProperty(l.id, k, paint[k]);
      }
    },
    get tone() { return tone; },
    /** Resorts hiring now (index.hiringIds): a green ring, and a pulse unless the viewer prefers reduced motion. */
    setHiring(ids) {
      hiringIds = Array.isArray(ids) ? [...ids] : [];
      if (loaded) syncLayers();
    },
    /** The padding of the last fit: the open map area is the container minus it (the pan limit centers on it). */
    get openPadding() { return { ...openPad }; },
    /** null for default colors, or { key, prop, images, format, attribution } from recolor.js. */
    setRecolor(next) { recolor = next; if (loaded) applyRecolor(); },
    /** f: the chips' state (data.js NO_FILTER shape); ids: the resorts Hiring now and the pass chips keep, or null. */
    setFilter(f, ids) {
      facility = (f && f.facility) || 'all';
      onlyIds = Array.isArray(ids) ? ids : null;
      if (loaded) applyFilter();
    },
    resize() { map.resize(); },
    /** 'resorts' (the default) or 'events': EVENTS mode hides the resort layers and picks through hooks.pick. */
    setMode(next, hooks = null) {
      const m = next === 'events' ? 'events' : 'resorts';
      modeHooks = m === 'events' ? hooks : null;
      if (m === mode) return;
      mode = m;
      if (loaded) applyMode();
    },
    get mode() { return mode; },
  };
}

function hoverKey(t) {
  const source = { resort: 'resorts', cluster: 'clusters', state: 'states', housing: 'detail-housing', lift: 'detail-lifts', base: 'detail-base', stop: 'detail-transit-stops', venue: 'event-venues' }[t.kind];
  return { source, id: t.id };
}

function area(b) { return b ? (b[1][0] - b[0][0]) * (b[1][1] - b[0][1]) : Infinity; }

