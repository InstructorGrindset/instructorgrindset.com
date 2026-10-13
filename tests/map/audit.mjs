// Phase 8 audits for the Career Map. Rerun with:
//
//   (cd tests/map && npm install)      # axe-core and lighthouse, test only
//   node tests/map/audit.mjs [report_dir] [--no-lighthouse | --lighthouse-only]
//
// 1. Performance budget: /map/ and a resort page interactive under 3 s with Chrome DevTools
//    throttling (4G: 150 ms RTT, 1.6 Mbps down, 750 Kbps up, 4x CPU slowdown).
//    Interactive means the panel and the list view work (for a resort page: the full resort record is
//    rendered) and the first map frame is on screen. The first map frame is the 2D preview that
//    map/js/preview.js draws from the slim index. The WebGL map starts on the first click or tap (owner
//    decision 9); the audit clicks once the page settles and reports click to WebGL `load`.
//    Times come from the page's own performance marks (navigation start = 0).
// 2. The homepage makes zero requests to /map/ or /data/build/.
// 3. axe-core on the national map, a resort page, the list view and the phone layout.
// 4. Lighthouse (mobile defaults) on /map/ and two resort pages.
//
// Files are served by a small static server with gzip, like GitHub Pages; python's
// http.server sends everything uncompressed, which would misstate the budget.
import http from 'node:http';
import zlib from 'node:zlib';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..');
const OUT = path.resolve(process.argv.find((a, i) => i > 1 && !a.startsWith('--')) || path.join(os.tmpdir(), 'career-map-audit'));
const RUN_LH = !process.argv.includes('--no-lighthouse');
const ONLY_LH = process.argv.includes('--lighthouse-only');
fs.mkdirSync(OUT, { recursive: true });

function loadPlaywright() {
  for (const p of ['playwright', '/opt/node-tools/node_modules/playwright']) {
    try { return require(p); } catch { /* next */ }
  }
  throw new Error('playwright not found');
}
const { chromium } = loadPlaywright();
const CHROME = (() => {
  const base = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  for (const d of fs.readdirSync(base)) {
    const p = path.join(base, d, 'chrome-linux', 'chrome');
    if (d.startsWith('chromium-') && fs.existsSync(p)) return p;
  }
  return undefined;
})();
const ARGS = ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'];

const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.geojson': 'application/geo+json', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml',
  '.xml': 'application/xml', '.txt': 'text/plain', '.woff2': 'font/woff2' };
const COMPRESS = /^(text\/|application\/(json|geo\+json|xml)|image\/svg)/;

function serve(root, port) {
  const server = http.createServer((req, res) => {
    let p = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    let file = path.join(root, p);
    if (!file.startsWith(root)) { res.writeHead(403).end(); return; }
    if (fs.existsSync(file) && fs.statSync(file).isDirectory()) {
      if (!p.endsWith('/')) { res.writeHead(301, { Location: `${p}/` }).end(); return; }
      file = path.join(file, 'index.html');
    }
    if (!fs.existsSync(file)) { res.writeHead(404, { 'Content-Type': 'text/plain' }).end('Not found'); return; }
    const type = TYPES[path.extname(file)] || 'application/octet-stream';
    const body = fs.readFileSync(file);
    const headers = { 'Content-Type': type, 'Cache-Control': 'max-age=600' };
    if (COMPRESS.test(type) && /gzip/.test(req.headers['accept-encoding'] || '')) {
      res.writeHead(200, { ...headers, 'Content-Encoding': 'gzip' }).end(zlib.gzipSync(body, { level: 6 }));
    } else res.writeHead(200, headers).end(body);
  });
  return new Promise((resolve) => server.listen(port, '127.0.0.1', () => resolve(server)));
}

const results = { budget: [], homepage: null, axe: [], lighthouse: [] };
const failures = [];
const check = (ok, msg) => { console.log(`  ${ok ? 'ok  ' : 'FAIL'} ${msg}`); if (!ok) failures.push(msg); };

