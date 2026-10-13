// First map frame: a 2D picture (inline SVG) of the clusters and resorts for the current view, from the slim
// index, shown until the WebGL map starts. It uses the WebGL map's framing and colors and is removed when the
// WebGL map is ready. It is a picture only; the panel and list view carry the controls.
// SVG rather than a 2D canvas: the first canvas draw waits on the GPU process (a synchronous GPU channel setup)
// while it is busy with the page's first frame, which held the main thread for 100 to 300 ms. SVG is drawn by
// the compositor off the main thread. See docs/map/ARCHITECTURE.md "Performance budget".
import { config } from './config.js';
import { viewFrame, clampPadding, resortRadius } from './camera.js';
import { INSET_STATE, insetGeometry, insetFrameBounds, toInset } from './inset.js';

const C = config.basemap.colors;
const TILE = 512;
const NS = 'http://www.w3.org/2000/svg';

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

function el(tag, attrs) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}

const n1 = (v) => Math.round(v * 10) / 10;

export function drawPreview(container, index, view, padding) {
  const w = container.clientWidth; const hgt = container.clientHeight;
  if (!w || !hgt) return null;
  const { b, maxZoom } = viewFrame(index, view);
  if (!b) return null;
  const cam = fitCamera(b, clampPadding(padding, w, hgt), w, hgt, maxZoom);
  const px = (lon, lat) => [mx(lon) * cam.world - cam.cx + w / 2, my(lat) * cam.world - cam.cy + hgt / 2];

  const svg = el('svg', { class: 'map-preview', 'aria-hidden': 'true', focusable: 'false', viewBox: `0 0 ${w} ${hgt}`, preserveAspectRatio: 'none' });
  svg.append(el('rect', { width: w, height: hgt, fill: C.background }));

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
    svg.append(el('rect', { x: n1(x0), y: n1(y0), width: n1(x1 - x0), height: n1(y1 - y0), fill: 'none', stroke: C.stateLine, 'stroke-width': 1, 'stroke-dasharray': '3 2' }));
  }
  // One path per cluster: fill then outline, each at its own opacity, as the canvas drew them.
  const clusterGroup = el('g', { fill: C.cluster, stroke: C.cluster, 'fill-rule': 'evenodd', 'stroke-linejoin': 'miter' });
  for (const c of index.clusters.values()) {
    if (c.fallback) continue;
    const ak = c.states.every((x) => x === INSET_STATE);
    const g = national && ak ? insetGeometry(c.geometry) : c.geometry;
    if (!g) continue;
    const polys = g.type === 'Polygon' ? [g.coordinates] : g.coordinates;
    let d = '';
    for (const poly of polys) {
      for (const ring of poly) {
        ring.forEach(([lon, lat], i) => { const [x, y] = px(lon, lat); d += `${i ? 'L' : 'M'}${n1(x)} ${n1(y)}`; });
        d += 'Z';
      }
    }
    const on = !cluster || c.id === cluster.id;
    clusterGroup.append(el('path', { d, 'fill-opacity': on ? (cluster ? 0.1 : 0.08) : 0.03, 'stroke-opacity': on ? (cluster ? 1 : 0.5) : 0.2, 'stroke-width': cluster && on ? 2.2 : 1 }));
  }
  svg.append(clusterGroup);

  // Tier B first, Tier A on top, as on the map.
  const dots = el('g', null);
  const list = [...index.resorts.values()].sort((a, b2) => (a.tier === 'A') - (b2.tier === 'A'));
  for (const r of list) {
    const [x, y] = national && r.state === INSET_STATE ? px(...toInset(r.lon, r.lat)) : px(r.lon, r.lat);
    if (x < -10 || y < -10 || x > w + 10 || y > hgt + 10) continue;
    const dim = focus && !focus.has(r.id);
    const a = r.tier === 'A';
    const alpha = dim ? 0.3 : (a ? 1 : 0.85);
    dots.append(el('circle', { cx: n1(x), cy: n1(y), r: n1(resortRadius(r.size_rank, cam.zoom)), fill: a ? C.tierA : C.tierB,
      stroke: a ? '#fff4dc' : C.background, 'stroke-width': a ? 1.5 : 1, 'fill-opacity': alpha, 'stroke-opacity': alpha }));
  }
  svg.append(dots);
  container.append(svg);
  performance.mark('careermap:preview');
  return svg;
}
