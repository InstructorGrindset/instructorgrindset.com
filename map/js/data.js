// Fetch the build output and index it for the UI.
//
// First paint needs only map-index.json (slim national index, see docs/map/ARCHITECTURE.md "Frontend"),
// clusters.geojson and states.json. They are checked by hand here, so Zod stays off the critical path.
// resorts.json, sources.json and definitions.json load after the first paint (loadDetails); each full
// resort record is validated with Zod the first time a view needs it.
import { config } from './config.js';

export class DataError extends Error {}

let schemaModule = null;
/** Zod schemas, loaded on first use. */
export function schemas() {
  if (!schemaModule) schemaModule = import('./schemas.js');
  return schemaModule;
}

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

// ── Hand checks for the first paint files (mirror schemas.js; keep them in step) ──

const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const FACILITY = new Set(['ski_area', 'indoor', 'training_center']);
const isStr = (v) => typeof v === 'string' && v.length > 0;
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const numOrNull = (v) => (isNum(v) ? v : null);
const strArray = (v) => Array.isArray(v) && v.every((x) => typeof x === 'string');

function isArea(g) {
  return g && (g.type === 'Polygon' || g.type === 'MultiPolygon') && Array.isArray(g.coordinates) && g.coordinates.length > 0;
}

/** One map-index.json row (an array in `fields` order) to a slim resort, or a reason string. */
function slimFromRow(fields, row) {
  if (!Array.isArray(row)) return 'not an array';
  const o = {};
  fields.forEach((k, i) => { o[k] = row[i]; });
  if (!isStr(o.id) || !SLUG.test(o.id)) return 'id';
  if (!isStr(o.name)) return 'name';
  if (o.tier !== 'A' && o.tier !== 'B') return 'tier';
  if (typeof o.state !== 'string' || o.state.length !== 2) return 'state';
  if (!isStr(o.cluster_id)) return 'cluster_id';
  if (!isNum(o.lat) || o.lat < -90 || o.lat > 90 || !isNum(o.lon) || o.lon < -180 || o.lon > 180) return 'lat/lon';
  const rank = Number.isInteger(o.size_rank) && o.size_rank >= 1 && o.size_rank <= 5 ? o.size_rank : 1;
  return {
    id: o.id, name: o.name, tier: o.tier,
    tier_a_status: o.tier_a_status === 'draft' || o.tier_a_status === 'approved' ? o.tier_a_status : null,
    facility_type: FACILITY.has(o.facility_type) ? o.facility_type : 'ski_area',
    state: o.state.toUpperCase(), cluster_id: o.cluster_id, lat: o.lat, lon: o.lon,
    acres: numOrNull(o.acres), size_rank: rank,
    climate_available: o.climate_available === true, detail_available: o.detail_available === true,
    season_length_days: numOrNull(o.season_length_days), snowfall_annual_in: numOrNull(o.snowfall_annual_in),
    lucrativeness_mid: numOrNull(o.lucrativeness_mid),
  };
}

function checkCluster(f) {
  const p = f && f.properties;
  if (!f || f.type !== 'Feature' || !isArea(f.geometry) || !p) return null;
  if (!isStr(p.id) || !isStr(p.name) || !strArray(p.states) || !p.states.length || !p.states.every((x) => x.length === 2)) return null;
  if (!strArray(p.resort_ids) || !Number.isInteger(p.tier_a_count) || p.tier_a_count < 0) return null;
  const text = (v) => (typeof v === 'string' ? v : null);
  return {
    type: 'Feature', geometry: f.geometry,
    properties: {
      id: p.id, name: p.name, states: p.states.map((x) => x.toUpperCase()), resort_ids: p.resort_ids,
      tier_a_count: p.tier_a_count, overview_md: text(p.overview_md), alexandre_take_md: text(p.alexandre_take_md),
      sources: strArray(p.sources) ? p.sources : [],
    },
  };
}