async function throttled(browser, url, label, { phone = false } = {}) {
  const ctx = await browser.newContext(phone
    ? { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true }
    : { viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  const cdp = await ctx.newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });
  await cdp.send('Network.emulateNetworkConditions', {
    offline: false, latency: 150, downloadThroughput: (1.6 * 1024 * 1024) / 8, uploadThroughput: (750 * 1024) / 8,
  });
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
  let bytes = 0;
  cdp.on('Network.loadingFinished', (e) => { bytes += e.encodedDataLength; });
  await page.goto(url, { waitUntil: 'commit' });
  await page.waitForFunction(() => window.__careerMap && window.__careerMap.ready, null, { timeout: 60000 });
  await page.waitForFunction(() => performance.getEntriesByName('careermap:preview').length > 0
    && !document.querySelector('#panel-content[aria-busy="true"]'), null, { timeout: 60000, polling: 100 });
  // Owner decision 9: the WebGL map starts on the first click or tap. Click once the page has settled.
  await page.waitForTimeout(500);
  await page.locator('#map-start').click();
  await page.waitForFunction(() => performance.getEntriesByName('careermap:map').length > 0, null, { timeout: 60000, polling: 100 });
  const m = await page.evaluate(() => {
    const t = (n) => { const e = performance.getEntriesByName(n)[0]; return e ? Math.round(e.startTime) : null; };
    return { panel: t('careermap:panel'), panelFull: t('careermap:panel-full'), preview: t('careermap:preview'),
      click: t('careermap:start-map'), firstRender: t('careermap:first-render'), load: t('careermap:map'), fcp: t('first-contentful-paint') };
  });
  const panelMs = m.panelFull ?? m.panel;
  const frameMs = m.preview;
  const interactive = Math.max(panelMs, frameMs);
  const r = { label, url, panel_ms: panelMs, first_map_frame_ms: frameMs, interactive_ms: interactive,
    webgl_click_to_load_ms: m.load - m.click, fcp_ms: m.fcp, transferred_kb: Math.round(bytes / 1024) };
  results.budget.push(r);
  check(interactive < 3000, `${label}: interactive in ${interactive} ms (panel ${panelMs} ms, first map frame ${frameMs} ms, ${r.transferred_kb} KB transferred)`);
  console.log(`       WebGL map after the click: first frame in ${m.firstRender - m.click} ms, load in ${m.load - m.click} ms`);
  await ctx.close();
}

async function homepage(browser, base) {
  const page = await browser.newPage();
  const reqs = [];
  page.on('request', (r) => reqs.push(new URL(r.url()).pathname));
  await page.goto(`${base}/`, { waitUntil: 'networkidle' });
  const bad = reqs.filter((p) => p.startsWith('/map/') || p.startsWith('/data/build/'));
  results.homepage = { requests: reqs.length, map_or_data_requests: bad };
  check(bad.length === 0, `homepage makes zero requests to /map/ or /data/build/ (${reqs.length} requests total)`);
  await page.close();
}

async function axe(browser, base) {
  const axeSrc = fs.readFileSync(require.resolve('axe-core/axe.min.js', { paths: [HERE] }), 'utf8');
  const cases = [
    { label: 'national map, before the map starts', url: '/map/', waiting: true },
    { label: 'national map', url: '/map/' },
    { label: 'resort page (Vail)', url: '/map/co/i70-corridor/vail/' },
    { label: 'list view', url: '/map/', list: true },
    { label: 'resort page, phone', url: '/map/co/i70-corridor/vail/', phone: true },
    { label: 'trail map dialog (Vail)', url: '/map/co/i70-corridor/vail/', trail: true },
    { label: 'trail map dialog, phone', url: '/map/co/i70-corridor/vail/', trail: true, phone: true },
  ];
  for (const c of cases) {
    const ctx = await browser.newContext(c.phone ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } : { viewport: { width: 1280, height: 800 } });
    const page = await ctx.newPage();
    await page.goto(base + c.url);
    await page.waitForFunction(() => window.__careerMap && window.__careerMap.ready, null, { timeout: 30000 });
    if (c.waiting) await page.waitForTimeout(800);
    else {
      await page.locator('#map-start').click();
      await page.waitForFunction(() => performance.getEntriesByName('careermap:map').length > 0, null, { timeout: 30000 });
      await page.waitForTimeout(1500);
    }
    if (c.list) { await page.locator('#list-toggle').click(); await page.waitForTimeout(300); }
    if (c.trail) {
      await page.locator('#panel-content button', { hasText: 'Open the trail map' }).click();
      await page.waitForFunction(() => { const t = window.__careerMap.trail; return t && t.map && t.map.loaded(); }, null, { timeout: 30000 });
      await page.locator('.trailmap-runs summary').click();
      await page.waitForTimeout(300);
    }
    await page.addScriptTag({ content: axeSrc });
    const res = await page.evaluate(async () => {
      // The WebGL canvas is exposed as one labelled region; its pixels have no DOM to audit.
      const r = await window.axe.run(document, { resultTypes: ['violations'], runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'] } });
      return r.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.slice(0, 3).map((n) => n.target.join(' ')) }));
    });
    results.axe.push({ label: c.label, violations: res });
    check(res.length === 0, `axe ${c.label}: ${res.length} violations${res.length ? `\n      ${res.map((v) => `${v.id} (${v.impact}): ${v.help} ${JSON.stringify(v.nodes)}`).join('\n      ')}` : ''}`);
    await ctx.close();
  }
}

