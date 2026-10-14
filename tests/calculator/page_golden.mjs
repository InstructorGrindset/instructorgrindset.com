// Golden vectors for the Season Income Calculator (/income-calculator/).
//
//   node tests/calculator/page_golden.mjs            check: the page reproduces every golden vector
//   node tests/calculator/page_golden.mjs --capture  capture: rewrite golden.json from the page
//
// CALC_PAGE_ROOT=<dir> serves <dir> instead of this repository (default). CALC_PAGE_COMMIT=<sha>
// records the commit that tree came from in golden.json's captured_from.
//
// Capture needs a page with the formulas in an inline classic script: it instruments the page's
// globals (Math.round, Number.prototype.toFixed, window.fmtPct, window.federalTax, window.$) to record
// the exact doubles the page computed, so it must never run against the shared module page. The
// current golden.json was captured from main's inline page (commit be2553b, blob 736d84a), exported
// with `git archive origin/main | tar -x -C <dir>` and served with CALC_PAGE_ROOT=<dir>.
//
// Two kinds of vectors:
// * "ui": driven through the real select and sliders. Check mode replays them on the current
//   page and compares every result element (text, classes, list markup) exactly.
// * "extended": inputs outside the slider ranges (zero wages, fractional pipeline style
//   inputs, federal bracket boundaries). Captured by feeding the original calc() through a
//   stubbed $(). They are checked by the JS and Python parity scripts, not on the page.
//
// Serves the page root with `python3 -m http.server` and blocks every non local request.
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import fs from 'node:fs';
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

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..');
const ROOT = path.resolve(process.env.CALC_PAGE_ROOT || REPO);
const PAGE = 'income-calculator/index.html';
const GOLDEN = path.join(HERE, 'golden.json');
const CAPTURE = process.argv.includes('--capture');
const PORT = 8831;

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

