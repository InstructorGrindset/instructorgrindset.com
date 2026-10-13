// MapLibre map: state outlines, cluster polygons, resort circles.
import { config, prefersReducedMotion } from './config.js';
import { h } from './ui.js';
import { viewFrame, clampPadding as clampTo } from './camera.js';
import { INSET_STATE, HIDDEN_CODES, insetFeatures, insetFrame, insetPanel, insetCorners } from './inset.js';
import {
  addDetailLayers, setDetailData, showSiteCommutes, setGroupVisible, detailBounds, groupsWithData,
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
  A: { fill: C.tierA, stroke: '#fff4dc' },
  B: { fill: C.tierB, stroke: C.background },
};

// Trail map control icon: a peak with a run down it (static markup).
const TRAIL_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><path d="M2 17 L9 4 L13 10 L15 7 L19 17 Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M9 6 C8 9, 11 11, 9 16" fill="none" stroke="#e0a03a" stroke-width="1.6" stroke-linecap="round"/></svg>';

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
  const font = `600 ${10.5 * ratio}px Inter, system-ui, sans-serif`;
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

const DEFAULT_PAINT = {
  'circle-color': ['case', ['==', ['get', 'tier'], 'A'], C.tierA, C.tierB],
  'circle-stroke-color': ['case', fs('hover'), C.ring, ['==', ['get', 'tier'], 'A'], '#fff4dc', C.background],
  'circle-stroke-width': ['case', fs('hover'), 2.5, ['==', ['get', 'tier'], 'A'], 1.5, 1],
};
const DEFAULT_ICON = ['concat', 'fac-', ['get', 'facility_type'], '-', ['get', 'tier']];

