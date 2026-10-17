// First map frame: a 2D picture (inline SVG) of the clusters and resorts for the current view, from the slim
// index, shown until the WebGL map starts. It uses the WebGL map's framing and colors and is removed when the
// WebGL map is ready. It is a picture only; the panel and list view carry the controls.
// SVG rather than a 2D canvas: the first canvas draw waits on the GPU process (a synchronous GPU channel setup)
// while it is busy with the page's first frame, which held the main thread for 100 to 300 ms. SVG is drawn by
// the compositor off the main thread. See docs/map/ARCHITECTURE.md "Performance budget".
// The satellite style underlay (underlay.js) joins the picture after the first paint (addPreviewUnderlay). In MAP
// mode (the default) it is darkened with the same raster math the WebGL map applies (toneMatrix), so the switch from
// the picture to the map shows no jump. Resort dots get a soft halo from one blur filter on their group.
import { config } from './config.js';
import { viewFrame, clampPadding, resortRadius } from './camera.js';

const C = config.basemap.colors;

/**
 * MapLibre's raster paint (raster-saturation, raster-contrast, raster-brightness-min and -max) as one SVG
 * feColorMatrix: the shader's three steps are linear, rgb + (mean - rgb) s, then (x - 0.5) c + 0.5, then
 * lo + (hi - lo) x. Returns the 20 values, or null for a neutral paint.
 */
export function toneMatrix(paint) {
  const sat = paint['raster-saturation'] || 0;
  const con = paint['raster-contrast'] || 0;
  const lo = paint['raster-brightness-min'] ?? 0;
  const hi = paint['raster-brightness-max'] ?? 1;
  if (!sat && !con && lo === 0 && hi === 1) return null;
  const s = sat > 0 ? 1 - 1 / (1.001 - sat) : -sat;
  const c = con > 0 ? 1 / (1 - con) : 1 + con;
  const k = (hi - lo) * c;
  const b = (hi - lo) * 0.5 * (1 - c) + lo;
  const own = k * (1 - s + s / 3);
  const other = k * (s / 3);
  return [own, other, other, 0, b, other, own, other, 0, b, other, other, own, 0, b, 0, 0, 0, 1, 0];
}
const toneOf = (name) => config.basemap.tone[name] || config.basemap.tone[config.basemap.defaultTone];
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
 * The images a picture draws: [{ url, box: [x0, y0, x1, y1] }], each the smallest copy that covers it. Images off
 * the picture are skipped (Alaska, up and to the left of the national view, often is).
 */
function underlayPlan(cam, underlay) {
  const { px } = cam;
  const r = underlay.rasters;
  const dpr = window.devicePixelRatio || 1;
  const out = [];
  for (const raster of [r.lower48, r.alaska]) {
    if (!raster) continue;
    const c = raster.coordinates;
    const [x0, y0] = px(c[0][0], c[0][1]);
    const [x1, y1] = px(c[2][0], c[2][1]);
    if (Math.min(cam.w, x1) <= Math.max(0, x0) || Math.min(cam.hgt, y1) <= Math.max(0, y0)) continue;
    // The quarter size copy up to 2x upscaled: plenty under a muted picture, and it skips the full image's decode.
    const src = raster.small && (x1 - x0) * dpr <= raster.small.width * 2 ? raster.small : raster;
    out.push({ url: src.url, box: [x0, y0, x1, y1] });
  }
  return out;
}

/** The image URLs this picture would draw (none for a resort picture), to load and decode ahead. */
export function previewUnderlayUrls(svg, underlay) {
  const cam = svg && cams.get(svg);
  return cam && underlay && cam.zoom <= UNDERLAY_MAX_ZOOM ? underlayPlan(cam, underlay).map((p) => p.url) : [];
}

/**
 * Draw the underlay (underlay.js) under the clusters and dots of a picture, in its Web Mercator projection: the
 * lower 48 and Alaska, each at its true place. Each image is an SVG feImage on a rect clipped to the view: a picture
 * drawn by a filter is never a Largest Contentful Paint candidate (an SVG image element is), so the late image leaves
 * LCP alone. No fade: each frame of one would raster the filter again.
 */
