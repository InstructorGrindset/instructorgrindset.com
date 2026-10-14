// Heat surfaces for the map color modes (lucrativeness, season length, snowfall).
//
// Settings come from definitions.json `heat` (config/definitions.yaml); the method is in docs/map/ARCHITECTURE.md,
// "Heat maps", and pipeline/heat.py is the Python twin that precomputes the snowfall and season length rasters
// (pipeline/heat_lucrativeness.py the lucrativeness ones, with legend_scale as the twin of legendScale below).
// This module draws the fallback when a raster file is missing.
// * Color: classed modes (`breaks`, `colors`) take the color of the value's class; the others use the ramp.
// * Field: adaptive modified Shepard interpolation over the resorts with a value, merged first within decluster_km
//   so a cluster of hills counts once. At a point, take the `neighbors` nearest and the distance R to the next one;
//   w = ((R - d) / (R * max(d, min_distance_km)))^2. Weights reach zero just as a resort leaves the set, so the
//   surface is continuous and covers the whole map. It is solved on every grid_step_px pixel, bilinear between.
// * Opacity: max_alpha within full_opacity_km of the nearest resort with data, a smooth fade to floor_alpha at
//   fade_end_km, floor_alpha beyond; with fade_to "neutral" the color blends into the neutral grey as it fades.
//   Inside states with no data (season length) the color ends no_data_reach_km past their border. Density never
//   raises opacity or value.
// * One image per region (lower 48, Alaska), rows evenly spaced in Web Mercator (MapLibre image sources expect
//   that), clipped to the state outlines.
// Interpolated values never show as numbers: tooltips and the panel show only the resorts' sourced values.

const KM_PER_DEG_LAT = 110.57;
const KM_PER_DEG_LON = 111.32;
const RAD = Math.PI / 180;
const HIDDEN = new Set(['AS', 'GU', 'MP', 'PR', 'VI', 'HI', 'DC']);
const REGION_STATES = { lower48: (code) => !HIDDEN.has(code) && code !== 'AK', alaska: (code) => code === 'AK' };

export const mercY = (lat) => Math.log(Math.tan(Math.PI / 4 + (Math.max(-85, Math.min(85, lat)) * RAD) / 2));
const latOf = (y) => (2 * Math.atan(Math.exp(y)) - Math.PI / 2) / RAD;

const hexRgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));

/** Ramp helpers for the config colors: rgb(t) for t in [0, 1] (linear between stops in sRGB) and the CSS gradient. */
export function makeRamp(colors) {
  const RGB = colors.map(hexRgb);
  return {
    colors,
    rgb(t) {
      const x = Math.min(1, Math.max(0, t)) * (RGB.length - 1);
      const i = Math.min(RGB.length - 2, Math.floor(x));
      const f = x - i;
      return RGB[i].map((c, k) => Math.round(c + (RGB[i + 1][k] - c) * f));
    },
    css: `linear-gradient(to right, ${colors.map((c, i) => `${c} ${((i / (colors.length - 1)) * 100).toFixed(1)}%`).join(', ')})`,
  };
}

/** Position in [0, 1] of value v on the scale (linear or sqrt). */
export function scaleT(v, scale, domain) {
  const span = domain.max - domain.min;
  if (!(span > 0)) return 0.5;
  const t = Math.min(1, Math.max(0, (v - domain.min) / span));
  return scale === 'sqrt' ? Math.sqrt(t) : t;
}

/** Class of v: 0 below breaks[0], i for breaks[i - 1] <= v < breaks[i], breaks.length from the last (pipeline class_index). */
export function classIndex(v, breaks) {
  let lo = 0; let hi = breaks.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (v >= breaks[mid]) lo = mid + 1; else hi = mid; }
  return lo;
}

