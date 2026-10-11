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
    if (m.type() === 'warning' && /\[map\]/.test(m.text())) log(`    warn ${m.text()}`);
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
  check(await page.locator('.resort-list .tier-draft').count() === 1, 'Draft profile tag on draft Tier A resort');
  await shot(page, 'desktop-03-cluster.png');

  // Resort with climate.
  await page.locator('.resort-list a', { hasText: 'Alpha Peak' }).click();
  await page.waitForURL(/\/map\/co\/fixture-front-range\/alpha-peak\//);
  await page.waitForFunction(() => document.querySelectorAll('.climate canvas').length >= 5, null, { timeout: 10000 });
  await mapIdle(page);
  await page.waitForTimeout(500);
  check(await heading(page) === 'Alpha Peak', 'resort heading');
  check((await page.title()).startsWith('Alpha Peak: Season, Snow and Size'), 'resort title');
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
  check(await page.locator('.src-tag', { hasText: 'Estimated' }).count() === 1, 'density chart labelled Estimated');

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
  check((await page.title()).startsWith('Alpha Peak'), 'stub title matches');
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
