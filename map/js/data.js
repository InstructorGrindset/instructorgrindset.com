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
const YEAR_MONTH = /^20\d\d-(0[1-9]|1[0-2])$/;

/** [start_month, end_month] (YYYY-MM, start first), or null. */
function monthWindow(v) {
  return Array.isArray(v) && v.length === 2 && YEAR_MONTH.test(v[0]) && YEAR_MONTH.test(v[1]) && v[0] <= v[1] ? [v[0], v[1]] : null;
}

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
    webcams_available: o.webcams_available === true, jobs_available: o.jobs_available === true,
    season_length_days: numOrNull(o.season_length_days), snowfall_annual_in: numOrNull(o.snowfall_annual_in),
    lucrativeness_mid: numOrNull(o.lucrativeness_mid),
    hiring_window: monthWindow(o.hiring_window),
    job_employers: strArray(o.job_employers) && o.job_employers.length ? o.job_employers.filter((x) => SLUG.test(x)) : null,
    hiring_now: false,
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
    webcams_available: Boolean(r.webcams), jobs_available: Boolean(r.jobs),
    season_length_days: p.season_length_days !== undefined ? numOrNull(p.season_length_days) : (r.season.length_days ? r.season.length_days.value : null),
    snowfall_annual_in: numOrNull(p.snowfall_annual_in),
    lucrativeness_mid: p.lucrativeness_mid !== undefined ? numOrNull(p.lucrativeness_mid)
      : (r.career && r.career.lucrativeness ? r.career.lucrativeness.mid : null),
    hiring_window: r.jobs && r.jobs.hiring_window ? monthWindow([r.jobs.hiring_window.start_month, r.jobs.hiring_window.end_month]) : null,
    job_employers: r.jobs && r.jobs.feed ? [...new Set(r.jobs.links.map((l) => l.employer_id))].sort() : null,
    hiring_now: false,
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

  // sources.json, definitions.json and Zod: what source links need (index.sourcesReady once done).
  let sourcesPending = null;
  index.loadSources = () => {
    if (!sourcesPending) {
      sourcesPending = Promise.all([fetchJson('sources.json', { optional: true }), loadDefinitions(), schemas()])
        .then(([sourcesRaw, definitions, sch]) => {
          schemaMod = schemaMod || sch;
          if (sourcesRaw) {
            const sf = sch.SourcesFile.safeParse(sourcesRaw);
            if (sf.success) for (const s of sf.data.sources) index.sources.set(s.id, s);
            else console.warn('[map] sources.json failed validation', sf.error.issues.slice(0, 5));
          }
          index.definitions = definitions;
          index.sourcesReady = true;
          return index;
        }).catch((err) => {
          console.warn('[map] sources failed to load:', err.message);
          sourcesPending = null;
          throw err;
        });
    }
    return sourcesPending;
  };

  index.loadDetails = () => {
    if (!pending) {
      pending = Promise.all([
        legacy ? null : fetchJson('resorts.json', { optional: true }),
        index.loadSources(),
      ]).then(([resortsRaw]) => {
        if (resortsRaw) {
          const file = schemaMod.ResortsFile.safeParse(resortsRaw);
          if (file.success) {
            for (const r of file.data.resorts) if (r && typeof r === 'object' && typeof r.id === 'string') raw.set(r.id, r);
          } else console.warn('[map] resorts.json failed validation', file.error.issues.slice(0, 5));
        }
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

  /**
   * Warm the HTTP cache for loadDetails() without parsing anything: a link prefetch downloads off the main thread,
   * where fetching and parsing resorts.json (2.6 MB) is a long task. Views that need the records load them.
   */
  let prefetched = false;
  index.prefetchDetails = () => {
    if (prefetched) return;
    prefetched = true;
    const files = [pending || legacy ? null : 'resorts.json', sourcesPending ? null : 'sources.json'].filter(Boolean);
    for (const f of files) {
      const l = document.createElement('link');
      l.rel = 'prefetch';
      l.href = config.dataBase + f;
      document.head.append(l);
    }
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

const isolineCache = new Map();
const isIsoline = (f) => f && f.type === 'Feature' && f.geometry && f.geometry.type === 'LineString'
  && Array.isArray(f.geometry.coordinates) && f.geometry.coordinates.length >= 2
  && f.properties && typeof f.properties.value === 'number' && Number.isFinite(f.properties.value);

/**
 * Isolines listed in a raster manifest (`isolines.files`, GeoJSON LineStrings with `value`, `major`, `label`,
 * `region`), merged into one FeatureCollection. Loaded only with their mode. Resolves to null when there are none
 * or none load; malformed features are dropped.
 */
export function loadHeatIsolines(manifest) {
  const files = manifest && manifest.isolines && Array.isArray(manifest.isolines.files) ? manifest.isolines.files : [];
  const names = files.map((f) => f && f.file).filter((f) => typeof f === 'string' && /^[a-z0-9-]+\.geojson$/.test(f));
  if (!names.length) return Promise.resolve(null);
  const key = `${manifest.mode}:${names.join(',')}`;
  if (!isolineCache.has(key)) {
    const p = Promise.all(names.map((n) => fetchJson(`heat/${n}`, { optional: true }).catch((err) => {
      console.warn(`[map] heat/${n}`, err.message);
      return null;
    }))).then((parts) => {
      const features = parts.flatMap((fc) => (fc && Array.isArray(fc.features) ? fc.features.filter(isIsoline) : []));
      return features.length ? { type: 'FeatureCollection', features } : null;
    });
    isolineCache.set(key, p);
  }
  return isolineCache.get(key);
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

let paintedPromise = null;

/**
 * Resolves once the browser has shown the first contentful paint (or after `latestMs`), for loads the first paint
 * does not need. A slow first frame otherwise lets them start before it, and Lighthouse then counts them as needed
 * for the paint (docs/map/ARCHITECTURE.md "Performance budget").
 */
export function whenPainted(latestMs = 8000) {
  if (!paintedPromise) {
    paintedPromise = new Promise((resolve) => {
      const go = () => setTimeout(resolve, 0);
      try {
        if (performance.getEntriesByName('first-contentful-paint').length) { go(); return; }
        const po = new PerformanceObserver((list) => {
          if (list.getEntriesByName('first-contentful-paint').length) { po.disconnect(); go(); }
        });
        po.observe({ type: 'paint', buffered: true });
      } catch { /* no paint timing: the timer below */ }
      setTimeout(resolve, latestMs);
    });
  }
  return paintedPromise;
}

let postingsRawPromise = null;
/** jobs/postings.json as fetched (one request for the panel and the Hiring now count), after the first paint. */
function postingsRaw() {
  if (!postingsRawPromise) {
    postingsRawPromise = whenPainted().then(() => fetchJson('jobs/postings.json', { optional: true })).catch((err) => {
      console.warn('[map] jobs/postings.json', err.message);
      return null;
    });
  }
  return postingsRawPromise;
}

let postingsPromise = null;

/**
 * Open job postings (jobs/postings.json, refreshed daily by pipeline/jobs_feed.py), loaded once, only when a resort
 * or cluster view has jobs links, after the first paint. Resolves to the validated file, or null when it is absent,
 * empty or invalid.
 */
export function loadPostings() {
  if (!postingsPromise) {
    postingsPromise = Promise.all([postingsRaw(), schemas()]).then(([raw, sch]) => {
      if (!raw) return null;
      const r = sch.PostingsFile.safeParse(raw);
      if (!r.success) {
        console.warn('[map] jobs/postings.json failed validation', r.error.issues.slice(0, 5));
        return null;
      }
      return r.data.postings.length ? r.data : null;
    }).catch((err) => {
      console.warn('[map] jobs/postings.json', err.message);
      return null;
    });
  }
  return postingsPromise;
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

// ── Hiring now filter (docs/map/ARCHITECTURE.md "Hiring now filter") ──

/** "YYYY-MM" of a date in the viewer's time zone. */
export function monthKey(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
}

/** True when the month of `date` falls inside [start_month, end_month]. Mirrors pipeline/hiring.py covers(). */
export function hiringWindowCovers(window, date) {
  if (!window) return false;
  const key = monthKey(date);
  return window[0] <= key && key <= window[1];
}

/**
 * Ids of the resorts that are hiring now: a sourced hiring window covers `date`, or postings.json has an open role
 * at one of the resort's linked employers that names this resort or no single resort. `postings` is the raw file
 * (or null); only rows of employers with a board and an https url count, as in the panel's open roles.
 */
export function hiringNowIds(resorts, postings, date) {
  const out = new Set();
  for (const r of resorts) if (hiringWindowCovers(r.hiring_window, date)) out.add(r.id);
  const boards = postings && Array.isArray(postings.boards) ? new Set(postings.boards.map((b) => b && b.employer_id)) : null;
  const rows = boards && Array.isArray(postings.postings) ? postings.postings : [];
  if (!rows.length) return out;
  const named = new Map(); // employer id -> Set of resort ids named by its postings ('' = no single resort)
  for (const p of rows) {
    if (!p || !isStr(p.employer_id) || !boards.has(p.employer_id) || !Array.isArray(p.resort_ids)
      || typeof p.url !== 'string' || !p.url.startsWith('https://')) continue;
    if (!named.has(p.employer_id)) named.set(p.employer_id, new Set());
    const set = named.get(p.employer_id);
    if (!p.resort_ids.length) set.add('');
    for (const id of p.resort_ids) if (typeof id === 'string') set.add(id);
  }
  for (const r of resorts) {
    if (!r.job_employers || out.has(r.id)) continue;
    if (r.job_employers.some((e) => named.has(e) && (named.get(e).has(r.id) || named.get(e).has('')))) out.add(r.id);
  }
  return out;
}

/**
 * Mark the slim records with hiring_now, keep the sorted ids in index.hiringIds and return the count. At startup
 * `postings` is null, so only the hiring windows in the slim index count; app.js calls it again with the raw
 * postings once jobs/postings.json has loaded after the first paint.
 */
export function markHiring(index, postings, date = new Date()) {
  const ids = hiringNowIds(index.resortList, postings, date);
  for (const r of index.resortList) r.hiring_now = ids.has(r.id);
  index.hiringIds = [...ids].sort();
  return ids.size;
}

/** Resolves to the raw postings file when some resort links an employer with a feed, else null (no request). */
export function hiringPostings(index) {
  return index.resortList.some((r) => r.job_employers) ? postingsRaw() : Promise.resolve(null);
}

/** Counts by facility type for a list of resorts. */
export function facilityCounts(list) {
  const out = { ski_area: 0, indoor: 0, training_center: 0, total: list.length };
  for (const r of list) out[r.facility_type] = (out[r.facility_type] || 0) + 1;
  return out;
}

/** Filter key used by the facility filter: all | ski_area | facility (indoor and training) | hiring (Hiring now). */
export function matchesFilter(resort, filter) {
  if (!filter || filter === 'all') return true;
  if (filter === 'ski_area') return resort.facility_type === 'ski_area';
  if (filter === 'hiring') return resort.hiring_now === true;
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
