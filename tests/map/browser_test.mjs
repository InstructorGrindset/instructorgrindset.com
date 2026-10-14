// Browser test for the Career Map. Rerun with:
//
//   node tests/map/browser_test.mjs [screenshot_dir]
//
// 1. Serves the repository root with `python -m http.server` and drives
//    /map/?data=/tests/map/fixtures/ (synthetic fixtures) through state,
//    cluster, resort and comparison views at 1280x800 and 390x844.
// 2. Builds a throwaway site root in a temp dir (symlinks to /map/js, /map/css,
//    /map/vendor, /assets, ...; fixtures copied to its data/build), runs
//    pipeline/routes.py against it, and loads real deep link stubs.
//    Nothing is written to the repository's data/build.
// 3. Loads /map/ with the real data/build output, when present.
//
// Fails (exit 1) on any console error, page error, failed request (except the
// site wide Rooftop font that /assets/global.css references), or assertion.
import { spawn, execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
function loadPlaywright() {
  for (const p of ['playwright', '/opt/node-tools/node_modules/playwright']) {
    try { return require(p); } catch { /* next */ }
  }
  throw new Error('playwright not found; set NODE_PATH or install it');
}
const { chromium } = loadPlaywright();

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const SHOTS = path.resolve(process.argv[2] || path.join(os.tmpdir(), 'career-map-screens'));
fs.mkdirSync(SHOTS, { recursive: true });
const FIX = '/tests/map/fixtures/';
const Q = `?data=${FIX}`;

const failures = [];
const log = (...a) => console.log(...a);
function check(cond, msg) {
  if (cond) log(`  ok   ${msg}`);
  else { failures.push(msg); log(`  FAIL ${msg}`); }
}

function findChromium() {
  const base = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  const candidates = [path.join(base, 'chromium')];
  try {
    for (const d of fs.readdirSync(base)) {
      if (d.startsWith('chromium-')) candidates.push(path.join(base, d, 'chrome-linux', 'chrome'));
    }
  } catch { /* none */ }
  return candidates.find((p) => { try { return fs.statSync(p).isFile(); } catch { return false; } });
}

async function serve(root, port) {
  const proc = spawn('python3', ['-m', 'http.server', String(port), '--bind', '127.0.0.1', '--directory', root],
    { stdio: ['ignore', 'ignore', 'pipe'] });
  for (let i = 0; i < 50; i++) {
    try { const r = await fetch(`http://127.0.0.1:${port}/`); if (r.ok || r.status) return proc; } catch { /* wait */ }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`server on ${port} did not start`);
}

function makeStubSite() {
  const t = fs.mkdtempSync(path.join(os.tmpdir(), 'career-map-site-'));
  for (const d of ['assets', 'partials', 'images', 'tests']) fs.symlinkSync(path.join(REPO, d), path.join(t, d));
  fs.mkdirSync(path.join(t, 'map'));
  for (const d of ['js', 'css', 'vendor']) fs.symlinkSync(path.join(REPO, 'map', d), path.join(t, 'map', d));
  fs.copyFileSync(path.join(REPO, 'map', 'index.html'), path.join(t, 'map', 'index.html'));
  fs.mkdirSync(path.join(t, 'data'));
  fs.cpSync(path.join(REPO, 'tests', 'map', 'fixtures'), path.join(t, 'data', 'build'), { recursive: true });
  // A copy of the fixtures without the slim index, for the fallback path.
  fs.cpSync(path.join(REPO, 'tests', 'map', 'fixtures'), path.join(t, 'legacy-build'), { recursive: true });
  fs.rmSync(path.join(t, 'legacy-build', 'map-index.json'));
  // ... and without the precomputed heat rasters, for the snowfall fallback to the resort only surface.
  fs.rmSync(path.join(t, 'legacy-build', 'heat'), { recursive: true, force: true });
  fs.rmSync(path.join(t, 'data', 'build', 'map-index.json'), { force: true });
  const out = execFileSync('python3', [path.join(REPO, 'pipeline', 'routes.py'), '--root', t], { encoding: 'utf8' });
  log(`  ${out.trim()}`);
  // A stub left behind for a cluster that no longer exists.
  fs.mkdirSync(path.join(t, 'map', 'co', 'retired-cluster'), { recursive: true });
  fs.copyFileSync(path.join(t, 'map', 'index.html'), path.join(t, 'map', 'co', 'retired-cluster', 'index.html'));
  return t;
}

function watch(page, label) {
  const problems = [];
  const requests = [];
  page.on('console', (m) => {
    if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) problems.push(`console: ${m.text()}`);
    if (m.type() === 'warning' && /\[map\]/.test(m.text())) {
      log(`    warn ${m.text()}`);
      // MapLibre style and expression errors are bugs, not data gaps.
      if (/layers\.|does not exist|expression|Expected|Unimplemented/.test(m.text())) problems.push(`style: ${m.text()}`);
    }
  });
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
  page.on('request', (r) => requests.push(r.url()));
  page.on('response', (r) => {
    // Allowed: the site wide Rooftop font (missing repo wide) and definitions.json, which is optional.
    if (r.status() === 404 && /\/definitions\.json$/.test(r.url())) { log(`    note definitions.json absent at ${r.url()}`); return; }
    // The fallback test build has no slim index on purpose.
    if (r.status() === 404 && /\/legacy-build\/(map-index|heat\/snowfall|heat\/season)\.json$/.test(r.url())) return;
    // The report form test answers one send with 500 on purpose to exercise the retry.
    if (r.status() === 500 && r.url() === 'https://reports.example.test/submit') return;
    if (r.status() >= 400 && !/\/fonts\/Rooftop/.test(r.url())) problems.push(`HTTP ${r.status()} ${r.url()}`);
  });
  page.on('requestfailed', (r) => {
    if (/\/fonts\/Rooftop/.test(r.url())) return;
    // MapLibre cancels imagery tiles it no longer needs while the camera moves.
    if (/basemap\.nationalmap\.gov\//.test(r.url()) && r.failure() && r.failure().errorText === 'net::ERR_ABORTED') return;
    // The Inter web font loads after the first paint, so a test that moves on quickly can cancel its download.
    if (/^https:\/\/fonts\.gstatic\.com\//.test(r.url()) && r.failure() && r.failure().errorText === 'net::ERR_ABORTED') return;
    problems.push(`request failed ${r.url()} ${r.failure() && r.failure().errorText}`);
  });
  return {
    requests,
    assertClean() {
      check(problems.length === 0, `${label}: no console errors or failed requests${problems.length ? `\n      ${problems.join('\n      ')}` : ''}`);
      problems.length = 0;
    },
  };
}

async function ready(page, { start = true } = {}) {
  await page.waitForFunction(() => window.__careerMap && window.__careerMap.ready, null, { timeout: 20000 });
  // The WebGL map starts on the first click or tap (owner decision 9); most checks need it running.
  if (start && await page.evaluate(() => Boolean(window.__careerMap.startMap) && !window.__careerMap.mapStarted)) {
    await page.locator('#map-start').click();
    await page.waitForFunction(() => window.__careerMap.mapCtl || document.querySelector('.map-fallback'), null, { timeout: 20000 });
  }
}

async function mapIdle(page) {
  // State outlines load after the map is interactive (Phase 8 performance); wait for them too.
  await page.waitForFunction(() => !window.__careerMap || !window.__careerMap.mapCtl || window.__careerMap.statesReady, null, { timeout: 15000 });
  await page.waitForFunction(() => {
    const m = window.__careerMap && window.__careerMap.mapCtl && window.__careerMap.mapCtl.map;
    return !m || (m.loaded() && !m.isMoving());
  }, null, { timeout: 15000 });
  await page.waitForTimeout(250);
}

const heading = (page) => page.locator('#panel-heading').innerText();
const shot = (page, name) => page.screenshot({ path: path.join(SHOTS, name) });

async function clickMapAt(page, lngLat) {
  const pt = await page.evaluate((ll) => {
    const m = window.__careerMap.mapCtl.map;
    const p = m.project(ll);
    const r = m.getContainer().getBoundingClientRect();
    return { x: r.left + p.x, y: r.top + p.y };
  }, lngLat);
  await page.mouse.click(pt.x, pt.y);
}

// Trail map dialog (map/js/trailmap.js): open, legend, a run hover, focus trap, Escape, reopen with its last view.
async function trailMapChecks(page, label, file, { shotName, via = 'panel', phone = false, fresh = true, base: home = null } = {}) {
  const tm = JSON.parse(fs.readFileSync(file, 'utf8'));
  const opener = via === 'control' ? page.locator('.trailmap-ctrl') : page.locator('#panel-content button', { hasText: 'Open the interactive map' });
  await opener.waitFor({ timeout: 10000 });
  check(await opener.count() === 1, `${label}: trail map ${via === 'control' ? 'map control' : 'button'} present`);
  await opener.click();
  const dlg = page.locator('dialog.trailmap-dialog');
  await page.waitForFunction(() => { const t = window.__careerMap.trail; return t && t.map && t.map.loaded() && t.map.getLayer('tm-run'); }, null, { timeout: 20000 });
  await page.waitForTimeout(300);
  check(await dlg.evaluate((d) => d.open && d.matches(':modal')), `${label}: trail map opens as a modal dialog`);
  check(await page.evaluate(() => document.querySelector('dialog.trailmap-dialog').contains(document.activeElement)), `${label}: focus moves into the dialog`);
  const legend = await dlg.locator('.trailmap-legend li').allInnerTexts();
  const signs = ['Green circle', 'Blue square', 'Black diamond', 'Double black diamond', 'Terrain park'];
  check(signs.every((s2) => legend.some((t) => t.startsWith(s2))) && legend.some((t) => t.startsWith('Lift')) && legend.some((t) => t.startsWith('Base area')),
    `${label}: legend lists the trail signs, lifts and the base area`);
  const attrib = await dlg.locator('.maplibregl-ctrl-attrib-inner').innerHTML();
  check(/OpenSkiMap/.test(attrib) && /OpenStreetMap contributors/.test(attrib) && /ODbL/.test(attrib), `${label}: attribution to OpenSkiMap and OpenStreetMap contributors (ODbL)`);
  const fit = await page.evaluate((b) => { const m = window.__careerMap.trail.map; const v = m.getBounds(); return v.getWest() <= b[0] + 1e-4 && v.getEast() >= b[2] - 1e-4 && v.getSouth() <= b[1] + 1e-4 && v.getNorth() >= b[3] - 1e-4; }, tm.bounds);
  if (fresh) check(fit, `${label}: trail map fits the resort bounds`);
  // Hover (or tap) the middle of a named line run.
  // The named, rated run whose middle lies farthest from the base area marker.
  const mid = (f) => f.geometry.coordinates[Math.floor(f.geometry.coordinates.length / 2)];
  const away = (f) => (home ? Math.hypot(mid(f)[0] - home[0], mid(f)[1] - home[1]) : 0);
  const cands = tm.runs.features.filter((f) => f.geometry.type === 'LineString' && f.properties.name && f.properties.difficulty_us && f.geometry.coordinates.length >= 3);
  const run = cands.sort((a, b2) => away(b2) - away(a))[0];
  const pick = mid(run);
  const pt = await page.evaluate((ll) => { const m = window.__careerMap.trail.map; const p2 = m.project(ll); const r = m.getContainer().getBoundingClientRect(); return { x: r.left + p2.x, y: r.top + p2.y }; }, pick);
  if (phone) await page.mouse.click(pt.x, pt.y); else await page.mouse.move(pt.x, pt.y);
  await page.waitForTimeout(250);
  const tip = await dlg.locator('.trailmap-label').innerText().catch(() => '');
  check(/\. (Green circle|Blue square|Black diamond|Double black diamond|Terrain park|No rating in OpenStreetMap)$/.test(tip), `${label}: ${phone ? 'tapping' : 'hovering'} a run names it and its sign (${tip})`);
  if (shotName) await page.screenshot({ path: path.join(SHOTS, shotName) });
  // Tab stays inside the dialog.
  let inside = true;
  for (let i = 0; i < 25; i++) {
    await page.keyboard.press('Tab');
    inside = inside && await page.evaluate(() => document.querySelector('dialog.trailmap-dialog').contains(document.activeElement));
  }
  check(inside, `${label}: Tab stays inside the dialog`);
  // Change the view, close with Escape, reopen: same view, focus returns to the opener.
  const z0 = await page.evaluate(() => { const m = window.__careerMap.trail.map; m.jumpTo({ zoom: m.getZoom() + 1 }); return m.getZoom(); });
  await page.keyboard.press('Escape');
  await page.waitForTimeout(150);
  check(!(await dlg.evaluate((d) => d.open)), `${label}: Escape closes the trail map`);
  check(await opener.evaluate((el) => el === document.activeElement), `${label}: focus returns to the ${via === 'control' ? 'map control' : 'button'}`);
  await opener.click();
  await page.waitForTimeout(250);
  const z1 = await page.evaluate(() => window.__careerMap.trail.map.getZoom());
  check(await dlg.evaluate((d) => d.open) && Math.abs(z1 - z0) < 1e-6, `${label}: reopening keeps the last view`);
  if (phone) {
    const box = await dlg.boundingBox();
    const vp = page.viewportSize();
    check(Math.abs(box.width - vp.width) <= 1 && Math.abs(box.height - vp.height) <= 1, `${label}: full screen sheet on the phone`);
  }
  await dlg.locator('.trailmap-close').click();
  await page.waitForTimeout(150);
  check(!(await dlg.evaluate((d) => d.open)) && await opener.evaluate((el) => el === document.activeElement), `${label}: the close button closes it and focus returns`);
}

// Imagery never gates readiness: tiles that hang or fail still give a working map on the dark base.
async function imageryOutage(browser, base) {
  log('\nImagery outage (tiles hang, then fail)');
  for (const mode of ['hang', 'fail']) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    await ctx.route(/basemap\.nationalmap\.gov\//, (route) => (mode === 'fail' ? route.abort('failed') : undefined));
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(`${base}/map/${Q}`);
    await ready(page);
    const t0 = Date.now();
    await page.waitForFunction(() => window.__careerMap.mapCtl && window.__careerMap.mapCtl.ready && !document.getElementById('map-start'), null, { timeout: 15000 })
      .catch(() => {});
    const st = await page.evaluate(() => {
      const c = window.__careerMap.mapCtl;
      return { ready: c.ready, button: Boolean(document.getElementById('map-start')), preview: document.querySelectorAll('.map-preview').length,
        resorts: c.map.queryRenderedFeatures({ layers: ['resorts'] }).length, mlLoaded: c.map.loaded() };
    });
    check(st.ready && !st.button && st.preview === 0 && st.resorts > 0,
      `imagery ${mode}: the map is ready in ${Date.now() - t0} ms after the click and the data layers draw (${st.resorts} resorts)`);
    if (mode === 'hang') check(!st.mlLoaded, 'imagery hang: readiness does not wait for MapLibre load (tiles still pending)');
    check(errors.length === 0, `imagery ${mode}: no page errors${errors.length ? ` (${errors.join('; ')})` : ''}`);
    if (mode === 'hang') await page.screenshot({ path: path.join(SHOTS, 'desktop-00b-imagery-hang.png') });
    await ctx.close();
  }
}

// Sample one pixel of the heat images at lon, lat (rows evenly spaced in Web Mercator), as [r, g, b, a].
function sampleHeat(page, lngLats) {
  return page.evaluate(async (pts) => {
    const m = window.__careerMap.mapCtl.map;
    const imgs = m.getStyle().layers.filter((l) => l.type === 'raster' && l.id.startsWith('heat-') && !l.id.endsWith('-inset'))
      .map((l) => m.getSource(l.id)).filter(Boolean).map((src) => ({ url: src.url, coordinates: src.coordinates }));
    const my = (la) => Math.log(Math.tan(Math.PI / 4 + (la * Math.PI) / 360));
    const decoded = [];
    for (const im of imgs) {
      const img = new Image(); img.src = im.url; await img.decode();
      const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight;
      const ctx = c.getContext('2d', { willReadFrequently: true }); ctx.drawImage(img, 0, 0);
      decoded.push({ ...im, ctx, width: c.width, height: c.height });
    }
    return pts.map(([lon, lat]) => {
      for (const im of decoded) {
        const [[w, n], , [e, s]] = im.coordinates;
        if (lon < w || lon > e || lat < s || lat > n) continue;
        const x = Math.floor(((lon - w) / (e - w)) * im.width);
        const y = Math.floor(((my(n) - my(lat)) / (my(n) - my(s))) * im.height);
        return [...im.ctx.getImageData(x, y, 1, 1).data];
      }
      return [0, 0, 0, 0];
    });
  }, lngLats);
}

async function desktop(browser, base) {
  log('\nDesktop 1280x800, fixtures via ?data=');
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const w = watch(page, 'desktop');
  await page.goto(`${base}/map/${Q}`);
  await ready(page, { start: false });
  await page.waitForTimeout(300);
  const before = await page.evaluate(() => ({
    started: window.__careerMap.mapStarted, maplibre: typeof window.maplibregl, preview: document.querySelectorAll('.map-preview').length,
    label: document.getElementById('map-start') && document.getElementById('map-start').textContent,
  }));
  check(!before.started && before.maplibre === 'undefined' && before.preview === 1, 'the WebGL map waits for a click; a 2D picture shows the view');
  check(before.label === 'Click to explore the map', `start button reads "${before.label}"`);
  await shot(page, 'desktop-00-start-map.png');
  await page.locator('#map-start').focus();
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => window.__careerMap.mapCtl, null, { timeout: 20000 });
  await page.waitForFunction(() => !document.getElementById('map-start'), null, { timeout: 20000 });
  check(await page.evaluate(() => document.activeElement === window.__careerMap.mapCtl.map.getCanvas()), 'Enter on the start button starts the map and focus moves to it');
  const imagery = await page.evaluate(() => {
    const m = window.__careerMap.mapCtl.map;
    const ids = m.getStyle().layers.map((l) => l.id);
    const src = m.getStyle().sources.imagery;
    const data = ['states-fill', 'clusters-fill', 'resorts'].map((id) => ids.indexOf(id));
    return { src: src && src.type, tiles: src && src.tiles[0], idx: ids.indexOf('imagery'), data, attrib: document.querySelector('.maplibregl-ctrl-attrib-inner').textContent };
  });
  check(imagery.src === 'raster' && /basemap\.nationalmap\.gov/.test(imagery.tiles) && imagery.idx === 1 && imagery.data.every((i) => i > imagery.idx),
    'USGS imagery raster sits under every data layer');
  check(imagery.attrib.includes('Imagery: USGS The National Map'), 'imagery attribution shows');
  await ready(page);
  await mapIdle(page);
  check(await heading(page) === 'United States', 'national heading');
  check(await page.title() === 'Instructor Grindset • Snowsports Career Map', 'national title');
  check(await page.locator('.nav-list a', { hasText: 'Colorado' }).count() === 1, 'national lists states as links');
  check(await page.evaluate(() => typeof window.Chart === 'undefined'), 'Chart.js not loaded on national view');
  check(await page.locator('#dev-badge').isVisible(), 'fixture badge visible');
  const fac = await page.evaluate(() => {
    const m = window.__careerMap.mapCtl.map;
    return { layer: !!m.getLayer('resorts-facility'), n: m.queryRenderedFeatures({ layers: ['resorts-facility'] }).length,
      images: ['fac-indoor-A', 'fac-training_center-A'].every((id) => m.hasImage(id)) };
  });
  check(fac.layer && fac.images && fac.n === 2, `indoor and training markers render as symbols (${fac.n})`);
  check(await page.locator('.legend', { hasText: 'Year round training center' }).count() === 1, 'legend lists facility markers');
  await page.getByRole('button', { name: 'Indoor and training' }).click();
  check(await page.evaluate(() => window.__careerMap.mapCtl.map.getLayoutProperty('resorts', 'visibility')) === 'none', 'filter hides ski area markers');
  await page.waitForTimeout(300);
  await shot(page, 'desktop-01b-national-facility-filter.png');
  await page.getByRole('button', { name: 'All', exact: true }).click();
  await shot(page, 'desktop-01-national.png');

  // Color modes: value heat maps (map/js/heat.js) with the resorts on top.
  const modes = page.locator('.recolor-modes .filter-btn');
  await page.waitForFunction(() => document.querySelectorAll('.recolor-modes .filter-btn').length === 4, null, { timeout: 10000 });
  check(JSON.stringify(await modes.allInnerTexts()) === JSON.stringify(['Default', 'Estimated net income', 'Season length', 'Snowfall']), 'recolor control lists four modes (no Professionalism)');
  check(await page.locator('.recolor-modes [aria-disabled="true"]').count() === 0, 'every mode enabled with fixture data');
  const leg = page.locator('#recolor-legend');
  const heatState = () => page.evaluate(() => {
    const m = window.__careerMap.mapCtl.map;
    const layers = m.getStyle().layers;
    const heat = layers.filter((l) => l.type === 'raster' && l.id.startsWith('heat-'));
    const at = (id) => layers.findIndex((l) => l.id === id);
    return {
      ids: heat.map((l) => l.id),
      belowResorts: heat.every((l) => at(l.id) < at('resorts') && at(l.id) < at('states-line')),
      color: JSON.stringify(m.getPaintProperty('resorts', 'circle-color')),
      stroke: JSON.stringify(m.getPaintProperty('resorts', 'circle-stroke-color')),
      resortsVisible: m.getLayoutProperty('resorts', 'visibility') !== 'none',
    };
  });
  // Points far from every fixture resort, between distant clusters, all inside the fixture states.
  const BETWEEN = [[-115.5, 40.0], [-108.5, 42.8], [-117.0, 37.5], [-112.5, 37.6], [-72.0, 43.6]];
  // Season length covers the states with season data (NV, CA, UT here); deep inside one without (WY) it stays clear.
  const SEASON_BETWEEN = [[-115.5, 40.0], [-117.0, 37.5], [-112.5, 37.6]];
  const heatModes = [
    { mode: 'lucrativeness', prop: 'lucrativeness_mid', unit: 'USD per season', range: 'Resort values: $8,900 to $10,600', n: 2, first: '$8.5K', last: '$11K', raster: true, between: BETWEEN },
    { mode: 'season', prop: 'season_length_days', unit: 'Days', range: 'Resort values: 112 to 168', n: 11, first: '90', last: '180', raster: true, classed: true, between: SEASON_BETWEEN, clear: [[-107.5, 43.6]] },
    { mode: 'snowfall', prop: 'snowfall_annual_in', unit: 'Inches per season', range: null, n: 9, first: '0', last: '800', raster: true, classed: true, between: BETWEEN },
  ];
  const firstRaster = w.requests.length;
  for (const hm of heatModes) {
    const before = w.requests.length;
    await page.locator(`.recolor-modes .filter-btn[data-mode="${hm.mode}"]`).click();
    await page.waitForFunction((k) => window.__careerMap.mapCtl.map.getStyle().layers.some((l) => l.id.startsWith(`heat-${k}-`)), hm.mode, { timeout: 10000 });
    await page.waitForFunction(() => window.__careerMap.mapCtl.map.loaded(), null, { timeout: 10000 });
    await page.waitForTimeout(300);
    const st = await heatState();
    const text = await leg.innerText();
    const ticks = await leg.locator('.heat-tick').allInnerTexts();
    check(st.ids.length >= 1 && st.ids.every((id) => id.startsWith(`heat-${hm.mode}-`)), `${hm.mode}: drawn as a heat surface (${st.ids.join(', ')})`);
    check(st.belowResorts && st.resortsVisible, `${hm.mode}: heat sits under the state lines and the resort points`);
    check(st.color.includes(hm.prop) && st.stroke.includes('#e0a03a'), `${hm.mode}: points stay on top, Tier A outlined, no data points grey`);
    if (hm.ticks) {
      check(JSON.stringify(ticks) === JSON.stringify(hm.ticks) && !/Min|Max|100|Resort values/.test(text), `${hm.mode}: legend shows only ${hm.ticks.join(', ')}`);
    } else {
      check(await leg.isVisible() && text.includes(hm.unit) && (hm.range ? text.includes(hm.range) : !/Resort values/.test(text))
        && ticks.length >= 3 && ticks[0] === hm.first && ticks[ticks.length - 1] === hm.last,
        `${hm.mode}: legend with numeric ticks (${ticks.join(', ')}), units and ${hm.range ? 'the sourced resort range' : 'its fixed scale'}`);
    }
    check(text.includes(`${hm.n} resort`) && await leg.locator('.heat-bar').count() === 1, `${hm.mode}: legend counts ${hm.n} resorts with data and shows the ramp`);
    if (hm.classed) {
      const bar = await leg.locator('.heat-bar').evaluate((el) => getComputedStyle(el).backgroundImage);
      check(await leg.locator('.heat-bar-classed').count() === 1 && (bar.match(/rgb\(/g) || []).length >= 16,
        `${hm.mode}: legend draws hard edged class boxes`);
    }
    check(/smoothed estimates/.test(text) && /only sourced values/.test(text), `${hm.mode}: legend says the colors between resorts are smoothed estimates`);
    // A real heat map: every point between distant resorts is colored (no gaps), at the floor opacity or more.
    const between = await sampleHeat(page, hm.between);
    check(between.every((px) => px[3] >= 0.33 * 255), `${hm.mode}: continuous coverage between distant resorts (alpha ${between.map((px) => px[3]).join(', ')})`);
    if (hm.clear) {
      const clear = await sampleHeat(page, hm.clear);
      check(clear.every((px) => px[3] === 0), `${hm.mode}: a state with no data stays clear far from its border (alpha ${clear.map((px) => px[3]).join(', ')})`);
    }
    const iso = await page.evaluate(() => ['heat-iso-minor', 'heat-iso-major', 'heat-iso-label'].filter((id) => window.__careerMap.mapCtl.map.getLayer(id)));
    if (hm.raster) {
      const src = await page.evaluate((k) => {
        const m = window.__careerMap.mapCtl.map;
        const ids = m.getStyle().layers.filter((l) => l.type === 'raster' && l.id.startsWith(`heat-${k}-`)).map((l) => l.id);
        return { urls: ids.map((id) => m.getSource(id).url), credit: Boolean(m.getSource('heat-credit')),
          attrib: (document.querySelector('.maplibregl-ctrl-attrib-inner') || {}).textContent || '' };
      }, hm.mode);
      const re = new RegExp(`/tests/map/fixtures/heat/${hm.mode}-lower48\\.png$`);
      check(src.urls.length >= 1 && src.urls.every((u) => re.test(u)), `${hm.mode}: the precomputed raster source loads (${src.urls.join(', ')})`);
      const mine = w.requests.slice(before);
      check(mine.some((u) => u.endsWith(`/heat/${hm.mode}.json`)) && mine.some((u) => u.endsWith(`/heat/${hm.mode}-lower48.png`))
        && !w.requests.slice(0, before).some((u) => u.includes(`/heat/${hm.mode}`)), `${hm.mode}: the sidecar and the PNG are fetched once the mode is selected`);
      if (hm.mode === 'snowfall') {
        check(src.credit && src.attrib.includes('Snowfall: NOAA NCEI 1991 to 2020 normals and NRCS SNOTEL'), `snowfall: attribution names the NOAA normals and NRCS SNOTEL (${src.attrib.slice(0, 160)})`);
        check(/NOAA station normals, 1991 to 2020, and NRCS SNOTEL mountain stations/.test(text), 'snowfall: legend names the NOAA and SNOTEL background');
        check(/Grey: under 24 in, little or no snow/.test(text) && /Lines every 50 in from 200, bolder every 100/.test(text),
          'snowfall: legend explains the grey class and the isolines');
        await page.waitForFunction(() => Boolean(window.__careerMap.mapCtl.map.getLayer('heat-iso-label')), null, { timeout: 10000 }).catch(() => {});
        const isoState = await page.evaluate(() => {
          const m = window.__careerMap.mapCtl.map;
          const layers = m.getStyle().layers.map((l) => l.id);
          const src = m.getSource('heat-iso');
          return { layers: ['heat-iso-minor', 'heat-iso-major', 'heat-iso-label'].map((id) => layers.indexOf(id)), states: layers.indexOf('states-line'),
            resorts: layers.indexOf('resorts'), features: src ? (src._data && src._data.features ? src._data.features.length : -1) : 0,
            labelMin: m.getLayer('heat-iso-label') ? m.getLayer('heat-iso-label').minzoom : null, image: m.hasImage('iso-300') };
        });
        check(isoState.layers.every((i) => i >= 0 && i < isoState.resorts) && isoState.layers[1] < isoState.states && isoState.features !== 0,
          `snowfall: isolines draw over the heat surface and under the resorts (${JSON.stringify(isoState)})`);
        check(isoState.labelMin >= 6 && isoState.image, 'snowfall: isoline labels are images (no glyph server) shown from mountain range zoom');
        check(mine.some((u) => u.endsWith('/heat/snowfall-isolines-lower48.geojson')), 'snowfall: the isolines load with the mode');
      } else if (hm.mode === 'season') {
        check(!src.credit && /States with no resort data stay clear/.test(text) && iso.length === 0,
          'season: resort values only (no NOAA credit), no isolines, and the legend says states without data stay clear');
      } else {
        check(!src.credit && !/States with no resort data stay clear/.test(text) && iso.length === 0,
          'lucrativeness: the precomputed resort surface, no NOAA credit, no isolines');
      }
    } else {
      check(iso.length === 0, `${hm.mode}: no isolines`);
    }
    await shot(page, `desktop-01c-heat-${hm.mode}.png`);
  }
  check(!w.requests.slice(0, firstRaster).some((u) => /\/heat\//.test(u)), 'heat rasters load only once a raster mode is selected');
  // Zoomed into the Front Range: the surface stays smooth around each resort.
  await page.evaluate(() => window.__careerMap.mapCtl.map.jumpTo({ center: [-106.1, 39.6], zoom: 7 }));
  await page.waitForTimeout(600);
  await shot(page, 'desktop-01d-heat-snowfall-zoomed.png');
  await page.evaluate(() => window.__careerMap.mapCtl.refit());
  await page.locator('.recolor-modes .filter-btn[data-mode="default"]').click();
  await page.waitForTimeout(200);
  check(await leg.isHidden() && (await heatState()).ids.length === 0, 'Default removes the heat surface and hides its legend');
  check(await page.evaluate(() => !window.__careerMap.mapCtl.map.getSource('heat-credit') && !window.__careerMap.mapCtl.map.getSource('heat-iso')
    && !window.__careerMap.mapCtl.map.getLayer('heat-iso-major')), 'Default removes the NOAA credit and the isolines');
  check((await heatState()).color.includes('#e0a03a') && !(await heatState()).color.includes('season_length_days'), 'Default restores tier colors');

  // The surface shows value, not density: a cluster of low resorts counts as one voice, opacity never grows with
  // density, a lone resort shows its own value, and far areas keep the floor opacity (never empty).
  const physics = await page.evaluate(async (fix) => {
    const { buildHeat, makeRamp } = await import('/map/js/heat.js');
    const defs = (await (await fetch(`${fix}definitions.json`)).json()).heat;
    const ramp = makeRamp(defs.ramp.colors);
    const sample = async (images, lon, lat) => {
      for (const im of images) {
        const [[w, n], , [e, s]] = im.coordinates;
        if (lon < w || lon > e || lat < s || lat > n) continue;
        const img = new Image(); img.src = im.url; await img.decode();
        const c = document.createElement('canvas'); c.width = im.width; c.height = im.height;
        const ctx = c.getContext('2d'); ctx.drawImage(img, 0, 0);
        const my = (la) => Math.log(Math.tan(Math.PI / 4 + (la * Math.PI) / 360));
        const x = Math.floor(((lon - w) / (e - w)) * im.width);
        const y = Math.floor(((my(n) - my(lat)) / (my(n) - my(s))) * im.height);
        return [...ctx.getImageData(x, y, 1, 1).data];
      }
      return [0, 0, 0, 0];
    };
    const dense = Array.from({ length: 12 }, (_, i) => ({ lon: -106 + (i % 4) * 0.05, lat: 39.5 + Math.floor(i / 4) * 0.05, value: 10 }));
    const lone = { lon: -100, lat: 39.5, value: 10 };
    const high = { lon: -94, lat: 39.5, value: 90 };
    const opts = { scale: 'linear', domain: { min: 10, max: 90 }, ramp, field: defs.field, regions: defs.regions };
    const imgs = buildHeat([...dense, lone, high], null, opts);
    // Cluster against a lone high resort, equally far from both: one voice each gives the middle of the ramp.
    const duel = buildHeat([...dense, { lon: -103, lat: 39.5, value: 90 }], null, opts);
    return {
      denseAt: await sample(imgs, -105.92, 39.55), loneAt: await sample(imgs, lone.lon, lone.lat), highAt: await sample(imgs, high.lon, high.lat),
      far: await sample(imgs, -70, 30), mid: await sample(duel, -104.46, 39.55), floor: defs.field.floor_alpha,
      low: ramp.rgb(0), top: ramp.rgb(1), middle: ramp.rgb(0.5),
    };
  }, FIX);
  const near = (a, b, tol = 6) => a.slice(0, 3).every((v, i) => Math.abs(v - b[i]) <= tol);
  check(near(physics.denseAt, physics.low) && near(physics.loneAt, physics.low), `twelve low resorts read as low as one low resort (${physics.denseAt} vs ${physics.loneAt})`);
  check(Math.abs(physics.denseAt[3] - physics.loneAt[3]) <= 3, `density does not raise opacity (${physics.denseAt[3]} vs ${physics.loneAt[3]})`);
  check(near(physics.highAt, physics.top), `a lone high resort shows the top of the ramp (${physics.highAt})`);
  check(near(physics.mid, physics.middle, 14), `a cluster of twelve counts as one voice against a lone resort (${physics.mid} vs ${physics.middle})`);
  check(physics.far[3] >= Math.floor(physics.floor * 255) - 1 && physics.far[3] <= Math.ceil(physics.floor * 255) + 1,
    `far from every resort the surface keeps the floor opacity (${physics.far[3]})`);

  // Click a state on the map.
  await clickMapAt(page, [-105.0, 38.4]);
  await page.waitForURL(/\/map\/co\/\?data=/);
  await mapIdle(page);
  check(await heading(page) === 'Colorado', 'map click on state opens Colorado');
  check(await page.evaluate(() => document.activeElement && document.activeElement.id === 'panel-heading'), 'focus moves to panel heading');
  check((await page.locator('#panel-status').innerText()).includes('Colorado'), 'aria-live status announces state');
  check(await page.locator('.nav-list a', { hasText: 'Fixture Front Range' }).count() === 1, 'state lists clusters');
  check((await page.locator('.intro').innerText()).includes('5 ski areas and 1 indoor facility'), 'state intro counts ski areas and facilities');
  await shot(page, 'desktop-02-state.png');

  // Click a cluster on the map, away from its resort.
  await page.evaluate(() => window.__careerMap.mapCtl.map.jumpTo({ center: [-107.81, 37.47], zoom: 9 }));
  await mapIdle(page);
  await clickMapAt(page, [-107.81, 37.55]);
  await page.waitForURL(/\/map\/co\/fixture-high-desert\//);
  check(await heading(page) === 'Fixture High Desert', 'map click on cluster opens cluster');

  // Panel navigation to another cluster.
  await page.locator('.crumbs a', { hasText: 'Colorado' }).click();
  await page.locator('.nav-list a', { hasText: 'Fixture Front Range' }).click();
  await page.waitForURL(/\/map\/co\/fixture-front-range\//);
  await mapIdle(page);
  check(await heading(page) === 'Fixture Front Range', 'cluster heading');
  const stats = await page.locator('.stats').first().innerText();
  const note = await page.locator('.stats-note').innerText();
  check(/Colorado/.test(stats) && /Resorts\s*5/i.test(stats) && /Tier A\s*3/i.test(stats) && note === 'Includes 4 ski areas and 1 indoor facility.',
    `cluster summary shows states, resort count with facilities, Tier A count (${stats.replace(/\s+/g, ' ')} | ${note})`);
  check(await page.locator('.resort-list .tier-facility', { hasText: 'Indoor ski facility' }).count() === 1, 'cluster list tags the indoor facility');
  check((await page.locator('.editorial', { hasText: 'About this cluster' }).innerText()).includes('Synthetic cluster take.'), 'cluster overview and take render');
  check(await page.locator('.panel-footer .related a').count() === 5, 'panel footer links related tools and reading');
  check(await page.locator('.resort-list .tier-draft').count() === 1, 'Draft profile tag on draft Tier A resort');
  await shot(page, 'desktop-03-cluster.png');

  // Resort with climate.
  await page.locator('.resort-list a', { hasText: 'Alpha Peak' }).click();
  await page.waitForURL(/\/map\/co\/fixture-front-range\/alpha-peak\//);
  await page.waitForFunction(() => document.querySelectorAll('.climate canvas').length >= 5, null, { timeout: 10000 });
  await mapIdle(page);
  await page.waitForTimeout(500);
  check(await heading(page) === 'Alpha Peak', 'resort heading');
  check((await page.title()).startsWith('Instructor pay, housing, and snow at Alpha Peak'), 'resort title');
  check(await page.locator('link[rel=canonical]').getAttribute('href') === 'https://instructorgrindset.com/map/co/fixture-front-range/alpha-peak/', 'resort canonical');
  check(await page.locator('.badge-published').count() > 5, 'published badges render');
  check(await page.locator('.badge-estimated').count() >= 1, 'estimated badge renders');
  check(await page.locator('.fact-meta', { hasText: 'Last verified Oct 1, 2026' }).count() > 3, 'last verified dates render');
  check(await page.locator('a.src-link', { hasText: 'Synthetic fixture: resort facts' }).count() > 0, 'source links resolve via sources.json');
  check(await page.locator('.tier-row .tier-approved', { hasText: 'Approved' }).count() === 1, 'approved Tier A shows Approved');
  check(await page.locator('.tier-row .tier-draft').count() === 0, 'approved Tier A has no Draft profile tag');
  check(await page.locator('.tier-row .tier-facility', { hasText: 'Ski area' }).count() === 1, 'resort shows facility type');
  check(await page.locator('.criteria .criterion', { hasText: 'Operator compensation' }).count() === 1, 'criterion label Operator compensation');
  check(await page.locator('.criteria .criterion-why', { hasText: 'Synthetic rationale for operator compensation.' }).count() === 1, 'criterion rationale renders');
  check(await page.locator('.criteria a.src-link').count() === 2, 'each criterion links its source');
  check(await page.locator('.panel-section .fact-meta', { hasText: 'Approved by Fixture Reviewer on Oct 5, 2026.' }).count() === 1, 'approval by and date render');
  check(await page.locator('.fact', { hasText: 'Summit elevation' }).locator('.badge-published').count() === 1, 'summit elevation renders with badge');
  check(await page.locator('.src-note', { hasText: 'synthetic source note' }).count() >= 1, 'source note renders');
  check(await page.locator('.src-tag', { hasText: 'SNOTEL' }).count() === 1, 'density chart labelled SNOTEL');
  check(await page.locator('.chart-caption', { hasText: 'A powder day brings 6 in or more' }).count() === 1, 'powder threshold read from definitions.json');
  // Chart.js draws the charts when their section nears the screen (charts.js nearView).
  check(await page.evaluate(() => typeof window.Chart === 'undefined'), 'Chart.js waits while the charts are out of view');
  await page.locator('.climate').scrollIntoViewIfNeeded();
  await page.waitForFunction(() => window.Chart && Object.keys(window.Chart.instances).length >= 5, null, { timeout: 10000 });
  check(await page.evaluate(() => {
    const c = window.Chart && Object.values(window.Chart.instances).find((x) => x.data.datasets.length === 3);
    return c ? c.data.datasets[0].label : '';
  }) === 'Dry, under 7%', 'density legend uses definitions.json cutoffs');
  check(await page.locator('.climate canvas').count() === 5, 'five climate charts');
  await page.locator('#panel-scroll').evaluate((el) => { el.scrollTop = 0; });
  check(w.requests.some((u) => u.includes('/climate/alpha-peak.json')), 'climate lazy loaded for resort');
  check(await page.locator('.panel-footer a[href="/contact"]').count() === 1, 'Free Consultation link in footer');
  await shot(page, 'desktop-04-resort.png');

  // Career section.
  const payRows = page.locator('.pay-table tbody tr');
  check(await payRows.count() === 6, 'pay table has a row per cert level');
  check(JSON.stringify(await page.locator('.pay-table tbody th').allInnerTexts()) ===
    JSON.stringify(['No certification', 'Level 1', 'Level 2', 'Level 3', 'Trainer', 'Examiner']), 'pay table cert labels in order');
  const l1 = page.locator('.pay-table tr[data-level="level_1"]');
  check(await l1.locator('.tier-stale', { hasText: 'Needs update' }).count() === 1, 'stale pay row shows Needs update');
  check(/2024\/25 season/.test(await l1.innerText()) && await l1.locator('.badge-published').count() === 1, 'pay row shows season and badge');
  check(await page.locator('.pay-table tr[data-level="level_2"] .tier-stale').count() === 0, 'fresh pay row has no Needs update tag');
  check(await page.locator('.pay-table tr[data-level="none"] a.src-link').count() === 1, 'pay row links its source');
  const rate = page.locator('.career-facts .fact', { hasText: 'Private lesson rate' });
  check((await rate.innerText()).includes('$150 to $220 per hour') && await rate.locator('.badge-published').count() === 1, 'private lesson rate with badge');
  const split = page.locator('.career-facts .fact', { hasText: 'Instructor split' });
  check((await split.innerText()).includes('45% to the instructor') && await split.locator('.badge-reported').count() === 1
    && await split.locator('.tier-stale').count() === 1, 'instructor split with badge and Needs update');
  // Owner decision 2026-10-13: no professionalism rating anywhere on the page.
  check(!/professionalism/i.test(await page.locator('body').innerText()) && await page.locator('.breakdown, .score-trigger, .professionalism').count() === 0,
    'the resort panel shows no professionalism rating');
  // Owner decision 2026-10-14: "Estimated net income" at the middle of the wage range, one basis line, then the range.
  const luc = page.locator('.score-block', { hasText: 'Estimated net income' });
  const lucText = await luc.innerText();
  check((await luc.locator('.sub').first().innerText()) === 'Estimated net income' && (await luc.locator('.fact-value').first().innerText()) === '$8,900'
    && (await luc.locator('.lucr-basis').innerText()) === 'Middle of the posted ski school wage range, 40 hours a week, minus median rent. Before tax.'
    && (await luc.locator('.lucr-range').innerText()) === 'Range: $3,200 to $14,600 across the wage scale.',
  'net income headline is the middle of the wage range, with the basis line and the bottom to top range');
  check(!/saving|mid case/i.test(lucText), `net income block never says savings or mid case (${lucText.replace(/\n/g, ' | ')})`);
  check(await page.locator('.assumptions li').count() === 9, 'lucrativeness lists the new assumptions (basis keys stay out)');
  const assumptions = (await page.locator('.assumptions').innerText()).replace(/\s+/g, ' ');
  check(assumptions.includes('Hours per week, standard full time 40') && assumptions.includes('$32.50') && assumptions.includes('Middle of the posted wage range (USD per hour) $26.75') && assumptions.includes('$1,150') && assumptions.includes('4.33')
    && assumptions.includes('Tips are not included yet') && assumptions.includes('Before tax and other living costs') && !assumptions.includes('Not set yet'), `assumption values formatted, tips note shown (${assumptions.replace(/\n/g, ' | ')})`);
  // Owner decision 2026-10-14: the median of the sourced median rents, with the place, the publishers and every source.
  check((await luc.locator('.rent-line .rent-text').innerText()) === 'Median rent $1,150 a month for one bedroom in Fixture Town, CO (median of 2 sources: Fixture Rentals, Fixture Daily), 2026'
    && await luc.locator('.rent-line a.src-link').count() === 2 && await luc.locator('.rent-line .badge-estimated').count() === 1,
  'median rent line names the place and the publishers and links every source');
  await shot(page, 'desktop-04b-career.png');
  check((await page.locator('.pay-table tr[data-level="level_3"]').innerText()).includes('Range from 4 reports')
    && await page.locator('.pay-table tr[data-level="level_3"] .badge-reported').count() === 1, 'reported pay row shows its aggregate count');
  check(await page.locator('.tag', { hasText: 'Destination families' }).count() === 1, 'client profile chips use readable labels');
  check(await page.locator('.sub', { hasText: 'Best suited for skiers and riders' }).count() === 1, 'best suited tags render');

  // Phase 5: resort detail map and Housing and commute.
  await page.waitForSelector('.site-select', { timeout: 10000 });
  // Owner decision 6: access rating with its one sentence summary and source.
  const acc = page.locator('.panel-section', { hasText: 'Housing and commute' }).locator('.access');
  check(await acc.count() === 1 && (await acc.locator('.access-rating').innerText()) === 'Transit friendly'
    && (await acc.innerText()).includes('A free bus links the staff village') && await acc.locator('a.src-link').count() === 1,
    'access rating, summary and source show in Housing and commute');
  await mapIdle(page);
  const counts = () => page.evaluate(() => {
    const m = window.__careerMap.mapCtl.map;
    const n = (id) => (m.getLayer(id) && m.getLayoutProperty(id, 'visibility') !== 'none' ? m.queryRenderedFeatures({ layers: [id] }).length : 0);
    return { lifts: n('detail-lifts'), point: n('detail-housing-point'), area: n('detail-housing-area'), iso: n('detail-iso-fill'),
      transit: n('detail-transit-line'), stops: n('detail-transit-stop'), base: n('detail-base'),
      bus: n('detail-commute-bus'), drive: n('detail-commute-drive') };
  });
  let c = await counts();
  check(c.lifts > 0 && c.base === 1 && c.point === 1 && c.area === 3 && c.iso > 0 && c.transit > 0 && c.stops === 2,
    `detail map draws lifts, base, housing by precision, isochrones, transit (${JSON.stringify(c)})`);
  check(c.bus === 0 && c.drive === 0, 'commute lines hidden until a site is selected');
  check(await page.locator('body.detail-mode').count() === 1 && await page.locator('#recolor').isHidden(), 'detail mode replaces the recolor control');
  check(JSON.stringify(await page.locator('.detail-toggles label').allInnerTexts()) === JSON.stringify(['Lifts', 'Housing', 'Commute', 'Transit', 'Drive time']), 'a toggle per layer with data');
  check((await page.locator('#detail-layers').innerText()).includes('45 min'), 'isochrone legend shows 15, 30, 45 minutes');
  check(await page.locator('.site-select').count() === 4, 'housing lists every site');
  check(JSON.stringify(await page.locator('.site-select .nav-name').allInnerTexts()) === JSON.stringify(['Fixture Staff Village', 'Fixture Town Lease', 'Fixture Listed Lodge', 'Fixture West Rentals']), 'employee housing listed first');
  const lease = page.locator('.site', { hasText: 'Fixture Town Lease' });
  check((await lease.innerText()).includes('$800 to $950 per month') && await lease.locator('.fact', { hasText: 'Rent' }).locator('.badge-reported').count() === 1, 'site rent range with badge');
  check(/Master lease\s+Fixture Town · Neighborhood area, shown as a circle/.test(await lease.innerText()), 'site type badge, town and precision note');
  check((await lease.locator('.commute-table').innerText()).includes('Bus\t28 min\t7.4 mi\tBlue'), 'commute row shows mode, minutes, distance and route name');
  check(await lease.locator('.not-verified', { hasText: 'Being calculated' }).count() === 1, 'missing commute minutes marked');
  check(await page.locator('.site', { hasText: 'Fixture West Rentals' }).locator('.not-verified', { hasText: 'Commute times are being calculated.' }).count() === 1, 'site without commutes shows empty state');
  check((await page.locator('.site', { hasText: 'Fixture Staff Village' }).innerText()).includes('Synthetic note: full time staff only.'), 'eligibility note renders');
  check(await page.locator('.housing .facts .fact', { hasText: 'HUD FMR' }).count() === 3 && await page.locator('.housing .fact', { hasText: 'ACS median gross rent' }).locator('.badge-estimated').count() === 1, 'rent baseline with badges');
  check((await page.locator('.rent-line .rent-text').innerText()).startsWith('Median rent $1,150 a month') && await page.locator('.rent-used').count() === 0
    && await page.locator('.rent-line a.src-link').count() === 2, 'lucrativeness says which rent was used, with its sources, once the housing data loads');
  check(await page.locator('.assumptions li', { hasText: 'Rent basis' }).count() === 0, 'rent basis keys stay out of the assumption list');
  check((await page.locator('.site', { hasText: 'Fixture Staff Village' }).locator('.fact', { hasText: 'Address' }).innerText()).includes('1 Fixture Way, Fixture Town'), 'building site shows its sourced address');
  await page.locator('.site-select', { hasText: 'Fixture Town Lease' }).click();
  await page.waitForFunction(() => window.__careerMap.mapCtl.map.queryRenderedFeatures({ layers: ['detail-commute-bus'] }).length > 0, null, { timeout: 5000 }).catch(() => {});
  c = await counts();
  check(await page.locator('.site-select[aria-pressed="true"]').innerText().then((t) => t.includes('Fixture Town Lease')) && c.bus > 0, 'selecting a site draws its commute line');
  check(await page.evaluate(() => window.__careerMap.mapCtl.map.getFeatureState({ source: 'detail-housing', id: 'alpha-town-lease' }).selected === true), 'selected site pin is highlighted');
  await page.locator('#panel-scroll').evaluate((el) => { el.scrollTop = el.querySelector('.housing').offsetTop - 80; });
  await shot(page, 'desktop-10-detail-map.png');
  // Click the building pin on the map.
  const fx = JSON.parse(fs.readFileSync(path.join(REPO, 'tests', 'map', 'fixtures', 'detail', 'alpha-peak.json'), 'utf8'));
  const sv = fx.housing.find((x) => x.id === 'alpha-staff-village');
  await clickMapAt(page, [sv.lon, sv.lat]);
  await page.waitForTimeout(300);
  check((await page.locator('.site-select[aria-pressed="true"]').innerText()).includes('Fixture Staff Village'), 'clicking a housing pin selects it in the panel');
  check(new URL(page.url()).pathname.endsWith('/alpha-peak/'), 'housing pin click keeps the resort view');
  // Lift hover name.
  const lift = fx.lifts.features[1].geometry.coordinates;
  const mid = [(lift[0][0] + lift[1][0]) / 2, (lift[0][1] + lift[1][1]) / 2];
  const lp = await page.evaluate((ll) => { const m = window.__careerMap.mapCtl.map; const p = m.project(ll); const r = m.getContainer().getBoundingClientRect(); return { x: r.left + p.x, y: r.top + p.y }; }, mid);
  await page.mouse.move(lp.x, lp.y);
  await page.waitForTimeout(200);
  check((await page.locator('.map-tip').innerText()).startsWith('Lift: Fixture'), 'lift hover shows its name');
  // Transit legend: each feed's agency (linked when agency.txt gives a web link), schedule dates and note.
  const feedRows = page.locator('#detail-layers .legend-feed');
  const feedText = await feedRows.allInnerTexts();
  check(feedText.length === 2 && feedText[0].includes('Fixture Transit') && feedText[0].includes('Schedule for May 21 to Dec 6, 2026')
    && feedText[0].includes('Offseason schedule. Winter ski service can differ; check the agency.')
    && feedText[1].includes('Fixture Shuttle') && feedText[1].includes('Schedule for Nov 19, 2025 to Apr 12, 2026')
    && feedText[1].includes("Last season's schedule. This winter's service can differ; check the agency."),
  `transit legend shows each feed's agency, dates and schedule note (${feedText.map((t) => t.replace(/\n/g, ' / ')).join(' | ')})`);
  check(await feedRows.nth(0).locator('a[href="https://transit.example.com/"][target="_blank"][rel="noopener"]').count() === 1
    && await feedRows.nth(1).locator('a').count() === 0, 'agency name links to its site; a link that is not http or https is dropped');
  // Route hover: route, agency and schedule (housing hidden so its soft circles do not catch the pointer).
  await page.locator('#layer-housing').uncheck();
  const blue = fx.transit.routes[0].geometry.coordinates[0];
  const rp = await page.evaluate((ll) => { const m = window.__careerMap.mapCtl.map; const p = m.project(ll); const r = m.getContainer().getBoundingClientRect(); return { x: r.left + p.x, y: r.top + p.y }; },
    [blue[0][0] * 0.6 + blue[1][0] * 0.4, blue[0][1] * 0.6 + blue[1][1] * 0.4]);
  await page.mouse.move(rp.x, rp.y);
  await page.waitForTimeout(200);
  const routeTip = await page.locator('.map-tip').innerText();
  check(routeTip === 'Route: Blue, Fixture Transit. Schedule for May 21 to Dec 6, 2026', `route hover shows the route, agency and schedule (${routeTip})`);
  await page.locator('#layer-housing').check();
  await page.locator('#layer-isochrones').uncheck();
  check(await page.evaluate(() => window.__careerMap.mapCtl.map.getLayoutProperty('detail-iso-fill', 'visibility')) === 'none', 'drive time toggle hides isochrones');
  await page.locator('#layer-isochrones').check();
  await shot(page, 'desktop-11-detail-pin-selected.png');
  // Official trail map: a link to the resort's own map (new tab), never an image or frame in the page.
  const off = page.locator('#panel-content .official-trailmap');
  check(await off.count() === 1 && (await off.locator('a').innerText()) === 'Official trail map from Alpha Peak'
    && (await off.locator('a').getAttribute('target')) === '_blank' && /noopener/.test(await off.locator('a').getAttribute('rel'))
    && (await off.innerText()).includes('PDF · 2025/26 season · opens in a new tab'), 'panel links the official trail map with format and season');
  check(await page.locator('img[src*="trail-map"], iframe, embed, object').count() === 0, 'the official map is never embedded');
  check((await page.locator('#panel-content .trailmap-drawn').innerText()).includes('Interactive map from OpenSkiMap'), 'the drawn map is labelled Interactive map from OpenSkiMap');
  const tmFixture = path.join(REPO, 'tests', 'map', 'fixtures', 'trailmap', 'alpha-peak.json');
  await trailMapChecks(page, 'trail map', tmFixture, { shotName: 'desktop-14-trail-map.png', base: [-106.37, 39.64] });
  await trailMapChecks(page, 'trail map from the map control', tmFixture, { via: 'control', fresh: false, base: [-106.37, 39.64] });
  check(await page.locator('.trailmap-runs .run-group').count() >= 5, 'trail map lists runs by trail sign');
  check(await page.locator('dialog.trailmap-dialog .trailmap-kind').innerText() === 'Interactive map from OpenSkiMap'
    && await page.locator('dialog.trailmap-dialog .official-trailmap a', { hasText: 'Official trail map from Alpha Peak' }).count() === 1,
  'the dialog names the drawn map and links the official one');

  // Phase 6: reviewed editorial with sources.
  const ed = page.locator('.editorial', { hasText: 'About Alpha Peak' });
  check(await ed.count() === 1, 'reviewed resort editorial renders');
  check(JSON.stringify(await ed.locator('.sub').allInnerTexts()) === JSON.stringify(['Overview', 'Culture', "Alexandre's take"]), 'editorial shows overview, culture and Alexandre\'s take');
  check(await ed.locator('strong', { hasText: 'bold' }).count() === 1 && await ed.locator('a[href="https://example.com/fixture/editorial"]').count() === 1, 'editorial Markdown renders bold and links');
  check(await ed.locator('.editorial-text p').count() === 4, 'editorial paragraphs split');
  check((await ed.innerText()).includes('Sources: Synthetic fixture: resort facts') && (await ed.innerText()).includes('Reviewed Oct 10, 2026.'), 'editorial cites sources and review date');
  const rrFact = page.locator('.housing .fact', { hasText: 'Reported rent near the resort' });
  check((await rrFact.innerText()).includes('$700 to $1,000 per month') && (await rrFact.innerText()).includes('Range from 4 reports')
    && await rrFact.locator('.badge-reported').count() === 1, 'reported area rent shows a Reported badge and its report count');

  // Phase 7: report form (endpoint intercepted; nothing leaves the test).
  const posted = [];
  let failNext = false;
  const accepts = [];
  await page.route('https://reports.example.test/submit', async (route) => {
    accepts.push(route.request().headers().accept);
    if (failNext) { failNext = false; await route.fulfill({ status: 500, contentType: 'application/json', body: '{"ok":false}' }); return; }
    posted.push(JSON.parse(route.request().postData()));
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });
  const form = page.locator('.report-form');
  check(await form.count() === 1, 'report form shows when an endpoint is configured');
  check(JSON.stringify(await form.locator('select[name=kind] option').allInnerTexts()) === JSON.stringify(['Hourly pay', 'Rent I paid near the resort', 'Rent at a listed housing site']), 'report kinds include listed housing once detail loads');
  await form.locator('input[name=amount]').fill('2');
  await form.locator('input[name=proof_url]').fill('ftp://nope');
  await form.locator('button[type=submit]').click();
  check(await form.locator('.form-error', { hasText: 'Choose your certification level.' }).count() === 1, 'pay report needs a cert level');
  check(await form.locator('.form-error', { hasText: 'between $5 and $300' }).count() === 1, 'hourly value range validated');
  check(await form.locator('.form-error', { hasText: 'starts with https://' }).count() === 1, 'proof link validated');
  check(await form.locator('[aria-invalid="true"]').count() === 3 && posted.length === 0, 'invalid fields flagged and nothing sent');
  await shot(page, 'desktop-12-report-form-errors.png');
  await form.locator('select[name=cert_level]').selectOption('level_2');
  await form.locator('input[name=amount]').fill('24.5');
  await form.locator('input[name=proof_url]').fill('https://example.com/stub.pdf');
  // A failed send shows an error with a retry that resends the same report.
  failNext = true;
  await form.locator('button[type=submit]').click();
  await page.waitForFunction(() => /could not be sent/.test(document.querySelector('.form-status').textContent), null, { timeout: 5000 });
  check(await form.locator('.report-retry').isVisible() && posted.length === 0, 'a failed send shows an error and a retry button');
  await form.locator('.report-retry').click();
  await page.waitForFunction(() => document.querySelector('.form-status').textContent === 'Thanks. Your report goes to review before anything appears on the map.', null, { timeout: 5000 });
  check(await form.locator('.report-retry').isHidden(), `retry sends it and shows the review message (${await form.locator('.form-status').innerText()})`);
  check(posted.length === 1 && posted[0].resort_id === 'alpha-peak' && posted[0].kind === 'pay' && posted[0].cert_level === 'level_2'
    && posted[0].hourly_usd === 24.5 && /^\d{4}-\d{2}$/.test(posted[0].season) && posted[0].proof_url === 'https://example.com/stub.pdf'
    && posted[0]._subject === 'Career Map report: Alpha Peak',
    `pay report posts the import columns and a subject (${JSON.stringify(posted[0])})`);
  check(accepts.every((a) => a === 'application/json'), 'the form asks for a JSON answer (Accept: application/json)');
  await form.locator('select[name=kind]').selectOption('housing');
  check(await form.locator('[data-field=cert_level]').isHidden() && await form.locator('[data-field=housing_site_id]').isVisible()
    && (await form.locator('[data-field=amount] label').innerText()) === 'Monthly rent in USD', 'housing report asks for a site and a monthly rent');
  await form.locator('select[name=housing_site_id]').selectOption('alpha-town-lease');
  await form.locator('input[name=amount]').fill('875');
  await form.locator('button[type=submit]').click();
  await page.waitForFunction((n) => document.querySelector('.form-status').textContent.includes('goes to review') && document.querySelector('.form-status').dataset.n !== n, '', { timeout: 5000 });
  await page.waitForTimeout(200);
  check(posted.length === 2 && posted[1].housing_site_id === 'alpha-town-lease' && posted[1].monthly_usd === 875 && !('cert_level' in posted[1]), 'housing report posts site and monthly value');
  check(await page.locator('#panel-content').evaluate((el) => !el.innerText.includes('875')), 'submitted values are never displayed');
  await page.locator('#panel-scroll').evaluate((el) => { el.scrollTop = 900; });
  await page.waitForTimeout(200);
  await shot(page, 'desktop-05-resort-charts.png');

  // Draft Tier A, estimated density.
  await page.goBack();
  await page.waitForURL(/fixture-front-range\/(\?|$)/);
  check(await heading(page) === 'Fixture Front Range', 'back button returns to cluster');
  await page.goForward();
  await page.waitForURL(/alpha-peak/);
  check(await heading(page) === 'Alpha Peak', 'forward button returns to resort');
  await page.locator('.crumbs a', { hasText: 'Fixture Front Range' }).click();
  await page.locator('.resort-list a', { hasText: 'Bravo Bowl' }).click();
  await page.waitForFunction(() => document.querySelectorAll('.climate canvas').length >= 5);
  check(await page.locator('.tier-row .tier-draft', { hasText: 'Draft profile' }).count() === 1, 'resort shows Draft profile tag');
  check(await page.locator('.badge-reported').count() >= 1, 'reported badge renders');
  check(await page.locator('.score-block .not-verified', { hasText: 'Scoring model is being finalized' }).count() === 1, 'assumptions_unset renders being finalized');
  check(!/professionalism|not yet rated/i.test(await page.locator('#panel-content').innerText()), 'no professionalism line on a resort without career data');
  check(await page.locator('.assumptions .not-verified', { hasText: 'Not set yet' }).count() === 1, 'unset assumption marked');
  check(await page.locator('.src-tag', { hasText: 'Estimated' }).count() === 1, 'density chart labelled Estimated');
  await page.locator('.crumbs a', { hasText: 'Fixture Front Range' }).click();
  await page.locator('.resort-list a', { hasText: 'Charlie Hill' }).click();
  await page.waitForURL(/charlie-hill/);
  check(await page.locator('.score-block .not-verified', { hasText: 'Not enough verified data yet' }).count() === 1, 'insufficient_data renders not enough data');

  // Lifts only detail (the shape of real Tier A data today), then leave detail mode.
  await page.evaluate(() => window.__careerMap.router.go({ level: 'resort', stateCode: 'UT', clusterId: 'fixture-wasatch-like', resortId: 'foxtrot-basin' }));
  await page.waitForFunction(() => document.querySelectorAll('.detail-toggles label').length > 0, null, { timeout: 10000 });
  check(JSON.stringify(await page.locator('.detail-toggles label').allInnerTexts()) === JSON.stringify(['Lifts']), 'layers without data are hidden');
  check(await page.locator('.housing .not-verified', { hasText: 'No employee housing listed publicly yet.' }).count() === 1, 'empty housing state');
  const zori = page.locator('.rent-line');
  check((await zori.locator('.rent-text').innerText()) === 'Zillow typical rent (ZORI) $1,150 a month (Fixture County, UT, Nov 2024)'
    && await zori.locator('.tier-stale').count() === 1 && await zori.locator('.badge-published').count() === 1,
  'fallback rent: Zillow typical rent with its place and month, flagged Needs update when older than 18 months');
  // Leave to a resort without detail.
  await page.evaluate(() => window.__careerMap.router.go({ level: 'resort', stateCode: 'CO', clusterId: 'fixture-front-range', resortId: 'charlie-hill' }));
  await page.waitForURL(/charlie-hill/);
  await mapIdle(page);
  check(await page.locator('#detail-layers').isHidden() && await page.locator('body.detail-mode').count() === 0, 'leaving the resort leaves detail mode');
  check(await page.evaluate(() => window.__careerMap.mapCtl.map.queryRenderedFeatures({ layers: ['detail-lifts'] }).length) === 0, 'detail layers cleared after leaving');

  // Indoor facility record.
  await page.locator('.crumbs a', { hasText: 'Fixture Front Range' }).click();
  await page.locator('.resort-list a', { hasText: 'Mike Snow Dome' }).click();
  await page.waitForURL(/mike-dome/);
  check(await page.locator('.tier-row .tier-facility', { hasText: 'Indoor ski facility' }).count() === 1, 'indoor facility label');
  check(await page.locator('.criteria .criterion', { hasText: 'Year round training' }).count() === 1, 'criterion label Year round training');
  await shot(page, 'desktop-06b-indoor.png');

  // Sparse resort: nulls and no climate.
  await page.locator('.crumbs a', { hasText: 'Fixture Front Range' }).click();
  await page.locator('.resort-list a', { hasText: 'Delta Ridge' }).click();
  await page.waitForURL(/delta-ridge/);
  check(await page.locator('.not-verified', { hasText: 'Not yet verified' }).count() >= 8, 'null values render Not yet verified');
  check(await page.locator('.climate', { hasText: 'Climate data is being compiled' }).count() === 1, 'missing climate message');
  check(await page.locator('.panel-section', { hasText: 'Career data is being collected.' }).count() === 1, 'career null renders being collected');
  check(await page.locator('.editorial').count() === 0, 'no editorial section when the build has none');
  check(await page.locator('.housing .not-verified', { hasText: 'No employee housing listed publicly yet.' }).count() === 1, 'resort without detail shows housing empty state');
  check(!w.requests.some((u) => u.includes('/detail/delta-ridge.json')), 'no detail fetch when detail_available is false');
  check(!w.requests.some((u) => u.includes('/climate/delta-ridge.json')), 'no climate fetch when climate_available is false');
  await shot(page, 'desktop-06-resort-sparse.png');

  // Comparison.
  await page.locator('.crumbs a', { hasText: 'Fixture Front Range' }).click();
  await page.getByRole('button', { name: 'Compare resorts' }).click();
  const submit = page.getByRole('button', { name: 'Compare selected' });
  check(await submit.isDisabled(), 'compare disabled with fewer than 2 picks');
  for (const id of ['alpha-peak', 'bravo-bowl', 'charlie-hill', 'delta-ridge']) await page.locator(`#cmp-${id}`).check();
  check(await submit.isEnabled(), 'compare enabled with 2 to 4 picks');
  await submit.click();
  await page.waitForURL(/compare=alpha-peak,bravo-bowl,charlie-hill,delta-ridge/);
  await page.waitForFunction(() => document.querySelectorAll('.climate canvas').length === 2, null, { timeout: 10000 });
  await mapIdle(page);
  await page.waitForTimeout(400);
  check(await heading(page) === 'Compare resorts in Fixture Front Range', 'comparison heading');
  check(await page.locator('.compare-table tbody tr').count() === 7, 'comparison table rows');
  check(await page.locator('.compare-table thead th').count() === 4, 'comparison table has 4 resorts');
  check(await page.locator('.not-verified', { hasText: 'Climate data is being compiled for Delta Ridge' }).count() === 1, 'comparison notes missing climate');
  await shot(page, 'desktop-07-compare.png');

  // Map click on a resort.
  await page.locator('.crumbs a', { hasText: 'Fixture Front Range' }).click();
  await mapIdle(page);
  await clickMapAt(page, [-105.90, 39.68]);
  await page.waitForURL(/charlie-hill/);
  check(await heading(page) === 'Charlie Hill', 'map click on resort opens resort');

  // Keyboard: Tab from heading reaches a panel link and Enter follows it.
  await page.locator('.crumbs a', { hasText: 'Colorado' }).click();
  await page.locator('#panel-heading').focus();
  await page.keyboard.press('Tab');
  const focused = await page.evaluate(() => document.activeElement && document.activeElement.textContent);
  check(/Fixture/.test(focused || ''), 'Tab moves from heading to the first cluster link');
  await page.keyboard.press('Enter');
  await page.waitForURL(/\/map\/co\/fixture-/);
  check((await heading(page)).startsWith('Fixture'), 'Enter on a cluster link navigates');

  // List view.
  await page.getByRole('button', { name: 'List view' }).click();
  check(await page.locator('#list-view').isVisible(), 'list view visible');
  check(await page.locator('#map').isHidden(), 'map hidden in list view');
  check(await page.locator('#list-view a.tree-resort').count() === 14, 'list view lists every resort and facility');
  await page.getByRole('button', { name: 'Indoor and training' }).click();
  check(await page.locator('#list-view a.tree-resort').count() === 2, 'facility filter applies to the list view');
  check(await page.locator('#list-view .tree-state').count() === 2, 'filter drops states with no match');
  await shot(page, 'desktop-08b-list-filtered.png');
  await page.getByRole('button', { name: 'Ski areas' }).click();
  check(await page.locator('#list-view a.tree-resort').count() === 12, 'ski area filter');
  await page.getByRole('button', { name: 'All', exact: true }).click();
  await shot(page, 'desktop-08-list-view.png');
  await page.locator('#list-view a.tree-resort', { hasText: 'Kilo Notch' }).click();
  await page.waitForURL(/\/map\/vt\/fixture-green-ridge\/kilo-notch\//);
  check(await heading(page) === 'Kilo Notch', 'list view link navigates');
  check(await page.locator('#list-view a[aria-current="page"]', { hasText: 'Kilo Notch' }).count() === 1, 'list view marks current resort');
  await page.getByRole('button', { name: 'Map view' }).click();
  check(await page.locator('#map').isVisible(), 'map returns');

  // Router fallbacks (in page, against the loaded index).
  const res = await page.evaluate(async () => {
    const { resolve } = await import('/map/js/router.js');
    const ix = window.__careerMap.index;
    const r = (p, q = '') => { const v = resolve(ix, p, q); return [v.level, v.stateCode, v.clusterId, v.resortId, v.fellBack].join('|'); };
    return {
      badState: r(['zz', 'fixture-front-range']),
      badCluster: r(['co', 'nope']),
      badResort: r(['co', 'fixture-front-range', 'nope']),
      wrongState: r(['ut', 'fixture-front-range', 'alpha-peak']),
      otherCluster: r(['co', 'fixture-high-desert', 'alpha-peak']),
      multi: r(['nv', 'fixture-lake-crest', 'hotel-lake']),
      cmpOne: r(['co', 'fixture-front-range'], '?compare=alpha-peak'),
      cmpBad: r(['co', 'fixture-front-range'], '?compare=alpha-peak,kilo-notch'),
    };
  });
  check(res.badState === 'national||||true', 'unknown state falls back to national');
  check(res.badCluster === 'state|CO|||true', 'unknown cluster falls back to state');
  check(res.badResort === 'cluster|CO|fixture-front-range||true', 'unknown resort falls back to cluster');
  check(res.wrongState === 'resort|CO|fixture-front-range|alpha-peak|true', 'wrong state segment is corrected');
  check(res.otherCluster === 'cluster|CO|fixture-high-desert||true', 'resort outside cluster falls back to cluster');
  check(res.multi === 'resort|NV|fixture-lake-crest|hotel-lake|false', 'multi state cluster keeps URL state');
  check(res.cmpOne.startsWith('cluster|') && res.cmpBad.startsWith('cluster|'), 'invalid comparisons fall back to cluster');

  w.assertClean();
  await page.close();
}

async function phone(browser, base) {
  log('\nPhone 390x844, deep link stubs generated by pipeline/routes.py from fixtures');
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, hasTouch: true, isMobile: true });
  const page = await ctx.newPage();
  const w = watch(page, 'phone');

  await page.goto(`${base}/map/`);
  await ready(page, { start: false });
  check(await page.locator('#map-start').innerText() === 'Tap to explore the map', 'phone: start button reads "Tap to explore the map"');
  await shot(page, 'phone-00-start-map.png');
  // A tap anywhere on the picture starts the map.
  await page.mouse.click(195, 160);
  await page.waitForFunction(() => window.__careerMap.mapCtl, null, { timeout: 20000 });
  check(true, 'phone: a tap on the picture starts the map');
  await ready(page);
  await mapIdle(page);
  check(await page.locator('#panel').getAttribute('data-sheet') === 'half', 'sheet starts half open');
  await shot(page, 'phone-01-national.png');

  await page.goto(`${base}/map/co/fixture-front-range/alpha-peak/`);
  await ready(page);
  await page.waitForFunction(() => document.querySelectorAll('.climate canvas').length >= 5, null, { timeout: 10000 });
  await mapIdle(page);
  check(await heading(page) === 'Alpha Peak', 'deep link stub loads the resort');
  check((await page.title()).startsWith('Instructor pay, housing, and snow at Alpha Peak'), 'stub title matches');
  await shot(page, 'phone-02-resort-half.png');

  const handle = page.locator('#sheet-handle');
  await handle.click();
  await page.waitForTimeout(400);
  check(await page.locator('#panel').getAttribute('data-sheet') === 'full', 'tap on handle expands to full');
  await page.locator('#panel-scroll').evaluate((el) => { el.scrollTop = 1100; });
  await page.waitForTimeout(300);
  await shot(page, 'phone-03-resort-full-charts.png');

  // Drag the handle down to peek.
  const box = await handle.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2, box.y + 300, { steps: 6 });
  await page.mouse.move(box.x + box.width / 2, 790, { steps: 6 });
  await page.mouse.up();
  await page.waitForTimeout(400);
  check(await page.locator('#panel').getAttribute('data-sheet') === 'peek', 'drag down snaps to peek');
  await shot(page, 'phone-04-peek.png');

  await page.keyboard.press('Tab'); // keyboard resize support
  await handle.focus();
  await page.keyboard.press('ArrowUp');
  await page.waitForTimeout(300);
  check(await page.locator('#panel').getAttribute('data-sheet') === 'half', 'ArrowUp on handle opens to half');

  await page.locator('.crumbs a', { hasText: 'Fixture Lake Crest' }).count().then(async (n) => {
    check(n === 0, 'breadcrumb shows the current cluster only');
  });
  await page.locator('.crumbs a', { hasText: 'Fixture Front Range' }).click();
  await page.waitForURL(/\/map\/co\/fixture-front-range\/$/);
  await mapIdle(page);
  await shot(page, 'phone-05-cluster.png');
  await page.getByRole('button', { name: 'Compare resorts' }).click();
  await page.locator('#cmp-alpha-peak').check();
  await page.locator('#cmp-bravo-bowl').check();
  await page.getByRole('button', { name: 'Compare selected' }).click();
  await page.waitForURL(/compare=/);
  await page.waitForFunction(() => document.querySelectorAll('.climate canvas').length === 2, null, { timeout: 10000 });
  await handle.click();
  await page.waitForTimeout(400);
  await shot(page, 'phone-06-compare.png');

  // Detail map on phone.
  await page.goto(`${base}/map/co/fixture-front-range/alpha-peak/`);
  await ready(page);
  await page.waitForSelector('.site-select', { timeout: 10000 });
  await mapIdle(page);
  check(await page.locator('#detail-layers').isVisible() && !(await page.locator('#detail-layers details').getAttribute('open') !== null), 'phone: detail layers start collapsed');
  await page.locator('#detail-layers summary').click();
  check(await page.locator('#layer-lifts').isVisible(), 'phone: layers open on tap');
  await page.locator('#detail-layers summary').click();
  await page.locator('.site-select', { hasText: 'Fixture Town Lease' }).click();
  await page.waitForTimeout(400);
  await shot(page, 'phone-09-detail-map.png');
  await handle.click();
  await page.waitForTimeout(300);
  await page.locator('#panel-scroll').evaluate((el) => { el.scrollTop = el.querySelector('.housing').offsetTop - 40; });
  await page.waitForTimeout(200);
  await shot(page, 'phone-10-housing.png');
  await trailMapChecks(page, 'phone trail map', path.join(REPO, 'tests', 'map', 'fixtures', 'trailmap', 'alpha-peak.json'),
    { shotName: 'phone-11-trail-map.png', phone: true, base: [-106.37, 39.64] });

  // Multi state cluster stub under the second state.
  await page.goto(`${base}/map/nv/fixture-lake-crest/`);
  await ready(page);
  check(await heading(page) === 'Fixture Lake Crest', 'second state cluster stub loads');
  check(await page.locator('link[rel=canonical]').getAttribute('href') === 'https://instructorgrindset.com/map/ca/fixture-lake-crest/', 'multi state cluster canonical uses first state');

  // Stale stub falls back to the nearest valid level.
  await page.goto(`${base}/map/co/retired-cluster/`);
  await ready(page);
  check(await heading(page) === 'Colorado', 'stale cluster link falls back to the state');
  check(new URL(page.url()).pathname === '/map/co/', 'URL rewritten to the fallback view');
  check(await page.locator('.notice').count() === 1, 'fallback notice shown');
  await shot(page, 'phone-07-fallback.png');

  await page.getByRole('button', { name: 'List view' }).click();
  await page.waitForTimeout(300);
  await shot(page, 'phone-08-list-view.png');

  w.assertClean();
  await ctx.close();
}

async function desktopStub(browser, base) {
  log('\nDesktop deep link stub');
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const w = watch(page, 'desktop stub');
  await page.goto(`${base}/map/ca/fixture-lake-crest/hotel-lake/`);
  await ready(page);
  await page.waitForFunction(() => document.querySelectorAll('.climate canvas').length >= 5, null, { timeout: 10000 });
  await mapIdle(page);
  check(await heading(page) === 'Hotel Lake', 'desktop deep link loads the resort');
  check(await page.evaluate(() => document.querySelector('meta[name=description]').content.includes('Hotel Lake')), 'stub description per page');
  // Phase 8 load order: the stub embeds its record, the slim index feeds the first paint, and the
  // 2D first frame gives way to the WebGL map.
  const order = await page.evaluate(() => {
    const t = (n) => { const e = performance.getEntriesByName(n)[0]; return e ? e.startTime : null; };
    const res = (f) => performance.getEntriesByType('resource').filter((e) => e.name.includes(`/data/build/${f}`));
    return {
      record: JSON.parse(document.getElementById('resort-record').textContent).resort.id,
      index: res('map-index.json').length, geo: res('resorts.geojson').length,
      preview: t('careermap:preview'), load: t('careermap:map'), previewLeft: document.querySelectorAll('.map-preview').length,
      pending: document.querySelectorAll('.src-pending').length, links: document.querySelectorAll('#panel-content .src-link').length,
    };
  });
  check(order.record === 'hotel-lake', 'resort stub embeds its own record');
  check(order.index === 1 && order.geo === 0, 'first paint reads map-index.json and never resorts.geojson');
  check(order.preview != null && order.preview < order.load && order.previewLeft === 0, 'first map frame drawn before WebGL loads, then removed');
  check(order.pending === 0 && order.links > 0, `every source placeholder is filled (${order.links} source links)`);
  await shot(page, 'desktop-09-deep-link.png');

  // Older builds without map-index.json still work (resorts.json validated up front).
  await page.goto(`${base}/map/co/fixture-front-range/alpha-peak/?data=/legacy-build/`);
  await ready(page);
  await mapIdle(page);
  await page.waitForFunction(() => document.querySelectorAll('#panel-content .facts').length >= 3, null, { timeout: 10000 });
  check(await heading(page) === 'Alpha Peak' && await page.evaluate(() => window.__careerMap.index.resorts.size) === 14,
    'a build without map-index.json falls back to resorts.json');
  // Without heat/snowfall.json the snowfall mode falls back to the resort only surface.
  await page.goto(`${base}/map/?data=/legacy-build/`);
  await ready(page);
  await page.waitForFunction(() => document.querySelectorAll('.recolor-modes .filter-btn').length === 4, null, { timeout: 10000 });
  await page.locator('.recolor-modes .filter-btn[data-mode="snowfall"]').click();
  await page.waitForFunction(() => window.__careerMap.mapCtl.map.getStyle().layers.some((l) => l.id.startsWith('heat-snowfall-')), null, { timeout: 10000 });
  const fallback = await page.evaluate(() => {
    const m = window.__careerMap.mapCtl.map;
    const ids = m.getStyle().layers.filter((l) => l.type === 'raster' && l.id.startsWith('heat-snowfall-')).map((l) => l.id);
    return { urls: ids.map((id) => m.getSource(id).url), credit: Boolean(m.getSource('heat-credit')), legend: document.getElementById('recolor-legend').innerText };
  });
  check(fallback.urls.length >= 1 && fallback.urls.every((u) => u.startsWith('data:image/png')) && !fallback.credit
    && !/NOAA/.test(fallback.legend) && /smoothed estimates/.test(fallback.legend),
    `without the snowfall rasters the map draws the resort only surface (${fallback.urls.length} images)`);
  // Without heat/season.json the season mode draws in the browser, with the same classes and the same fade.
  const t0 = Date.now();
  await page.locator('.recolor-modes .filter-btn[data-mode="season"]').click();
  await page.waitForFunction(() => window.__careerMap.mapCtl.map.getStyle().layers.some((l) => l.id.startsWith('heat-season-')), null, { timeout: 15000 });
  const seasonFallback = await page.evaluate(() => {
    const m = window.__careerMap.mapCtl.map;
    const ids = m.getStyle().layers.filter((l) => l.type === 'raster' && l.id.startsWith('heat-season-')).map((l) => l.id);
    return { urls: ids.map((id) => m.getSource(id).url), legend: document.getElementById('recolor-legend').innerText };
  });
  const seasonClear = await sampleHeat(page, [[-107.5, 43.6]]);
  const seasonIn = await sampleHeat(page, [[-112.5, 37.6]]);
  check(seasonFallback.urls.length >= 1 && seasonFallback.urls.every((u) => u.startsWith('data:image/png')) && /stay clear/.test(seasonFallback.legend)
    && seasonClear[0][3] === 0 && seasonIn[0][3] >= 0.33 * 255,
    `without the season rasters the browser draws them (${Date.now() - t0} ms; WY clear ${seasonClear[0][3]}, UT ${seasonIn[0][3]})`);
  await page.goto(`${base}/map/`);
  await ready(page);
  await page.waitForLoadState('networkidle');   // postings.json loads at idle for the Hiring now count; let it finish
  await page.goto(`${base}/map/co/fixture-front-range/?compare=alpha-peak,charlie-hill`);
  await ready(page);
  await page.waitForFunction(() => document.querySelectorAll('.climate canvas').length === 2, null, { timeout: 10000 });
  check(await heading(page) === 'Compare resorts in Fixture Front Range', 'comparison deep link loads from a stub');

  // Without JavaScript the stub still carries the resort summary (Phase 8 prerender).
  const nojs = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 1280, height: 800 } });
  const np = await nojs.newPage();
  await np.goto(`${base}/map/co/fixture-front-range/alpha-peak/`);
  const text = await np.locator('#panel-content').innerText();
  check((await np.locator('#panel-heading').innerText()) === 'Alpha Peak' && text.includes('Operator compensation') && text.includes('5,300')
    && text.includes('Level 1'), 'no JS visitors see the prerendered resort summary');
  check(await np.locator('#panel-content a[href="/contact"]').count() === 1 && await np.locator('#panel-content .related a').count() === 5, 'prerendered page links the consultation and related tools');
  const ld = JSON.parse(await np.locator('script[type="application/ld+json"]').textContent());
  check(ld['@graph'][0]['@type'] === 'SkiResort' && ld['@graph'][0].geo.latitude === 39.64, 'JSON-LD SkiResort with geo');
  check((await np.locator('meta[property="og:title"]').getAttribute('content')) === 'Instructor pay, housing, and snow at Alpha Peak', 'Open Graph title');
  await np.screenshot({ path: path.join(SHOTS, 'desktop-13-no-js.png') });
  await nojs.close();
  w.assertClean();
  await page.close();
}

async function realData(browser, base) {
  log('\nReal data/build output');
  const has = fs.existsSync(path.join(REPO, 'data', 'build', 'resorts.json'));
  if (!has) { log('  skip data/build/resorts.json not present'); return; }
  const n = JSON.parse(fs.readFileSync(path.join(REPO, 'data', 'build', 'resorts.json'), 'utf8')).resorts.length;
  for (const vp of [{ width: 1280, height: 800, name: 'desktop' }, { width: 390, height: 844, name: 'phone' }]) {
    const page = await browser.newPage({ viewport: { width: vp.width, height: vp.height } });
    const w = watch(page, `real data ${vp.name}`);
    const t0 = Date.now();
    await page.goto(`${base}/map/`);
    await ready(page);
    const tReady = Date.now() - t0;
    await mapIdle(page);
    const tIdle = Date.now() - t0;
    check(await heading(page) === 'United States', `real data ${vp.name}: national view renders (${n} resorts); ready ${tReady} ms, map idle ${tIdle} ms`);
    check(await page.locator('#dev-badge').isHidden(), `real data ${vp.name}: no fixture badge`);
    if (vp.name === 'desktop') {
      const geo = JSON.parse(fs.readFileSync(path.join(REPO, 'data', 'build', 'resorts.geojson'), 'utf8')).features;
      const props = { lucrativeness: 'lucrativeness_mid', season: 'season_length_days', snowfall: 'snowfall_annual_in' };
      check(!/professionalism/i.test(await page.locator('body').innerText()), 'real data: no professionalism text on the national view');
      for (const [mode, prop] of Object.entries(props)) {
        const has = geo.some((f) => f.properties[prop] != null);
        const btn = page.locator(`.recolor-modes .filter-btn[data-mode="${mode}"]`);
        const disabled = (await btn.getAttribute('aria-disabled')) === 'true';
        check(disabled === !has, `real data: ${mode} recolor ${has ? 'enabled' : 'disabled'} (${geo.filter((f) => f.properties[prop] != null).length} resorts with data)`);
        if (!has) {
          check(/^No resort has .+ data yet\.$/.test(await btn.getAttribute('title')), `real data: disabled ${mode} explains why in a tooltip`);
          await btn.click({ force: true }); // aria-disabled stays focusable and clickable; the click only announces why
          check((await btn.getAttribute('aria-pressed')) === 'false', `real data: disabled ${mode} cannot be selected`);
        }
      }
      await page.locator('.recolor-modes .filter-btn[data-mode="lucrativeness"]').hover();
      await shot(page, 'real-desktop-recolor-disabled.png');
      // Hiring now with the real build: with an empty feed the count is the windows whose months cover this month.
      const idx = JSON.parse(fs.readFileSync(path.join(REPO, 'data', 'build', 'map-index.json'), 'utf8'));
      const feed = JSON.parse(fs.readFileSync(path.join(REPO, 'data', 'build', 'jobs', 'postings.json'), 'utf8'));
      const now = new Date();
      const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
      const hw = idx.fields.indexOf('hiring_window');
      const windows = idx.resorts.filter((r) => r[hw] && r[hw][0] <= month && month <= r[hw][1]).length;
      await page.waitForFunction(() => performance.getEntriesByType('resource').some((e) => e.name.includes('/jobs/postings.json')), null, { timeout: 15000 }).catch(() => {});
      await page.waitForTimeout(300);
      const chipText = await page.locator('#facility-filter .filter-btn[data-filter="hiring"]').innerText();
      check(feed.postings.length ? /^Hiring now \(\d+\)$/.test(chipText) : chipText === `Hiring now (${windows})`,
        `real data: ${chipText} (${idx.resorts.filter((r) => r[hw]).length} parsed hiring windows, ${feed.postings.length} postings)`);
      // Pass chips with the real build: the list view keeps exactly the index rows on each pass.
      const pf = idx.fields.indexOf('passes');
      const onPass = (p) => idx.resorts.filter((r) => Array.isArray(r[pf]) && r[pf].includes(p)).length;
      await page.getByRole('button', { name: 'List view' }).click();
      for (const p of ['epic', 'ikon']) {
        await page.locator(`#facility-filter .filter-btn[data-filter="${p}"]`).click();
        const shown = new Set(await page.locator('#list-view a.tree-resort').evaluateAll((els) => els.map((a) => a.getAttribute('href').split('/').filter(Boolean).pop()))).size;
        check(onPass(p) > 0 && shown === onPass(p), `real data: the ${p} chip lists ${shown} resorts (${onPass(p)} in the index)`);
        await page.locator(`#facility-filter .filter-btn[data-filter="${p}"]`).click();
      }
      await page.getByRole('button', { name: 'Map view' }).click();
      await mapIdle(page);
      // Each heat mode with the real build: continuous coverage across the lower 48 and the Alaska inset.
      const REAL_BETWEEN = [[-116.5, 39.5], [-98.5, 38.5], [-99.0, 31.5], [-84.0, 32.5], [-108.0, 47.5], [-150.0, 64.5]];
      for (const [mode, prop] of Object.entries(props)) {
        if (!geo.some((f) => f.properties[prop] != null)) continue;
        await page.locator(`.recolor-modes .filter-btn[data-mode="${mode}"]`).click();
        await page.waitForFunction((k) => window.__careerMap.mapCtl.map.getStyle().layers.some((l) => l.id.startsWith(`heat-${k}-`)), mode, { timeout: 15000 });
        await page.waitForFunction(() => window.__careerMap.mapCtl.map.loaded(), null, { timeout: 15000 }).catch(() => {});
        await page.waitForTimeout(400);
        // Season length covers the states with season data; Kansas, Texas and Georgia have none and stay clear.
        const covered = mode === 'season' ? [REAL_BETWEEN[0], REAL_BETWEEN[4], REAL_BETWEEN[5]] : REAL_BETWEEN;
        const px = await sampleHeat(page, covered);
        check(px.every((c) => c[3] >= 0.33 * 255), `real data: ${mode} heat covers the map between distant resorts, Alaska included (alpha ${px.map((c) => c[3]).join(', ')})`);
        if (mode === 'season') {
          // Kansas, Texas and eastern Georgia: more than no_data_reach_km past any state with season data.
          const clear = await sampleHeat(page, [REAL_BETWEEN[1], REAL_BETWEEN[2], [-82.6, 32.2]]);
          check(clear.every((c) => c[3] === 0), `real data: season stays clear in Kansas, Texas and Georgia (alpha ${clear.map((c) => c[3]).join(', ')})`);
        }
        const layers = await page.evaluate(() => window.__careerMap.mapCtl.map.getStyle().layers.filter((l) => l.id.startsWith('heat-')).map((l) => l.id));
        check(layers.some((id) => id.endsWith('-inset')), `real data: ${mode} heat also draws in the Alaska inset`);
        if (mode === 'season' && fs.existsSync(path.join(REPO, 'data', 'build', 'heat', 'season.json'))) {
          const urls = await page.evaluate(() => {
            const m = window.__careerMap.mapCtl.map;
            return m.getStyle().layers.filter((l) => l.type === 'raster' && /^heat-season-\d+$/.test(l.id)).map((l) => m.getSource(l.id).url);
          });
          check(urls.length === 2 && urls.some((u) => u.endsWith('/data/build/heat/season-lower48.png')) && urls.some((u) => u.endsWith('/data/build/heat/season-alaska.png')),
            `real data: season draws the precomputed rasters (${urls.map((u) => u.split('/').pop()).join(', ')})`);
        }
        if (mode === 'snowfall' && fs.existsSync(path.join(REPO, 'data', 'build', 'heat', 'snowfall.json'))) {
          const urls = await page.evaluate(() => {
            const m = window.__careerMap.mapCtl.map;
            return m.getStyle().layers.filter((l) => l.type === 'raster' && /^heat-snowfall-\d+$/.test(l.id)).map((l) => m.getSource(l.id).url);
          });
          check(urls.length === 2 && urls.some((u) => u.endsWith('/data/build/heat/snowfall-lower48.png')) && urls.some((u) => u.endsWith('/data/build/heat/snowfall-alaska.png')),
            `real data: snowfall draws the NOAA rasters (${urls.map((u) => u.split('/').pop()).join(', ')})`);
        }
        await shot(page, `real-desktop-heat-${mode}.png`);
        if (mode === 'snowfall') {
          for (const [name, center, zoom] of [['wasatch', [-111.65, 40.6], 8], ['tahoe', [-120.1, 39.1], 7.5], ['cascades', [-121.6, 48.0], 7], ['colorado', [-106.3, 39.4], 7]]) {
            await page.evaluate(([c, z]) => window.__careerMap.mapCtl.map.jumpTo({ center: c, zoom: z }), [center, zoom]);
            await page.waitForFunction(() => window.__careerMap.mapCtl.map.loaded(), null, { timeout: 15000 }).catch(() => {});
            await page.waitForTimeout(400);
            await shot(page, `real-desktop-heat-snowfall-${name}.png`);
          }
          await page.evaluate(() => window.__careerMap.mapCtl.refit());
          await page.waitForTimeout(300);
        }
      }
      await page.locator('.recolor-modes .filter-btn[data-mode="default"]').click();
      await page.waitForTimeout(200);
    }
    await shot(page, `real-${vp.name}-national.png`);
    if (vp.name === 'phone') {
      await page.waitForFunction(() => document.querySelectorAll('.recolor-modes .filter-btn').length === 4, null, { timeout: 10000 });
      await page.locator('.recolor-modes .filter-btn[data-mode="snowfall"]').click();
      await page.waitForFunction(() => window.__careerMap.mapCtl.map.getStyle().layers.some((l) => l.id.startsWith('heat-snowfall-')), null, { timeout: 15000 });
      await page.waitForTimeout(600);
      const legendBox = await page.locator('#recolor-legend').boundingBox();
      check(legendBox && legendBox.x >= 0 && legendBox.x + legendBox.width <= vp.width && (await page.locator('.heat-about').getAttribute('open')) === null,
        'real data phone: the heat legend fits the screen with its notes folded');
      await shot(page, 'real-phone-heat-snowfall.png');
      await page.locator('.recolor-modes .filter-btn[data-mode="default"]').click();
    }
    // Alaska inset (national view): Alaska draws as a moved copy in the lower left; territories never draw.
    const geoAll = JSON.parse(fs.readFileSync(path.join(REPO, 'data', 'build', 'map-index.json'), 'utf8'));
    const akCount = geoAll.resorts.filter((r) => r[geoAll.fields.indexOf('state')] === 'AK').length;
    if (akCount) {
      const ins = await page.evaluate(async () => {
        const { insetFrameBounds } = await import('/map/js/inset.js');
        const m = window.__careerMap.mapCtl.map;
        const q = (layers) => m.queryRenderedFeatures({ layers });
        const inset = q(['inset-resorts', 'inset-resorts-facility']);
        const frame = insetFrameBounds();
        const cont = m.getContainer().getBoundingClientRect();
        const pts = inset.map((f) => m.project(f.geometry.coordinates));
        const fr = frame.map((c) => m.project(c));
        return {
          inset: new Set(inset.map((f) => f.properties.id)).size,
          mainAK: q(['resorts', 'resorts-facility']).filter((f) => f.properties.state === 'AK').length,
          states: [...new Set(q(['states-fill']).map((f) => f.properties.code))],
          insetState: q(['inset-states-fill']).length,
          lowerLeft: (fr[0].x + fr[1].x) / 2 < cont.width * 0.5
            && pts.every((p) => p.x >= Math.min(fr[0].x, fr[1].x) && p.x <= Math.max(fr[0].x, fr[1].x) && p.y >= Math.min(fr[0].y, fr[1].y) && p.y <= Math.max(fr[0].y, fr[1].y))
            && Math.min(fr[0].y, fr[1].y) > m.project([-120, 33]).y,
          inView: fr.every((p) => p.x >= 0 && p.y >= 0 && p.x <= cont.width && p.y <= cont.height),
          first: inset[0] && { id: inset[0].properties.id, ll: inset[0].geometry.coordinates },
        };
      });
      check(ins.inset === akCount && ins.mainAK === 0, `real data ${vp.name}: Alaska resorts draw only in the inset (${ins.inset} of ${akCount})`);
      check(ins.lowerLeft && ins.inView && ins.insetState > 0, `real data ${vp.name}: the inset sits in the lower left, framed and in view`);
      check(!ins.states.some((c) => ['PR', 'VI', 'GU', 'MP', 'AS', 'HI', 'AK'].includes(c)), `real data ${vp.name}: territories and Alaska's true shape hidden at the national view`);
      if (vp.name === 'desktop') {
        await shot(page, 'real-desktop-alaska-inset.png');
        await clickMapAt(page, ins.first.ll);
        await page.waitForURL(/\/map\/ak\//, { timeout: 10000 });
        await mapIdle(page);
        const after = await page.evaluate((id) => {
          const m = window.__careerMap.mapCtl.map;
          const r = window.__careerMap.index.resorts.get(id);
          const c = m.getCenter();
          return { insetHidden: m.getLayoutProperty('inset-resorts', 'visibility') === 'none', near: Math.abs(c.lng - r.lon) < 1 && Math.abs(c.lat - r.lat) < 1 };
        }, ins.first.id);
        check(after.insetHidden && after.near, 'real data: clicking an inset resort opens it at its true place');
        await page.goto(`${base}/map/`);
        await ready(page);
        await mapIdle(page);
      }
    }
    if (vp.name === 'desktop' && n) {
      // Drill down through the first state, cluster and resort in the real data.
      await page.locator('.nav-list a').first().click();
      await mapIdle(page);
      check(await page.locator('#panel-heading').count() === 1, `real data: state view renders (${await heading(page)})`);
      await page.locator('.nav-list a').first().click();
      await mapIdle(page);
      check(await page.locator('.resort-list a').count() > 0, `real data: cluster view lists resorts (${await heading(page)})`);
      await shot(page, 'real-desktop-cluster.png');
      check(await page.evaluate(() => window.__careerMap.mapCtl.map.getZoom()) <= 9.01, 'real data: cluster zoom capped at 9');
      await page.locator('.resort-list a').first().click();
      await mapIdle(page);
      check(await page.locator('.facts').count() >= 3, `real data: resort view renders (${await heading(page)})`);
      await shot(page, 'real-desktop-resort.png');
      const summary = await page.evaluate(() => {
        const ix = window.__careerMap.index;
        return { resorts: ix.resorts.size, clusters: ix.clusters.size, states: ix.states.size };
      });
      log(`  real data index: ${JSON.stringify(summary)}`);
    }
    if (vp.name === 'desktop' && n) {
      // Deep links served by the stubs routes.py generated from the real build.
      const manifestPath = path.join(REPO, 'map', '.routes-manifest.json');
      const pages = fs.existsSync(manifestPath) ? JSON.parse(fs.readFileSync(manifestPath, 'utf8')).pages : [];
      const pick = (depth) => pages.find((p) => p.split('/').filter(Boolean).length === depth && p.startsWith('co/'))
        || pages.find((p) => p.split('/').filter(Boolean).length === depth);
      for (const rel of [pick(1), pick(2), pick(3)].filter(Boolean)) {
        const t0 = Date.now();
        await page.goto(`${base}/map/${rel}`);
        await ready(page);
        const tReady = Date.now() - t0;
        await mapIdle(page);
        const tIdle = Date.now() - t0;
        const p = new URL(page.url()).pathname;
        check(p === `/map/${rel}`, `real deep link /map/${rel} resolves to its own view (${await heading(page)}); ready ${tReady} ms, map idle ${tIdle} ms`);
      }
      if (pages.length) await shot(page, 'real-desktop-deep-link.png');
      // An approved Tier A record, when the build has one.
      const all = JSON.parse(fs.readFileSync(path.join(REPO, 'data', 'build', 'resorts.json'), 'utf8')).resorts;
      const appr = all.find((r) => r.tier_a_status === 'approved' && pages.includes(`${r.state.toLowerCase()}/${r.cluster_id}/${r.id}/`));
      if (appr) {
        await page.goto(`${base}/map/${appr.state.toLowerCase()}/${appr.cluster_id}/${appr.id}/`);
        await ready(page);
        await mapIdle(page);
        check(await page.locator('.tier-row .tier-approved').count() === 1, `real data: approved Tier A shows Approved (${appr.name})`);
        check(await page.locator('.criteria .criterion').count() === appr.tier_a_criteria.length, 'real data: every criterion renders with a label');
        await shot(page, 'real-desktop-approved.png');
      } else log('  skip no approved Tier A stub in the real build yet');
      const inPages = (r) => r.tier === 'A' && r.detail_available && pages.includes(`${r.state.toLowerCase()}/${r.cluster_id}/${r.id}/`);
      const withDetail = all.find((r) => r.id === 'vail' && inPages(r)) || all.find((r) => r.id === 'arapahoe-basin' && inPages(r)) || all.find(inPages);
      if (withDetail) {
        await page.goto(`${base}/map/${withDetail.state.toLowerCase()}/${withDetail.cluster_id}/${withDetail.id}/`);
        await ready(page);
        await page.waitForFunction(() => document.querySelector('.housing') && !document.querySelector('.housing .loading'), null, { timeout: 15000 });
        await mapIdle(page);
        const n = await page.evaluate(() => window.__careerMap.mapCtl.map.queryRenderedFeatures({ layers: ['detail-lifts'] }).length);
        const file = JSON.parse(fs.readFileSync(path.join(REPO, 'data', 'build', 'detail', `${withDetail.id}.json`), 'utf8'));
        const timing = await page.evaluate((id) => {
          const e = performance.getEntriesByType('resource').find((x) => x.name.endsWith(`/detail/${id}.json`));
          return e ? { ms: Math.round(e.duration), bytes: e.transferSize || e.encodedBodySize } : null;
        }, withDetail.id);
        check(n > 0, `real data: lifts render for ${withDetail.name} (${n} rendered of ${file.lifts.features.length} lifts)`);
        log(`  real detail/${withDetail.id}.json: ${fs.statSync(path.join(REPO, 'data', 'build', 'detail', `${withDetail.id}.json`)).size} bytes on disk, fetch ${timing ? `${timing.ms} ms` : 'n/a'}`);
        const hasTransit = Boolean(file.transit && (file.transit.routes.length || file.transit.stops.length));
        const expected = [file.lifts.features.length && 'Lifts', file.housing.length && 'Housing',
          file.housing.some((s) => s.commutes.some((c) => c.geometry)) && 'Commute', hasTransit && 'Transit',
          file.isochrones.length && 'Drive time'].filter(Boolean);
        check(JSON.stringify(await page.locator('.detail-toggles label').allInnerTexts()) === JSON.stringify(expected), `real data: only layers with data get a toggle (${expected.join(', ')})`);
        if (hasTransit) {
          const t = await page.evaluate(() => ['detail-transit-line', 'detail-transit-stop'].map((id) => window.__careerMap.mapCtl.map.queryRenderedFeatures({ layers: [id] }).length));
          check(t[0] > 0 && t[1] > 0, `real data: GTFS transit routes and stops draw for ${withDetail.name} (${t[0]} line pieces, ${t[1]} stops; ${file.transit.source_ids.join(', ')})`);
          const rows = await page.locator('#detail-layers .legend-feed').allInnerTexts();
          check(rows.length === file.transit.feeds.length && rows.every((r) => /Schedule (for|from) [A-Z][a-z]{2} \d/.test(r)),
            `real data: transit legend lists each feed with its schedule dates (${rows.map((r) => r.replace(/\n/g, ' / ')).join(' | ')})`);
        }
        check(file.housing.length ? await page.locator('.site-select').count() === file.housing.length
          : await page.locator('.housing .not-verified', { hasText: 'No employee housing listed publicly yet.' }).count() === 1,
        `real data: ${file.housing.length ? `${file.housing.length} employee housing sites listed` : 'employee housing empty state'}`);
        const endpoint = JSON.parse(fs.readFileSync(path.join(REPO, 'data', 'build', 'definitions.json'), 'utf8')).submissions.endpoint;
        check(endpoint ? await page.locator('.report-form').count() === 1
          : await page.locator('.panel-section', { hasText: 'Pay and rent reports open soon.' }).count() === 1 && await page.locator('.report-form').count() === 0,
        `real data: report form ${endpoint ? `shows for ${endpoint}` : 'hidden while the endpoint is null'}`);
        const unnamed = file.lifts.features.findIndex((f) => !f.properties.name);
        if (unnamed >= 0) log(`  real data: ${file.lifts.features.filter((f) => !f.properties.name).length} unnamed lifts render as "Unnamed lift"`);
        await shot(page, 'real-desktop-detail-lifts.png');
      } else log('  skip no Tier A resort with detail_available in the real build yet');
      for (const id of ['vail', 'big-sky']) {
        const r = all.find((x) => x.id === id && x.trailmap_available && pages.includes(`${x.state.toLowerCase()}/${x.cluster_id}/${x.id}/`));
        if (!r) { log(`  skip no trail map for ${id} in the real build`); continue; }
        await page.goto(`${base}/map/${r.state.toLowerCase()}/${r.cluster_id}/${r.id}/`);
        await ready(page);
        await mapIdle(page);
        if (r.official_trailmap) {
          const href = await page.locator('#panel-content .official-trailmap a').getAttribute('href');
          check(href === r.official_trailmap.url, `real data ${r.name}: official trail map link (${href})`);
        }
        if (r.webcams && r.webcams.cams_page) {
          const cam = await page.locator('#panel-content a.live-cams-link').getAttribute('href');
          check(cam === r.webcams.cams_page.url, `real data ${r.name}: Open live cams links the resort's cams page`);
        }
        if (r.jobs) {
          check(await page.locator('#panel-content section.jobs a.job-link').count() === r.jobs.links.length,
            `real data ${r.name}: every jobs link renders (${r.jobs.links.length})`);
        }
        await trailMapChecks(page, `real data ${r.name}`, path.join(REPO, 'data', 'build', 'trailmap', `${id}.json`), { shotName: `real-desktop-trail-map-${id}.png`, base: [r.lon, r.lat] });
      }
    }
    w.assertClean();
    await page.close();
  }
}

// Live cams and jobs (map/js/livelinks.js, pipeline/routes.py): links only, each in a new tab with noopener; job
// groups in order; open roles from the fixture jobs/postings.json; the cluster Hiring summary; an empty feed;
// prerendered stubs; keyboard use; axe on the panel.
async function liveLinksChecks(browser, stubBase) {
  log('\nLive cams and jobs');
  const axeSrc = (() => {
    try { return fs.readFileSync(require.resolve('axe-core/axe.min.js', { paths: [path.join(REPO, 'tests', 'map')] }), 'utf8'); } catch { return null; }
  })();
  const newCtx = async () => {
    const c = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    // Links point at example.com; answer the new tabs locally (the sandbox has no outside network).
    await c.route(/^https:\/\/example\.com\//, (route) => route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>Example</title>' }));
    return c;
  };
  async function axePanel(page, label) {
    if (!axeSrc) { log(`  skip axe ${label}: run (cd tests/map && npm install) for axe-core`); return; }
    await page.addScriptTag({ content: axeSrc });
    const res = await page.evaluate(async () => {
      const r = await window.axe.run(document, { resultTypes: ['violations'], runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'] } });
      return r.violations.map((v) => `${v.id} (${v.impact}): ${v.help} ${JSON.stringify(v.nodes.slice(0, 3).map((n) => n.target.join(' ')))}`);
    });
    check(res.length === 0, `axe ${label}: ${res.length} violations${res.length ? `\n      ${res.join('\n      ')}` : ''}`);
  }

  const ctx = await newCtx();
  const page = await ctx.newPage();
  const w = watch(page, 'live cams and jobs');
  await page.goto(`${stubBase}/map/co/fixture-front-range/alpha-peak/`);
  await ready(page, { start: false });
  await page.waitForSelector('#panel-content .jobs-live:not([hidden])', { timeout: 15000 });
  const panel = page.locator('#panel-content');
  const cams = panel.locator('.live-cams');
  const open = cams.locator('a.live-cams-link');
  check(await cams.locator('.live-cams-title').innerText() === 'Live cams' && await open.innerText() === 'Open live cams'
    && await open.getAttribute('href') === 'https://example.com/fixture/alpha-peak/cams', 'Live cams row: Open live cams links the resort cams page');
  check(JSON.stringify(await cams.locator('.cam-list a').allInnerTexts()) === JSON.stringify(['Summit cam', 'Base area', 'Back bowls', 'Terrain park']),
    'Live cams row lists the named cams');
  const camText = await cams.innerText();
  check(camText.includes('Live stream') && camText.includes('Still image') && camText.includes('example.com · opens in a new tab'),
    'named cams say what they are; the cams page names its site');
  check(await cams.locator('.badge-estimated').count() === 5, 'every cam link shows its confidence badge');
  check(await panel.locator('iframe, video, embed, object, img').count() === 0, 'cams are linked, never embedded');
  const jobs = panel.locator('section.jobs');
  const win = jobs.locator('.jobs-window');
  check(await win.count() === 1 && (await win.innerText()).startsWith('Hiring window: Applications open August 15; staff training starts in mid November.')
    && await win.locator('.badge-estimated').count() === 1 && (await win.innerText()).includes('Last verified Oct 1, 2026'),
  'hiring window line tops the Jobs section in the source\'s words, with its badge and date');
  check(JSON.stringify(await jobs.locator('.job-group h4').allInnerTexts())
    === JSON.stringify(['Ski school', 'Coaching and clubs', 'Management', 'Parent company', 'Job boards']), 'job groups in order');
  check(JSON.stringify(await jobs.locator('.job-group[data-group="coaching"] .tag').allInnerTexts()) === JSON.stringify(['Race', 'Freeride']),
    'coaching and club links show disciplines as tags');
  check(await jobs.locator('.job-links li').count() === 5 && await jobs.locator('.job-links .badge-estimated').count() === 5,
    'every jobs link shows its confidence badge');
  const ski = await jobs.locator('.job-group[data-group="ski_school"] li').innerText();
  check(ski.includes('Last verified Oct 1, 2026') && !ski.includes('Synthetic fixture: careers page'), 'a link whose source is the same page shows only its date');
  check((await jobs.locator('.job-group[data-group="management"] li').innerText()).includes('Synthetic fixture: resort facts'),
    'a link cites its source when the source is another page');
  const live = jobs.locator('.jobs-live');
  const liveText = await live.innerText();
  check((await live.locator('.jobs-open').innerText()).startsWith('7 open roles') && await live.locator('.jobs-open .badge-published').count() === 1,
    'open roles: count with a Published badge');
  check(JSON.stringify(await live.locator('.posting-list a').allInnerTexts())
    === JSON.stringify(['Adaptive Instructor', 'Kids Ski Instructor', 'Ski Instructor', 'Snowboard Instructor', 'Freeride Coach']),
  'open roles: up to 5 titles, ski school first');
  check(liveText.includes('2 more roles on the job boards below.') && liveText.includes('Plus 2 roles at Fixture Parent with no single resort named.'),
    'open roles: the rest and the employer wide roles are counted');
  check(liveText.includes('Updated Oct 12, 2026 from Fixture Parent job board.') && !liveText.includes('Bravo Bowl'),
    "open roles: Updated date and board; another resort's roles stay off");
  const rels = await page.$$eval('#panel-content .live-cams a, #panel-content section.jobs a', (as) => as.map((a) => [a.target, a.rel]));
  check(rels.length >= 5 + 5 + 5 && rels.every(([t, rel]) => t === '_blank' && /\bnoopener\b/.test(rel)),
    `every cam, jobs and posting link opens in a new tab with rel noopener (${rels.length} links)`);
  // Keyboard: Tab from the heading reaches Open live cams, and Enter opens it in a new tab.
  await page.locator('#panel-heading').focus();
  let reached = false;
  for (let i = 0; i < 40 && !reached; i++) {
    await page.keyboard.press('Tab');
    reached = await page.evaluate(() => document.activeElement && document.activeElement.classList.contains('live-cams-link'));
  }
  check(reached, 'Tab reaches Open live cams');
  const [popup] = await Promise.all([ctx.waitForEvent('page'), page.keyboard.press('Enter')]);
  await popup.waitForLoadState();
  check(popup.url() === 'https://example.com/fixture/alpha-peak/cams' && new URL(page.url()).pathname.endsWith('/alpha-peak/'),
    'Enter opens the cams page in a new tab and the map stays put');
  await popup.close();
  await shot(page, 'desktop-20-live-cams-jobs.png');
  await axePanel(page, 'resort page with live cams, jobs and open roles');

  // Cluster: compact Hiring summary, and Live cams in the resort list.
  await page.locator('.crumbs a', { hasText: 'Fixture Front Range' }).click();
  await page.waitForSelector('#panel-content .hiring:not([hidden])', { timeout: 15000 });
  const hiring = (await panel.locator('.hiring').textContent()).replace(/\s+/g, ' ');
  check(hiring.startsWith('Hiring') && hiring.includes('Fixture Parent') && hiring.includes('8 open roles') && hiring.includes('Updated Oct 12, 2026.'),
    `cluster Hiring summary lists employers with open roles (${hiring.replace(/\s+/g, ' ')})`);
  check((await panel.locator('.resort-list a', { hasText: 'Alpha Peak' }).innerText()).includes('Live cams'), 'cluster list marks resorts with live cams');
  await axePanel(page, 'cluster page with the Hiring summary');
  w.assertClean();
  await ctx.close();

  // A Tier A resort without jobs links never loads postings; a Tier B resort without them shows no Jobs section.
  const ctx2 = await newCtx();
  const p2 = await ctx2.newPage();
  const w2 = watch(p2, 'resorts without jobs');
  await p2.goto(`${stubBase}/map/vt/fixture-green-ridge/kilo-notch/`);
  await ready(p2, { start: false });
  await p2.waitForSelector('#panel-content section.jobs', { timeout: 15000 });
  check((await p2.locator('#panel-content section.jobs .jobs-window').innerText()).startsWith('Hiring window: Instructor posting opened September 1, 2026'),
    'a hiring window shows even before any jobs links');
  check((await p2.locator('#panel-content section.jobs').innerText()).includes('Jobs links are being collected.')
    && await p2.locator('#panel-content .live-cams').count() === 0, 'Tier A without links: Jobs says links are being collected; no Live cams row');
  // postings.json loads only for the Hiring now count here, once and after the first paint; the panel shows no roles.
  await p2.waitForFunction(() => performance.getEntriesByType('resource').some((e) => e.name.includes('/jobs/postings.json')), null, { timeout: 15000 });
  const fetchAt = await p2.evaluate(() => ({
    n: performance.getEntriesByType('resource').filter((e) => e.name.includes('/jobs/postings.json')).length,
    start: performance.getEntriesByType('resource').find((e) => e.name.includes('/jobs/postings.json')).startTime,
    fcp: (performance.getEntriesByName('first-contentful-paint')[0] || { startTime: Infinity }).startTime,
  }));
  check(fetchAt.n === 1 && fetchAt.start >= fetchAt.fcp && await p2.locator('#panel-content .jobs-live').count() === 0,
    `a resort without a feed shows no open roles; postings.json loads once after the first paint for the count (${Math.round(fetchAt.start)} ms, paint ${Math.round(fetchAt.fcp)} ms)`);
  await p2.waitForLoadState('networkidle');   // the web font loads after `load`; let it finish first
  await p2.goto(`${stubBase}/map/co/fixture-front-range/charlie-hill/`);
  await ready(p2, { start: false });
  await p2.waitForFunction(() => document.querySelectorAll('#panel-content .facts').length >= 3, null, { timeout: 15000 });
  check(await p2.locator('#panel-content section.jobs').count() === 0, 'Tier B without links has no Jobs section');
  // An empty feed: the panel shows the links only.
  await ctx2.route(/\/data\/build\/jobs\/postings\.json$/, (route) => route.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify({ version: 1, generated_at: '2026-10-13T00:00:00Z', fetched_at: null, keywords: [], boards: [], sources: [], postings: [] }) }));
  await p2.waitForLoadState('networkidle');   // the web font loads after `load`; let it finish first
  await p2.goto(`${stubBase}/map/co/fixture-front-range/alpha-peak/`);
  await ready(p2, { start: false });
  await p2.waitForSelector('#panel-content section.jobs .job-link', { timeout: 15000 });
  await p2.waitForFunction(() => performance.getEntriesByType('resource').some((e) => e.name.includes('/jobs/postings.json')), null, { timeout: 15000 });
  await p2.waitForTimeout(300);
  check(await p2.locator('#panel-content .jobs-live').isHidden() && await p2.locator('#panel-content .job-link').count() === 5,
    'with an empty feed the Jobs section shows the links only');
  w2.assertClean();
  await ctx2.close();

  // Without JavaScript the stub carries the cams and jobs links (never the postings, which change daily).
  const nojs = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 1280, height: 800 } });
  const np = await nojs.newPage();
  await np.goto(`${stubBase}/map/co/fixture-front-range/alpha-peak/`);
  check(await np.locator('#panel-content a.live-cams-link').count() === 1 && await np.locator('#panel-content .cam-list a').count() === 4
    && await np.locator('#panel-content .job-link').count() === 5 && await np.locator('#panel-content .jobs-live, #panel-content .posting-list').count() === 0,
  'prerendered stub links the cams and jobs');
  check(JSON.stringify(await np.locator('#panel-content .job-group h4').allInnerTexts())
    === JSON.stringify(['Ski school', 'Coaching and clubs', 'Management', 'Parent company', 'Job boards']), 'prerendered job groups in order');
  await nojs.close();
}

// Housing (ROADMAP item 8c): Furnished rentals, Employee housing with "Show on the detail map", and the housing search
// tools ranked by independent sources, inside Housing and commute; the stub prerenders the links; keyboard; axe.
async function housingChecks(browser, stubBase) {
  log('\nHousing links and employee housing');
  const axeSrc = (() => {
    try { return fs.readFileSync(require.resolve('axe-core/axe.min.js', { paths: [path.join(REPO, 'tests', 'map')] }), 'utf8'); } catch { return null; }
  })();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await ctx.route(/^https:\/\/example\.(com|org|net)\//, (route) => route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>Example</title>' }));
  const page = await ctx.newPage();
  const w = watch(page, 'housing');
  await page.goto(`${stubBase}/map/co/fixture-front-range/alpha-peak/`);
  await ready(page, { start: false });
  await page.waitForSelector('#panel-content .housing .tool-link', { timeout: 15000 });
  await page.waitForSelector('#panel-content .housing .site-select', { timeout: 15000 });
  const sec = page.locator('#panel-content .panel-section', { has: page.locator('h3', { hasText: 'Housing and commute' }) });
  check(JSON.stringify(await sec.locator('h4').allInnerTexts())
    === JSON.stringify(['Furnished rentals', 'Employee housing', 'Where people find housing here', 'Rent near the resort']),
  'Housing and commute: Furnished rentals, Employee housing, Where people find housing here, Rent near the resort');
  check(await sec.locator('.access').count() === 1 && await page.evaluate(() => {
      const s = [...document.querySelectorAll('#panel-content .panel-section')].find((x) => x.querySelector('h3').textContent === 'Housing and commute');
      return s.querySelector('.access').compareDocumentPosition(s.querySelector('.ff-link')) === Node.DOCUMENT_POSITION_FOLLOWING;
    }), 'Getting to the mountain stays first');
  const ff = sec.locator('a.ff-link');
  check(await ff.innerText() === 'Furnished Finder listings near Fixture Town' && await ff.getAttribute('href') === 'https://example.com/fixture/furnished/fixture-town'
    && await sec.locator('.ff .badge-estimated').count() === 1 && (await sec.locator('.ff').innerText()).includes('opens in a new tab · Last verified Oct 1, 2026'),
  'Furnished rentals: one sourced link to the listings near the town, with its badge and date');
  check(await sec.locator('iframe, embed, object, img').count() === 0, 'listings are linked, never embedded');
  // Employee housing: type badge, town, precision note; rent only where sourced.
  check(JSON.stringify(await sec.locator('.site-type').allInnerTexts()) === JSON.stringify(['Resort owned', 'Master lease', 'Listed by the resort', 'Market']),
    'every site shows its type badge (Resort owned, Master lease, Listed by the resort, Market)');
  const listed = sec.locator('.site', { hasText: 'Fixture Listed Lodge' });
  const listedSrc = listed.locator('.site-sources');
  check((await listedSrc.textContent()).replace(/\s+/g, ' ').trim()
    === 'Estimated Source: Synthetic fixture: careers page · Location: Synthetic fixture: base area location · Last verified Oct 1, 2026'
    && await listedSrc.locator('a').first().getAttribute('href') === 'https://example.com/fixture/careers'
    && await listed.locator('.fact', { hasText: 'Rent' }).count() === 0,
  'the listing page is the site\'s main source, beside its location source; no rent without a sourced one');
  check((await sec.locator('.site', { hasText: 'Fixture Town Lease' }).locator('.site-sources').textContent()).startsWith('Reported Location: Synthetic fixture: base area location'),
    'a site without a listing page cites its location source');
  const village = await sec.locator('.site', { hasText: 'Fixture Staff Village' }).locator('.nav-meta').innerText();
  const west = await sec.locator('.site', { hasText: 'Fixture West Rentals' }).locator('.nav-meta').innerText();
  check(/Fixture Town · Listed address, shown as a pin/.test(village) && /^Market\s+Neighborhood area, shown as a circle$/.test(west.trim()),
    `site town and precision note; no town when none is sourced (${village.replace(/\n/g, ' ')} | ${west.replace(/\n/g, ' ')})`);
  // Tools: ranked, kind tags, the sources' reason, Named by N sources with the source links.
  const tools = sec.locator('ol.housing-tools > li');
  check(JSON.stringify(await tools.locator('.tool-link').allInnerTexts())
    === JSON.stringify(['Fixture Staff Housing Portal', 'Fixture Front Range Rooms', 'Fixture Valley Listings']),
  'tools keep the build ranking: resort scoped first, then the cluster by independent sources');
  check(JSON.stringify(await tools.locator('.tool-kind').allInnerTexts()) === JSON.stringify(['Employer portal', 'Facebook group', 'Listing site']),
    'each tool shows its kind');
  check(await tools.locator('.badge-estimated').count() === 3, 'each tool shows its confidence badge');
  const srcLines = await tools.locator('.tool-sources').allInnerTexts();
  check(srcLines[0].startsWith('Named by 3 sources: Synthetic fixture: housing news, Synthetic fixture: housing guide, Synthetic fixture: forum thread one')
    && srcLines[1].startsWith('Named by 2 sources across 3 pages: ') && srcLines[2].startsWith('Named by 1 source: Synthetic fixture: housing guide')
    && srcLines.every((t) => t.includes('Last verified Oct 1, 2026')), `Named by N sources with the source links (${srcLines.join(' | ')})`);
  check((await tools.nth(1).locator('.tool-why').innerText()) === 'Synthetic reason: both sources say rooms post here first each fall.',
    'each tool shows the sources\' reason');
  check(JSON.stringify(await tools.nth(0).locator('.tool-sources a').evaluateAll((as) => as.map((a) => a.href)))
    === JSON.stringify(['https://example.org/fixture/housing-news', 'https://example.net/fixture/housing-guide', 'https://example.com/fixture/forum/1']),
  'source links point at each source');
  const rels = await sec.locator('.housing a').evaluateAll((as) => as.map((a) => [a.target, a.rel]));
  check(rels.length >= 1 + 3 + 7 && rels.every(([t, rel]) => t === '_blank' && /\bnoopener\b/.test(rel)),
    `every housing link opens in a new tab with rel noopener (${rels.length} links)`);
  // Keyboard: Tab reaches the Furnished Finder link and Enter opens it in a new tab.
  await page.locator('#panel-heading').focus();
  let reached = false;
  for (let i = 0; i < 80 && !reached; i++) {
    await page.keyboard.press('Tab');
    reached = await page.evaluate(() => document.activeElement && document.activeElement.classList.contains('ff-link'));
  }
  check(reached, 'Tab reaches the Furnished Finder link');
  const [popup] = await Promise.all([ctx.waitForEvent('page'), page.keyboard.press('Enter')]);
  await popup.waitForLoadState();
  check(popup.url() === 'https://example.com/fixture/furnished/fixture-town' && new URL(page.url()).pathname.endsWith('/alpha-peak/'),
    'Enter opens the listings in a new tab and the map stays put');
  await popup.close();
  // Show on the detail map: starts the WebGL map, switches housing on and fits the map to the pins.
  const show = sec.locator('button.housing-show-btn');
  check(await show.innerText() === 'Show on the detail map' && !(await page.evaluate(() => window.__careerMap.mapStarted)), 'Show on the detail map waits for a click');
  await show.focus();
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => window.__careerMap.mapCtl && window.__careerMap.mapCtl.ready, null, { timeout: 30000 });
  await page.waitForFunction(() => /housing sites on the detail map/.test(document.getElementById('panel-status').textContent), null, { timeout: 15000 });
  await mapIdle(page);
  const fx = JSON.parse(fs.readFileSync(path.join(REPO, 'tests', 'map', 'fixtures', 'detail', 'alpha-peak.json'), 'utf8'));
  const fit = await page.evaluate((sites) => {
    const m = window.__careerMap.mapCtl.map;
    const b = m.getBounds();
    const n = (id) => (m.getLayoutProperty(id, 'visibility') !== 'none' ? m.queryRenderedFeatures({ layers: [id] }).length : 0);
    return { inside: sites.every((s) => b.contains([s.lon, s.lat])), zoom: m.getZoom(), point: n('detail-housing-point'), area: n('detail-housing-area'),
      blur: [m.getPaintProperty('detail-housing-point', 'circle-blur') || 0, m.getPaintProperty('detail-housing-area', 'circle-blur')],
      status: document.getElementById('panel-status').textContent, focus: document.activeElement && document.activeElement.className };
  }, fx.housing);
  check(fit.inside && fit.zoom > 10 && fit.point === 1 && fit.area === 3,
    `the detail map centers on the housing pins: 1 building pin and 3 neighborhood circles (${JSON.stringify(fit)})`);
  check(fit.blur[0] === 0 && fit.blur[1] > 0, 'building pins and neighborhood circles draw in distinct styles');
  check(fit.status === 'Showing 4 housing sites on the detail map. Pins mark listed addresses and circles mark neighborhood areas.'
    && /housing-show-btn/.test(fit.focus), 'the move is announced and focus stays on the button');
  // With the housing layer switched off, the button switches it back on.
  await page.locator('#layer-housing').uncheck();
  await page.evaluate(() => window.__careerMap.mapCtl.map.jumpTo({ center: [-100, 40], zoom: 4 }));
  await show.click();
  await page.waitForFunction(() => window.__careerMap.mapCtl.map.getZoom() > 10, null, { timeout: 10000 });
  check(await page.locator('#layer-housing').isChecked()
    && await page.evaluate(() => window.__careerMap.mapCtl.map.getLayoutProperty('detail-housing-point', 'visibility') !== 'none'),
  'Show on the detail map turns the housing layer back on');
  await page.locator('#panel-scroll').evaluate((el) => { el.scrollTop = el.querySelector('.housing').offsetTop - 60; });
  await shot(page, 'desktop-22-housing.png');
  if (axeSrc) {
    await page.addScriptTag({ content: axeSrc });
    const res = await page.evaluate(async () => (await window.axe.run(document, { resultTypes: ['violations'], runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'] } }))
      .violations.map((v) => `${v.id} (${v.impact}): ${v.help} ${JSON.stringify(v.nodes.slice(0, 3).map((n) => n.target.join(' ')))}`));
    check(res.length === 0, `axe resort panel with housing: ${res.length} violations${res.length ? `\n      ${res.join('\n      ')}` : ''}`);
  } else log('  skip axe housing: run (cd tests/map && npm install) for axe-core');

  // A Tier B resort gets its cluster's tools and no listed sites; a Tier A resort without links says so.
  await page.locator('.crumbs a', { hasText: 'Fixture Front Range' }).click();
  await page.locator('.resort-list a', { hasText: 'Charlie Hill' }).click();
  await page.waitForSelector('#panel-content .housing .tool-link', { timeout: 15000 });
  const b = page.locator('#panel-content .housing');
  check(JSON.stringify(await b.locator('.tool-link').allInnerTexts()) === JSON.stringify(['Fixture Front Range Rooms', 'Fixture Valley Listings'])
    && await b.locator('.ff-link').count() === 0 && (await b.innerText()).includes('No employee housing listed publicly yet.')
    && await b.locator('.housing-show-btn').count() === 0,
  'Tier B resort: cluster tools, no Furnished Finder link without a sourced url, no listed sites');
  w.assertClean();
  const p2 = await ctx.newPage();
  const w2 = watch(p2, 'housing, Tier A without links');
  await p2.goto(`${stubBase}/map/vt/fixture-green-ridge/kilo-notch/`);
  await ready(p2, { start: false });
  await p2.waitForSelector('#panel-content .housing .tools', { timeout: 15000 });
  const k = await p2.locator('#panel-content .housing').innerText();
  check(k.includes('Housing search tools are being collected.') && k.includes('No employee housing listed publicly yet.') && !k.includes('Furnished rentals'),
    'Tier A without links: tools are being collected, no employee housing listed, no Furnished rentals');
  w2.assertClean();
  await ctx.close();

  // Phone: the button drops the sheet to its peek so the map shows the pins.
  const pctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, hasTouch: true, isMobile: true });
  const pp = await pctx.newPage();
  const wp = watch(pp, 'housing phone');
  await pp.goto(`${stubBase}/map/co/fixture-front-range/alpha-peak/`);
  await ready(pp, { start: false });
  await pp.waitForSelector('#panel-content .housing-show-btn', { timeout: 15000 });
  await pp.locator('#panel-content .housing-show-btn').scrollIntoViewIfNeeded();
  await pp.locator('#panel-content .housing-show-btn').tap();
  await pp.waitForFunction(() => /housing sites on the detail map/.test(document.getElementById('panel-status').textContent), null, { timeout: 30000 });
  await pp.waitForTimeout(1200);
  const ph = await pp.evaluate((sites) => {
    const m = window.__careerMap.mapCtl.map;
    const b = m.getBounds();
    return { sheet: document.getElementById('panel').dataset.sheet, inside: sites.every((s) => b.contains([s.lon, s.lat])) };
  }, fx.housing);
  check(ph.sheet === 'peek' && ph.inside, `phone: the sheet drops to its peek and the map shows every pin (${JSON.stringify(ph)})`);
  await shot(pp, 'phone-12-housing-map.png');
  wp.assertClean();
  await pctx.close();

  // Without JavaScript the stub carries the Furnished Finder link and the tool names as plain links.
  const nojs = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 1280, height: 800 } });
  const np = await nojs.newPage();
  await np.goto(`${stubBase}/map/co/fixture-front-range/alpha-peak/`);
  const pre = np.locator('#panel-content .housing-search');
  check(await pre.locator('h3').textContent() === 'Housing' && await pre.locator('a.ff-link').innerText() === 'Furnished Finder listings near Fixture Town'
    && JSON.stringify(await pre.locator('a.tool-link').allInnerTexts())
      === JSON.stringify(['Fixture Staff Housing Portal', 'Fixture Front Range Rooms', 'Fixture Valley Listings'])
    && (await pre.locator('a').evaluateAll((as) => as.every((a) => a.target === '_blank' && /\bnoopener\b/.test(a.rel)))),
  'prerendered stub links Furnished Finder and the tools, in rank order, in a new tab');
  await nojs.close();
}