/**
 * Legend boxes of a classed scale: [{ lo, hi, color, x0, x1 }] with x in [0, 1].
 * A box is as wide as its class spans in value. The end classes span to the domain when it is fixed, else one
 * typical class (the most common span between breaks). Classes that end at or below `compressBelow` take half a
 * typical class each, so the range that matters (snowfall: 180 to 900 in) fills most of the bar.
 */
export function classBoxes(lg) {
  const { breaks, colors } = lg;
  const inner = breaks.slice(1).map((b, i) => b - breaks[i]);
  const count = new Map();
  for (const sp of inner) count.set(sp, (count.get(sp) || 0) + 1);
  const typical = inner.length ? [...count.entries()].sort((a, b) => (b[1] - a[1]) || (b[0] - a[0]))[0][0] : 1;
  const fixed = Boolean(lg.fixed);
  const boxes = colors.map((color, i) => {
    const lo = i === 0 ? (fixed ? Math.min(lg.domain.min, breaks[0]) : breaks[0] - typical) : breaks[i - 1];
    const hi = i === breaks.length ? (fixed ? Math.max(lg.domain.max, breaks[i - 1]) : breaks[i - 1] + typical) : breaks[i];
    const w = lg.compressBelow != null && hi <= lg.compressBelow ? typical / 2 : Math.max(hi - lo, typical / 4);
    return { lo, hi, color, w };
  });
  const total = boxes.reduce((a, b) => a + b.w, 0) || 1;
  let x = 0;
  return boxes.map((b) => { const x0 = x; x += b.w / total; return { lo: b.lo, hi: b.hi, color: b.color, x0, x1: x }; });
}

/** Position in [0, 1] of value v on a legend (classed: inside its class box, linearly). */
export function legendPos(v, lg) {
  if (lg.scale !== 'classed') return scaleT(v, lg.scale, lg.domain);
  const boxes = classBoxes(lg);
  const b = boxes[classIndex(v, lg.breaks)];
  const t = b.hi > b.lo ? Math.min(1, Math.max(0, (v - b.lo) / (b.hi - b.lo))) : 0;
  return b.x0 + (b.x1 - b.x0) * t;
}

/** CSS background of the legend bar: the ramp, or hard edged class boxes. */
export function legendCss(lg, ramp) {
  if (lg.scale !== 'classed') return ramp.css;
  const stops = classBoxes(lg).map((b) => `${b.color} ${(b.x0 * 100).toFixed(2)}% ${(b.x1 * 100).toFixed(2)}%`);
  return `linear-gradient(to right, ${stops.join(', ')})`;
}

/** Smallest of 1, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8 or 10 times a power of ten that is at least x (pipeline nice_ceil). */
export function niceCeil(x) {
  if (!(x > 0)) return 1;
  const m = 10 ** Math.floor(Math.log10(x));
  for (const f of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 10]) if (f * m >= x - 1e-9) return f * m;
  return 10 * m;
}

/** Legend scale for a mode: { scale, domain: {min, max}, ticks } from its config and the resort values. */
export function legendScale(modeCfg, values) {
  const scale = modeCfg.scale || 'linear';
  if (scale === 'classed') {
    const domain = modeCfg.domain ? { min: modeCfg.domain[0], max: modeCfg.domain[1] } : { min: Math.min(...values), max: Math.max(...values) };
    return { scale, domain, ticks: modeCfg.legend_ticks || modeCfg.breaks, breaks: modeCfg.breaks, colors: modeCfg.colors,
      compressBelow: modeCfg.legend_compress_below ?? null, fixed: Boolean(modeCfg.domain) };
  }
  if (modeCfg.domain) {
    const [min, max] = modeCfg.domain;
    return { scale, domain: { min, max }, ticks: modeCfg.ticks || [min, max] };
  }
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  if (scale === 'sqrt') {
    const min = modeCfg.domain_min ?? lo;
    const max = niceCeil(Math.max(hi, min + 1));
    const ticks = (modeCfg.ticks || []).filter((t) => t >= min && t < max && 1 - scaleT(t, 'sqrt', { min, max }) >= 0.12);
    return { scale, domain: { min, max }, ticks: [...ticks, max] };
  }
  // Linear: four or fewer round steps that cover the values.
  const low = modeCfg.domain_min ?? lo;
  const raw = Math.max(hi - low, Math.abs(hi) * 0.1, 1e-9) / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((f) => f * mag).find((s) => s >= raw) || 10 * mag;
  const min = Math.floor(low / step) * step;
  let max = Math.ceil(hi / step) * step;
  if (max === min) max = min + step;
  const ticks = [];
  for (let t = min; t <= max + step / 2; t += step) ticks.push(Math.round(t / step) * step);
  return { scale, domain: { min, max }, ticks };
}

