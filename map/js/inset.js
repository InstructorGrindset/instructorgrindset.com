// Alaska inset for the national view, the way US atlases draw it: a scaled and moved copy of Alaska's outline,
// clusters, resorts and heat surface in the lower left, below the Pacific coast, inside a thin frame.
// The transform is a uniform scale plus a move in Web Mercator, so shapes keep their form and an image source
// (the heat surface) maps by its corners. Drilling into Alaska uses true coordinates again.

export const INSET_STATE = 'AK';
/** Territories and states with no ski areas, hidden from the map (Hawaii, Puerto Rico, the island territories, DC). */
export const HIDDEN_CODES = ['AS', 'GU', 'MP', 'PR', 'VI', 'HI', 'DC'];

// Alaska as drawn (Aleutians east of the antimeridian included) and the box it moves into.
const SOURCE = { west: -180, east: -129.5, south: 51.2, north: 71.6 };
const TARGET = { west: -125.0, east: -112.0, south: 22.2, north: 31.4 };
const PAD_PX = 0.0012; // frame padding in Web Mercator units

const mx = (lon) => (lon + 180) / 360;
const my = (lat) => {
  const s = Math.sin((Math.max(-85, Math.min(85, lat)) * Math.PI) / 180);
  return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI);
};
const lonOf = (x) => x * 360 - 180;
const latOf = (y) => (360 / Math.PI) * Math.atan(Math.exp((0.5 - y) * 2 * Math.PI)) - 90;

const sx0 = mx(SOURCE.west); const sx1 = mx(SOURCE.east);
const sy0 = my(SOURCE.north); const sy1 = my(SOURCE.south); // y grows southward
const tx0 = mx(TARGET.west); const tx1 = mx(TARGET.east);
const ty0 = my(TARGET.north); const ty1 = my(TARGET.south);
const SCALE = Math.min((tx1 - tx0) / (sx1 - sx0), (ty1 - ty0) / (sy1 - sy0));

/** Alaska lon, lat to its inset position (anchored at the target box's lower left). */
export function toInset(lon, lat) {
  const x = tx0 + (mx(lon) - sx0) * SCALE;
  const y = ty1 - (sy1 - my(lat)) * SCALE;
  return [lonOf(x), latOf(y)];
}

/** Frame around the inset: [[west, south], [east, north]]. */
export function insetFrameBounds() {
  const x0 = tx0 - PAD_PX; const x1 = tx0 + (sx1 - sx0) * SCALE + PAD_PX;
  const y1 = ty1 + PAD_PX; const y0 = ty1 - (sy1 - sy0) * SCALE - PAD_PX;
  return [[lonOf(x0), latOf(y1)], [lonOf(x1), latOf(y0)]];
}

export function insetFrame() {
  const [[w, s], [e, n]] = insetFrameBounds();
  return { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: [[w, s], [e, s], [e, n], [w, n], [w, s]] } }] };
}

/** Rings that cross the antimeridian wrap west; parts west of the drawn box are dropped. */
function mapRing(ring) {
  const wrapped = ring.map(([lon, lat]) => [lon > 0 ? lon - 360 : lon, lat]);
  if (wrapped.every(([lon]) => lon < SOURCE.west)) return null;
  return wrapped.map(([lon, lat]) => toInset(Math.max(lon, SOURCE.west), lat));
}

export function insetGeometry(g) {
  if (!g) return null;
  switch (g.type) {
    case 'Point': return { type: 'Point', coordinates: toInset(g.coordinates[0], g.coordinates[1]) };
    case 'LineString': return { type: 'LineString', coordinates: g.coordinates.map(([a, b]) => toInset(a, b)) };
    case 'Polygon': {
      const rings = g.coordinates.map(mapRing).filter(Boolean);
      return rings.length ? { type: 'Polygon', coordinates: rings } : null;
    }
    case 'MultiPolygon': {
      const polys = g.coordinates.map((p) => p.map(mapRing)).filter((p) => p[0]).map((p) => p.filter(Boolean));
      return polys.length ? { type: 'MultiPolygon', coordinates: polys } : null;
    }
    default: return null;
  }
}

/** Copies of features moved into the inset (features without a drawable geometry are skipped). */
export function insetFeatures(features) {
  const out = [];
  for (const f of features) {
    const geometry = insetGeometry(f.geometry);
    if (geometry) out.push({ ...f, geometry, properties: { ...f.properties, inset: true } });
  }
  return { type: 'FeatureCollection', features: out };
}

/** Image corners [[w, n], [e, n], [e, s], [w, s]] moved into the inset. */
export function insetCorners(c) {
  const [w, n] = toInset(c[0][0], c[0][1]);
  const [e, s] = toInset(c[2][0], c[2][1]);
  return [[w, n], [e, n], [e, s], [w, s]];
}
