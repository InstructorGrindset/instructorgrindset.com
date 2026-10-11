// Fetch and validate the build output, then index it for the UI.
import { config } from './config.js';
import {
  ResortsFile, Resort, FeatureCollectionShell, ClusterFeature, ResortFeature, StateFeature,
  StatesFile, SourcesFile, ClimateFile,
} from './schemas.js';

export class DataError extends Error {}

async function fetchJson(name, { optional = false } = {}) {
  const url = config.dataBase + name;
  let res;
  try {
    res = await fetch(url, { credentials: 'same-origin' });
  } catch (err) {
    if (optional) return null;
    throw new DataError(`Could not reach ${url}`);
  }
  if (!res.ok) {
    if (optional) {
      console.warn(`[map] optional file ${url} returned ${res.status}`);
      return null;
    }
    throw new DataError(`${url} returned ${res.status}`);
  }
  try {
    return await res.json();
  } catch {
    throw new DataError(`${url} is not valid JSON`);
  }
}

function parseOrThrow(schema, data, name) {
  const r = schema.safeParse(data);
  if (!r.success) {
    console.warn(`[map] ${name} failed validation`, r.error.issues.slice(0, 5));
    throw new DataError(`${name} does not match the data contract`);
  }
  return r.data;
}

/** Validate each item on its own; drop and report the invalid ones. */
function parseEach(schema, items, name, idOf) {
  const out = [];
  let bad = 0;
  items.forEach((item, i) => {
    const r = schema.safeParse(item);
    if (r.success) out.push(r.data);
    else {
      bad += 1;
      if (bad <= 5) {
        const id = idOf ? idOf(item) : i;
        console.warn(`[map] ${name}[${id}] skipped:`, r.error.issues.slice(0, 3).map((x) => `${x.path.join('.')}: ${x.message}`).join('; '));
      }
    }
  });
  if (bad > 5) console.warn(`[map] ${name}: ${bad} records skipped in total`);
  return out;
}

export function bboxOf(geometry) {
  let minX = Infinity; let minY = Infinity; let maxX = -Infinity; let maxY = -Infinity;
  const walk = (c) => {
    if (typeof c[0] === 'number') {
      if (c[0] < minX) minX = c[0];
      if (c[0] > maxX) maxX = c[0];
      if (c[1] < minY) minY = c[1];
      if (c[1] > maxY) maxY = c[1];
    } else c.forEach(walk);
  };
  walk(geometry.coordinates);
  return Number.isFinite(minX) ? [[minX, minY], [maxX, maxY]] : null;
}

export function unionBbox(a, b) {
  if (!a) return b;
  if (!b) return a;
  return [[Math.min(a[0][0], b[0][0]), Math.min(a[0][1], b[0][1])],
    [Math.max(a[1][0], b[1][0]), Math.max(a[1][1], b[1][1])]];
}

function pointBbox(points) {
  if (!points.length) return null;
  return bboxOf({ coordinates: points });
}

function sizeRankFallback(acres) {
  if (acres == null) return 1;
  if (acres < 500) return 1;
  if (acres < 1000) return 2;
  if (acres < 2000) return 3;
  if (acres < 4000) return 4;
  return 5;
}

const byName = (a, b) => a.name.localeCompare(b.name);