/** Greedy merge of points within km of a group's first point (mean place, mean value); north to south, west to east. */
export function decluster(points, km) {
  if (!(km > 0) || points.length < 2) return points.slice();
  const groups = [];
  const sorted = points.slice().sort((a, b) => (b.lat - a.lat) || (a.lon - b.lon) || (a.value - b.value));
  for (const p of sorted) {
    const kmLon = KM_PER_DEG_LON * Math.cos(p.lat * RAD);
    const g = groups.find((q) => Math.hypot((q.lon0 - p.lon) * kmLon, (q.lat0 - p.lat) * KM_PER_DEG_LAT) <= km);
    if (g) { g.lon += p.lon; g.lat += p.lat; g.value += p.value; g.n += 1; } else groups.push({ lon0: p.lon, lat0: p.lat, lon: p.lon, lat: p.lat, value: p.value, n: 1 });
  }
  return groups.map((g) => ({ lon: g.lon / g.n, lat: g.lat / g.n, value: g.value / g.n }));
}

/** Adaptive modified Shepard value from distances d and values v of up to k + 1 nearest points, nearest first. */
export function shepard(d, v, k, minKm) {
  const n = d.length;
  if (!n) return null;
  let sw = 0; let swv = 0;
  if (n <= k) {
    for (let i = 0; i < n; i++) { const w = 1 / Math.max(d[i], minKm) ** 2; sw += w; swv += w * v[i]; }
  } else {
    const R = d[k];
    for (let i = 0; i < k; i++) {
      if (d[i] >= R) continue;
      const w = ((R - d[i]) / (R * Math.max(d[i], minKm))) ** 2;
      sw += w; swv += w * v[i];
    }
  }
  return sw > 0 ? swv / sw : v[0];
}

const smooth = (t) => { const x = t < 0 ? 0 : t > 1 ? 1 : t; return x * x * (3 - 2 * x); };

/** Share of the fade (0 to 1) at distance dKm (km) from the nearest point with data. */
export function fadeAt(dKm, f) {
  if (dKm <= f.full_opacity_km) return 0;
  return smooth((dKm - f.full_opacity_km) / (f.fade_end_km - f.full_opacity_km));
}

/** Opacity from the distance (km) to the nearest point with data. */
export function opacityAt(dKm, f) {
  return f.max_alpha - (f.max_alpha - f.floor_alpha) * fadeAt(dKm, f);
}

/** Share of the color kept dKm past the border of a state with no data (pipeline reach). */
export function reachAt(dKm, reachKm) {
  return reachKm > 0 ? 1 - smooth(dKm / reachKm) : 0;
}