// Hiring now (ROADMAP item 13): the chip counts resorts with an open role in jobs/postings.json (at a linked employer,
// naming the resort or no single resort) or a hiring window whose months cover today. It filters the map and the list
// view, works with an empty feed and stays visible, disabled, at 0. The clock is fixed so the fixture windows hold.
async function hiringChecks(browser, base) {
  log('\nHiring now filter');
  const open = async (iso, { empty = false, width = 1280, height = 800 } = {}) => {
    const ctx = await browser.newContext({ viewport: { width, height } });
    await ctx.clock.setFixedTime(new Date(iso));
    if (empty) {
      await ctx.route(/\/tests\/map\/fixtures\/jobs\/postings\.json$/, (route) => route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ version: 1, generated_at: '2026-10-13T00:00:00Z', fetched_at: null, keywords: [], boards: [], sources: [], postings: [] }) }));
    }
    const page = await ctx.newPage();
    const w = watch(page, `hiring now ${iso.slice(0, 10)}${empty ? ' empty feed' : ''}`);
    // The fixed clock also empties the performance timeline, so wait on the response itself (the paint wait then
    // falls back to its timer).
    const postings = page.waitForResponse((r) => r.url().includes('/jobs/postings.json'), { timeout: 20000 });
    await page.goto(`${base}/map/${Q}`);
    await ready(page, { start: false });
    await postings;
    await page.waitForTimeout(300);
    return { ctx, page, w, chip: page.locator('#facility-filter .filter-btn[data-filter="hiring"]') };
  };
  const listNames = async (page) => (await page.locator('#list-view a.tree-resort').allInnerTexts()).sort();

  // Open roles at Alpha Peak and Bravo Bowl (Fixture Parent board) plus Kilo Notch's window (2026-09 to 2026-12).
  // Alpha Peak's window names no end, so the text alone would not count.
  let { ctx, page, w, chip } = await open('2026-10-13T12:00:00');
  check(await chip.innerText() === 'Hiring now (3)' && await chip.getAttribute('aria-disabled') === null,
    `chip counts open roles and current hiring windows (${await chip.innerText()})`);
  check(JSON.stringify(await page.locator('#facility-filter .filter-btn').allInnerTexts()) === JSON.stringify(['All', 'Ski areas', 'Indoor and training', 'Hiring now (3)', 'Epic', 'Ikon']),
    'Hiring now sits next to All, Ski areas and Indoor and training, before the pass chips');
  await page.getByRole('button', { name: 'List view' }).click();
  await chip.click();
  check(await chip.getAttribute('aria-pressed') === 'true' && JSON.stringify(await listNames(page)) === JSON.stringify(['Alpha Peak', 'Bravo Bowl', 'Kilo Notch']),
    'list view: Hiring now lists the hiring resorts only');
  check(await page.waitForFunction(() => document.getElementById('panel-status').textContent.includes('Showing 3 resorts hiring now.'), null, { timeout: 3000 })
    .then(() => true, () => false), 'the filter change is announced');
  await shot(page, 'hiring-01-list.png');
  await page.getByRole('button', { name: 'Map view' }).click();
  await ready(page);
  await mapIdle(page);
  const shown = await page.evaluate(() => {
    const m = window.__careerMap.mapCtl.map;
    return [...new Set(m.queryRenderedFeatures({ layers: ['resorts', 'resorts-facility'] }).map((f) => f.properties.id))].sort();
  });
  check(JSON.stringify(shown) === JSON.stringify(['alpha-peak', 'bravo-bowl', 'kilo-notch']), `map: Hiring now keeps only the hiring resorts (${shown.join(', ')})`);
  await shot(page, 'hiring-02-map.png');
  await page.getByRole('button', { name: 'All', exact: true }).click();
  await page.waitForTimeout(300);
  const all = await page.evaluate(() => new Set(window.__careerMap.mapCtl.map.queryRenderedFeatures({ layers: ['resorts', 'resorts-facility'] }).map((f) => f.properties.id)).size);
  check(all > 3, `All brings every resort back (${all} on screen)`);
  // The rule itself (map/js/hiring.js hiringNowIds) on synthetic rows: which postings and windows count.
  const unit = await page.evaluate(async () => {
    const { hiringNowIds } = await import('/map/js/hiring.js');
    const R = (id, hiring_window, job_employers) => ({ id, hiring_window, job_employers });
    const rows = [R('named', null, ['emp']), R('wide', null, ['wide-co']), R('other', null, ['emp']), R('no-board', null, ['ghost']),
      R('http', null, ['plain']), R('win', ['2026-09', '2026-10'], null), R('next', ['2026-11', '2027-02'], null), R('none', null, null)];
    const P = (employer_id, resort_ids, url = 'https://jobs.example.test/1') => ({ employer_id, resort_ids, url });
    const feed = { boards: [{ employer_id: 'emp' }, { employer_id: 'wide-co' }, { employer_id: 'plain' }],
      postings: [P('emp', ['named']), P('wide-co', []), P('ghost', ['no-board']), P('plain', ['http'], 'http://jobs.example.test/2'), P('emp', ['elsewhere'])] };
    const at = (d) => [...hiringNowIds(rows, feed, new Date(d))].sort();
    return { oct: at('2026-10-31T12:00:00'), nov: at('2026-11-01T12:00:00'), noFeed: [...hiringNowIds(rows, null, new Date('2026-12-15T12:00:00'))] };
  });
  check(JSON.stringify(unit.oct) === JSON.stringify(['named', 'wide', 'win']) && JSON.stringify(unit.nov) === JSON.stringify(['named', 'next', 'wide'])
    && JSON.stringify(unit.noFeed) === JSON.stringify(['next']),
  `rule: a role naming the resort or no resort counts; another resort's role, an employer without a board and an http link do not; window months are inclusive (${JSON.stringify(unit)})`);
  w.assertClean();
  await ctx.close();

  // A click before postings.json is in (held back here): the chip counts the windows at once, and the open roles
  // join the selected filter when the file lands.
  {
    const c = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    await c.clock.setFixedTime(new Date('2026-10-13T12:00:00'));
    let release;
    const held = new Promise((r) => { release = r; });
    await c.route(/\/tests\/map\/fixtures\/jobs\/postings\.json$/, async (route) => { await held; await route.continue(); });
    const p = await c.newPage();
    const wp = watch(p, 'hiring now early click');
    await p.goto(`${base}/map/${Q}`);
    await ready(p, { start: false });
    const early = p.locator('#facility-filter .filter-btn[data-filter="hiring"]');
    check(/^Hiring now( \(1\))?$/.test(await early.innerText()), `until hiring.js loads the chip reads Hiring now (${await early.innerText()})`);
    await p.getByRole('button', { name: 'List view' }).click();
    await early.click();
    await p.waitForFunction(() => document.querySelector('[data-filter="hiring"]').getAttribute('aria-pressed') === 'true', null, { timeout: 5000 });
    const first = await listNames(p);
    release();
    await p.waitForFunction(() => document.querySelectorAll('#list-view a.tree-resort').length === 3, null, { timeout: 20000 });
    check(JSON.stringify(first) === JSON.stringify(['Kilo Notch']) && JSON.stringify(await listNames(p)) === JSON.stringify(['Alpha Peak', 'Bravo Bowl', 'Kilo Notch'])
      && await early.innerText() === 'Hiring now (3)', `an early click filters by the windows, then the open roles join (${first.join(', ')})`);
    wp.assertClean();
    await c.close();
  }

  // An empty feed (today's real postings.json): the hiring windows alone.
  ({ ctx, page, w, chip } = await open('2026-10-13T12:00:00', { empty: true }));
  check(await chip.innerText() === 'Hiring now (1)', `empty feed: the chip counts the hiring windows (${await chip.innerText()})`);
  await page.getByRole('button', { name: 'List view' }).click();
  await chip.click();
  check(JSON.stringify(await listNames(page)) === JSON.stringify(['Kilo Notch']), 'empty feed: the list shows the resort whose window covers today');
  w.assertClean();
  await ctx.close();

  // After every window and with no postings: 0, visible and disabled with a short title; a click changes nothing.
  ({ ctx, page, w, chip } = await open('2027-01-15T12:00:00', { empty: true, width: 390, height: 844 }));
  check(await chip.isVisible() && await chip.innerText() === 'Hiring now (0)' && await chip.getAttribute('aria-disabled') === 'true'
    && await chip.getAttribute('title') === 'No resort lists an open role or a hiring window for this month.', 'at 0 the chip stays visible, disabled, with a title');
  await chip.click({ force: true });
  check(await chip.getAttribute('aria-pressed') === 'false' && await page.getByRole('button', { name: 'All', exact: true }).getAttribute('aria-pressed') === 'true',
    'a disabled Hiring now cannot be selected');
  const fit = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, vw: innerWidth }));
  check(fit.sw <= fit.vw, 'phone: the six chips never widen the page');
  await shot(page, 'hiring-03-phone-zero.png');
  w.assertClean();
  await ctx.close();
}

