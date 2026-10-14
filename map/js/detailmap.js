// Resort detail map layers (Phase 5): base area, lifts, housing, commutes, transit, drive time isochrones.
import { config } from './config.js';
import { transitSchedule, transitNote } from './labels.js';

const C = config.basemap.colors;
const EMPTY = { type: 'FeatureCollection', features: [] };

// Housing type colors: validated as a 3 color set (all pairs) on the dark map surface.
export const HOUSING_COLOR = { resort_owned: '#199e70', master_lease: '#9085e9', market: '#d95926' };
// Isochrones: one hue, light (closest) to dark (farthest).
export const ISO_COLOR = { 15: '#86b6ef', 30: '#5598e7', 45: '#2a78d6' };
// Commute modes share one ink color and differ by dash pattern.
export const COMMUTE_DASH = { drive: null, bus: [3, 2], walk: [0.5, 1.5], shuttle: [4, 1.5] };
const COMMUTE_INK = '#f2f4f8';
const TRANSIT_DEFAULT = '#c98500';

const fs = (key) => ['boolean', ['feature-state', key], false];

export const LAYER_GROUPS = {
  isochrones: ['detail-iso-fill', 'detail-iso-line'],
  transit: ['detail-transit-line', 'detail-transit-stop'],
  lifts: ['detail-lifts'],
  commute: Object.keys(COMMUTE_DASH).map((m) => `detail-commute-${m}`),
  housing: ['detail-housing-area', 'detail-housing-point'],
};

function hex(color) {
  if (!color) return TRANSIT_DEFAULT;
  const c = String(color).replace(/^#/, '');
  return /^[0-9a-f]{6}$/i.test(c) ? `#${c}` : TRANSIT_DEFAULT;
}

function isoColorExpr() {
  return ['case', ['<=', ['get', 'minutes'], 15], ISO_COLOR[15], ['<=', ['get', 'minutes'], 30], ISO_COLOR[30], ISO_COLOR[45]];
}

/** Add empty detail sources and layers. Call once after the style loads. */
export function addDetailLayers(map) {
  for (const id of ['detail-iso', 'detail-transit-routes', 'detail-transit-stops', 'detail-commute', 'detail-base']) {
    map.addSource(id, { type: 'geojson', data: EMPTY });
  }
  map.addSource('detail-lifts', { type: 'geojson', data: EMPTY, generateId: true });
  map.addSource('detail-housing', { type: 'geojson', data: EMPTY, promoteId: 'id' });

  const below = (id) => (map.getLayer(id) ? id : undefined);
  map.addLayer({
    id: 'detail-iso-fill', type: 'fill', source: 'detail-iso',
    layout: { 'fill-sort-key': ['-', 0, ['get', 'minutes']] },
    paint: { 'fill-color': isoColorExpr(), 'fill-opacity': 0.14 },
  }, below('clusters-fill'));
  map.addLayer({
    id: 'detail-iso-line', type: 'line', source: 'detail-iso',
    paint: { 'line-color': isoColorExpr(), 'line-opacity': 0.6, 'line-width': 1 },
  }, below('clusters-fill'));
  map.addLayer({
    id: 'detail-transit-line', type: 'line', source: 'detail-transit-routes',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': ['get', 'color'], 'line-width': 2.5, 'line-opacity': 0.85 },
  }, below('resorts-halo'));
  map.addLayer({
    id: 'detail-lifts', type: 'line', source: 'detail-lifts',
    layout: { 'line-cap': 'round' },
    paint: {
      'line-color': '#e7e9ee',
      'line-opacity': ['case', fs('hover'), 1, 0.7],
      'line-width': ['interpolate', ['linear'], ['zoom'], 10, ['case', fs('hover'), 3, 1], 14, ['case', fs('hover'), 3.5, 2]],
    },
  }, below('resorts-halo'));
  for (const [mode, dash] of Object.entries(COMMUTE_DASH)) {
    map.addLayer({
      id: `detail-commute-${mode}`, type: 'line', source: 'detail-commute',
      filter: ['all', ['==', ['get', 'mode'], mode], ['==', ['get', 'site_id'], '']],
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': COMMUTE_INK, 'line-width': 3, ...(dash ? { 'line-dasharray': dash } : {}) },
    }, below('resorts-halo'));
  }
  map.addLayer({
    id: 'detail-transit-stop', type: 'circle', source: 'detail-transit-stops',
    paint: { 'circle-radius': 3.5, 'circle-color': C.background, 'circle-stroke-color': '#e7e9ee', 'circle-stroke-width': 1.5 },
  });
  map.addLayer({
    id: 'detail-housing-area', type: 'circle', source: 'detail-housing',
    filter: ['==', ['get', 'precision'], 'neighborhood'],
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 12, 14, 42],
      'circle-color': ['match', ['get', 'type'], 'resort_owned', HOUSING_COLOR.resort_owned, 'master_lease', HOUSING_COLOR.master_lease, HOUSING_COLOR.market],
      'circle-opacity': ['case', fs('selected'), 0.45, 0.28],
      'circle-blur': 0.45,
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': ['case', fs('selected'), 2, fs('hover'), 1.5, 0],
      'circle-stroke-opacity': 0.9,
    },
  });
  map.addLayer({
    id: 'detail-housing-point', type: 'circle', source: 'detail-housing',
    filter: ['==', ['get', 'precision'], 'building'],
    paint: {
      'circle-radius': ['case', fs('selected'), 8, 6],
      'circle-color': ['match', ['get', 'type'], 'resort_owned', HOUSING_COLOR.resort_owned, 'master_lease', HOUSING_COLOR.master_lease, HOUSING_COLOR.market],
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': ['case', fs('selected'), 3, fs('hover'), 2.5, 1.5],
    },
  });
  map.addLayer({
    id: 'detail-base', type: 'circle', source: 'detail-base',
    paint: { 'circle-radius': 7, 'circle-color': '#ffffff', 'circle-stroke-color': C.background, 'circle-stroke-width': 3 },
  });
}

