// EVENTS mode on the WebGL map (ROADMAP item 22): one glowing dot per event venue, colored by its main discipline
// group (config events.groups, from events.json), sized by the number of events the filters keep, with a count label
// from state zooms and a ring on the selected venue. Loaded by events.js once the WebGL map runs in EVENTS mode.
// Venues outside the United States are never dots (list only). map.js hides the resort layers and hands picking to
// the hooks given to mapCtl.setMode.
import { config } from './config.js';

const SOURCE = 'event-venues';
export const LAYERS = ['event-venues-glow', 'event-venues', 'event-venues-halo', 'event-venues-count'];
const fs = (key) => ['boolean', ['feature-state', key], false];
const SQRT_N = ['sqrt', ['max', 1, ['get', 'count']]];
/** Dot radius in px by the number of events, scaled with zoom (events.js previewRadius is the same curve at zoom 3.5). */
const RADIUS = ['interpolate', ['linear'], ['zoom'],
  3, ['+', 2.4, ['*', 2.1, SQRT_N]], 7, ['+', 4, ['*', 2.8, SQRT_N]], 11, ['+', 6, ['*', 3.4, SQRT_N]]];
const GLOW = ['interpolate', ['linear'], ['zoom'],
  3, ['+', 6, ['*', 3.4, SQRT_N]], 7, ['+', 9, ['*', 4.2, SQRT_N]], 11, ['+', 12, ['*', 5, SQRT_N]]];
const HALO = ['interpolate', ['linear'], ['zoom'],
  3, ['+', 6.4, ['*', 2.1, SQRT_N]], 7, ['+', 8, ['*', 2.8, SQRT_N]], 11, ['+', 10, ['*', 3.4, SQRT_N]]];

const FONT = "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif";
/** A count label: white digits on a near black tag with a thin border (canvas drawn; the style has no glyphs). */
function countImage(n) {
  const ratio = 2;
  const text = String(n);
  const c = document.createElement('canvas');
  const ctx = c.getContext('2d', { willReadFrequently: true });
  const font = `700 ${10 * ratio}px ${FONT}`;
  ctx.font = font;
  const w = Math.ceil(ctx.measureText(text).width + 8 * ratio);
  c.width = Math.max(w, 16 * ratio);
  c.height = 15 * ratio;
  ctx.font = font;
  ctx.fillStyle = 'rgba(10,10,10,0.92)';
  ctx.strokeStyle = 'rgba(232,232,232,0.55)';
  ctx.lineWidth = ratio;
  ctx.beginPath();
  ctx.rect(ratio / 2, ratio / 2, c.width - ratio, c.height - ratio);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = '#ffffff';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, c.width / 2, c.height / 2 + ratio / 2);
  const d = ctx.getImageData(0, 0, c.width, c.height);
  return { width: c.width, height: c.height, data: new Uint8Array(d.data.buffer) };
}

/**
 * Add the venue layers to a running map (mapCtl from map.js). Returns { setData(fc), setSelected(id), setVisible(on),
 * pick(point, box) }. fc: venue Points with { id, name, count, color, tip }.
 */
export function attachVenueLayers(mapCtl) {
  const map = mapCtl.map;
  let selected = null;
  let visible = true;
  const empty = { type: 'FeatureCollection', features: [] };
  if (!map.getSource(SOURCE)) {
    map.addSource(SOURCE, { type: 'geojson', data: empty, promoteId: 'id' });
    map.addLayer({
      id: 'event-venues-glow', type: 'circle', source: SOURCE,
      layout: { 'circle-sort-key': ['get', 'count'] },
      paint: { 'circle-radius': GLOW, 'circle-color': ['get', 'color'], 'circle-blur': 1, 'circle-opacity': 0.55, 'circle-pitch-alignment': 'map' },
    });
    map.addLayer({
      id: 'event-venues-halo', type: 'circle', source: SOURCE, filter: ['==', ['get', 'id'], ''],
      paint: { 'circle-radius': HALO, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': config.basemap.colors.ring, 'circle-stroke-width': 2 },
    });
    map.addLayer({
      id: 'event-venues', type: 'circle', source: SOURCE,
      layout: { 'circle-sort-key': ['get', 'count'] },
      paint: {
        'circle-radius': RADIUS,
        'circle-color': ['get', 'color'],
        'circle-stroke-color': ['case', fs('hover'), config.basemap.colors.ring, '#0a0a0a'],
        'circle-stroke-width': ['case', fs('hover'), 2.5, 1],
        'circle-opacity': 0.95,
      },
    });
    map.addLayer({
      id: 'event-venues-count', type: 'symbol', source: SOURCE, minzoom: 5, filter: ['>=', ['get', 'count'], 2],
      layout: {
        'icon-image': ['concat', 'evc-', ['to-string', ['get', 'count']]],
        'icon-anchor': 'bottom-left', 'icon-offset': [3, -3], 'icon-allow-overlap': false, 'icon-padding': 1,
        'symbol-sort-key': ['-', 0, ['get', 'count']],
      },
    });
  }
  function images(fc) {
    for (const f of fc.features) {
      const n = f.properties.count;
      if (n >= 2 && !map.hasImage(`evc-${n}`)) map.addImage(`evc-${n}`, countImage(n), { pixelRatio: 2 });
    }
  }
  return {
    setData(fc) {
      images(fc);
      const src = map.getSource(SOURCE);
      if (src) src.setData(fc);
    },
    setSelected(id) {
      selected = id || null;
      if (map.getLayer('event-venues-halo')) map.setFilter('event-venues-halo', ['==', ['get', 'id'], selected || '']);
    },
    setVisible(on) {
      visible = Boolean(on);
      for (const id of LAYERS) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
    },
    /** The venue under the pointer (the most events first), for map.js pick in EVENTS mode. */
    pick(point, box) {
      if (!visible || !map.getLayer('event-venues')) return null;
      const hits = map.queryRenderedFeatures(box, { layers: ['event-venues'] });
      if (!hits.length) return null;
      const f = hits.sort((a, b) => b.properties.count - a.properties.count)[0];
      return { kind: 'venue', id: f.properties.id, name: f.properties.name, tip: f.properties.tip };
    },
  };
}