// Epic and Ikon pass chips (ROADMAP item 12): each keeps the resorts on its pass; together they keep either pass;
// they combine with the facility chips and Hiring now (AND) in the list view and on the map; All clears them. The
// resort panel and its prerendered stub show a "Passes" line with badge, season, blackout rule and source.
async function passChecks(browser, base, stubBase) {
  log('\nEpic and Ikon pass chips');
  const axeSrc = (() => {
    try { return fs.readFileSync(require.resolve('axe-core/axe.min.js', { paths: [path.join(REPO, 'tests', 'map')] }), 'utf8'); } catch { return null; }
  })();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await ctx.clock.setFixedTime(new Date('2026-10-13T12:00:00'));
  const page = await ctx.newPage();
  const w = watch(page, 'pass chips');
  const postings = page.waitForResponse((r) => r.url().includes('/jobs/postings.json'), { timeout: 20000 });
  await page.goto(`${base}/map/${Q}`);
  await ready(page, { start: false });
  await postings;
  await page.waitForTimeout(300);
  const chip = (k) => page.locator(`#facility-filter .filter-btn[data-filter="${k}"]`);
  const pressed = async () => (await page.locator('#facility-filter .filter-btn[aria-pressed="true"]').evaluateAll((els) => els.map((e) => e.dataset.filter)));
  const names = async () => (await page.locator('#list-view a.tree-resort').allInnerTexts()).sort();
  const said = (text) => page.waitForFunction((t) => document.getElementById('panel-status').textContent === t, text, { timeout: 3000 }).then(() => true, () => false);
  check(await chip('epic').innerText() === 'Epic' && await chip('ikon').innerText() === 'Ikon'
    && await chip('epic').getAttribute('title') === 'Resorts on the Epic Pass' && await chip('epic').getAttribute('aria-disabled') === null,
  'Epic and Ikon chips follow Hiring now, with a title naming the pass');
  // Before the WebGL map starts, the 2D picture keeps only the resorts the chips keep.
  const dots = () => page.locator('#map .map-preview circle').count();
  const allDots = await dots();
  await chip('epic').click();
  const epicDots = await dots();
  await chip('epic').click();
  check(allDots === 14 && epicDots === 3 && await dots() === 14, `2D picture: Epic keeps 3 of ${allDots} dots, and turning it off brings them back`);
  await page.getByRole('button', { name: 'List view' }).click();
  await chip('epic').click();
  check(JSON.stringify(await names()) === JSON.stringify(['Alpha Peak', 'Bravo Bowl', 'Mike Snow Dome']) && await said('Showing 3 resorts on the Epic Pass.'),
    'list view: Epic keeps the Epic Pass resorts, facilities included, and is announced');
  await chip('ikon').click();
  check((await names()).length === 6 && JSON.stringify(await pressed()) === JSON.stringify(['epic', 'ikon'])
    && await said('Showing 6 resorts on the Epic Pass or Ikon Pass.'), 'Epic and Ikon together keep the resorts on either pass');
  await chip('epic').click();
  check(JSON.stringify(await names()) === JSON.stringify(['Charlie Hill', 'Foxtrot Basin', 'Kilo Notch']), 'a second click turns Epic off');
  await chip('hiring').click();
  check(JSON.stringify(await names()) === JSON.stringify(['Kilo Notch']) && JSON.stringify(await pressed()) === JSON.stringify(['hiring', 'ikon'])
    && await said('Showing 1 resort hiring now on the Ikon Pass.'), 'Ikon AND Hiring now keeps the hiring Ikon resorts');
  await page.getByRole('button', { name: 'All', exact: true }).click();
  check((await names()).length === 14 && JSON.stringify(await pressed()) === JSON.stringify(['all']) && await said('Showing every resort.'),
    'All clears every chip');
  await chip('epic').click();
  await page.getByRole('button', { name: 'Ski areas' }).click();
  check(JSON.stringify(await names()) === JSON.stringify(['Alpha Peak', 'Bravo Bowl']) && await said('Showing 2 ski areas on the Epic Pass.'),
    'Epic AND Ski areas drops the indoor facility');
  await page.getByRole('button', { name: 'Indoor and training' }).click();
  check(JSON.stringify(await names()) === JSON.stringify(['Mike Snow Dome']) && JSON.stringify(await pressed()) === JSON.stringify(['facility', 'epic']),
    'Epic AND Indoor and training keeps the indoor facility only');
  await shot(page, 'passes-01-list.png');
  // The map: the same rule on the resort and facility layers.
  await page.getByRole('button', { name: 'Map view' }).click();
  await ready(page);
  await mapIdle(page);
  const onMap = () => page.evaluate(() => {
    const m = window.__careerMap.mapCtl.map;
    return [...new Set(m.queryRenderedFeatures({ layers: ['resorts', 'resorts-facility'] }).map((f) => f.properties.id))].sort();
  });
  check(JSON.stringify(await onMap()) === JSON.stringify(['mike-dome']), 'map: Epic AND Indoor and training (set before the map started) applies once it runs');
  await page.getByRole('button', { name: 'Indoor and training' }).click();
  await chip('ikon').click();
  await page.waitForTimeout(300);
  check(JSON.stringify(await onMap()) === JSON.stringify(['alpha-peak', 'bravo-bowl', 'charlie-hill', 'foxtrot-basin', 'kilo-notch', 'mike-dome']),
    `map: Epic or Ikon keeps the six pass resorts (${(await onMap()).join(', ')})`);
  await chip('hiring').click();
  await page.waitForTimeout(300);
  check(JSON.stringify(await onMap()) === JSON.stringify(['alpha-peak', 'bravo-bowl', 'kilo-notch']), 'map: the passes AND Hiring now');
  await shot(page, 'passes-02-map.png');
  await page.getByRole('button', { name: 'All', exact: true }).click();
  await page.waitForTimeout(300);
  check((await onMap()).length > 6, 'map: All brings every resort back');
  // A build without pass data (older index): the chips stay visible and disabled with a title.
  const unit = await page.evaluate(async () => {
    const { matchesFilter, isFiltered, NO_FILTER } = await import('/map/js/data.js');
    const { passLine } = await import('/map/js/labels.js');
    const r = (facility_type, passes, hiring_now = false) => ({ facility_type, passes, hiring_now });
    const f = (o) => ({ ...NO_FILTER, ...o });
    return {
      none: matchesFilter(r('ski_area', []), NO_FILTER) && !isFiltered(NO_FILTER),
      either: [r('ski_area', ['epic']), r('ski_area', ['ikon']), r('ski_area', ['mountain_collective'])].map((x) => matchesFilter(x, f({ passes: ['epic', 'ikon'] }))),
      and: [r('indoor', ['epic']), r('ski_area', ['epic'], true), r('ski_area', ['epic'])].map((x) => matchesFilter(x, f({ facility: 'ski_area', hiring: true, passes: ['epic'] }))),
      lines: [passLine({ pass: 'epic', access: 'unlimited' }), passLine({ pass: 'ikon', access: 'limited', days: 7 }),
        passLine({ pass: 'mountain_collective', access: 'limited', days: null }), passLine({ pass: 'ikon', access: 'unspecified' }),
        passLine({ pass: 'epic', access: 'unlimited', season: '2025/26', season_fallback: true })],
    };
  });
  check(unit.none && JSON.stringify(unit.either) === '[true,true,false]' && JSON.stringify(unit.and) === '[false,true,false]',
    `rule: passes match any chosen pass, and every active part must match (${JSON.stringify(unit)})`);
  check(JSON.stringify(unit.lines) === JSON.stringify(['Epic Pass: unlimited', 'Ikon Pass: 7 days', 'Mountain Collective: limited days',
    'Ikon Pass: days not stated', 'Epic Pass: unlimited (2025/26 list)']), 'pass lines read "Epic Pass: unlimited", "Ikon Pass: 7 days", "Epic Pass: unlimited (2025/26 list)"');
  w.assertClean();
  await ctx.close();

  // Resort panel: the Passes line under Owner, with badge, season, blackout rule, date and source.
  const rp = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const wr = watch(rp, 'passes panel');
  await rp.goto(`${stubBase}/map/co/fixture-front-range/alpha-peak/`);
  await ready(rp, { start: false });
  await rp.waitForSelector('#panel-content .fact.passes .src-link', { timeout: 15000 });
  const items = await rp.locator('#panel-content .fact.passes li').allInnerTexts();
  check(items.length === 2 && items[0].includes('Epic Pass: unlimited') && /estimated/i.test(items[0])
    && items[0].includes('2026/27 season · No blackout dates · Last verified Oct 1, 2026') && items[0].includes('Synthetic fixture: pass company page')
    && items[1].includes('Mountain Collective: 2 days'), `panel: Passes line per pass with badge, season, blackout and source (${items.map((x) => x.replace(/\s+/g, ' ')).join(' | ')})`);
  check(await rp.locator('#panel-content .facts .fact').nth(1).locator('dt').innerText() === 'Passes', 'panel: Passes sits right under Owner');
  if (axeSrc) {
    await rp.addScriptTag({ content: axeSrc });
    const res = await rp.evaluate(async () => (await window.axe.run(document, { resultTypes: ['violations'], runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'] } }))
      .violations.map((v) => `${v.id} (${v.impact}): ${v.help} ${JSON.stringify(v.nodes.slice(0, 3).map((n) => n.target.join(' ')))}`));
    check(res.length === 0, `axe resort panel with Passes: ${res.length} violations${res.length ? `\n      ${res.join('\n      ')}` : ''}`);
  } else log('  skip axe passes: run (cd tests/map && npm install) for axe-core');
  await shot(rp, 'passes-03-panel.png');
  await rp.goto(`${stubBase}/map/vt/fixture-green-ridge/lima-hollow/`);
  await ready(rp, { start: false });
  await rp.waitForSelector('#panel-content .facts', { timeout: 15000 });
  check(await rp.locator('#panel-content .fact.passes').count() === 0, 'panel: a resort with no membership shows no Passes line');
  wr.assertClean();
  await rp.close();

  // Prerendered stub: the same line before any JavaScript runs.
  const html = await (await fetch(`${stubBase}/map/co/fixture-front-range/bravo-bowl/`)).text();
  const pre = html.slice(html.indexOf('<!-- prerender:start -->'), html.indexOf('<!-- prerender:end -->'));
  check(pre.includes('<dt>Passes</dt>') && pre.includes('Epic Pass: 7 days (2025/26 list)') && pre.includes('2025/26 season · Blackout dates apply')
    && pre.includes('badge-estimated') && pre.includes('href="https://example.com/fixture/pass-site"'), 'stub: prerendered Passes line with badge and source; a previous season fallback names its list');

  // Phone: six chips scroll sideways inside their row.
  const pp = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const wpp = watch(pp, 'pass chips phone');
  await pp.goto(`${base}/map/${Q}`);
  await ready(pp, { start: false });
  await pp.locator('#facility-filter .filter-btn[data-filter="ikon"]').scrollIntoViewIfNeeded();
  await pp.locator('#facility-filter .filter-btn[data-filter="ikon"]').click();
  const fit = await pp.evaluate(() => ({ sw: document.documentElement.scrollWidth, vw: innerWidth,
    pressed: document.querySelector('[data-filter="ikon"]').getAttribute('aria-pressed') }));
  check(fit.sw <= fit.vw && fit.pressed === 'true', 'phone: the Ikon chip is reachable and the row never widens the page');
  await shot(pp, 'passes-04-phone.png');
  wpp.assertClean();
  await pp.close();
}

// Resort search (map/js/search.js): loose matching and ranking, keyboard selection before the map starts, the deep
// link a result lands on, the list view, the WebGL map flying to a result, and the phone layout above the sheet.
async function searchChecks(browser, base, stubBase) {
  log('\nResort search');
  // Wait for the background loads (map prefetch after the window load, climate and charts) so leaving the page
  // aborts nothing.
  const inflight = new WeakMap();
  const track = (p) => {
    inflight.set(p, 0);
    p.on('request', () => inflight.set(p, inflight.get(p) + 1));
    for (const ev of ['requestfinished', 'requestfailed']) p.on(ev, () => inflight.set(p, inflight.get(p) - 1));
  };
  const settle = async (p) => {
    await p.waitForFunction(() => performance.getEntriesByType('resource').some((e) => e.name.includes('/map/js/map.js')), null, { timeout: 10000 });
    for (let quiet = 0; quiet < 5;) { await p.waitForTimeout(100); quiet = inflight.get(p) <= 0 ? quiet + 1 : 0; }
  };
  const fix = JSON.parse(fs.readFileSync(path.join(REPO, 'tests', 'map', 'fixtures', 'map-index.json'), 'utf8'));
  const at = (id) => { const row = fix.resorts.find((r) => r[0] === id); return [row[fix.fields.indexOf('lon')], row[fix.fields.indexOf('lat')]]; };
  const inView = (page, lngLat) => page.evaluate((ll) => { const m = window.__careerMap.mapCtl.map; return m.getBounds().contains(ll) && m.getZoom() > 7; }, lngLat);
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const w = watch(page, 'search');
  await page.goto(`${base}/map/${Q}`);
  await ready(page, { start: false });
  const input = page.getByRole('combobox', { name: 'Search resorts, clusters and states' });
  const list = page.locator('#map-search-list');
  const opts = list.getByRole('option');
  const loaded = () => page.evaluate(() => performance.getEntriesByType('resource').some((e) => e.name.includes('/map/js/search.js')));
  check(await input.isVisible() && await input.getAttribute('aria-expanded') === 'false' && !(await loaded()), 'search box shows; search.js waits for first use');

  const unit = await page.evaluate(async () => {
    const m = await import('/map/js/search.js');
    const items = m.buildItems(window.__careerMap.index);
    const names = (q) => m.search(items, q).map((x) => x.name);
    return { norm: m.normalize("Smúgglers’ NOTCH, Mt. Hood"), ch: names('ch'), alpha: names('ALPHA'), co: names('co')[0], none: names('zzz').length, one: names('e') };
  });
  check(unit.norm === 'smugglers notch mt hood', `matching ignores accents, case and punctuation ("${unit.norm}")`);
  check(unit.ch[0] === 'Charlie Hill' && unit.ch.indexOf('Echo Mesa') > 0, `prefix matches rank first (ch: ${unit.ch.join(', ')})`);
  check(unit.alpha[0] === 'Alpha Peak' && unit.co === 'Colorado' && unit.none === 0, 'case loose names and state codes match');
  check(unit.one.join() === 'Echo Mesa', `one letter matches word starts only (e: ${unit.one.join(', ')})`);

  await input.focus();
  await page.keyboard.type('bravo');
  await list.waitFor({ state: 'visible' });
  const first = (await opts.first().innerText()).replace(/\s+/g, ' ');
  check(await input.getAttribute('aria-expanded') === 'true' && await opts.count() === 1 && first === 'Bravo Bowl Tier A resort in Colorado',
    `typing lists matches with tier and state ("${first}")`);
  await page.keyboard.press('ArrowDown');
  const active = await input.getAttribute('aria-activedescendant');
  const ring = active && await page.locator(`#${active}`).evaluate((el) => `${el.getAttribute('aria-selected')} ${getComputedStyle(el).outlineStyle} ${getComputedStyle(el).outlineWidth}`);
  check(ring === 'true solid 2px', `ArrowDown makes the option active with a visible focus ring (${ring})`);
  check(await input.evaluate((el) => getComputedStyle(el).outlineStyle) === 'solid', 'the search box shows a focus ring');
  await shot(page, 'search-01-results.png');
  await page.keyboard.press('Enter');
  await page.waitForURL(/\/map\/co\/fixture-front-range\/bravo-bowl\/\?data=/);
  check(await heading(page) === 'Bravo Bowl', 'Enter opens the resort through the router');
  check(await list.isHidden() && await input.getAttribute('aria-expanded') === 'false' && await input.inputValue() === '', 'the list closes and the box clears');
  check(await page.evaluate(() => document.activeElement.id === 'panel-heading'), 'focus moves to the panel heading, as after a click');
  check(await page.evaluate(() => !window.__careerMap.mapStarted && !window.__careerMap.mapCtl && document.querySelectorAll('.map-preview').length === 1),
    'a result chosen before the map starts leaves the WebGL map waiting and redraws the picture');

  await input.focus();
  await page.keyboard.type('fixture');
  await list.waitFor({ state: 'visible' });
  const n = await opts.count();
  await page.keyboard.press('ArrowUp');
  check(n === 5 && await input.getAttribute('aria-activedescendant') === `map-search-list-opt-${n - 1}`, `ArrowUp wraps to the last of ${n} results`);
  await page.keyboard.press('Escape');
  check(await list.isHidden() && await input.inputValue() === 'fixture', 'Escape closes the list and keeps the text');
  await page.keyboard.press('Escape');
  check(await input.inputValue() === '', 'a second Escape clears the box');
  await page.keyboard.type('zz top');
  check(await page.locator('.map-search-empty').isVisible() && await list.isHidden(), 'no match says so');
  await input.fill('');

  // List view: the panel follows and the list marks the result.
  await page.getByRole('button', { name: 'List view' }).click();
  await input.focus();
  await page.keyboard.type('kilo');
  await list.waitFor({ state: 'visible' });
  await page.keyboard.press('Enter');
  await page.waitForURL(/\/map\/vt\/fixture-green-ridge\/kilo-notch\//);
  const cur = page.locator('#list-view a[aria-current="page"]');
  const shown = await cur.evaluate((el) => { const r = el.getBoundingClientRect(); const l = el.closest('#list-view').getBoundingClientRect(); return r.top >= l.top && r.bottom <= l.bottom; });
  check(await heading(page) === 'Kilo Notch' && await page.locator('#list-view').isVisible() && await cur.innerText() === 'Kilo Notch' && shown,
    'list view: a result opens the resort and the list marks it');
  await shot(page, 'search-02-list-view.png');
  await page.getByRole('button', { name: 'Map view' }).click();

  // The map starts on the chosen view, then flies to the next result (picked with the mouse).
  await page.locator('#map-start').click();
  await page.waitForFunction(() => window.__careerMap.mapCtl, null, { timeout: 20000 });
  await mapIdle(page);
  check(await inView(page, at('kilo-notch')), 'the WebGL map starts on the result chosen before it ran');
  check(await page.locator('.maplibregl-ctrl-attrib-inner a[href="/map/credits/"]').count() === 1
    && await page.locator('#panel-content .panel-footer a[href="/map/credits/"]').count() === 1, 'the map attribution and the panel footer link Sources and credits');
  await input.click();
  await page.keyboard.type('hotel');
  await list.waitFor({ state: 'visible' });
  await opts.first().click();
  await page.waitForURL(/\/map\/ca\/fixture-lake-crest\/hotel-lake\//);
  await page.waitForFunction(() => window.__careerMap.mapCtl.map.isMoving(), null, { timeout: 3000 }).catch(() => {});
  await mapIdle(page);
  check(await heading(page) === 'Hotel Lake' && await inView(page, at('hotel-lake')), 'with the map running, a clicked result flies the map there');
  w.assertClean();
  await page.close();

  // Deep link stubs: a result lands on a real page URL, and a cluster keeps the state the visitor is in.
  const sp = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const ws = watch(sp, 'search on stubs');
  track(sp);
  await sp.goto(`${stubBase}/map/co/fixture-front-range/alpha-peak/`);
  await ready(sp, { start: false });
  const sInput = sp.getByRole('combobox', { name: 'Search resorts, clusters and states' });
  await sInput.focus();
  await sp.keyboard.type('Hotel');
  await sp.locator('#map-search-list').waitFor({ state: 'visible' });
  await sp.keyboard.press('Enter');
  await sp.waitForURL(/\/map\/ca\/fixture-lake-crest\/hotel-lake\/$/);
  await settle(sp); // let the background loads (map prefetch, climate, charts) finish before leaving the page
  await sp.reload();
  await ready(sp, { start: false });
  check(await heading(sp) === 'Hotel Lake' && (await sp.title()).startsWith('Instructor pay, housing, and snow at Hotel Lake'),
    'the URL a result lands on is a working deep link');
  await settle(sp);
  await sp.goto(`${stubBase}/map/nv/`);
  await ready(sp, { start: false });
  await sInput.focus();
  await sp.keyboard.type('lake crest');
  await sp.locator('#map-search-list').waitFor({ state: 'visible' });
  await sp.keyboard.press('Enter');
  await sp.waitForURL(/\/map\/nv\/fixture-lake-crest\/$/);
  check(await heading(sp) === 'Fixture Lake Crest', 'a cluster result keeps the current state (/map/nv/fixture-lake-crest/)');
  await settle(sp);
  ws.assertClean();
  await sp.close();

  // Phone: the box fits the top bar and the results stop above the bottom sheet.
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, hasTouch: true, isMobile: true });
  const pp = await ctx.newPage();
  const wp = watch(pp, 'search phone');
  await pp.goto(`${base}/map/${Q}`);
  await ready(pp, { start: false });
  const box = (sel) => pp.locator(sel).boundingBox();
  const [hdr, sbox, tog] = [await box('.map-header'), await box('#map-search'), await box('#list-toggle')];
  check(sbox.y >= hdr.y && sbox.y + sbox.height <= hdr.y + hdr.height && sbox.x + sbox.width <= tog.x, 'phone: the search box fits the top bar');
  await pp.locator('#sheet-handle').click();
  await pp.waitForTimeout(400);
  const pInput = pp.getByRole('combobox', { name: 'Search resorts, clusters and states' });
  await pInput.tap();
  await pp.keyboard.type('ra');
  await pp.locator('#map-search-list').waitFor({ state: 'visible' });
  // Wait for the sheet to finish sliding to half.
  await pp.waitForFunction(() => { const t = document.getElementById('panel').getBoundingClientRect().top; const same = window.__sheetTop === t; window.__sheetTop = t; return same; },
    null, { polling: 150, timeout: 5000 });
  const fit = await pp.evaluate(() => ({ list: document.getElementById('map-search-list').getBoundingClientRect().bottom,
    sheet: document.getElementById('panel').getBoundingClientRect().top, state: document.getElementById('panel').dataset.sheet,
    rows: document.querySelectorAll('#map-search-list [role="option"]').length }));
  check(fit.state === 'half' && fit.list <= fit.sheet && fit.rows > 1, `phone: results stop above the sheet (list ${Math.round(fit.list)} px, sheet ${Math.round(fit.sheet)} px; a full sheet drops to half)`);
  await shot(pp, 'search-03-phone.png');
  await pp.locator('#map-search-list [role="option"]', { hasText: 'November Ramps' }).tap();
  await pp.waitForURL(/\/map\/ut\/fixture-wasatch-like\/november-ramps\//);
  check(await heading(pp) === 'November Ramps', 'phone: a tapped result opens the resort');
  wp.assertClean();
  await ctx.close();
}

// MAP_TEST_PORT moves both servers (repository, stub site) so parallel runs do not collide; default 8811 and 8812.
const PORT = Number(process.env.MAP_TEST_PORT) || 8811;
const REPO_BASE = `http://127.0.0.1:${PORT}`;
const STUB_BASE = `http://127.0.0.1:${PORT + 1}`;
const servers = [];
let stubRoot = null;
try {
  const executablePath = findChromium();
  const browser = await chromium.launch({
    executablePath,
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
  });
  // Imagery tiles (config.basemap.imagery) come from a synthetic local tile: the USGS service is outside the
  // test sandbox. Every context gets the route.
  const tile = fs.readFileSync(path.join(REPO, 'tests', 'map', 'fixtures', 'imagery-tile.png'));
  const newContext = browser.newContext.bind(browser);
  browser.newContext = async (opts) => {
    const c = await newContext(opts);
    await c.route(/basemap\.nationalmap\.gov\//, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: tile, headers: { 'Access-Control-Allow-Origin': '*' } }));
    return c;
  };
  browser.newPage = async (opts) => (await browser.newContext(opts)).newPage();
  servers.push(await serve(REPO, PORT));
  stubRoot = makeStubSite();
  servers.push(await serve(stubRoot, PORT + 1));

  await imageryOutage(browser, REPO_BASE);
  await desktop(browser, REPO_BASE);
  await desktopStub(browser, STUB_BASE);
  await phone(browser, STUB_BASE);
  await searchChecks(browser, REPO_BASE, STUB_BASE);
  await liveLinksChecks(browser, STUB_BASE);
  await housingChecks(browser, STUB_BASE);
  await hiringChecks(browser, REPO_BASE);
  await passChecks(browser, REPO_BASE, STUB_BASE);
  await realData(browser, REPO_BASE);
  await browser.close();
} catch (err) {
  failures.push(`crashed: ${err.stack || err}`);
  log(err);
} finally {
  for (const s of servers) s.kill();
  if (stubRoot) fs.rmSync(stubRoot, { recursive: true, force: true });
}

log(`\nScreenshots: ${SHOTS}`);
if (failures.length) {
  log(`\n${failures.length} failure(s):\n - ${failures.join('\n - ')}`);
  process.exit(1);
}
log('\nAll browser checks passed.');