const FACILITY_LAYER = {
  id: 'resorts-facility', type: 'symbol', source: 'resorts',
  // (an inset copy of this layer reads the inset-resorts source)
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
  const img = config.basemap.imagery;
  return {
    version: 8,
    sources: {
      ...(img ? { imagery: { type: 'raster', tiles: img.tiles, tileSize: img.tileSize, maxzoom: img.maxzoom, attribution: img.attribution } } : {}),
      states: { type: 'geojson', data: index.geo.usStates, promoteId: 'code' },
      clusters: { type: 'geojson', data: index.geo.clusters, promoteId: 'id' },
      resorts: { type: 'geojson', data: index.geo.resorts, promoteId: 'id' },
      // Alaska inset (national view only): moved copies of its outline, clusters and resorts.
      'inset-states': { type: 'geojson', data: { type: 'FeatureCollection', features: [] } },
      'inset-clusters': { type: 'geojson', data: insetFeatures(index.geo.clusters.features.filter((f) => f.properties.ak)) },
      'inset-resorts': { type: 'geojson', data: insetFeatures(index.geo.resorts.features.filter((f) => f.properties.state === INSET_STATE)) },
      'inset-frame': { type: 'geojson', data: insetFrame() },
      'inset-panel': { type: 'geojson', data: insetPanel() },
    },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': C.background } },
      // Satellite imagery sits under every data layer.
      ...(img ? [{ id: 'imagery', type: 'raster', source: 'imagery', paint: img.paint }] : []),
      {
        id: 'states-fill', type: 'fill', source: 'states',
        paint: {
          'fill-opacity': img ? img.state_fill_opacity : 1,
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
      // The inset sits on its own dark panel: the imagery under it is not Alaska.
      { id: 'inset-panel', type: 'fill', source: 'inset-panel', paint: { 'fill-color': C.background, 'fill-opacity': 0.92 } },
      { id: 'inset-frame', type: 'line', source: 'inset-frame', paint: { 'line-color': C.stateLine, 'line-width': 1, 'line-dasharray': [3, 2] } },
      { id: 'inset-states-fill', type: 'fill', source: 'inset-states', paint: { 'fill-color': C.stateFillActive } },
      { id: 'inset-states-line', type: 'line', source: 'inset-states', paint: { 'line-color': C.stateLine, 'line-width': 0.8 } },
      { id: 'inset-clusters-fill', type: 'fill', source: 'inset-clusters', paint: { 'fill-color': C.cluster, 'fill-opacity': ['case', ['boolean', ['get', 'fallback'], false], 0, 0.08] } },
      { id: 'inset-clusters-line', type: 'line', source: 'inset-clusters', filter: ['!=', ['get', 'fallback'], true], paint: { 'line-color': C.cluster, 'line-opacity': 0.5, 'line-width': 1 } },
      {
        id: 'inset-resorts', type: 'circle', source: 'inset-resorts', filter: IS_SKI_AREA,
        layout: { 'circle-sort-key': ['get', 'tier_a'] },
        paint: { 'circle-radius': radius(), ...DEFAULT_PAINT, 'circle-opacity': ['case', ['==', ['get', 'tier'], 'A'], 1, 0.85] },
      },
    ],
  };
}

const INSET_LAYERS = ['inset-panel', 'inset-frame', 'inset-states-fill', 'inset-states-line', 'inset-clusters-fill', 'inset-clusters-line', 'inset-resorts', 'inset-resorts-facility'];
const NOT_HIDDEN = ['!', ['in', ['get', 'code'], ['literal', HIDDEN_CODES]]];

export function createMap(container, index, maplibregl, { onPick, getPadding }) {
  if (!maplibregl) throw new Error('MapLibre failed to load');
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
  performance.mark('careermap:map-created');
  map.once('styledata', () => performance.mark('careermap:style'));
  map.once('render', () => performance.mark('careermap:first-render'));
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
    national = view.level === 'national';
    syncLayers();

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
    map.fitBounds(b, {
      padding: clampPadding(getPadding(view)),
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
    const c = map.queryRenderedFeatures(point, { layers: layersIn(['clusters-fill', 'inset-clusters-fill']) });
    if (c.length) {
      // Smallest cluster wins where polygons overlap.
      const best = c.map((f) => index.clusters.get(f.properties.id)).filter(Boolean)
        .filter((cl) => !cl.fallback || (current && current.stateCode && cl.states.includes(current.stateCode)))
        .sort((a, b) => (a.fallback - b.fallback) || (area(a.bbox) - area(b.bbox)))[0];
      if (best) return { kind: 'cluster', id: best.id, name: best.name };
    }
    const s = map.queryRenderedFeatures(point, { layers: layersIn(['states-fill', 'inset-states-fill']) });
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
        : t.kind === 'cluster' ? `${t.name} cluster` : t.name;
      const x = Math.min(e.point.x + 14, container.clientWidth - tip.offsetWidth - 8);
      tip.style.transform = `translate(${Math.max(4, x)}px, ${Math.max(4, e.point.y - 34)}px)`;
    } else tip.hidden = true;
  });
  map.on('mouseout', () => { setHover(null); tip.hidden = true; });

  map.on('click', (e) => {
    const t = pick(e.point);
    if (t && !t.passive) onPick(t, current);
  });

  let facility = 'all'; // the facility chips: all | ski_area | facility
  let onlyIds = null;    // resort ids Hiring now and the pass chips keep (null: no such filter)
  let detail = null;
  let selectedSite = null;
  let trailCtl = null;
  let national = true;
  let heatAK = [];     // heat layers of Alaska only regions, at true coordinates
  let heatInset = [];  // their copies in the national inset
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
    heatAK = [];
    heatInset = [];
  }

  function applyRecolor() {
    if (!map.getLayer('resorts')) return;
    clearHeat();
    if (!recolor) {
      for (const id of ['resorts', 'inset-resorts']) {
        for (const [k, v] of Object.entries(DEFAULT_PAINT)) map.setPaintProperty(id, k, v);
        map.setPaintProperty(id, 'circle-radius', radius());
      }
      map.setPaintProperty('inset-clusters-fill', 'fill-opacity', ['case', ['boolean', ['get', 'fallback'], false], 0, 0.08]);
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
    if (recolor.attribution) {
      map.addSource('heat-credit', { type: 'geojson', data: { type: 'FeatureCollection', features: [] }, attribution: recolor.attribution });
      map.addLayer({ id: 'heat-credit', type: 'fill', source: 'heat-credit', paint: { 'fill-opacity': 0 } }, before);
      heatIds.push('heat-credit');
    }
    recolor.images.forEach((img, i) => {
      const id = `heat-${recolor.key}-${i}`;
      raster(id, img.url, img.coordinates, before);
      if (img.ak) {
        // The same image, moved into the national inset (the inset transform keeps image corners aligned).
        heatAK.push(id);
        raster(`${id}-inset`, img.url, insetCorners(img.coordinates), map.getLayer('inset-states-line') ? 'inset-states-line' : undefined);
        heatInset.push(`${id}-inset`);
      }
    });
    if (recolor.isolines) {
      const mine = recolor;
      Promise.resolve(recolor.isolines).then((fc) => { if (fc && recolor === mine && map.getLayer('resorts')) addIsolines(fc); })
        .catch((err) => console.warn('[map] isolines', err.message));
    }
    syncLayers();
    // Cluster tints would shift the heat colors; outlines stay.
    map.setPaintProperty('clusters-fill', 'fill-opacity', 0);
    const isNull = ['==', ['get', recolor.prop], null];
    for (const id of ['resorts', 'inset-resorts']) {
      map.setPaintProperty(id, 'circle-radius', ['interpolate', ['linear'], ['zoom'], 3, 2.2, 7, 3.4, 11, 5]);
      map.setPaintProperty(id, 'circle-color', ['case', isNull, NO_DATA, HEAT_DOT.fill]);
      map.setPaintProperty(id, 'circle-stroke-color', ['case', fs('hover'), C.ring, ['==', ['get', 'tier'], 'A'], HEAT_DOT.strokeA, isNull, C.background, HEAT_DOT.strokeB]);
      map.setPaintProperty(id, 'circle-stroke-width', ['case', fs('hover'), 2.5, ['==', ['get', 'tier'], 'A'], 1.8, 1]);
    }
    map.setPaintProperty('inset-clusters-fill', 'fill-opacity', 0);
    if (map.getLayer('resorts-facility')) map.setPaintProperty('resorts-facility', 'icon-opacity', ['case', fs('dim'), 0.3, isNull, 0.6, 1]);
  }
  /** Snowfall isolines over the heat surface (true coordinates only; the national inset is too small for them). */
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
    syncLayers();
  }
  function resortLayers() { return layersIn(['resorts', 'resorts-facility', 'inset-resorts', 'inset-resorts-facility']); }
  function applyFilter() { syncLayers(); }

  /**
   * Visibility and filters for the facility filter and the national Alaska inset: at the national view Alaska
   * draws only in the inset; elsewhere at its true place. Territories without ski areas never draw.
   */
  function syncLayers() {
    const vis = (id, on) => { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none'); };
    const notAK = (key) => (national ? ['!=', ['get', key], INSET_STATE] : true);
    if (map.getLayer('states-fill')) {
      map.setFilter('states-fill', ['all', NOT_HIDDEN, notAK('code')]);
      map.setFilter('states-line', ['all', NOT_HIDDEN, notAK('code')]);
    }
    const notAKCluster = national ? ['!=', ['get', 'ak'], true] : true;
    map.setFilter('clusters-fill', notAKCluster);
    map.setFilter('clusters-line', ['all', ['!=', ['get', 'fallback'], true], notAKCluster]);
    map.setFilter('clusters-line-fallback', ['all', ['==', ['get', 'fallback'], true], notAKCluster]);
    // Hiring now and the pass chips keep only the listed resorts, ski areas and facilities alike.
    const only = onlyIds ? [['in', ['get', 'id'], ['literal', onlyIds]]] : [];
    map.setFilter('resorts', ['all', IS_SKI_AREA, notAK('state'), ...only]);
    if (map.getLayer('resorts-facility')) map.setFilter('resorts-facility', ['all', ['!', IS_SKI_AREA], notAK('state'), ...only]);
    map.setFilter('inset-resorts', ['all', IS_SKI_AREA, ...only]);
    if (map.getLayer('inset-resorts-facility')) map.setFilter('inset-resorts-facility', ['all', ['!', IS_SKI_AREA], ...only]);
    vis('resorts', facility !== 'facility');
    vis('resorts-facility', facility !== 'ski_area');
    for (const id of INSET_LAYERS) vis(id, national);
    if (national) { vis('inset-resorts', facility !== 'facility'); vis('inset-resorts-facility', facility !== 'ski_area'); }
    for (const id of heatAK) vis(id, !national);
    for (const id of heatInset) vis(id, national);
    // Alaska's isolines draw at its true place only, which the national view hides.
    const isoAK = national ? ['!=', ['get', 'region'], 'alaska'] : true;
    if (map.getLayer('heat-iso-minor')) {
      const major = ['boolean', ['get', 'major'], false];
      map.setFilter('heat-iso-minor', ['all', ['!', major], isoAK]);
      map.setFilter('heat-iso-major', ['all', major, isoAK]);
      map.setFilter('heat-iso-label', ['all', ['==', ['get', 'label'], true], isoAK]);
    }
  }

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
  map.on('sourcedata', checkReady);
  map.on('render', checkReady);

  async function setup() {
    for (const { id, img } of await facilityImages()) if (!map.hasImage(id)) map.addImage(id, img, { pixelRatio: 2 });
    map.addLayer(FACILITY_LAYER);
    map.addLayer({ ...FACILITY_LAYER, id: 'inset-resorts-facility', source: 'inset-resorts', paint: { 'icon-opacity': 1 } });
    addDetailLayers(map);
    if (detail) setDetailData(map, detail);
    applyFilter();
    applyRecolor();
    loaded = true;
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
        const ins = map.getSource('inset-states');
        if (ins) ins.setData(insetFeatures(fc.features.filter((f) => f.properties.code === INSET_STATE)));
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
    /** Trail map button on the map (resort views with a trail map). Pass null to remove it. */
    setTrailControl(onOpen) {
      if (trailCtl) { map.removeControl(trailCtl); trailCtl = null; }
      if (!onOpen) return;
      trailCtl = {
        onAdd() {
          const btn = h('button', {
            type: 'button', class: 'trailmap-ctrl', 'aria-haspopup': 'dialog', title: 'Open the interactive map', 'aria-label': 'Open the interactive map',
            onclick: () => onOpen(btn),
          });
          btn.innerHTML = TRAIL_ICON;
          this.el = h('div', { class: 'maplibregl-ctrl maplibregl-ctrl-group' }, btn);
          return this.el;
        },
        onRemove() { this.el.remove(); },
      };
      map.addControl(trailCtl, 'top-left');
    },
    /** null for default colors, or { key, prop, images, format, attribution } from recolor.js. */
    setRecolor(next) { recolor = next; if (loaded) applyRecolor(); },
    /** f: the chips' state (data.js NO_FILTER shape); ids: the resorts Hiring now and the pass chips keep, or null. */
    setFilter(f, ids) {
      facility = (f && f.facility) || 'all';
      onlyIds = Array.isArray(ids) ? ids : null;
      if (loaded) applyFilter();
    },
    resize() { map.resize(); },
  };
}

function hoverKey(t) {
  const source = { resort: 'resorts', cluster: 'clusters', state: 'states', housing: 'detail-housing', lift: 'detail-lifts', base: 'detail-base', stop: 'detail-transit-stops' }[t.kind];
  return { source, id: t.id };
}

function area(b) { return b ? (b[1][0] - b[0][0]) * (b[1][1] - b[0][1]) : Infinity; }

