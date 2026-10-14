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
  creditsUrl: '/map/credits/',
  vendor: {
    chartjs: '/map/vendor/chart.js/chart.umd.min.js',
  },
  basemap: {
    // Protomaps PMTiles basemap plugs in here once tiles are hosted (R2).
    pmtiles_url: null,
    // Satellite style underlay served with the site (images/basemap/, pipeline/tools/make_underlay.py): Natural Earth
    // relief, public domain, muted like the imagery paint below. It needs no outside host and shows in the 2D picture
    // before the click and in the WebGL map under every data layer. Resolved from this module, so it works under any
    // site root. Set to null to turn it off.
    underlay: {
      url: new URL('../../images/basemap/underlay.json', import.meta.url).href,
    },
    // Satellite imagery for detail (owner request 2026-10-13). Public domain US government imagery only.
    // Loads only once the WebGL map starts (first click or tap). Set to null to turn it off.
    imagery: {
      tiles: ['https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}'],
      tileSize: 256,
      // Above the underlay from zoom 6.5, faded in by 7.5 (cluster and resort zooms). No tile loads below 6.5,
      // and where tiles fail the underlay still shows.
      minzoom: 6.5,
      maxzoom: 16,
      attribution: 'Imagery: <a href="https://www.usgs.gov/programs/national-geospatial-program/national-map" target="_blank" rel="noopener">USGS The National Map</a>',
      // Subtle: muted and darkened so markers, outlines and heat colors stay readable.
      paint: { 'raster-saturation': -0.35, 'raster-brightness-max': 0.7, 'raster-contrast': -0.1, 'raster-opacity': ['interpolate', ['linear'], ['zoom'], 6.5, 0, 7.5, 1], 'raster-fade-duration': 0 },
    },
    // State fills turn translucent over the underlay and the imagery, as [zoom, opacity] stops (0 shows them plainly,
    // 1 hides them inside the states): light over the dark underlay, darker over the brighter imagery.
    state_fill_opacity: [[6.5, 0.3], [7.5, 0.55]],
    attribution: 'Ski areas: <a href="https://openskimap.org/" target="_blank" rel="noopener">OpenSkiMap</a>, \u00a9 <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a> (ODbL). State outlines: US Census Bureau via us-atlas. Relief: <a href="https://www.naturalearthdata.com/" target="_blank" rel="noopener">Natural Earth</a>. <a href="/map/credits/">Sources and credits</a>',
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
  // The contiguous US, framed by the national view. Alaska draws at its true place, up and to the left of it.
  nationalBounds: [[-125.0, 24.4], [-66.9, 49.5]],
  // How far the WebGL map pans and zooms out (map.js constrainCamera). The middle of the open map area (the map
  // minus the side panel or the phone sheet) stays inside `center`, [[west, south], [east, north]]: from past the
  // antimeridian, so the far Aleutians reach the middle, to past Maine, and from below the Florida Keys to the Arctic
  // coast past the Brooks Range. The map draws world copies, so Alaska's islands west of 180 degrees sit beside it.
  // Zooming out stops at `minZoom`, or sooner where the screen would span more than `maxWorldShare` of the world
  // (the map side by side with its own copy). At those zooms Alaska and the lower 48 show together.
  pan: { center: [[-190.0, 18.0], [-60.0, 72.0]], minZoom: 1.2, maxWorldShare: 0.62 },
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
