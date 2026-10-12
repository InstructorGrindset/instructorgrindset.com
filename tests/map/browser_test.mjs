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
    if (r.status() >= 400 && !/\/fonts\/Rooftop/.test(r.url())) problems.push(`HTTP ${r.status()} ${r.url()}`);
  });
  page.on('requestfailed', (r) => /\/fonts\/Rooftop/.test(r.url()) || problems.push(`request failed ${r.url()} ${r.failure() && r.failure().errorText}`));
  return {
    requests,
    assertClean() {
      check(problems.length === 0, `${label}: no console errors or failed requests${problems.length ? `\n      ${problems.join('\n      ')}` : ''}`);
      problems.length = 0;
    },
  };
}

async function ready(page) {
  await page.waitForFunction(() => window.__careerMap && window.__careerMap.ready, null, { timeout: 20000 });
}

async function mapIdle(page) {
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

async function desktop(browser, base) {
  log('\nDesktop 1280x800, fixtures via ?data=');
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const w = watch(page, 'desktop');
  await page.goto(`${base}/map/${Q}`);
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

  // Recolor modes.
  const modes = page.locator('.recolor-modes .filter-btn');
  check(JSON.stringify(await modes.allInnerTexts()) === JSON.stringify(['Default', 'Lucrativeness', 'Professionalism', 'Season length', 'Snowfall']), 'recolor control lists five modes');
  check(await page.locator('.recolor-modes [aria-disabled="true"]').count() === 0, 'every mode enabled with fixture data');
  await page.locator('.recolor-modes .filter-btn[data-mode="season"]').click();
  await page.waitForTimeout(300);
  const leg = page.locator('#recolor-legend');
  check(await leg.isVisible() && (await leg.innerText()).includes('Typical season length in days'), 'season legend visible with title');
  check(await leg.locator('.ramp li').count() === 6 && (await leg.innerText()).includes('112 days to'), 'legend shows value range classes and No data');
  check(await page.evaluate(() => JSON.stringify(window.__careerMap.mapCtl.map.getPaintProperty('resorts', 'circle-color')).includes('season_length_days')), 'circles recolored by season length');
  check(await page.evaluate(() => JSON.stringify(window.__careerMap.mapCtl.map.getPaintProperty('resorts', 'circle-stroke-color')).includes('#e0a03a')), 'Tier A keeps an amber outline in recolor modes');
  await shot(page, 'desktop-01c-recolor-season.png');
  await page.locator('.recolor-modes .filter-btn[data-mode="lucrativeness"]').click();
  check((await leg.innerText()).includes('$6.1K'), 'lucrativeness legend in dollars');
  await page.locator('.recolor-modes .filter-btn[data-mode="default"]').click();
  check(await leg.isHidden(), 'Default hides the recolor legend');

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
  check(await page.evaluate(() => {
    const c = window.Chart && Object.values(window.Chart.instances).find((x) => x.data.datasets.length === 3);
    return c ? c.data.datasets[0].label : '';
  }) === 'Dry, under 7%', 'density legend uses definitions.json cutoffs');
  check(await page.locator('.climate canvas').count() === 5, 'five climate charts');
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
  const trigger = page.locator('.score-trigger');
  const tip = page.locator('.breakdown');
  check(await tip.isHidden(), 'breakdown hidden until hover or focus');
  await page.locator('#panel-scroll').evaluate((el) => {
    const t = el.querySelector('.score-trigger'); el.scrollTop = t.offsetTop - 120;
  });
  await trigger.hover();
  check(await tip.isVisible(), 'breakdown shows on hover');
  check((await tip.innerText()).includes('Clinic access (needs update)'), 'stale professionalism component marked in breakdown');
  check(await tip.locator('tbody tr').count() === 6 && JSON.stringify(await tip.locator('thead th').allInnerTexts()) ===
    JSON.stringify(['Component', 'Weight', 'Value', 'Points']), 'breakdown lists component, weight, value, points');
  await shot(page, 'desktop-04b-professionalism-hover.png');
  await page.mouse.move(5, 400);
  check(await tip.isHidden(), 'breakdown hides on mouse leave');
  await trigger.focus();
  check(await tip.isVisible() && await trigger.getAttribute('aria-expanded') === 'true', 'breakdown shows on keyboard focus');
  await page.keyboard.press('Escape');
  check(await tip.isHidden(), 'Escape closes the breakdown');
  check((await trigger.innerText()).includes('72'), 'professionalism score 0 to 100 renders');
  check((await page.locator('.score-block', { hasText: 'Lucrativeness' }).innerText()).includes('$3,200 to $8,900'), 'lucrativeness range renders');
  check(await page.locator('.assumptions li').count() === 13, 'lucrativeness assumptions listed beneath (8 config, 2 spreads, 3 resort inputs)');
  const assumptions = await page.locator('.assumptions').innerText();
  check(assumptions.includes('Level 2') && assumptions.includes('$650') && assumptions.includes('15% of earnings') && assumptions.includes('$27.50'),
    'assumption values formatted (cert label, dollars, spreads)');
  check((await page.locator('.pay-table tr[data-level="level_3"]').innerText()).includes('Range from 4 reports')
    && await page.locator('.pay-table tr[data-level="level_3"] .badge-reported').count() === 1, 'reported pay row shows its aggregate count');
  check(await page.locator('.tag', { hasText: 'Destination families' }).count() === 1, 'client profile chips use readable labels');
  check(await page.locator('.sub', { hasText: 'Best suited for skiers and riders' }).count() === 1, 'best suited tags render');

  // Phase 5: resort detail map and Housing and commute.
  await page.waitForSelector('.site-select', { timeout: 10000 });
  await mapIdle(page);
  const counts = () => page.evaluate(() => {
    const m = window.__careerMap.mapCtl.map;
    const n = (id) => (m.getLayer(id) && m.getLayoutProperty(id, 'visibility') !== 'none' ? m.queryRenderedFeatures({ layers: [id] }).length : 0);
    return { lifts: n('detail-lifts'), point: n('detail-housing-point'), area: n('detail-housing-area'), iso: n('detail-iso-fill'),
      transit: n('detail-transit-line'), stops: n('detail-transit-stop'), base: n('detail-base'),
      bus: n('detail-commute-bus'), drive: n('detail-commute-drive') };
  });
  let c = await counts();
  check(c.lifts > 0 && c.base === 1 && c.point === 1 && c.area === 2 && c.iso > 0 && c.transit > 0 && c.stops === 2,
    `detail map draws lifts, base, housing by precision, isochrones, transit (${JSON.stringify(c)})`);
  check(c.bus === 0 && c.drive === 0, 'commute lines hidden until a site is selected');
  check(await page.locator('body.detail-mode').count() === 1 && await page.locator('#recolor').isHidden(), 'detail mode replaces the recolor control');
  check(JSON.stringify(await page.locator('.detail-toggles label').allInnerTexts()) === JSON.stringify(['Lifts', 'Housing', 'Commute', 'Transit', 'Drive time']), 'a toggle per layer with data');
  check((await page.locator('#detail-layers').innerText()).includes('45 min'), 'isochrone legend shows 15, 30, 45 minutes');
  check(await page.locator('.site-select').count() === 3, 'housing lists every site');
  check(JSON.stringify(await page.locator('.site-select .nav-name').allInnerTexts()) === JSON.stringify(['Fixture Staff Village', 'Fixture Town Lease', 'Fixture West Rentals']), 'employee housing listed first');
  const lease = page.locator('.site', { hasText: 'Fixture Town Lease' });
  check((await lease.innerText()).includes('$800 to $950 per month') && await lease.locator('.badge-reported').count() === 1, 'site rent range with badge');
  check((await lease.innerText()).includes('Master lease · Neighborhood area'), 'site type and precision label');
  check((await lease.locator('.commute-table').innerText()).includes('Bus\t28 min\t7.4 mi\tBlue'), 'commute row shows mode, minutes, distance and route name');
  check(await lease.locator('.not-verified', { hasText: 'Being calculated' }).count() === 1, 'missing commute minutes marked');
  check(await page.locator('.site', { hasText: 'Fixture West Rentals' }).locator('.not-verified', { hasText: 'Commute times are being calculated.' }).count() === 1, 'site without commutes shows empty state');
  check((await page.locator('.site', { hasText: 'Fixture Staff Village' }).innerText()).includes('Synthetic note: full time staff only.'), 'eligibility note renders');
  check(await page.locator('.housing .facts .fact', { hasText: 'HUD FMR' }).count() === 3 && await page.locator('.housing .fact', { hasText: 'ACS median gross rent' }).locator('.badge-estimated').count() === 1, 'rent baseline with badges');
  check((await page.locator('.rent-used').innerText()) === 'Rent used: Fixture Staff Village, the cheapest employee housing listed.'
    && await page.locator('.rent-used + a.src-link', { hasText: 'Synthetic fixture: resort facts' }).count() === 1, 'lucrativeness says which rent was used, with its source');
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
  await page.locator('#layer-isochrones').uncheck();
  check(await page.evaluate(() => window.__careerMap.mapCtl.map.getLayoutProperty('detail-iso-fill', 'visibility')) === 'none', 'drive time toggle hides isochrones');
  await page.locator('#layer-isochrones').check();
  await shot(page, 'desktop-11-detail-pin-selected.png');

  // Phase 6: reviewed editorial with sources.
  const ed = page.locator('.editorial', { hasText: 'About Alpha Peak' });
  check(await ed.count() === 1, 'reviewed resort editorial renders');
  check(JSON.stringify(await ed.locator('.sub').allInnerTexts()) === JSON.stringify(['Overview', 'Culture', "Alexandre's take"]), 'editorial shows overview, culture and Alexandre\'s take');
  check(await ed.locator('strong', { hasText: 'bold' }).count() === 1 && await ed.locator('a[href="https://example.com/fixture/editorial"]').count() === 1, 'editorial Markdown renders bold and links');
  check(await ed.locator('.editorial-text p').count() === 4, 'editorial paragraphs split');
  check((await ed.innerText()).includes('Sources: Synthetic fixture: resort facts') && (await ed.innerText()).includes('Reviewed Oct 10, 2026.'), 'editorial cites sources and review date');
  check((await page.locator('.housing .fact', { hasText: 'Reported rent near the resort' }).innerText()).includes('$700 to $1,000 per month'), 'reported area rent renders with badge');

  // Phase 7: report form (endpoint intercepted; nothing leaves the test).
  const posted = [];
  await page.route('https://reports.example.test/submit', async (route) => {
    posted.push(JSON.parse(route.request().postData()));
    await route.fulfill({ status: 202, contentType: 'application/json', body: '{"ok":true}' });
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
  await form.locator('button[type=submit]').click();
  await page.waitForFunction(() => /moderation queue/.test(document.querySelector('.form-status').textContent), null, { timeout: 5000 });
  check(posted.length === 1 && posted[0].resort_id === 'alpha-peak' && posted[0].kind === 'pay' && posted[0].cert_level === 'level_2'
    && posted[0].values.hourly_usd === 24.5 && /^\d{4}-\d{2}$/.test(posted[0].season) && posted[0].proof_url === 'https://example.com/stub.pdf',
    `pay report posts the contract fields (${JSON.stringify(posted[0])})`);
  await form.locator('select[name=kind]').selectOption('housing');
  check(await form.locator('[data-field=cert_level]').isHidden() && await form.locator('[data-field=housing_site_id]').isVisible()
    && (await form.locator('[data-field=amount] label').innerText()) === 'Monthly rent in USD', 'housing report asks for a site and a monthly rent');
  await form.locator('select[name=housing_site_id]').selectOption('alpha-town-lease');
  await form.locator('input[name=amount]').fill('875');
  await form.locator('button[type=submit]').click();
  await page.waitForFunction(() => document.querySelector('.form-status').textContent.includes('moderation'), null, { timeout: 5000 });
  check(posted.length === 2 && posted[1].housing_site_id === 'alpha-town-lease' && posted[1].values.monthly_usd === 875 && !('cert_level' in posted[1]), 'housing report posts site and monthly value');
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
  check(await page.locator('.score-block .not-verified', { hasText: 'Scoring model is being finalized' }).count() === 2, 'weights_unset and assumptions_unset render being finalized');
  check(await page.locator('.assumptions .not-verified', { hasText: 'Not set yet' }).count() === 1, 'unset assumption marked');
  check(await page.locator('.src-tag', { hasText: 'Estimated' }).count() === 1, 'density chart labelled Estimated');
  await page.locator('.crumbs a', { hasText: 'Fixture Front Range' }).click();
  await page.locator('.resort-list a', { hasText: 'Charlie Hill' }).click();
  await page.waitForURL(/charlie-hill/);
  check(await page.locator('.score-block .not-verified', { hasText: 'Not enough verified data yet' }).count() === 2, 'insufficient_data renders not enough data');

  // Lifts only detail (the shape of real Tier A data today), then leave detail mode.
  await page.evaluate(() => window.__careerMap.router.go({ level: 'resort', stateCode: 'UT', clusterId: 'fixture-wasatch-like', resortId: 'foxtrot-basin' }));
  await page.waitForFunction(() => document.querySelectorAll('.detail-toggles label').length > 0, null, { timeout: 10000 });
  check(JSON.stringify(await page.locator('.detail-toggles label').allInnerTexts()) === JSON.stringify(['Lifts']), 'layers without data are hidden');
  check(await page.locator('.housing .not-verified', { hasText: 'Housing data is being collected.' }).count() === 1, 'empty housing state');
  check((await page.locator('.rent-used').innerText()).includes('HUD Fair Market Rent for a studio'), 'baseline rent source named');
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
  check(await page.locator('.housing .not-verified', { hasText: 'Housing data is being collected.' }).count() === 1, 'resort without detail shows housing empty state');
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
  await shot(page, 'desktop-09-deep-link.png');
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
      const props = { lucrativeness: 'lucrativeness_mid', professionalism: 'professionalism_score', season: 'season_length_days', snowfall: 'snowfall_annual_in' };
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
    }
    await shot(page, `real-${vp.name}-national.png`);
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
        check(JSON.stringify(await page.locator('.detail-toggles label').allInnerTexts()) === JSON.stringify(['Lifts']), 'real data: only layers with data get a toggle');
        check(await page.locator('.housing .not-verified', { hasText: 'Housing data is being collected.' }).count() === 1, 'real data: housing empty state');
        check(await page.locator('.panel-section', { hasText: 'Pay and rent reports open soon.' }).count() === 1 && await page.locator('.report-form').count() === 0,
          'real data: report form hidden while the endpoint is null');
        const unnamed = file.lifts.features.findIndex((f) => !f.properties.name);
        if (unnamed >= 0) log(`  real data: ${file.lifts.features.filter((f) => !f.properties.name).length} unnamed lifts render as "Unnamed lift"`);
        await shot(page, 'real-desktop-detail-lifts.png');
      } else log('  skip no Tier A resort with detail_available in the real build yet');
    }
    w.assertClean();
    await page.close();
  }
}

const servers = [];
let stubRoot = null;
try {
  const executablePath = findChromium();
  const browser = await chromium.launch({
    executablePath,
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
  });
  servers.push(await serve(REPO, 8811));
  stubRoot = makeStubSite();
  servers.push(await serve(stubRoot, 8812));

  await desktop(browser, 'http://127.0.0.1:8811');
  await desktopStub(browser, 'http://127.0.0.1:8812');
  await phone(browser, 'http://127.0.0.1:8812');
  await realData(browser, 'http://127.0.0.1:8811');
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