export function addPreviewUnderlay(svg, underlay) {
  const cam = cams.get(svg);
  if (!cam || !underlay || cam.zoom > UNDERLAY_MAX_ZOOM || svg.querySelector('.map-preview-underlay')) return;
  const group = el('g', { class: 'map-preview-underlay' });
  const matrix = toneMatrix(toneOf(cam.tone).underlay);
  for (const { url, box: [x0, y0, x1, y1] } of underlayPlan(cam, underlay)) {
    const id = `map-preview-underlay-${++filterSeq}`;
    const cx0 = Math.max(0, x0); const cy0 = Math.max(0, y0);
    const box = { x: n1(cx0), y: n1(cy0), width: n1(Math.min(cam.w, x1) - cx0), height: n1(Math.min(cam.hgt, y1) - cy0) };
    const f = el('filter', { id, filterUnits: 'userSpaceOnUse', primitiveUnits: 'userSpaceOnUse', 'color-interpolation-filters': 'sRGB', ...box });
    f.append(el('feImage', { href: url, x: n1(x0), y: n1(y0), width: n1(x1 - x0), height: n1(y1 - y0), preserveAspectRatio: 'none' }));
    if (matrix) f.append(el('feColorMatrix', { type: 'matrix', values: matrix.map((v) => Math.round(v * 1e4) / 1e4).join(' ') }));
    group.append(f, el('rect', { ...box, fill: cam.bgColor, filter: `url(#${id})` }));
  }
  if (group.childNodes.length) cam.bg.after(group);
}

/**
 * keep (optional): the filter chips' predicate; resorts it rejects are left out, as on the WebGL map.
 * underlay (optional): the loaded underlay (underlay.js), drawn at once.
 * tone: 'map' (darkened, the default) or 'satellite' (config.basemap.tone).
 */
export function drawPreview(container, index, view, padding, keep = null, underlay = null, tone = config.basemap.defaultTone) {
  const w = container.clientWidth; const hgt = container.clientHeight;
  if (!w || !hgt) return null;
  const { b, maxZoom } = viewFrame(index, view);
  if (!b) return null;
  const cam = fitCamera(b, clampPadding(padding, w, hgt), w, hgt, maxZoom);
  const px = (lon, lat) => [mx(lon) * cam.world - cam.cx + w / 2, my(lat) * cam.world - cam.cy + hgt / 2];

  const svg = el('svg', { class: 'map-preview', 'aria-hidden': 'true', focusable: 'false', viewBox: `0 0 ${w} ${hgt}`, preserveAspectRatio: 'none' });
  const bgColor = toneOf(tone).background;
  const bg = el('rect', { width: w, height: hgt, fill: bgColor });
  svg.append(bg);

  const cluster = view.clusterId ? index.clusters.get(view.clusterId) : null;
  const state = view.stateCode ? index.states.get(view.stateCode) : null;
  let focus = null;
  if (cluster) focus = new Set(cluster.resort_ids);
  else if (state) {
    focus = new Set();
    for (const cid of state.cluster_ids) for (const id of index.clusters.get(cid).resort_ids) focus.add(id);
  }

  // Everything at its true place, Alaska included, as on the WebGL map. One path per cluster: fill then outline,
  // each at its own opacity, as the canvas drew them.
  const clusterGroup = el('g', { fill: C.cluster, stroke: C.cluster, 'fill-rule': 'evenodd', 'stroke-linejoin': 'miter' });
  for (const c of index.clusters.values()) {
    if (c.fallback) continue;
    const g = c.geometry;
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

  // Tier B first, Tier A on top, as on the map. One blur of the whole group under it gives every dot its halo.
  const glowId = `map-preview-glow-${++filterSeq}`;
  const glow = el('filter', { id: glowId, x: '-2%', y: '-2%', width: '104%', height: '104%', 'color-interpolation-filters': 'sRGB' });
  glow.append(el('feGaussianBlur', { in: 'SourceGraphic', stdDeviation: '2.4', result: 'halo' }),
    el('feColorMatrix', { in: 'halo', type: 'matrix', values: '1 0 0 0 0 0 1 0 0 0 0 0 1 0 0 0 0 0 2.2 0', result: 'bright' }));
  const merge = el('feMerge', null);
  merge.append(el('feMergeNode', { in: 'bright' }), el('feMergeNode', { in: 'SourceGraphic' }));
  glow.append(merge);
  svg.append(glow);
  const dots = el('g', { filter: `url(#${glowId})` });
  const list = [...index.resorts.values()].sort((a, b2) => (a.tier === 'A') - (b2.tier === 'A'));
  for (const r of list) {
    if (keep && !keep(r)) continue;
    const [x, y] = px(r.lon, r.lat);
    if (x < -10 || y < -10 || x > w + 10 || y > hgt + 10) continue;
    const dim = focus && !focus.has(r.id);
    const a = r.tier === 'A';
    const alpha = dim ? 0.3 : (a ? 1 : 0.85);
    dots.append(el('circle', { cx: n1(x), cy: n1(y), r: n1(resortRadius(r.size_rank, cam.zoom)), fill: a ? C.tierA : C.tierB,
      stroke: a ? C.tierAStroke : bgColor, 'stroke-width': a ? 1.2 : 1, 'fill-opacity': alpha, 'stroke-opacity': alpha }));
  }
  svg.append(dots);
  cams.set(svg, { px, w, hgt, bg, bgColor, tone, zoom: cam.zoom });
  if (underlay) addPreviewUnderlay(svg, underlay);
  container.append(svg);
  performance.mark('careermap:preview');
  return svg;
}
