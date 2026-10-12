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
