// Value heat surface for the map color modes (lucrativeness, professionalism, season length, snowfall).
//
// Method (documented in docs/map/ARCHITECTURE.md, "Frontend"): every pixel takes the inverse distance
// weighted mean of the resorts with a value within config.heat.radiusKm. The weight is
// taper(d) / max(d, minDistanceKm)^power with taper(d) = (1 - (d / R)^2)^2, so it falls to zero at R.
// Opacity depends only on the distance to the nearest resort with data (full up to fadeStart × R, zero at R),
// so a cluster of many hills reads as their typical value and never as brighter or hotter.
// Resorts without a value add nothing. The result is clipped to the state outlines and drawn as one
// MapLibre image source per region (rows are evenly spaced in Web Mercator, as MapLibre expects).
import { config } from './config.js';

// One hue, dark (low) to light (high) on the dark map: steps 600 to 100 of the sequential blue ramp.
// The darkest step keeps at least 2:1 against the map background so low values stay visible.
export const HEAT_RAMP = ['#184f95', '#256abf', '#3987e5', '#6da7ec', '#9ec5f4', '#cde2fb'];

const RGB = HEAT_RAMP.map((hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)));
const KM_PER_DEG_LAT = 110.57;
const KM_PER_DEG_LON = 111.32;
const RAD = Math.PI / 180;

/** Ramp color for t in [0, 1] as [r, g, b]. */
export function rampColor(t) {
  const x = Math.min(1, Math.max(0, t)) * (RGB.length - 1);
  const i = Math.min(RGB.length - 2, Math.floor(x));
  const f = x - i;
  return RGB[i].map((c, k) => Math.round(c + (RGB[i + 1][k] - c) * f));
}

/** CSS gradient for the legend, the same stops as the surface. */
export function rampCss() {
  return `linear-gradient(to right, ${HEAT_RAMP.map((c, i) => `${c} ${Math.round((i / (HEAT_RAMP.length - 1)) * 100)}%`).join(', ')})`;
}

const mercY = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * RAD) / 2));
const latOf = (y) => (2 * Math.atan(Math.exp(y)) - Math.PI / 2) / RAD;

/** Group points whose padded boxes overlap, so Alaska and the lower 48 get separate, compact images. */
function regions(points, padLat, padLonAt) {
  let boxes = points.map((p) => {
    const pl = padLonAt(p.lat);
    return { w: p.lon - pl, e: p.lon + pl, s: p.lat - padLat, n: p.lat + padLat, pts: [p] };
  });
  let merged = true;
  while (merged) {
    merged = false;
    const out = [];
    for (const b of boxes) {
      const hit = out.find((o) => b.w <= o.e && b.e >= o.w && b.s <= o.n && b.n >= o.s);
      if (hit) {
        hit.w = Math.min(hit.w, b.w); hit.e = Math.max(hit.e, b.e);
        hit.s = Math.min(hit.s, b.s); hit.n = Math.max(hit.n, b.n);
        hit.pts.push(...b.pts);
        merged = true;
      } else out.push({ ...b, pts: [...b.pts] });
    }
    boxes = out;
  }
  return boxes;
}

function smoothstep(a, b, x) {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
}

