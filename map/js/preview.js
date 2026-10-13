// First map frame: a 2D canvas drawing of the clusters and resorts for the current view, from the slim
// index, shown while MapLibre downloads and starts WebGL. It uses the WebGL map's framing and colors and is
// removed when the WebGL map fires `load`. It is a picture only; the panel and list view carry the controls.
import { config } from './config.js';
import { viewFrame, clampPadding, resortRadius } from './camera.js';
import { INSET_STATE, insetGeometry, insetFrameBounds, toInset } from './inset.js';

const C = config.basemap.colors;
const TILE = 512;

const mx = (lon) => (lon + 180) / 360;
const my = (lat) => {
  const s = Math.sin((Math.max(-85, Math.min(85, lat)) * Math.PI) / 180);
  return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI);
};

/** Center and zoom like MapLibre's fitBounds for these bounds, padding and zoom cap. */
export function fitCamera(b, pad, w, hgt, maxZoom) {
  const x0 = mx(b[0][0]); const x1 = mx(b[1][0]);
  const y0 = my(b[1][1]); const y1 = my(b[0][1]);
  const availW = Math.max(1, w - pad.left - pad.right);
  const availH = Math.max(1, hgt - pad.top - pad.bottom);
  const zoom = Math.min(maxZoom, Math.log2(Math.min(availW / ((x1 - x0) * TILE), availH / ((y1 - y0) * TILE))));
  const world = TILE * 2 ** zoom;
  const cx = ((x0 + x1) / 2) * world - (pad.left - pad.right) / 2;
  const cy = ((y0 + y1) / 2) * world - (pad.top - pad.bottom) / 2;
  return { zoom, world, cx, cy };
}

export function drawPreview(container, index, view, padding) {
  const w = container.clientWidth; const hgt = container.clientHeight;
  if (!w || !hgt) return null;
  const { b, maxZoom } = viewFrame(index, view);
  if (!b) return null;
  const cam = fitCamera(b, clampPadding(padding, w, hgt), w, hgt, maxZoom);
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const canvas = document.createElement('canvas');
  canvas.className = 'map-preview';
  canvas.setAttribute('aria-hidden', 'true');
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(hgt * dpr);
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  const px = (lon, lat) => [mx(lon) * cam.world - cam.cx + w / 2, my(lat) * cam.world - cam.cy + hgt / 2];

  ctx.fillStyle = C.background;
  ctx.fillRect(0, 0, w, hgt);

  const cluster = view.clusterId ? index.clusters.get(view.clusterId) : null;
  const state = view.stateCode ? index.states.get(view.stateCode) : null;
  let focus = null;
  if (cluster) focus = new Set(cluster.resort_ids);
  else if (state) {
    focus = new Set();
    for (const cid of state.cluster_ids) for (const id of index.clusters.get(cid).resort_ids) focus.add(id);
  }

  // National view: Alaska draws in the lower left inset, as on the WebGL map.
  const national = view.level === 'national';
  if (national) {
    const [[fw, fs], [fe, fn]] = insetFrameBounds();
    const [x0, y0] = px(fw, fn); const [x1, y1] = px(fe, fs);
    ctx.globalAlpha = 1;
    ctx.strokeStyle = C.stateLine;
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 2]);
    ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);
    ctx.setLineDash([]);
  }
  for (const c of index.clusters.values()) {
    if (c.fallback) continue;
    const ak = c.states.every((x) => x === INSET_STATE);
    const g = national && ak ? insetGeometry(c.geometry) : c.geometry;
    if (!g) continue;
    const polys = g.type === 'Polygon' ? [g.coordinates] : g.coordinates;
    ctx.beginPath();
    for (const poly of polys) {
      for (const ring of poly) {
        ring.forEach(([lon, lat], i) => { const [x, y] = px(lon, lat); if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
        ctx.closePath();
      }
    }
    const on = !cluster || c.id === cluster.id;
    ctx.globalAlpha = on ? (cluster ? 0.1 : 0.08) : 0.03;
    ctx.fillStyle = C.cluster;
    ctx.fill('evenodd');
    ctx.globalAlpha = on ? (cluster ? 1 : 0.5) : 0.2;
    ctx.strokeStyle = C.cluster;
    ctx.lineWidth = cluster && on ? 2.2 : 1;
    ctx.stroke();
  }

  // Tier B first, Tier A on top, as on the map.
  const list = [...index.resorts.values()].sort((a, b2) => (a.tier === 'A') - (b2.tier === 'A'));
  for (const r of list) {
    const [x, y] = national && r.state === INSET_STATE ? px(...toInset(r.lon, r.lat)) : px(r.lon, r.lat);
    if (x < -10 || y < -10 || x > w + 10 || y > hgt + 10) continue;
    const dim = focus && !focus.has(r.id);
    const a = r.tier === 'A';
    ctx.globalAlpha = dim ? 0.3 : (a ? 1 : 0.85);
    ctx.beginPath();
    ctx.arc(x, y, resortRadius(r.size_rank, cam.zoom), 0, Math.PI * 2);
    ctx.fillStyle = a ? C.tierA : C.tierB;
    ctx.fill();
    ctx.lineWidth = a ? 1.5 : 1;
    ctx.strokeStyle = a ? '#fff4dc' : C.background;
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
  container.append(canvas);
  performance.mark('careermap:preview');
  return canvas;
}
