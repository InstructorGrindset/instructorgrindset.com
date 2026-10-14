// First map frame: a 2D picture (inline SVG) of the clusters and resorts for the current view, from the slim
// index, shown until the WebGL map starts. It uses the WebGL map's framing and colors and is removed when the
// WebGL map is ready. It is a picture only; the panel and list view carry the controls.
// SVG rather than a 2D canvas: the first canvas draw waits on the GPU process (a synchronous GPU channel setup)
// while it is busy with the page's first frame, which held the main thread for 100 to 300 ms. SVG is drawn by
// the compositor off the main thread. See docs/map/ARCHITECTURE.md "Performance budget".
// The satellite style underlay (underlay.js) joins the picture after the first paint (addPreviewUnderlay).
import { config, prefersReducedMotion } from './config.js';
import { viewFrame, clampPadding, resortRadius } from './camera.js';
import { INSET_STATE, insetGeometry, insetFrameBounds, toInset, insetCorners } from './inset.js';

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

// Each picture's projection, for adding the underlay later.
const cams = new WeakMap();
let filterSeq = 0;
// The picture draws the underlay up to cluster zooms. Closer in it is a blur (58 px per degree upscaled 30 times at
// a resort), so resort pictures skip it and its download; the WebGL map's USGS imagery takes over there.
const UNDERLAY_MAX_ZOOM = 9;

/** True when this picture draws the underlay (its zoom is national to cluster). */
export function wantsUnderlay(svg) {
  const cam = svg && cams.get(svg);
  return Boolean(cam && cam.zoom <= UNDERLAY_MAX_ZOOM);
}

/**
 * Draw the underlay (underlay.js) under the clusters and dots of a picture, in its Web Mercator projection: the
 * lower 48, Alaska at its true place or (national view) in the inset on its dark panel. Each image is an SVG feImage
 * on a rect clipped to the view: a picture drawn by a filter is never a Largest Contentful Paint candidate (an SVG
 * image element is), so the late image leaves LCP alone. fade: ease it in (skipped for reduced motion).
 */
export function addPreviewUnderlay(svg, underlay, { fade = false } = {}) {
  const cam = cams.get(svg);
  if (!cam || !underlay || cam.zoom > UNDERLAY_MAX_ZOOM || svg.querySelector('.map-preview-underlay')) return;
  const { px, w, hgt, national, bg, panel } = cam;
  const r = underlay.rasters;
  const group = (items) => {
    const g = el('g', { class: 'map-preview-underlay' });
    for (const [raster, corners] of items) {
      if (!raster) continue;
      const [x0, y0] = px(corners[0][0], corners[0][1]);
      const [x1, y1] = px(corners[2][0], corners[2][1]);
      const cx0 = Math.max(0, x0); const cy0 = Math.max(0, y0);
      const cx1 = Math.min(w, x1); const cy1 = Math.min(hgt, y1);
      if (cx1 <= cx0 || cy1 <= cy0) continue;
      const id = `map-preview-underlay-${++filterSeq}`;
      const box = { x: n1(cx0), y: n1(cy0), width: n1(cx1 - cx0), height: n1(cy1 - cy0) };
      const f = el('filter', { id, filterUnits: 'userSpaceOnUse', primitiveUnits: 'userSpaceOnUse', 'color-interpolation-filters': 'sRGB', ...box });
      f.append(el('feImage', { href: raster.url, x: n1(x0), y: n1(y0), width: n1(x1 - x0), height: n1(y1 - y0), preserveAspectRatio: 'none' }));
      g.append(f, el('rect', { ...box, fill: C.background, filter: `url(#${id})` }));
    }
    return g.childNodes.length ? g : null;
  };
  const base = group([[r.lower48, r.lower48 && r.lower48.coordinates], [!national && r.alaska, r.alaska && r.alaska.coordinates]]);
  const inset = national && panel && r.alaska ? group([[r.alaska, insetCorners(r.alaska.coordinates)]]) : null;
  for (const [g, after] of [[base, bg], [inset, panel]]) {
    if (!g) continue;
    after.after(g);
    if (fade && !prefersReducedMotion()) {
      g.style.opacity = '0';
      g.style.transition = 'opacity 450ms ease-out';
      requestAnimationFrame(() => requestAnimationFrame(() => { g.style.opacity = '1'; }));
    }
  }
}

/**
 * keep (optional): the filter chips' predicate; resorts it rejects are left out, as on the WebGL map.
 * underlay (optional): the loaded underlay (underlay.js), drawn at once.
 */
export function drawPreview(container, index, view, padding, keep = null, underlay = null) {
  const w = container.clientWidth; const hgt = container.clientHeight;
  if (!w || !hgt) return null;
  const { b, maxZoom } = viewFrame(index, view);
  if (!b) return null;
  const cam = fitCamera(b, clampPadding(padding, w, hgt), w, hgt, maxZoom);
  const px = (lon, lat) => [mx(lon) * cam.world - cam.cx + w / 2, my(lat) * cam.world - cam.cy + hgt / 2];

  const svg = el('svg', { class: 'map-preview', 'aria-hidden': 'true', focusable: 'false', viewBox: `0 0 ${w} ${hgt}`, preserveAspectRatio: 'none' });
  const bg = el('rect', { width: w, height: hgt, fill: C.background });
  svg.append(bg);

  const cluster = view.clusterId ? index.clusters.get(view.clusterId) : null;
  const state = view.stateCode ? index.states.get(view.stateCode) : null;
  let focus = null;
  if (cluster) focus = new Set(cluster.resort_ids);
  else if (state) {
    focus = new Set();
    for (const cid of state.cluster_ids) for (const id of index.clusters.get(cid).resort_ids) focus.add(id);
  }

  // National view: Alaska draws in the lower left inset on its own dark panel, as on the WebGL map.
  const national = view.level === 'national';
  let panel = null;
  if (national) {
    const [[fw, fs], [fe, fn]] = insetFrameBounds();
    const [x0, y0] = px(fw, fn); const [x1, y1] = px(fe, fs);
    const box = { x: n1(x0), y: n1(y0), width: n1(x1 - x0), height: n1(y1 - y0) };
    panel = el('rect', { ...box, fill: C.background, 'fill-opacity': 0.92 });
    svg.append(panel, el('rect', { ...box, fill: 'none', stroke: C.stateLine, 'stroke-width': 1, 'stroke-dasharray': '3 2' }));
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
    if (keep && !keep(r)) continue;
    const [x, y] = national && r.state === INSET_STATE ? px(...toInset(r.lon, r.lat)) : px(r.lon, r.lat);
    if (x < -10 || y < -10 || x > w + 10 || y > hgt + 10) continue;
    const dim = focus && !focus.has(r.id);
    const a = r.tier === 'A';
    const alpha = dim ? 0.3 : (a ? 1 : 0.85);
    dots.append(el('circle', { cx: n1(x), cy: n1(y), r: n1(resortRadius(r.size_rank, cam.zoom)), fill: a ? C.tierA : C.tierB,
      stroke: a ? '#fff4dc' : C.background, 'stroke-width': a ? 1.5 : 1, 'fill-opacity': alpha, 'stroke-opacity': alpha }));
  }
  svg.append(dots);
  cams.set(svg, { px, w, hgt, national, bg, panel, zoom: cam.zoom });
  if (underlay) addPreviewUnderlay(svg, underlay);
  container.append(svg);
  performance.mark('careermap:preview');
  return svg;
}