/** Chamfer distance (in node steps) from every node to the nearest node marked inside; two passes. */
function chamfer(inside, nc, nr) {
  const d = new Float64Array(nc * nr);
  for (let q = 0; q < d.length; q++) d[q] = inside[q] ? 0 : Infinity;
  const D = Math.SQRT2;
  for (let r = 0; r < nr; r++) {
    for (let c = 0; c < nc; c++) {
      const q = r * nc + c; let v = d[q];
      if (!v) continue;
      if (c > 0) v = Math.min(v, d[q - 1] + 1);
      if (r > 0) { v = Math.min(v, d[q - nc] + 1); if (c > 0) v = Math.min(v, d[q - nc - 1] + D); if (c < nc - 1) v = Math.min(v, d[q - nc + 1] + D); }
      d[q] = v;
    }
  }
  for (let r = nr - 1; r >= 0; r--) {
    for (let c = nc - 1; c >= 0; c--) {
      const q = r * nc + c; let v = d[q];
      if (!v) continue;
      if (c < nc - 1) v = Math.min(v, d[q + 1] + 1);
      if (r < nr - 1) { v = Math.min(v, d[q + nc] + 1); if (c < nc - 1) v = Math.min(v, d[q + nc + 1] + D); if (c > 0) v = Math.min(v, d[q + nc - 1] + D); }
      d[q] = v;
    }
  }
  return d;
}

function nodePositions(n, step) {
  const out = [];
  for (let i = 0; i < n; i += step) out.push(i);
  if (out[out.length - 1] !== n - 1) out.push(n - 1);
  return out;
}

/** Per pixel: the node interval it falls in and the fraction along it. */
function cellsOf(pos, n) {
  const cell = new Int32Array(n); const frac = new Float32Array(n);
  let c = 0;
  for (let i = 0; i < n; i++) {
    while (c < pos.length - 2 && pos[c + 1] <= i) c++;
    cell[i] = c; frac[i] = (i - pos[c]) / (pos[c + 1] - pos[c]);
  }
  return { cell, frac };
}