function checkStates(raw) {
  if (!raw || !Number.isInteger(raw.version) || typeof raw.generated_at !== 'string' || !Array.isArray(raw.states)) return null;
  const out = [];
  for (const s of raw.states) {
    if (!s || typeof s.code !== 'string' || s.code.length !== 2 || !isStr(s.name) || !strArray(s.cluster_ids)
      || !Number.isInteger(s.resort_count) || s.resort_count < 0) return null;
    out.push({ code: s.code.toUpperCase(), name: s.name, cluster_ids: s.cluster_ids, resort_count: s.resort_count });
  }
  return out;
}

/** Keep the valid items, warn about the rest (same reporting as parseEach). */
function keepValid(items, check, name, idOf) {
  const out = [];
  let bad = 0;
  items.forEach((item, i) => {
    const v = check(item);
    if (v && typeof v === 'object') out.push(v);
    else {
      bad += 1;
      if (bad <= 5) console.warn(`[map] ${name}[${idOf(item) || i}] skipped${typeof v === 'string' ? `: ${v}` : ''}`);
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

const byName = (a, b) => a.name.localeCompare(b.name);

/** Per state catch all clusters from the pipeline (`other-co`). */
export function isFallbackCluster(id) { return id.startsWith('other-'); }

/** Named clusters first, then fallback clusters, each by name. */
export const clusterOrder = (a, b) => (a.fallback - b.fallback) || a.name.localeCompare(b.name);

/** Slim record from a full (validated) resort plus its resorts.geojson properties. */
function slimFromFull(r, props) {
  const p = props || {};
  return {
    id: r.id, name: r.name, tier: r.tier, tier_a_status: r.tier_a_status, facility_type: r.facility_type,
    state: r.state, cluster_id: r.cluster_id, lat: r.lat, lon: r.lon,
    acres: r.size.acres ? r.size.acres.value : null,
    size_rank: Number.isInteger(p.size_rank) ? p.size_rank : 1,
    climate_available: r.climate_available, detail_available: r.detail_available,
    season_length_days: p.season_length_days !== undefined ? numOrNull(p.season_length_days) : (r.season.length_days ? r.season.length_days.value : null),
    snowfall_annual_in: numOrNull(p.snowfall_annual_in),
    lucrativeness_mid: p.lucrativeness_mid !== undefined ? numOrNull(p.lucrativeness_mid)
      : (r.career && r.career.lucrativeness ? r.career.lucrativeness.mid : null),
  };
}

const RECOLOR_KEYS = ['season_length_days', 'snowfall_annual_in', 'lucrativeness_mid'];

/**
 * Load what the first paint needs and build the index. Resolves to
 * { resorts (Map of slim records), resortList, clusters, states, stateList, sources (filled by loadDetails),
 *   definitions, generatedAt, geo, full(id), loadDetails(), detailsReady }.
 */
export async function loadAll() {
  // us-states.geojson (about 90 KB gzipped) is left out of the critical path: see loadStateOutlines.
  const [indexRaw, clustersRaw, statesRaw] = await Promise.all([
    fetchJson('map-index.json', { optional: true }),
    fetchJson('clusters.geojson'),
    fetchJson('states.json'),
  ]);
  performance.mark('careermap:fetched');

  let slim;
  let generatedAt;
  let legacy = null; // { full: Map, sources, definitions } when map-index.json is absent
  if (indexRaw) {
    if (!Array.isArray(indexRaw.fields) || !Array.isArray(indexRaw.resorts) || typeof indexRaw.generated_at !== 'string') {
      throw new DataError('map-index.json does not match the data contract');
    }
    slim = keepValid(indexRaw.resorts, (row) => slimFromRow(indexRaw.fields, row), 'map-index.json resorts',
      (row) => (Array.isArray(row) ? row[0] : null));
    generatedAt = indexRaw.generated_at;
  } else {
    // Older builds without the slim index: validate resorts.json up front, as before.
    legacy = await loadLegacy();
    slim = legacy.slim;
    generatedAt = legacy.generatedAt;
  }

  if (!clustersRaw || clustersRaw.type !== 'FeatureCollection' || !Array.isArray(clustersRaw.features)) {
    throw new DataError('clusters.geojson does not match the data contract');
  }
  const clusterFeatures = keepValid(clustersRaw.features, checkCluster, 'clusters.geojson features',
    (f) => f && f.properties && f.properties.id);
  const stateRows = checkStates(statesRaw);
  if (!stateRows) throw new DataError('states.json does not match the data contract');

  const resorts = new Map(slim.map((r) => [r.id, r]));

  const clusters = new Map();
  for (const f of clusterFeatures) {
    const p = f.properties;
    const ids = p.resort_ids.filter((id) => resorts.has(id));
    clusters.set(p.id, {
      ...p,
      resort_ids: ids,
      fallback: isFallbackCluster(p.id),
      geometry: f.geometry,
      bbox: unionBbox(bboxOf(f.geometry), pointBbox(ids.map((id) => [resorts.get(id).lon, resorts.get(id).lat]))),
    });
  }
  // Resorts whose cluster is missing from clusters.geojson still appear in the list view.
  for (const r of slim) {
    if (!clusters.has(r.cluster_id)) console.warn(`[map] resort ${r.id} references unknown cluster ${r.cluster_id}`);
  }

  // State frames start from clusters and resorts; loadStateOutlines widens them to the outlines later.
  const states = new Map();
  for (const s of stateRows) {
    const clusterIds = s.cluster_ids.filter((id) => clusters.has(id));
    const stateResorts = slim.filter((r) => r.state === s.code);
    let bbox = null;
    for (const cid of clusterIds) bbox = unionBbox(bbox, clusters.get(cid).bbox);
    bbox = unionBbox(bbox, pointBbox(stateResorts.map((r) => [r.lon, r.lat])));
    states.set(s.code, { ...s, cluster_ids: clusterIds, bbox, baseBbox: bbox });
  }

  const resortPoints = slim.map((r) => {
    const props = {
      id: r.id, name: r.name, tier: r.tier, tier_a: r.tier === 'A' ? 1 : 0, facility_type: r.facility_type,
      cluster_id: r.cluster_id, state: r.state, acres: r.acres, size_rank: r.size_rank,
    };
    for (const k of RECOLOR_KEYS) props[k] = r[k];
    return { type: 'Feature', geometry: { type: 'Point', coordinates: [r.lon, r.lat] }, properties: props };
  });

  const index = {
    definitions: null,
    generatedAt,
    resorts,
    resortList: [...slim].sort(byName),
    clusters,
    states,
    stateList: [...states.values()].sort(byName),
    sources: new Map(),
    detailsReady: false,
    geo: {
      usStates: { type: 'FeatureCollection', features: [] },
      // `ak` marks Alaska only clusters, drawn in the national inset (inset.js).
      clusters: { type: 'FeatureCollection', features: clusterFeatures.map((f) => ({ type: 'Feature', geometry: f.geometry, properties: { id: f.properties.id, name: f.properties.name, fallback: isFallbackCluster(f.properties.id), ak: f.properties.states.every((x) => x === 'AK') } })) },
      resorts: { type: 'FeatureCollection', features: resortPoints },
    },
  };
  attachDetails(index, legacy);
  return index;
}

/** resorts.json validated in full (older builds without map-index.json). */
async function loadLegacy() {
  const [resortsRaw, geoRaw, sch] = await Promise.all([
    fetchJson('resorts.json'), fetchJson('resorts.geojson', { optional: true }), schemas()]);
  const file = parseOrThrow(sch.ResortsFile, resortsRaw, 'resorts.json');
  const list = parseEach(sch.Resort, file.resorts, 'resorts.json resorts', (r) => r && r.id);
  const props = new Map();
  if (geoRaw && Array.isArray(geoRaw.features)) {
    for (const f of parseEach(sch.ResortFeature, geoRaw.features, 'resorts.geojson features', (x) => x && x.properties && x.properties.id)) {
      props.set(f.properties.id, f.properties);
    }
  }
  return {
    generatedAt: file.generated_at,
    slim: list.map((r) => slimFromFull(r, props.get(r.id))),
    full: new Map(list.map((r) => [r.id, r])),
  };
}

/**
 * Full records, sources and definitions. index.loadDetails() starts the background load (once) and resolves
 * when it is done; index.full(id) returns the validated full record, or null when it is missing or invalid.
 */
/** The record a resort page embeds (pipeline/routes.py), or null. */
function embeddedRecord() {
  const el = document.getElementById('resort-record');
  if (!el) return null;
  try {
    const d = JSON.parse(el.textContent);
    return d && d.resort && typeof d.resort.id === 'string' ? { resort: d.resort, sources: Array.isArray(d.sources) ? d.sources : [] } : null;
  } catch {
    console.warn('[map] the embedded resort record is not valid JSON');
    return null;
  }
}

function attachDetails(index, legacy) {
  const raw = new Map();
  const parsed = legacy ? legacy.full : new Map();
  const failed = new Set();
  const embedded = embeddedRecord();
  let schemaMod = null;
  let pending = null;
  let pendingOne = null;

  const rawOf = (id) => raw.get(id) || (embedded && embedded.resort.id === id ? embedded.resort : null);

  /** True when full(id) can answer now (the record is here and Zod is loaded). */
  index.fullReady = (id) => parsed.has(id) || failed.has(id) || index.detailsReady || Boolean(schemaMod && rawOf(id));

  index.full = (id) => {
    if (parsed.has(id)) return parsed.get(id);
    const item = rawOf(id);
    if (!schemaMod || failed.has(id) || !item) return null;
    const r = schemaMod.Resort.safeParse(item);
    if (!r.success) {
      failed.add(id);
      console.warn(`[map] resorts.json resorts[${id}] skipped:`, r.error.issues.slice(0, 3).map((x) => `${x.path.join('.')}: ${x.message}`).join('; '));
      return null;
    }
    parsed.set(id, r.data);
    return r.data;
  };

  index.loadDetails = () => {
    if (!pending) {
      pending = Promise.all([
        legacy ? null : fetchJson('resorts.json', { optional: true }),
        fetchJson('sources.json', { optional: true }),
        loadDefinitions(),
        schemas(),
      ]).then(([resortsRaw, sourcesRaw, definitions, sch]) => {
        schemaMod = sch;
        if (resortsRaw) {
          const file = sch.ResortsFile.safeParse(resortsRaw);
          if (file.success) {
            for (const r of file.data.resorts) if (r && typeof r === 'object' && typeof r.id === 'string') raw.set(r.id, r);
          } else console.warn('[map] resorts.json failed validation', file.error.issues.slice(0, 5));
        }
        if (sourcesRaw) {
          const sf = sch.SourcesFile.safeParse(sourcesRaw);
          if (sf.success) for (const s of sf.data.sources) index.sources.set(s.id, s);
          else console.warn('[map] sources.json failed validation', sf.error.issues.slice(0, 5));
        }
        index.definitions = definitions;
        index.detailsReady = true;
        performance.mark('careermap:details');
        return index;
      }).catch((err) => {
        console.warn('[map] details failed to load:', err.message);
        pending = null;
        throw err;
      });
    }
    return pending;
  };

  /** One resort: from the record its page embeds when there is one (no resorts.json wait), else loadDetails. */
  index.loadResort = (id) => {
    if (!embedded || embedded.resort.id !== id || index.detailsReady) return index.loadDetails();
    if (!pendingOne) {
      pendingOne = Promise.all([schemas(), loadDefinitions()]).then(([sch, definitions]) => {
        schemaMod = schemaMod || sch;
        if (!index.detailsReady) {
          const sf = sch.SourcesFile.safeParse({ version: 1, generated_at: '', sources: embedded.sources });
          if (sf.success) for (const x of sf.data.sources) if (!index.sources.has(x.id)) index.sources.set(x.id, x);
          if (index.definitions == null) index.definitions = definitions;
        }
        return index;
      });
    }
    return pendingOne;
  };
}

/**
 * definitions.json (thresholds from config/definitions.yaml). Optional: when it is
 * missing or malformed the UI keeps generic labels and states no thresholds.
 */
let definitionsPromise = null;
export function loadDefinitions() {
  if (!definitionsPromise) definitionsPromise = readDefinitions();
  return definitionsPromise;
}

async function readDefinitions() {
  let raw = null;
  try { raw = await fetchJson('definitions.json', { optional: true }); } catch { raw = null; }
  if (!raw) return null;
  const r = (await schemas()).DefinitionsFile.safeParse(raw);
  if (!r.success) { console.warn('[map] definitions.json failed validation', r.error.issues.slice(0, 3)); return null; }
  return r.data;
}

const heatRasterCache = new Map();

/**
 * Precomputed heat rasters for a mode (`heat/<mode>.json` and its PNG images, written by pipeline/heat.py).
 * Loaded only when that mode is selected. Resolves to the validated manifest with absolute image URLs, or null
 * when the files are absent or invalid (the caller falls back to the resort only surface).
 */
export function loadHeatRaster(mode) {
  if (!heatRasterCache.has(mode)) {
    const p = Promise.all([fetchJson(`heat/${encodeURIComponent(mode)}.json`, { optional: true }), schemas()]).then(([raw, sch]) => {
      if (!raw) return null;
      const r = sch.HeatRasterFile.safeParse(raw);
      if (!r.success) {
        console.warn(`[map] heat/${mode}.json failed validation`, r.error.issues.slice(0, 5));
        return null;
      }
      return { ...r.data, rasters: r.data.rasters.map((x) => ({ ...x, url: `${config.dataBase}heat/${x.image}` })) };
    }).catch((err) => {
      console.warn(`[map] heat/${mode}.json`, err.message);
      return null;
    });
    heatRasterCache.set(mode, p);
  }
  return heatRasterCache.get(mode);
}

const climateCache = new Map();

/** Lazy load climate/{id}.json. Resolves to null when unavailable or invalid. */
export function loadClimate(resortId) {
  if (!climateCache.has(resortId)) {
    const p = Promise.all([fetchJson(`climate/${encodeURIComponent(resortId)}.json`, { optional: true }), schemas()]).then(([raw, sch]) => {
      if (!raw) return null;
      const r = sch.ClimateFile.safeParse(raw);
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

/**
 * State outlines (optional file), loaded after the map is interactive. Clones features with a has_resorts flag
 * for styling and widens each state's frame to its outline. Resolves to the FeatureCollection, or null.
 */
export function loadStateOutlines(index) {
  if (!index.statesPromise) index.statesPromise = readStateOutlines(index);
  return index.statesPromise;
}

async function readStateOutlines(index) {
  const [raw, sch] = await Promise.all([fetchJson('us-states.geojson', { optional: true }).catch(() => null), schemas()]);
  if (!raw) return null;
  const shell = sch.FeatureCollectionShell.safeParse(raw);
  if (!shell.success) { console.warn('[map] us-states.geojson failed validation'); return null; }
  const feats = parseEach(sch.StateFeature, shell.data.features, 'us-states.geojson features', (f) => f && f.properties && f.properties.code);
  for (const f of feats) {
    const code = f.properties.code.toUpperCase();
    const st = index.states.get(code);
    if (!st) continue;
    const b = bboxOf(f.geometry);
    // Antimeridian (Alaska): keep the frame around the resorts.
    if (b && b[1][0] - b[0][0] <= 60) st.bbox = unionBbox(st.baseBbox, b);
  }
  const fc = {
    type: 'FeatureCollection',
    features: feats.map((f) => ({
      ...f,
      properties: { ...f.properties, code: f.properties.code.toUpperCase(), has_resorts: index.states.has(f.properties.code.toUpperCase()) },
    })),
  };
  index.geo.usStates = fc;
  return fc;
}

const detailCache = new Map();

/** Lazy load detail/{id}.json (Tier A resort detail map). Resolves to null when unavailable or invalid. */
export function loadDetail(resortId) {
  if (!detailCache.has(resortId)) {
    const p = Promise.all([fetchJson(`detail/${encodeURIComponent(resortId)}.json`, { optional: true }), schemas()]).then(([raw, sch]) => {
      if (!raw) return null;
      const r = sch.DetailFile.safeParse(raw);
      if (!r.success) {
        console.warn(`[map] detail/${resortId}.json failed validation`, r.error.issues.slice(0, 5));
        return null;
      }
      const d = r.data;
      return {
        ...d,
        lifts: { type: 'FeatureCollection', features: parseEach(sch.LiftFeature, d.lifts.features, `detail/${resortId} lifts`) },
        housing: parseEach(sch.HousingSite, d.housing, `detail/${resortId} housing`, (x) => x && x.id),
      };
    }).catch((err) => {
      console.warn(`[map] detail/${resortId}.json`, err.message);
      return null;
    });
    detailCache.set(resortId, p);
  }
  return detailCache.get(resortId);
}

const trailCache = new Map();

/** Lazy load trailmap/{id}.json (the trail map dialog). Resolves to null when unavailable or invalid. */
export function loadTrailMap(resortId) {
  if (!trailCache.has(resortId)) {
    const p = Promise.all([fetchJson(`trailmap/${encodeURIComponent(resortId)}.json`, { optional: true }), schemas()]).then(([raw, sch]) => {
      if (!raw) return null;
      const r = sch.TrailMapFile.safeParse(raw);
      if (!r.success) {
        console.warn(`[map] trailmap/${resortId}.json failed validation`, r.error.issues.slice(0, 5));
        return null;
      }
      return {
        ...r.data,
        runs: { type: 'FeatureCollection', features: parseEach(sch.RunFeature, r.data.runs.features, `trailmap/${resortId} runs`) },
        lifts: { type: 'FeatureCollection', features: parseEach(sch.LiftFeature, r.data.lifts.features, `trailmap/${resortId} lifts`) },
      };
    }).catch((err) => {
      console.warn(`[map] trailmap/${resortId}.json`, err.message);
      trailCache.delete(resortId);
      return null;
    });
    trailCache.set(resortId, p);
  }
  return trailCache.get(resortId);
}

/** Counts by facility type for a list of resorts. */
export function facilityCounts(list) {
  const out = { ski_area: 0, indoor: 0, training_center: 0, total: list.length };
  for (const r of list) out[r.facility_type] = (out[r.facility_type] || 0) + 1;
  return out;
}

/** Filter key used by the facility filter: all | ski_area | facility (indoor and training). */
export function matchesFilter(resort, filter) {
  if (!filter || filter === 'all') return true;
  if (filter === 'ski_area') return resort.facility_type === 'ski_area';
  return resort.facility_type === 'indoor' || resort.facility_type === 'training_center';
}

/** Resorts of a cluster: Tier A first, then by size, then by name. */
export function clusterResorts(index, cluster) {
  return cluster.resort_ids.map((id) => index.resorts.get(id)).filter(Boolean).sort((a, b) => {
    if (a.tier !== b.tier) return a.tier === 'A' ? -1 : 1;
    const aa = a.acres ?? -1;
    const bb = b.acres ?? -1;
    if (aa !== bb) return bb - aa;
    return a.name.localeCompare(b.name);
  });
}