// Async on purpose: the static server runs in this process, so a blocking spawn would starve it.
function run(bin, args, env, timeoutMs) {
  return new Promise((resolve) => {
    const p = spawn(bin, args, { env });
    let stderr = '';
    p.stderr.on('data', (d) => { stderr += d; });
    const timer = setTimeout(() => p.kill('SIGKILL'), timeoutMs);
    p.on('close', (status) => { clearTimeout(timer); resolve({ status, stderr }); });
  });
}

async function lighthouse(base) {
  const bin = path.join(HERE, 'node_modules', '.bin', 'lighthouse');
  if (!fs.existsSync(bin)) { console.log('  skip lighthouse not installed (cd tests/map && npm install)'); return; }
  const pages = ['/map/', '/map/co/i70-corridor/vail/', '/map/wy/tetons/jackson-hole/'];
  for (const p of pages) {
    const out = path.join(OUT, `lighthouse${p.replace(/\//g, '_')}.json`);
    const res = await run(bin, [base + p, '--quiet', '--output=json', `--output-path=${out}`,
      '--only-categories=performance,accessibility,best-practices,seo',
      `--chrome-flags=--headless=new --no-sandbox ${ARGS.join(' ')}`], { ...process.env, CHROME_PATH: CHROME }, 240000);
    if (res.status !== 0 || !fs.existsSync(out)) { failures.push(`lighthouse ${p} failed: ${res.stderr.slice(-400)}`); console.log(`  FAIL lighthouse ${p}`); continue; }
    const lhr = JSON.parse(fs.readFileSync(out, 'utf8'));
    const score = (k) => Math.round(lhr.categories[k].score * 100);
    const s = { page: p, performance: score('performance'), accessibility: score('accessibility'), best_practices: score('best-practices'), seo: score('seo'),
      lcp_ms: Math.round(lhr.audits['largest-contentful-paint'].numericValue), tbt_ms: Math.round(lhr.audits['total-blocking-time'].numericValue) };
    const low = Object.values(lhr.categories).flatMap((cat) => cat.auditRefs.filter((a) => a.weight > 0).map((a) => lhr.audits[a.id])
      .filter((a) => a.score !== null && a.score < 0.9).map((a) => `${cat.id}:${a.id}`));
    s.failing_audits = [...new Set(low)];
    results.lighthouse.push(s);
    const ok = [s.performance, s.accessibility, s.best_practices, s.seo].every((x) => x >= 90);
    check(ok, `lighthouse ${p}: performance ${s.performance}, accessibility ${s.accessibility}, best practices ${s.best_practices}, seo ${s.seo} (LCP ${s.lcp_ms} ms, TBT ${s.tbt_ms} ms)${s.failing_audits.length ? `\n      below 90: ${s.failing_audits.join(', ')}` : ''}`);
  }
}

const server = await serve(REPO, 8821);
const base = 'http://127.0.0.1:8821';
try {
  const browser = ONLY_LH ? null : await chromium.launch({ executablePath: CHROME, args: ARGS });
  if (browser) {
    // Imagery tiles: a synthetic local tile stands in for the USGS service, which this sandbox cannot reach.
    const tile = fs.readFileSync(path.join(HERE, 'fixtures', 'imagery-tile.png'));
    const newContext = browser.newContext.bind(browser);
    browser.newContext = async (opts) => {
      const c = await newContext(opts);
      await c.route(/basemap\.nationalmap\.gov\//, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: tile, headers: { 'Access-Control-Allow-Origin': '*' } }));
      return c;
    };
    browser.newPage = async (opts) => (await browser.newContext(opts)).newPage();
  }
  if (browser) {
  console.log('Performance budget (4G, 4x CPU, gzip)');
  await throttled(browser, `${base}/map/`, 'national map, desktop');
  await throttled(browser, `${base}/map/`, 'national map, phone', { phone: true });
  await throttled(browser, `${base}/map/co/i70-corridor/vail/`, 'resort page (Vail), desktop');
  await throttled(browser, `${base}/map/co/i70-corridor/vail/`, 'resort page (Vail), phone', { phone: true });
  console.log('\nHomepage isolation');
  await homepage(browser, base);
  console.log('\nAccessibility (axe-core)');
  await axe(browser, base);
  await browser.close();
  }
  if (RUN_LH) { console.log('\nLighthouse'); await lighthouse(base); }
} catch (err) {
  failures.push(`crashed: ${err.stack || err}`);
  console.log(err);
} finally {
  server.close();
}
fs.writeFileSync(path.join(OUT, 'audit-results.json'), JSON.stringify(results, null, 2));
console.log(`\nResults: ${path.join(OUT, 'audit-results.json')}`);
if (failures.length) { console.log(`\n${failures.length} failure(s)`); process.exit(1); }
console.log('\nAll audits passed.');