/** Trace the region's state outlines into the current path, projected into its pixel grid. */
function traceOutlines(ctx, features, toPx, westOfZero) {
  ctx.beginPath();
  for (const f of features) {
    const g = f.geometry;
    const polys = g.type === 'Polygon' ? [g.coordinates] : g.coordinates;
    for (const poly of polys) {
      for (const ring of poly) {
        ring.forEach(([lon, lat], i) => {
          // Rings that cross the antimeridian (Aleutians) wrap to the western side.
          const [x, y] = toPx(westOfZero && lon > 0 ? lon - 360 : lon, lat);
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.closePath();
      }
    }
  }
}

/**
 * Build the heat images.
 * points: [{ lon, lat, value }] (resorts with a value); opts: { domain, scale, ramp (makeRamp), field, regions } from
 * definitions.json `heat`, plus for classed modes `breaks` and `colors`, `neutral` (the fade_to "neutral" color) and
 * `dataStates` (codes of the states with a resort value; the others keep color only `field.no_data_reach_km` past
 * their border). outlines: state FeatureCollection or null (then nothing is clipped).
 * Returns [{ url, coordinates: [[w, n], [e, n], [e, s], [w, s]], width, height, region }].
 */
export function buildHeat(points, outlines, opts) {
  const f = opts.field;
  const pts = decluster(points.map((p) => ({ lon: p.lon > 0 && p.lat > 45 ? p.lon - 360 : p.lon, lat: p.lat, value: p.value })), f.decluster_km);
  if (!pts.length) return [];
  const np = pts.length;
  const plon = Float64Array.from(pts, (p) => p.lon);
  const plat = Float64Array.from(pts, (p) => p.lat);
  const pval = Float64Array.from(pts, (p) => p.value);
  const K = f.neighbors;
  const take = Math.min(K + 1, pts.length);
  const classed = opts.scale === 'classed' && opts.breaks && opts.colors;
  const classRgb = classed ? opts.colors.map(hexRgb) : null;
  const neutral = f.fade_to === 'neutral' && opts.neutral ? hexRgb(opts.neutral) : null;
  const reachKm = opts.dataStates && f.no_data_reach_km != null ? f.no_data_reach_km : null;
  const out = [];
  for (const [name, box] of Object.entries(opts.regions)) {
    const feats = outlines && outlines.features ? outlines.features.filter((x) => REGION_STATES[name] && REGION_STATES[name](String(x.properties.code).toUpperCase())) : null;
    if (feats && !feats.length) continue;
    const { west: w, east: e } = box;
    const n = box.north; const s = box.south;
    const yN = mercY(n); const yS = mercY(s);
    let W = Math.max(2, Math.round((e - w) * f.px_per_degree));
    let H = Math.max(2, Math.round(((yN - yS) / RAD) * f.px_per_degree));
    if (W * H > f.max_pixels) { const k = Math.sqrt(f.max_pixels / (W * H)); W = Math.max(2, Math.round(W * k)); H = Math.max(2, Math.round(H * k)); }

    // Coverage of each pixel by the region's states (antialiased canvas fill), or everything without outlines.
    const canvas = document.createElement('canvas');
    canvas.width = W; canvas.height = H;
    // CPU backed: the outline mask is read back once, which is slow from a GPU canvas.
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    const toPx = (lon, lat) => [((lon - w) / (e - w)) * W, ((yN - mercY(lat)) / (yN - yS)) * H];
    const mask = (list) => {
      ctx.clearRect(0, 0, W, H);
      ctx.fillStyle = '#000';
      traceOutlines(ctx, list, toPx, name === 'alaska');
      ctx.fill('evenodd');
      const m = ctx.getImageData(0, 0, W, H).data;
      const a = new Uint8Array(W * H);
      for (let i = 0; i < W * H; i++) a[i] = m[i * 4 + 3];
      ctx.clearRect(0, 0, W, H);
      return a;
    };
    const cover = feats ? mask(feats) : null;
    // States with no data in this region: their share of each pixel, and the states with data for the border.
    const empty = feats && reachKm != null ? feats.filter((x) => !opts.dataStates.has(String(x.properties.code).toUpperCase())) : [];
    const noData = empty.length ? mask(empty) : null;
    const withData = noData ? mask(feats.filter((x) => opts.dataStates.has(String(x.properties.code).toUpperCase()))) : null;

    const cols = nodePositions(W, f.grid_step_px);
    const rows = nodePositions(H, f.grid_step_px);
    const nc = cols.length;
    const cx = cellsOf(cols, W); const cy = cellsOf(rows, H);
    const need = new Uint8Array(nc * rows.length);
    for (let j = 0; j < H; j++) {
      const r = cy.cell[j] * nc;
      for (let i = 0; i < W; i++) {
        if (cover && !cover[j * W + i]) continue;
        const q = r + cx.cell[i];
        need[q] = need[q + 1] = need[q + nc] = need[q + nc + 1] = 1;
      }
    }

    // Solve the field at the needed nodes: value and the distance to the nearest point.
    const val = new Float64Array(nc * rows.length).fill(NaN);
    const dist = new Float64Array(nc * rows.length).fill(NaN);
    const bd = new Float64Array(take); const bv = new Float64Array(take); // squared distances, values
    const kd = new Float64Array(take);
    for (let r = 0; r < rows.length; r++) {
      const lat = latOf(yN - ((rows[r] + 0.5) / H) * (yN - yS));
      const kmLon = KM_PER_DEG_LON * Math.cos(lat * RAD);
      for (let c = 0; c < nc; c++) {
        const q = r * nc + c;
        if (!need[q]) continue;
        const lon = w + ((cols[c] + 0.5) / W) * (e - w);
        let m = 0;
        for (let t = 0; t < np; t++) {
          const dx = (plon[t] - lon) * kmLon; const dy = (plat[t] - lat) * KM_PER_DEG_LAT;
          const d2 = dx * dx + dy * dy;
          if (m === take && d2 >= bd[m - 1]) continue;
          let at = m < take ? m++ : take - 1;
          while (at > 0 && bd[at - 1] > d2) { bd[at] = bd[at - 1]; bv[at] = bv[at - 1]; at--; }
          bd[at] = d2; bv[at] = pval[t];
        }
        for (let t = 0; t < m; t++) kd[t] = Math.sqrt(bd[t]);
        val[q] = shepard(kd.subarray(0, m), bv.subarray(0, m), K, f.min_distance_km);
        dist[q] = kd[0];
      }
    }
    // Distance (km) past the border of the states with data, on the nodes (pipeline border_distance).
    let border = null;
    if (withData) {
      const inside = new Uint8Array(nc * rows.length);
      for (let r = 0; r < rows.length; r++) for (let c = 0; c < nc; c++) inside[r * nc + c] = withData[rows[r] * W + cols[c]] > 127 ? 1 : 0;
      border = chamfer(inside, nc, rows.length);
      const stepDeg = ((e - w) / W) * f.grid_step_px;
      for (let r = 0; r < rows.length; r++) {
        const kmStep = KM_PER_DEG_LON * Math.cos(latOf(yN - ((rows[r] + 0.5) / H) * (yN - yS)) * RAD) * stepDeg;
        for (let c = 0; c < nc; c++) { const q = r * nc + c; border[q] = Number.isFinite(border[q]) ? border[q] * kmStep : 1e9; }
      }
    }

    // Color and opacity lookups, then every covered pixel: bilinear between its four nodes.
    const LUT_N = 1024;
    const lut = new Uint8Array(LUT_N * 3);
    if (!classed) for (let i = 0; i < LUT_N; i++) lut.set(opts.ramp.rgb(i / (LUT_N - 1)), i * 3);
    const sqrt = opts.scale === 'sqrt';
    const dmin = opts.domain.min; const dspan = opts.domain.max - opts.domain.min || 1;
    const img = ctx.createImageData(W, H);
    const px = img.data;
    const lerp = (q, tx, ty, arr) => {
      const top = arr[q] + (arr[q + 1] - arr[q]) * tx;
      return top + (arr[q + nc] + (arr[q + nc + 1] - arr[q + nc]) * tx - top) * ty;
    };
    for (let j = 0; j < H; j++) {
      const r = cy.cell[j] * nc; const ty = cy.frac[j];
      for (let i = 0; i < W; i++) {
        const k = j * W + i;
        const cov = cover ? cover[k] : 255;
        if (!cov) continue;
        const q = r + cx.cell[i]; const tx = cx.frac[i];
        const v = lerp(q, tx, ty, val);
        if (!Number.isFinite(v)) continue;
        const d = lerp(q, tx, ty, dist);
        let cr; let cg; let cb;
        if (classed) {
          [cr, cg, cb] = classRgb[classIndex(v, opts.breaks)];
        } else {
          let t = (v - dmin) / dspan;
          t = t < 0 ? 0 : t > 1 ? 1 : t;
          if (sqrt) t = Math.sqrt(t);
          const li = Math.round(t * (LUT_N - 1)) * 3;
          cr = lut[li]; cg = lut[li + 1]; cb = lut[li + 2];
        }
        const fade = fadeAt(d, f);
        let a = f.max_alpha - (f.max_alpha - f.floor_alpha) * fade;
        if (neutral && fade > 0) {
          cr += (neutral[0] - cr) * fade; cg += (neutral[1] - cg) * fade; cb += (neutral[2] - cb) * fade;
        }
        if (noData && noData[k]) {
          const nd = noData[k] / 255;
          a *= (1 - nd) + nd * reachAt(lerp(q, tx, ty, border), reachKm);
        }
        a *= cov / 255;
        const o = k * 4;
        px[o] = Math.round(cr); px[o + 1] = Math.round(cg); px[o + 2] = Math.round(cb); px[o + 3] = Math.round(a * 255);
      }
    }
    ctx.putImageData(img, 0, 0);
    out.push({ url: canvas.toDataURL('image/png'), coordinates: [[w, n], [e, n], [e, s], [w, s]], width: W, height: H,
      region: name });
  }
  return out;
}
