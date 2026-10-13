// Heat surfaces for the map color modes (lucrativeness, season length, snowfall).
//
// Settings come from definitions.json `heat` (config/definitions.yaml); the method is in docs/map/ARCHITECTURE.md,
// "Heat maps", and pipeline/heat.py is the Python twin that precomputes the snowfall rasters.
// * Field: adaptive modified Shepard interpolation over the resorts with a value, merged first within decluster_km
//   so a cluster of hills counts once. At a point, take the `neighbors` nearest and the distance R to the next one;
//   w = ((R - d) / (R * max(d, min_distance_km)))^2. Weights reach zero just as a resort leaves the set, so the
//   surface is continuous and covers the whole map. It is solved on every grid_step_px pixel, bilinear between.
// * Opacity: max_alpha within full_opacity_km of the nearest resort with data, a smooth fade to floor_alpha at
//   fade_end_km, floor_alpha beyond. Density never raises opacity or value.
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

/** Position in [0, 1] of value v on the scale. */
export function scaleT(v, scale, domain) {
  const span = domain.max - domain.min;
  if (!(span > 0)) return 0.5;
  const t = Math.min(1, Math.max(0, (v - domain.min) / span));
  return scale === 'sqrt' ? Math.sqrt(t) : t;
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

/** Opacity from the distance (km) to the nearest point with data. */
export function opacityAt(dKm, f) {
  if (dKm <= f.full_opacity_km) return f.max_alpha;
  if (dKm >= f.fade_end_km) return f.floor_alpha;
  const t = (dKm - f.full_opacity_km) / (f.fade_end_km - f.full_opacity_km);
  return f.max_alpha - (f.max_alpha - f.floor_alpha) * t * t * (3 - 2 * t);
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
 * points: [{ lon, lat, value }] (resorts with a value); opts: { domain, scale, ramp (makeRamp), field, regions }
 * from definitions.json `heat`; outlines: state FeatureCollection or null (then nothing is clipped).
 * Returns [{ url, coordinates: [[w, n], [e, n], [e, s], [w, s]], width, height, region, ak }].
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
    let cover = null;
    if (feats) {
      const toPx = (lon, lat) => [((lon - w) / (e - w)) * W, ((yN - mercY(lat)) / (yN - yS)) * H];
      ctx.fillStyle = '#000';
      traceOutlines(ctx, feats, toPx, name === 'alaska');
      ctx.fill('evenodd');
      const m = ctx.getImageData(0, 0, W, H).data;
      cover = new Uint8Array(W * H);
      for (let i = 0; i < W * H; i++) cover[i] = m[i * 4 + 3];
      ctx.clearRect(0, 0, W, H);
    }

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

    // Ramp and opacity lookups, then every covered pixel: bilinear between its four nodes.
    const LUT_N = 1024;
    const lut = new Uint8Array(LUT_N * 3);
    for (let i = 0; i < LUT_N; i++) lut.set(opts.ramp.rgb(i / (LUT_N - 1)), i * 3);
    const sqrt = opts.scale === 'sqrt';
    const dmin = opts.domain.min; const dspan = opts.domain.max - opts.domain.min || 1;
    const img = ctx.createImageData(W, H);
    const px = img.data;
    for (let j = 0; j < H; j++) {
      const r = cy.cell[j] * nc; const ty = cy.frac[j];
      for (let i = 0; i < W; i++) {
        const k = j * W + i;
        const cov = cover ? cover[k] : 255;
        if (!cov) continue;
        const q = r + cx.cell[i]; const tx = cx.frac[i];
        const a0 = val[q]; const a1 = val[q + 1]; const a2 = val[q + nc]; const a3 = val[q + nc + 1];
        const top = a0 + (a1 - a0) * tx;
        const v = top + (a2 + (a3 - a2) * tx - top) * ty;
        if (!Number.isFinite(v)) continue;
        const d0 = dist[q]; const d1 = dist[q + 1]; const d2 = dist[q + nc]; const d3 = dist[q + nc + 1];
        const dt = d0 + (d1 - d0) * tx;
        const d = dt + (d2 + (d3 - d2) * tx - dt) * ty;
        let t = (v - dmin) / dspan;
        t = t < 0 ? 0 : t > 1 ? 1 : t;
        if (sqrt) t = Math.sqrt(t);
        const li = Math.round(t * (LUT_N - 1)) * 3;
        const a = (d <= f.full_opacity_km ? f.max_alpha : opacityAt(d, f)) * (cov / 255);
        const o = k * 4;
        px[o] = lut[li]; px[o + 1] = lut[li + 1]; px[o + 2] = lut[li + 2]; px[o + 3] = Math.round(a * 255);
      }
    }
    ctx.putImageData(img, 0, 0);
    out.push({ url: canvas.toDataURL('image/png'), coordinates: [[w, n], [e, n], [e, s], [w, s]], width: W, height: H,
      region: name, ak: name === 'alaska' });
  }
  return out;
}
