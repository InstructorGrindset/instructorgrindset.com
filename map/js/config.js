// Career Map runtime configuration. No resort facts live here.

const DEFAULT_DATA_BASE = '/data/build/';

// Development only: `?data=/tests/map/fixtures/` points the map at another
// same origin folder. Only plain absolute paths are accepted.
function readDevDataBase() {
  let raw = null;
  try { raw = new URLSearchParams(window.location.search).get('data'); } catch { raw = null; }
  if (!raw) return null;
  const ok = /^\/(?!\/)[A-Za-z0-9_\-./]*$/.test(raw) && !raw.includes('..');
  if (!ok) {
    console.warn('[map] ignoring ?data= value; use a same origin absolute path such as /tests/map/fixtures/');
    return null;
  }
  return raw.endsWith('/') ? raw : raw + '/';
}

const devDataBase = readDevDataBase();

export const config = Object.freeze({
  dataBase: devDataBase || DEFAULT_DATA_BASE,
  /** Query params that must survive in-app navigation (dev data override). */
  preservedParams: devDataBase ? { data: devDataBase } : {},
  isDevData: Boolean(devDataBase),
  mapBase: '/map/',
  siteOrigin: 'https://instructorgrindset.com',
  consultUrl: '/contact',
  vendor: {
    chartjs: '/map/vendor/chart.js/chart.umd.min.js',
  },
  basemap: {
    // Protomaps PMTiles basemap plugs in here once tiles are hosted (R2).
    pmtiles_url: null,
    attribution: 'State outlines: US Census Bureau via us-atlas',
    colors: {
      background: '#0a0e14',
      stateFill: '#121821',
      stateFillActive: '#18202c',
      stateFillSelected: '#1f2a3a',
      stateLine: '#344055',
      stateLineSelected: '#a6c8ff',
      cluster: '#a6c8ff',
      tierA: '#e0a03a',
      tierB: '#7fa6d9',
      ring: '#ffffff',
    },
  },
  // Contiguous US, used for the national view.
  nationalBounds: [[-125.0, 24.4], [-66.9, 49.5]],
  phoneMaxWidth: 759.98,
  desktopPanelWidth: 380,
  comparePanelWidth: 600,
  compare: { min: 2, max: 4 },
});

export function prefersReducedMotion() {
  return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function isPhone() {
  return window.matchMedia(`(max-width: ${config.phoneMaxWidth}px)`).matches;
}