/** Trace state outlines into the current path, projected into this region's pixel grid. */
function traceOutlines(ctx, outlines, toPx, westOfZero) {
  ctx.beginPath();
  for (const f of outlines.features) {
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
 * points: [{ lon, lat, value }] (only resorts with a value), domain: { min, max },
 * outlines: state FeatureCollection or null (then the surface is bounded by the radius only).
 * Returns [{ url, coordinates: [[w, n], [e, n], [e, s], [w, s]], width, height }].
 */
export function buildHeat(points, domain, outlines, opts = {}) {
  const o = { ...config.heat, ...opts };
  const R = o.radiusKm;
  const span = domain.max - domain.min;
  const padLat = R / KM_PER_DEG_LAT;
  const padLonAt = (lat) => R / (KM_PER_DEG_LON * Math.max(0.2, Math.cos(lat * RAD)));
  const out = [];
  for (const reg of regions(points, padLat, padLonAt)) {
    const w = reg.w; const e = reg.e;
    const n = Math.min(84, reg.n); const s = Math.max(-84, reg.s);
    const yN = mercY(n); const yS = mercY(s);
    const width = Math.max(2, Math.round((e - w) * o.pxPerDegree));
    const height = Math.max(2, Math.round(((yN - yS) / RAD) * o.pxPerDegree));
    const scale = Math.min(1, Math.sqrt(o.maxPixels / (width * height)));
    const W = Math.max(2, Math.round(width * scale));
    const H = Math.max(2, Math.round(height * scale));

    // Bucket points on a grid of cells about R wide so each pixel scans only its neighbors.
    const cell = padLat;
    const buckets = new Map();
    for (const p of reg.pts) {
      const key = `${Math.floor(p.lon / cell)},${Math.floor(p.lat / cell)}`;
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key).push(p);
    }

    const img = new ImageData(W, H);
    const px = img.data;
    const fadeStart = o.fadeStart * R;
    for (let j = 0; j < H; j++) {
      const lat = latOf(yN - ((j + 0.5) / H) * (yN - yS));
      const kmLon = KM_PER_DEG_LON * Math.cos(lat * RAD);
      const cy = Math.floor(lat / cell);
      const kx = Math.ceil(padLonAt(lat) / cell);
      for (let i = 0; i < W; i++) {
        const lon = w + ((i + 0.5) / W) * (e - w);
        const cx = Math.floor(lon / cell);
        let sw = 0; let swv = 0; let dmin = Infinity;
        for (let by = cy - 1; by <= cy + 1; by++) {
          for (let bx = cx - kx; bx <= cx + kx; bx++) {
            const list = buckets.get(`${bx},${by}`);
            if (!list) continue;
            for (const p of list) {
              const dx = (p.lon - lon) * kmLon;
              const dy = (p.lat - lat) * KM_PER_DEG_LAT;
              const d = Math.sqrt(dx * dx + dy * dy);
              if (d >= R) continue;
              if (d < dmin) dmin = d;
              const q = d / R;
              const taper = (1 - q * q) * (1 - q * q);
              const wt = taper / Math.max(d, o.minDistanceKm) ** o.power;
              sw += wt;
              swv += wt * p.value;
            }
          }
        }
        if (sw <= 0) continue;
        const v = swv / sw;
        const [r, g, b] = rampColor(span > 0 ? (v - domain.min) / span : 0.5);
        const a = o.maxAlpha * (1 - smoothstep(fadeStart, R, dmin));
        const k = (j * W + i) * 4;
        px[k] = r; px[k + 1] = g; px[k + 2] = b; px[k + 3] = Math.round(a * 255);
      }
    }

    const canvas = document.createElement('canvas');
    canvas.width = W; canvas.height = H;
    const ctx = canvas.getContext('2d');
    ctx.putImageData(img, 0, 0);
    if (outlines && outlines.features && outlines.features.length) {
      const toPx = (lon, lat) => [((lon - w) / (e - w)) * W, ((yN - mercY(Math.max(-84, Math.min(84, lat)))) / (yN - yS)) * H];
      ctx.globalCompositeOperation = 'destination-in';
      ctx.fillStyle = '#000';
      traceOutlines(ctx, outlines, toPx, e < 0);
      ctx.fill('evenodd');
    }
    // `ak`: a region of Alaska resorts only, which the national view also draws in the inset.
    out.push({ url: canvas.toDataURL('image/png'), coordinates: [[w, n], [e, n], [e, s], [w, s]], width: W, height: H,
      ak: reg.pts.every((p) => p.state === 'AK') });
  }
  return out;
}
