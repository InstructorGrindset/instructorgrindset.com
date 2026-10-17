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
    // The MAP / SATELLITE toggle (map/js/app.js, remembered per viewer). MAP darkens the underlay and the imagery into a
    // near black monochrome with MapLibre's raster paint (saturation, contrast, brightness); the 2D picture applies
    // the same math as an SVG color matrix (preview.js toneMatrix). SATELLITE keeps the full color.
    tone: {
      map: {
        // Water (about 0.23 in the underlay) and the image's faded edges fall to black, the base color, so the
        // image edges melt into it; land keeps its relief between about #141414 and #2a2a2a.
        background: '#000000',
        underlay: { 'raster-saturation': -1, 'raster-contrast': 0.5, 'raster-brightness-min': 0, 'raster-brightness-max': 0.75 },
        imagery: { 'raster-saturation': -1, 'raster-contrast': 0.2, 'raster-brightness-min': 0, 'raster-brightness-max': 0.38 },
      },
      satellite: {
        background: '#0a0e14',
        underlay: { 'raster-saturation': 0, 'raster-contrast': 0, 'raster-brightness-min': 0, 'raster-brightness-max': 1 },
        imagery: { 'raster-saturation': -0.35, 'raster-contrast': -0.1, 'raster-brightness-min': 0, 'raster-brightness-max': 0.7 },
      },
    },
    defaultTone: 'map',
    attribution: 'Ski areas: <a href="https://openskimap.org/" target="_blank" rel="noopener">OpenSkiMap</a>, \u00a9 <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a> (ODbL). State outlines: US Census Bureau via us-atlas. Relief: <a href="https://www.naturalearthdata.com/" target="_blank" rel="noopener">Natural Earth</a>. <a href="/map/credits/">Sources and credits</a>',
    // Console palette (World Monitor style): near black base, thin teal state lines, glowing category dots.
    colors: {
      background: '#000000',
      stateFill: '#141918',
      stateFillActive: '#1a2220',
      stateFillHover: '#22302b',
      stateFillSelected: '#25332e',
      stateLine: '#1f6b58',
      stateLineSelected: '#44ff88',
      cluster: '#4cc9f0',
      tierA: '#ffaa00',
      tierAStroke: '#ffe2a6',
      tierB: '#3388ff',
      ring: '#ffffff',
      hiring: '#44ff88',
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
  // The floating layers panel at the top left of the map (map.css --lp-w and --lp-inset match these). From a stage
  // of `besideFrom` px the map's padding keeps the panel beside the map; narrower stages fold its layers list and
  // start the map below its folded height.
  layersPanel: { width: 232, inset: 10, besideFrom: 1200, foldedHeight: 150 },
  desktopPanelWidth: 380,
  comparePanelWidth: 600,
  compare: { min: 2, max: 4 },
});

// The viewer's MAP / SATELLITE choice, kept in this browser only. Storage can be blocked (private windows, previews),
// so every read and write is guarded and the map falls back to config.basemap.defaultTone.
const TONE_KEY = 'careermap:basemap';
export function savedTone() {
  try {
    const v = window.localStorage.getItem(TONE_KEY);
    if (v && config.basemap.tone[v]) return v;
  } catch { /* storage unavailable */ }
  return config.basemap.defaultTone;
}
export function saveTone(v) {
  try { window.localStorage.setItem(TONE_KEY, v); } catch { /* storage unavailable: the choice lasts this visit */ }
}

export function prefersReducedMotion() {
  return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function isPhone() {
  return window.matchMedia(`(max-width: ${config.phoneMaxWidth}px)`).matches;
}