// git blob id of a file (same as `git hash-object`).
function gitBlob(file) {
  const body = fs.readFileSync(file);
  return createHash('sha1').update(`blob ${body.length}\0`).update(body).digest('hex');
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

// ------------------------------------------------------------------ vectors
// Slider ids, ranges and steps exactly as in the page markup.
const SLIDERS = {
  weeks: ['s-weeks', 10, 26, 1], days_per_week: ['s-dpw', 1, 7, 1], hours_per_day: ['s-hpd', 4, 10, 1],
  group_rate: ['s-grate', 12, 55, 1], private_rate: ['s-prate', 18, 90, 1], private_pct: ['s-ppct', 0, 100, 5],
  private_length_hours: ['s-plen', 1, 8, 1], tips_per_day: ['s-tips', 0, 1000, 5],
  rent_monthly: ['s-hous', 0, 4000, 50], life_monthly: ['s-life', 200, 4000, 50],
  transport: ['s-trans', 0, 1500, 25], gear: ['s-gear', 0, 2000, 50],
};
const STATES = ['Washington', 'Wyoming', 'Nevada', 'Alaska', 'New Hampshire', 'Colorado', 'Utah', 'Idaho',
  'Vermont', 'Montana', 'New York', 'Oregon', 'California'];
const DEFAULTS = { state: 'Colorado', weeks: 18, days_per_week: 5, hours_per_day: 7, group_rate: 22, private_rate: 38,
  private_pct: 30, private_length_hours: 2, tips_per_day: 25, rent_monthly: 150, life_monthly: 800, transport: 150,
  gear: 300 };

function vectors() {
  const out = [];
  const add = (kind, id, over) => out.push({ id, kind, inputs: { ...DEFAULTS, ...over } });
  add('ui', 'defaults', {});
  for (const s of STATES) add('ui', `state-${s.toLowerCase().replace(/ /g, '-')}`, { state: s });
  add('ui', 'all-minimum-zero-costs', { state: 'Washington', weeks: 10, days_per_week: 1, hours_per_day: 4,
    group_rate: 12, private_rate: 18, private_pct: 0, private_length_hours: 1, tips_per_day: 0, rent_monthly: 0,
    life_monthly: 200, transport: 0, gear: 0 });
  add('ui', 'all-maximum', { state: 'California', weeks: 26, days_per_week: 7, hours_per_day: 10, group_rate: 55,
    private_rate: 90, private_pct: 100, private_length_hours: 8, tips_per_day: 1000, rent_monthly: 4000,
    life_monthly: 4000, transport: 1500, gear: 2000 });
  add('ui', 'all-privates', { private_pct: 100, private_length_hours: 6, private_rate: 75 });
  add('ui', 'no-privates', { private_pct: 0 });
  add('ui', 'zero-tips-rent-travel-gear', { tips_per_day: 0, rent_monthly: 0, transport: 0, gear: 0 });
  add('ui', 'private-days-tie-2.5', { weeks: 10, days_per_week: 5, private_pct: 5 });
  add('ui', 'private-days-tie-17.5', { weeks: 10, days_per_week: 7, private_pct: 25 });
  add('ui', 'private-days-8.25', { weeks: 11, days_per_week: 5, private_pct: 15 });
  add('ui', 'losing-money-high-rent', { state: 'Oregon', rent_monthly: 2500, life_monthly: 1500, tips_per_day: 0 });
  add('ui', 'tight-season', { state: 'Vermont', rent_monthly: 1200, life_monthly: 1000, tips_per_day: 0 });
  add('ui', 'workable-season', { state: 'Utah', rent_monthly: 900, life_monthly: 900 });
  add('ui', 'strong-season', { state: 'Wyoming', rent_monthly: 0, private_pct: 60, private_rate: 70 });
  add('ui', 'short-season-part-time', { state: 'New York', weeks: 10, days_per_week: 2, hours_per_day: 5 });
  add('ui', 'long-season-full-time', { state: 'Montana', weeks: 26, days_per_week: 6, hours_per_day: 8,
    group_rate: 30 });
  add('ui', 'big-tips', { state: 'Idaho', tips_per_day: 1000 });
  add('ui', 'mid-bracket-4', { state: 'California', weeks: 26, days_per_week: 7, hours_per_day: 10, group_rate: 55,
    private_pct: 0 });
  // The rest of a private day is paid at the group rate: max(0, hours per day - private length).
  add('ui', 'private-remainder-half-days', { hours_per_day: 8, private_length_hours: 3, private_pct: 50 });
  add('ui', 'private-remainder-max', { hours_per_day: 10, private_length_hours: 1, private_pct: 100 });
  add('ui', 'private-length-equals-day', { hours_per_day: 6, private_length_hours: 6, private_pct: 40 });
  add('ui', 'private-length-exceeds-day', { hours_per_day: 4, private_length_hours: 8, private_pct: 50 });
  // Wages next to the 14600 standard deduction (federal tax applies to wages above it).
  add('ui', 'standard-deduction-just-below', { state: 'Nevada', weeks: 10, days_per_week: 3, hours_per_day: 5,
    group_rate: 43, private_rate: 62, private_pct: 95, private_length_hours: 8 });
  add('ui', 'standard-deduction-exact', { state: 'Nevada', weeks: 10, days_per_week: 2, hours_per_day: 9,
    group_rate: 50, private_rate: 90, private_pct: 100, private_length_hours: 7 });
  add('ui', 'standard-deduction-just-above', { state: 'Nevada', weeks: 10, days_per_week: 3, hours_per_day: 4,
    group_rate: 47, private_rate: 71, private_pct: 95, private_length_hours: 7 });
  // Transportation is a monthly cost: transport x months.
  add('ui', 'transport-monthly-max', { transport: 1500 });
  add('ui', 'transport-monthly-step-25', { weeks: 13, transport: 175 });
  add('ui', 'transport-monthly-only-cost', { state: 'Idaho', weeks: 23, rent_monthly: 0, life_monthly: 200,
    transport: 600, gear: 0 });
  // Deterministic sample across every slider (mulberry32, seed 9).
  let seed = 9;
  const rnd = () => {
    seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  for (let i = 0; i < 60; i++) {
    const inp = { state: STATES[Math.floor(rnd() * STATES.length)] };
    for (const [k, [, min, max, step]] of Object.entries(SLIDERS)) {
      inp[k] = min + step * Math.floor(rnd() * (Math.floor((max - min) / step) + 1));
    }
    add('ui', `sample-${String(i + 1).padStart(2, '0')}`, inp);
  }
  // Outside the slider ranges: original formulas only (parity scripts check these).
  add('extended', 'zero-wages-zero-tips', { group_rate: 0, private_rate: 0, tips_per_day: 0 });
  add('extended', 'zero-days-per-week', { days_per_week: 0 });
  add('extended', 'zero-hours-zero-private-length', { hours_per_day: 0, private_length_hours: 0, tips_per_day: 0 });
  add('extended', 'zero-life-costs', { life_monthly: 0 });
  add('extended', 'net-rounds-to-negative-zero', { state: 'Washington', group_rate: 0, private_rate: 0,
    tips_per_day: 0, rent_monthly: 0, life_monthly: 0, transport: 0.1, gear: 0 });
  for (const [g, name] of [[11600, '11600'], [47150, '47150'], [100525, '100525'], [191950, '191950'],
    [250000, '250000'], [11600.5, '11600.5']]) {
    add('extended', `federal-boundary-${name}`, { weeks: 1, days_per_week: 1, hours_per_day: 1, group_rate: g,
      private_pct: 0, tips_per_day: 0 });
  }
  // Bracket edges after the standard deduction: wages = 14600 + bracket edge.
  for (const g of [26200, 61750, 115125, 206550]) {
    add('extended', `federal-boundary-after-deduction-${g}`, { weeks: 1, days_per_week: 1, hours_per_day: 1,
      group_rate: g, private_pct: 0, tips_per_day: 0 });
  }
  for (const [g, name] of [[14599.99, 'just-below'], [14600, 'exact'], [14600.01, 'just-above']]) {
    add('extended', `standard-deduction-${name}-fractional`, { weeks: 1, days_per_week: 1, hours_per_day: 1,
      group_rate: g, private_pct: 0, tips_per_day: 0 });
  }
  add('extended', 'private-length-exceeds-day-fractional', { state: 'Montana', weeks: 15.5, days_per_week: 4.5,
    hours_per_day: 3.5, private_pct: 42.5, private_length_hours: 4.25 });
  add('extended', 'transport-monthly-fractional-months', { state: 'Utah', weeks: 125 / 7, transport: 87.5 });
  add('extended', 'pipeline-style-fractional', { state: 'Utah', weeks: 125 / 7, days_per_week: 1,
    hours_per_day: 32.5, group_rate: 23.75, private_rate: 0, private_pct: 0, private_length_hours: 0,
    tips_per_day: 0, rent_monthly: 812.5, life_monthly: 637.25, transport: 0, gear: 1349.99 });
  add('extended', 'pipeline-style-fractional-2', { state: 'Vermont', weeks: 141 / 7, days_per_week: 1,
    hours_per_day: 37.5, group_rate: 19.125, private_rate: 0, private_pct: 0, private_length_hours: 0,
    tips_per_day: 0, rent_monthly: 1075.33, life_monthly: 700.1, transport: 0, gear: 1105.5 });
  add('extended', 'fractional-everything', { state: 'Oregon', weeks: 16.5, days_per_week: 4.5, hours_per_day: 6.25,
    group_rate: 21.33, private_rate: 47.6, private_pct: 33.3, private_length_hours: 2.75, tips_per_day: 17.2,
    rent_monthly: 333.33, life_monthly: 777.77, transport: 123.45, gear: 67.89 });
  add('extended', 'top-bracket-high-private', { state: 'California', weeks: 26, days_per_week: 7, hours_per_day: 10,
    group_rate: 120, private_rate: 300, private_pct: 50, private_length_hours: 8, tips_per_day: 0 });
  return out;
}

// ------------------------------------------------------------- page driving
const MONEY_KEYS = ['net', 'weekly_net', 'gross', 'total_tax', 'after_tax', 'total_expenses', 'group_income',
  'private_income', 'taxable', 'federal_tax', 'state_tax', 'fica', 'after_tax_wages', 'tip_income', 'housing_cost',
  'life_cost', 'transport_cost'];

async function readDisplay(page) {
  return page.evaluate(() => {
    const g = (id) => document.getElementById(id);
    const d = {};
    for (const id of ['m-net', 'm-weekly-sub', 'm-gross', 'm-tax', 'm-taxrate', 'm-aftertax', 'm-exp',
      'verdict-label', 'verdict-body', 'state-note']) d[id] = g(id).textContent;
    d['m-net.class'] = g('m-net').className;
    d['verdict.class'] = g('verdict').className;
    for (const id of ['bar-chart', 'income-breakdown', 'expense-breakdown']) d[id] = g(id).innerHTML;
    for (const el of document.querySelectorAll('.field-val')) d[el.id] = el.textContent;
    return d;
  });
}

// Sets the select and every slider the way a person does (input events), then fires the
// select's change handler so the last calc() runs with every value in place.
async function driveUi(page, inputs, instrument) {
  await page.evaluate(({ inputs, sliders, instrument }) => {
    const sel = document.getElementById('state-select');
    const idx = [...sel.options].findIndex((o) => o.getAttribute('data-name') === inputs.state);
    if (idx < 0) throw new Error(`unknown state ${inputs.state}`);
    sel.selectedIndex = idx;
    for (const [k, [id]] of Object.entries(sliders)) {
      const el = document.getElementById(id);
      el.value = String(inputs[k]);
      if (el.value !== String(inputs[k])) throw new Error(`${id} clamped ${inputs[k]} to ${el.value}`);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    }
    if (instrument) window.__startLog();
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    if (instrument) window.__stopLog();
  }, { inputs, sliders: SLIDERS, instrument });
}

// Original page only: calc() with $('s-…') stubbed to return the vector's raw values.
async function driveExtended(page, inputs) {
  await page.evaluate(({ inputs, sliders }) => {
    const sel = document.getElementById('state-select');
    const idx = [...sel.options].findIndex((o) => o.getAttribute('data-name') === inputs.state);
    if (idx < 0) throw new Error(`unknown state ${inputs.state}`);
    sel.selectedIndex = idx;
    const byId = {};
    for (const [k, [id]] of Object.entries(sliders)) byId[id] = { value: String(inputs[k]) };
    const real = window.$;
    window.$ = (id) => (id in byId ? byId[id] : real(id));
    try { window.__startLog(); window.calc(); window.__stopLog(); } finally { window.$ = real; }
  }, { inputs, sliders: SLIDERS });
}

// Records the doubles the original page computes, in call order.
const INSTRUMENT = () => {
  const log = { round: [], fixed: [], pct: [], fed: [] };
  let on = false;
  const round = Math.round, toFixed = Number.prototype.toFixed, fmtPct = window.fmtPct, fed = window.federalTax;
  Math.round = (x) => { const out = round(x); if (on) log.round.push({ x, out }); return out; };
  Number.prototype.toFixed = function (d) { const out = toFixed.call(this, d); if (on) log.fixed.push({ x: Number(this), d, out }); return out; };
  window.fmtPct = (n) => { if (on) log.pct.push(n); return fmtPct(n); };
  window.federalTax = (g) => { const out = fed(g); if (on) log.fed.push({ g, out }); return out; };
  window.__startLog = () => { for (const k of Object.keys(log)) log[k] = []; on = true; };
  window.__stopLog = () => { on = false; };
  window.__log = () => JSON.parse(JSON.stringify(log));
};

function valuesFromLog(log, display, inputs) {
  const r = log.round.map((e) => e.x);
  const fail = (m) => { throw new Error(`capture mapping: ${m}`); };
  if (log.round.length !== 21 && log.round.length !== 22) fail(`expected 21 or 22 Math.round calls, got ${log.round.length}`);
  if (r[15] !== r[5] || r[20] !== r[6]) fail('repeated values differ');
  if (log.fed.length !== 1 || !(log.fed[0].g <= r[9])) fail('federalTax input is above taxable');
  if (log.fed[0].out !== r[10]) fail('federalTax output mismatch');
  const num = (s, re) => { const m = display['income-breakdown'].match(re); if (!m) fail(`no match ${re}`); return parseFloat(m[1]); };
  const v = {
    total_days: num(display, /Tips \((\S+) days/),
    private_days_raw: r[0], private_days: log.round[0].out,
    group_hours: num(display, /Group lessons \((\S+) hrs/), private_hours: num(display, /Private lessons \((\S+) hrs/),
    group_income: r[7], private_income: r[8], tip_income: r[14], taxable: r[9], gross: r[3],
    federal_taxable: log.fed[0].g, federal_tax: r[10], state_tax: r[11], fica: r[12], total_tax: r[4],
    effective_rate: log.pct[0], after_tax: r[5], after_tax_wages: r[13], months: log.fixed[14].x,
    housing_cost: r[16], life_cost: r[17], transport_cost: r[18], total_expenses: r[6], net: r[1], weekly_net: r[2],
  };
  if (log.fixed[14].d !== 1 || log.fixed[13].d !== 2) fail('toFixed order');
  if (r[19] !== inputs.gear) fail('gear position');
  const verdict = display['verdict.class'].replace('verdict verdict-', '');
  const dollars = {};
  for (const k of MONEY_KEYS) dollars[k] = log.round[{ net: 1, weekly_net: 2, gross: 3, total_tax: 4, after_tax: 5,
    total_expenses: 6, group_income: 7, private_income: 8, taxable: 9, federal_tax: 10, state_tax: 11, fica: 12,
    after_tax_wages: 13, tip_income: 14, housing_cost: 16, life_cost: 17, transport_cost: 18 }[k]].out;
  return { values: v, dollars, effective_rate_pct_1dp: log.fixed[0].out, months_1dp: log.fixed[14].out, verdict };
}

// --------------------------------------------------------------------- main
const executablePath = findChromium();
const server = await serve(ROOT, PORT);
const browser = await chromium.launch({ executablePath });
const failures = [];
try {
  const context = await browser.newContext({ locale: 'en-US' });
  await context.route('**/*', (route) => {
    const u = new URL(route.request().url());
    return u.hostname === '127.0.0.1' ? route.continue() : route.abort();
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(`console: ${m.text()}`); });
  await page.goto(`http://127.0.0.1:${PORT}/income-calculator/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.getElementById('m-net').textContent !== '—');
  const initial = await readDisplay(page);

  if (CAPTURE) {
    await page.evaluate(INSTRUMENT);
    const out = [];
    for (const vec of vectors()) {
      if (vec.kind === 'ui') await driveUi(page, vec.inputs, true);
      else await driveExtended(page, vec.inputs);
      const log = await page.evaluate(() => window.__log());
      const display = await readDisplay(page);
      const rec = { ...vec, ...valuesFromLog(log, display, vec.inputs), display };
      if (vec.kind === 'extended') {
        // field-val labels are not updated by the stubbed run; keep only result elements.
        for (const k of Object.keys(rec.display)) if (k.startsWith('v-')) delete rec.display[k];
      }
      out.push(rec);
    }
    const golden = {
      about: 'Season Income Calculator golden vectors, captured from the inline script calculator page on main '
        + '(private day remainder at the group rate, 14600 standard deduction, monthly transportation). '
        + 'values are the exact doubles the page computed (JSON shortest round trip). dollars are whole dollars by '
        + 'Math.round (half toward positive infinity). *_1dp strings are Number.prototype.toFixed (exact binary value, '
        + 'half away from zero). display is the rendered result markup.',
      captured_from: { page: PAGE, git_commit: process.env.CALC_PAGE_COMMIT || null, git_blob: gitBlob(path.join(ROOT, PAGE)),
        browser: `chromium ${browser.version()}`, locale: 'en-US', captured_on: new Date().toISOString().slice(0, 10) },
      initial_display: initial,
      vectors: out,
    };
    fs.writeFileSync(GOLDEN, `${JSON.stringify(golden, null, 1)}\n`);
    console.log(`captured ${out.length} vectors (${out.filter((v) => v.kind === 'ui').length} ui) from ${path.join(ROOT, PAGE)} to ${path.relative(REPO, GOLDEN)}`);
  } else {
    const golden = JSON.parse(fs.readFileSync(GOLDEN, 'utf8'));
    const cmp = (label, got, want) => {
      for (const k of Object.keys(want)) {
        if (got[k] !== want[k]) failures.push(`${label} ${k}\n    want ${JSON.stringify(want[k])}\n    got  ${JSON.stringify(got[k])}`);
      }
    };
    cmp('initial load', initial, golden.initial_display);
    let n = 0;
    for (const vec of golden.vectors.filter((v) => v.kind === 'ui')) {
      await driveUi(page, vec.inputs, false);
      cmp(vec.id, await readDisplay(page), vec.display);
      n++;
    }
    failures.push(...errors);
    console.log(`checked ${n} ui vectors and the initial render of ${path.join(ROOT, PAGE)} against ${path.relative(REPO, GOLDEN)}`);
  }
  await context.close();
} finally {
  await browser.close();
  server.kill();
}
if (failures.length) {
  console.error(`FAIL ${failures.length}\n  ${failures.slice(0, 20).join('\n  ')}`);
  process.exit(1);
}
console.log('OK');