export async function loadAll() {
  const [resortsRaw, clustersRaw, statesRaw, sourcesRaw, resortsGeoRaw, usStatesRaw] = await Promise.all([
    fetchJson('resorts.json'),
    fetchJson('clusters.geojson'),
    fetchJson('states.json'),
    fetchJson('sources.json', { optional: true }),
    fetchJson('resorts.geojson', { optional: true }),
    fetchJson('us-states.geojson', { optional: true }),
  ]);

  const resortsFile = parseOrThrow(ResortsFile, resortsRaw, 'resorts.json');
  const resortList = parseEach(Resort, resortsFile.resorts, 'resorts.json resorts', (r) => r && r.id);
  const clusterFeatures = parseEach(ClusterFeature,
    parseOrThrow(FeatureCollectionShell, clustersRaw, 'clusters.geojson').features,
    'clusters.geojson features', (f) => f && f.properties && f.properties.id);
  const statesFile = parseOrThrow(StatesFile, statesRaw, 'states.json');
  const sourcesFile = sourcesRaw ? SourcesFile.safeParse(sourcesRaw) : null;
  if (sourcesFile && !sourcesFile.success) console.warn('[map] sources.json failed validation', sourcesFile.error.issues.slice(0, 5));

  const resorts = new Map(resortList.map((r) => [r.id, r]));

  const clusters = new Map();
  for (const f of clusterFeatures) {
    const p = f.properties;
    const ids = p.resort_ids.filter((id) => resorts.has(id));
    clusters.set(p.id, {
      ...p,
      resort_ids: ids,
      geometry: f.geometry,
      bbox: unionBbox(bboxOf(f.geometry), pointBbox(ids.map((id) => [resorts.get(id).lon, resorts.get(id).lat]))),
    });
  }
  // Resorts whose cluster is missing from clusters.geojson still appear in the list view.
  for (const r of resortList) {
    if (!clusters.has(r.cluster_id)) console.warn(`[map] resort ${r.id} references unknown cluster ${r.cluster_id}`);
  }

  // State outlines (optional file). Features are cloned with a has_resorts flag for styling.
  let usStatesFC = { type: 'FeatureCollection', features: [] };
  const stateGeom = new Map();
  if (usStatesRaw) {
    const shell = FeatureCollectionShell.safeParse(usStatesRaw);
    if (shell.success) {
      const feats = parseEach(StateFeature, shell.data.features, 'us-states.geojson features', (f) => f && f.properties && f.properties.code);
      for (const f of feats) stateGeom.set(f.properties.code.toUpperCase(), f.geometry);
      usStatesFC = { type: 'FeatureCollection', features: feats };
    } else console.warn('[map] us-states.geojson failed validation');
  }

  const states = new Map();
  for (const s of statesFile.states) {
    const clusterIds = s.cluster_ids.filter((id) => clusters.has(id));
    const stateResorts = resortList.filter((r) => r.state === s.code);
    let bbox = null;
    const geom = stateGeom.get(s.code);
    if (geom) bbox = bboxOf(geom);
    // Antimeridian (Alaska) or missing outline: fall back to where the resorts are.
    if (!bbox || bbox[1][0] - bbox[0][0] > 60) bbox = null;
    for (const cid of clusterIds) bbox = unionBbox(bbox, clusters.get(cid).bbox);
    bbox = unionBbox(bbox, pointBbox(stateResorts.map((r) => [r.lon, r.lat])));
    states.set(s.code, { ...s, cluster_ids: clusterIds, bbox });
  }
  const activeCodes = new Set(states.keys());
  usStatesFC.features = usStatesFC.features.map((f) => ({
    ...f,
    properties: { ...f.properties, code: f.properties.code.toUpperCase(), has_resorts: activeCodes.has(f.properties.code.toUpperCase()) },
  }));

  // Resort points: prefer resorts.geojson, derive from resorts.json when absent.
  let resortPoints;
  if (resortsGeoRaw) {
    const shell = parseOrThrow(FeatureCollectionShell, resortsGeoRaw, 'resorts.geojson');
    resortPoints = parseEach(ResortFeature, shell.features, 'resorts.geojson features', (f) => f && f.properties && f.properties.id)
      .filter((f) => resorts.has(f.properties.id));
  } else {
    resortPoints = resortList.map((r) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [r.lon, r.lat] },
      properties: {
        id: r.id, name: r.name, tier: r.tier, cluster_id: r.cluster_id, state: r.state,
        acres: r.size.acres ? r.size.acres.value : null,
        size_rank: sizeRankFallback(r.size.acres ? r.size.acres.value : null),
      },
    }));
  }
  resortPoints = resortPoints.map((f) => ({
    ...f,
    properties: { ...f.properties, tier_a: f.properties.tier === 'A' ? 1 : 0 },
  }));

  const sources = new Map();
  if (sourcesFile && sourcesFile.success) for (const s of sourcesFile.data.sources) sources.set(s.id, s);

  return {
    generatedAt: resortsFile.generated_at,
    resorts,
    resortList: [...resortList].sort(byName),
    clusters,
    states,
    stateList: [...states.values()].sort(byName),
    sources,
    geo: {
      usStates: usStatesFC,
      clusters: { type: 'FeatureCollection', features: clusterFeatures.map((f) => ({ type: 'Feature', geometry: f.geometry, properties: { id: f.properties.id, name: f.properties.name } })) },
      resorts: { type: 'FeatureCollection', features: resortPoints },
    },
  };
}

const climateCache = new Map();

/** Lazy load climate/{id}.json. Resolves to null when unavailable or invalid. */
export function loadClimate(resortId) {
  if (!climateCache.has(resortId)) {
    const p = fetchJson(`climate/${encodeURIComponent(resortId)}.json`, { optional: true }).then((raw) => {
      if (!raw) return null;
      const r = ClimateFile.safeParse(raw);
      if (!r.success) {
        console.warn(`[map] climate/${resortId}.json failed validation`, r.error.issues.slice(0, 5));
        return null;
      }
      return r.data;
    }).catch((err) => {
      console.warn(`[map] climate/${resortId}.json`, err.message);
      return null;
    });
    climateCache.set(resortId, p);
  }
  return climateCache.get(resortId);
}

/** Resorts of a cluster: Tier A first, then by size, then by name. */
export function clusterResorts(index, cluster) {
  return cluster.resort_ids.map((id) => index.resorts.get(id)).filter(Boolean).sort((a, b) => {
    if (a.tier !== b.tier) return a.tier === 'A' ? -1 : 1;
    const aa = a.size.acres ? a.size.acres.value : -1;
    const bb = b.size.acres ? b.size.acres.value : -1;
    if (aa !== bb) return bb - aa;
    return a.name.localeCompare(b.name);
  });
}