const fc = (features) => ({ type: 'FeatureCollection', features });
const pt = (lon, lat, properties) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [lon, lat] }, properties });

// Feeds of the detail file last passed to groupsWithData, for the transit legend (detailcontrols.js).
let legendFeeds = [];

/** [{ agencies: [{ name, url }], schedule, note }] for the transit legend of the current detail map. */
export function transitLegend() { return legendFeeds; }

/** Legend rows for a detail file's transit feeds. */
export function transitFeeds(detail) {
  const t = detail && detail.transit;
  if (!t) return [];
  return t.feeds.map((f) => ({ agencies: f.agencies, schedule: transitSchedule(f), note: transitNote(f) }));
}

/** Which layer groups have data in a detail file. */
export function groupsWithData(detail) {
  legendFeeds = transitFeeds(detail);
  if (!detail) return [];
  const out = [];
  if (detail.isochrones.length) out.push('isochrones');
  if (detail.transit && (detail.transit.routes.length || detail.transit.stops.length)) out.push('transit');
  if (detail.lifts.features.length) out.push('lifts');
  if (detail.housing.some((s) => s.commutes.some((c) => c.geometry))) out.push('commute');
  if (detail.housing.length) out.push('housing');
  return out;
}

/** Bounds covering the base area, lifts and housing (isochrones excluded, they can be large). */
export function detailBounds(detail) {
  const pts = [];
  if (detail.base_area) pts.push([detail.base_area.lon, detail.base_area.lat]);
  for (const s of detail.housing) pts.push([s.lon, s.lat]);
  for (const f of detail.lifts.features) {
    const lines = f.geometry.type === 'LineString' ? [f.geometry.coordinates] : f.geometry.coordinates;
    for (const l of lines) for (const p of l) pts.push(p);
  }
  if (!pts.length) return null;
  let b = [[Infinity, Infinity], [-Infinity, -Infinity]];
  for (const [x, y] of pts) b = [[Math.min(b[0][0], x), Math.min(b[0][1], y)], [Math.max(b[1][0], x), Math.max(b[1][1], y)]];
  // Keep at least a couple of km around a single point.
  const pad = 0.02;
  return [[b[0][0] - pad, b[0][1] - pad], [b[1][0] + pad, b[1][1] + pad]];
}

/** Bounds around the housing pins only (the "Show on the detail map" button), with room for the circles. */
export function housingBounds(detail) {
  const pts = ((detail && detail.housing) || []).map((s) => [s.lon, s.lat]);
  if (!pts.length) return null;
  let b = [[Infinity, Infinity], [-Infinity, -Infinity]];
  for (const [x, y] of pts) b = [[Math.min(b[0][0], x), Math.min(b[0][1], y)], [Math.max(b[1][0], x), Math.max(b[1][1], y)]];
  const pad = 0.01;
  return [[b[0][0] - pad, b[0][1] - pad], [b[1][0] + pad, b[1][1] + pad]];
}

export function setDetailData(map, detail) {
  const set = (id, data) => { const s = map.getSource(id); if (s) s.setData(data); };
  if (!detail) {
    for (const id of ['detail-iso', 'detail-transit-routes', 'detail-transit-stops', 'detail-commute', 'detail-base', 'detail-lifts', 'detail-housing']) set(id, EMPTY);
    return;
  }
  set('detail-iso', fc(detail.isochrones.map((i) => ({ type: 'Feature', geometry: i.geometry, properties: { minutes: i.minutes } }))));
  const t = detail.transit;
  const feedOf = new Map(t ? t.feeds.map((f) => [f.agency, f]) : []);
  set('detail-transit-routes', fc(t ? t.routes.map((r) => ({ type: 'Feature', geometry: r.geometry, properties: {
    id: r.id, color: hex(r.color), name: r.short_name || r.long_name || r.id,
    agency: r.agency_name || '', schedule: transitSchedule(feedOf.get(r.agency)) || '' } })) : []));
  set('detail-transit-stops', fc(t ? t.stops.map((s) => pt(s.lon, s.lat, { id: s.id, name: s.name })) : []));
  set('detail-lifts', fc(detail.lifts.features.map((f) => ({ type: 'Feature', geometry: f.geometry, properties: { name: f.properties.name || null, lift_type: f.properties.lift_type || null } }))));
  set('detail-housing', fc(detail.housing.map((s) => pt(s.lon, s.lat, { id: s.id, name: s.name, type: s.type, precision: s.precision }))));
  set('detail-commute', fc(detail.housing.flatMap((s) => s.commutes.filter((c) => c.geometry)
    .map((c) => ({ type: 'Feature', geometry: c.geometry, properties: { site_id: s.id, mode: c.mode } })))));
  set('detail-base', fc(detail.base_area ? [pt(detail.base_area.lon, detail.base_area.lat, { name: 'Base area' })] : []));
}

export function showSiteCommutes(map, siteId) {
  for (const mode of Object.keys(COMMUTE_DASH)) {
    const id = `detail-commute-${mode}`;
    if (map.getLayer(id)) map.setFilter(id, ['all', ['==', ['get', 'mode'], mode], ['==', ['get', 'site_id'], siteId || '']]);
  }
}

export function setGroupVisible(map, group, on) {
  for (const id of LAYER_GROUPS[group]) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
}
