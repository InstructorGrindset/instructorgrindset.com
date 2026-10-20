// View framing shared by the WebGL map (map.js) and the first frame preview (preview.js).
import { config } from './config.js';

/** Grow a bbox around its center to at least spanX by spanY degrees. */
export function minSpan(b, spanX, spanY) {
  if (!b) return b;
  const cx = (b[0][0] + b[1][0]) / 2; const cy = (b[0][1] + b[1][1]) / 2;
  const hx = Math.max((b[1][0] - b[0][0]) / 2, spanX / 2);
  const hy = Math.max((b[1][1] - b[0][1]) / 2, spanY / 2);
  return [[cx - hx, cy - hy], [cx + hx, cy + hy]];
}

/** Bounds and zoom cap for a view, without the resort detail map. */
export function viewFrame(index, view) {
  if (view.level === 'events') {
    // EVENTS mode: the national view, or the selected venue with its neighbours (index.events, set by events.js).
    const v = view.venueId && index.events ? index.events.venuePoint(view.venueId) : null;
    if (!v) return { b: config.nationalBounds, maxZoom: 5 };
    const d = v.precision === 'region' ? 0.6 : 0.22;
    return { b: [[v.lon - d * 1.3, v.lat - d], [v.lon + d * 1.3, v.lat + d]], maxZoom: v.precision === 'venue' ? 11 : 9 };
  }
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

/** Shrink padding that would leave less than 60 px of map. */
export function clampPadding(p, w, hgt) {
  const out = { ...p };
  const fix = (a, b, total) => {
    const over = out[a] + out[b] - (total - 60);
    if (over > 0) { const s = (total - 60) / (out[a] + out[b]); out[a] = Math.max(0, Math.floor(out[a] * s)); out[b] = Math.max(0, Math.floor(out[b] * s)); }
  };
  fix('left', 'right', w);
  fix('top', 'bottom', hgt);
  return out;
}

/** Circle radius in px by size rank (1..5) at a zoom, the same stops as the map's circle layer. */
export function resortRadius(rank, zoom) {
  const at = (base, step) => base + rank * step;
  const stops = [[3, at(1.6, 0.9)], [7, at(3, 1.5)], [11, at(5, 2.2)]];
  if (zoom <= stops[0][0]) return stops[0][1];
  for (let i = 1; i < stops.length; i++) {
    if (zoom <= stops[i][0]) {
      const [z0, r0] = stops[i - 1]; const [z1, r1] = stops[i];
      return r0 + ((zoom - z0) / (z1 - z0)) * (r1 - r0);
    }
  }
  return stops[stops.length - 1][1];
}

const TILE = 512;
const MAX_LAT = 85.051129;
const mercX = (lon) => (lon + 180) / 360;
const mercY = (lat) => {
  const s = Math.sin((Math.max(-MAX_LAT, Math.min(MAX_LAT, lat)) * Math.PI) / 180);
  return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI);
};
const lonOf = (x) => x * 360 - 180;
const latOf = (y) => (360 / Math.PI) * Math.atan(Math.exp((0.5 - y) * 2 * Math.PI)) - 90;

/**
 * The pan limit (config.pan.center): the camera center for a container of w by hgt px at this zoom such that the
 * middle of the open map area (the container minus `pad`) stays inside `box` [[west, south], [east, north]] and the
 * container stays between the poles. The box may reach past -180 degrees: the map draws world copies, so lng comes
 * wrapped, and a lng in the gap east of the box counts from whichever side of it is nearer.
 * Returns [lng, lat] (west of -180 when past the antimeridian), or null when the camera is already inside.
 */
export function panCenter(lng, lat, zoom, w, hgt, pad, box) {
  if (!w || !hgt || !Number.isFinite(lng) || !Number.isFinite(lat)) return null;
  const [[west, south], [east, north]] = box;
  const mid = (east + west + 360) / 2; // middle of the gap between the box's east edge and its west edge a world on
  let lon = lng;
  while (lon > mid) lon -= 360;
  while (lon <= mid - 360) lon += 360;
  const ws = TILE * 2 ** zoom;
  const p = pad || {};
  const dx = ((p.left || 0) - (p.right || 0)) / 2;
  const dy = ((p.top || 0) - (p.bottom || 0)) / 2;
  const cx = mercX(lon) * ws; const cy = mercY(lat) * ws;
  const vx = Math.min(mercX(east) * ws, Math.max(mercX(west) * ws, cx + dx));
  const vy = Math.min(mercY(south) * ws, Math.max(mercY(north) * ws, cy + dy));
  const x = vx - dx;
  let y = vy - dy;
  y = ws >= hgt ? Math.min(ws - hgt / 2, Math.max(hgt / 2, y)) : ws / 2;
  if (Math.abs(x - cx) < 1e-6 && Math.abs(y - cy) < 1e-6) return null;
  return [lonOf(x / ws), latOf(y / ws)];
}

/** The zoom out limit for a screen w px wide: pan.minZoom, or more where the screen would span more than maxWorldShare of the world. */
export function panMinZoom(w, pan) {
  if (!w) return null;
  return Math.max(pan.minZoom, Math.log2(w / (TILE * pan.maxWorldShare)));
}
