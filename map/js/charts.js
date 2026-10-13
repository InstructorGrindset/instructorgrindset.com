// Climate charts. Chart.js loads lazily, only when a resort or comparison needs it.
import { config, prefersReducedMotion } from './config.js';
import { h, monthShort, monthLong, fmt1, fmtInt } from './ui.js';

/** Ski season months shown in charts, in season order. */
export const SEASON_MONTHS = [10, 11, 12, 1, 2, 3, 4, 5];
/** Full contract order (Oct to Sep). */
const CONTRACT_ORDER = [10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9];

// Categorical slots validated against the panel surface (#141a24, dark mode).
export const SERIES = ['#3987e5', '#d95926', '#199e70', '#c98500'];
// Ordinal ramp for density classes, dry (light) to moist (dark), one hue.
const DENSITY = { dry: '#86b6ef', average: '#3987e5', moist: '#1c5cab' };
const SURFACE = '#141a24';
const INK = '#e7e9ee';
const MUTED = '#9aa6b8';
const GRID = 'rgba(255,255,255,0.07)';

/** Thresholds from definitions.json (contract: snow.{powder,banner}_day_in, density.{dry_max,moist_min}_pct), or nulls. */
export function thresholds(defs) {
  const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
  const snow = (defs && defs.snow) || {};
  const dens = (defs && defs.density) || {};
  return {
    powder: num(snow.powder_day_in),
    banner: num(snow.banner_day_in),
    dryBelow: num(dens.dry_max_pct),
    moistAbove: num(dens.moist_min_pct),
  };
}

function dayCaption(t) {
  if (t.powder != null && t.banner != null) {
    return `Average days per month. A powder day brings ${fmt1(t.powder)} in or more of new snow in 24 hours, a banner day ${fmt1(t.banner)} in or more.`;
  }
  return 'Average days per month';
}

function densityLabels(t) {
  if (t.dryBelow == null || t.moistAbove == null) return { dry: 'Dry', average: 'Average', moist: 'Moist' };
  return {
    dry: `Dry, under ${fmt1(t.dryBelow)}%`,
    average: `Average, ${fmt1(t.dryBelow)} to ${fmt1(t.moistAbove)}%`,
    moist: `Moist, over ${fmt1(t.moistAbove)}%`,
  };
}

let chartLib = null;
const live = new Set();

export function loadChartJs() {
  if (!chartLib) {
    chartLib = new Promise((resolve, reject) => {
      if (window.Chart) { resolve(theme(window.Chart)); return; }
      const s = document.createElement('script');
      s.src = config.vendor.chartjs;
      s.async = true;
      s.onload = () => (window.Chart ? resolve(theme(window.Chart)) : reject(new Error('Chart.js missing')));
      s.onerror = () => { chartLib = null; reject(new Error('Chart.js failed to load')); };
      document.head.append(s);
    });
  }
  return chartLib;
}

function theme(Chart) {
  const d = Chart.defaults;
  d.color = MUTED;
  d.borderColor = GRID;
  d.font.family = 'Inter, system-ui, -apple-system, sans-serif';
  d.font.size = 11;
  d.maintainAspectRatio = false;
  d.responsive = true;
  d.animation = prefersReducedMotion() ? false : { duration: 350 };
  d.plugins.legend.labels.boxWidth = 10;
  d.plugins.legend.labels.boxHeight = 10;
  d.plugins.legend.labels.color = INK;
  d.plugins.tooltip.backgroundColor = '#0b0f15';
  d.plugins.tooltip.borderColor = '#2a3140';
  d.plugins.tooltip.borderWidth = 1;
  d.plugins.tooltip.titleColor = INK;
  d.plugins.tooltip.bodyColor = INK;
  d.plugins.tooltip.padding = 8;
  d.interaction = { mode: 'index', intersect: false };
  return Chart;
}

export function destroyCharts() {
  for (const c of live) c.destroy();
  live.clear();
}

/** Months of a climate file restricted to the ski season and sorted in season order. */
export function seasonMonths(climate) {
  const byMonth = new Map(climate.months.map((m) => [m.month, m]));
  return SEASON_MONTHS.map((m) => byMonth.get(m) || null);
}

export function allMonths(climate) {
  const byMonth = new Map(climate.months.map((m) => [m.month, m]));
  return CONTRACT_ORDER.map((m) => byMonth.get(m)).filter(Boolean);
}

/** Sum of monthly means over the season months; null when every month is null. */
export function seasonTotal(climate, key) {
  const vals = seasonMonths(climate).map((m) => (m ? m[key] : null)).filter((v) => v != null);
  return vals.length ? vals.reduce((a, b) => a + b, 0) : null;
}

export function densityLabel(climate) {
  const srcs = new Set(climate.months.filter((m) => m.density_mix).map((m) => m.density_source).filter(Boolean));
  if (!srcs.size) return null;
  if (srcs.size > 1) return 'SNOTEL and Estimated';
  return srcs.has('snotel') ? 'SNOTEL' : 'Estimated';
}

const labels = () => SEASON_MONTHS.map(monthShort);

function axes({ unit, stacked = false, max } = {}) {
  return {
    x: { stacked, grid: { display: false }, ticks: { color: MUTED } },
    y: {
      stacked, beginAtZero: true, max, grid: { color: GRID }, border: { display: false },
      ticks: { color: MUTED, maxTicksLimit: 5, callback: (v) => (unit ? `${v}${unit}` : v) },
    },
  };
}

function bar(label, data, color, extra = {}) {
  return {
    type: 'bar', label, data, backgroundColor: color, borderColor: SURFACE, borderWidth: 0,
    borderRadius: 4, borderSkipped: 'start', barPercentage: 0.86, categoryPercentage: 0.8, ...extra,
  };
}

function tooltipUnit(unit, digits = 1) {
  return {
    callbacks: {
      label: (ctx) => (ctx.raw == null ? `${ctx.dataset.label}: no data`
        : `${ctx.dataset.label}: ${digits ? fmt1(ctx.raw) : fmtInt(ctx.raw)}${unit}`),
    },
  };
}

function figure(title, caption, ariaLabel, { tall = false, extra = null } = {}) {
  const canvas = h('canvas', { role: 'img', 'aria-label': ariaLabel });
  const node = h('figure', { class: 'chart' },
    h('figcaption', null, h('span', { class: 'chart-title' }, title), extra, caption ? h('span', { class: 'chart-caption' }, caption) : null),
    h('div', { class: `chart-box${tall ? ' chart-box-tall' : ''}` }, canvas));
  return { node, canvas };
}

function describe(name, values, unit) {
  const parts = SEASON_MONTHS.map((m, i) => `${monthShort(m)} ${values[i] == null ? 'no data' : fmt1(values[i]) + unit}`);
  return `${name} by month. ${parts.join(', ')}.`;
}

function mount(Chart, canvas, cfg) {
  const c = new Chart(canvas, cfg);
  live.add(c);
  return c;
}

/** One chart per task, so drawing five charts never blocks the page for long. False once root has left the page. */
const nextChart = (root) => new Promise((resolve) => { setTimeout(() => resolve(root.isConnected), 0); });

/** Render resort climate charts into root. */
export async function renderResortCharts(root, climate, defs = null) {
  const Chart = await loadChartJs();
  const t = thresholds(defs);
  const dlabels = densityLabels(t);
  const months = seasonMonths(climate);
  const v = (k) => months.map((m) => (m ? m[k] : null));
  const snow = v('snowfall_in');
  const powder = v('powder_days');
  const banner = v('banner_days');
  const storm = v('avg_storm_depth_in');
  const hi = v('temp_high_f');
  const lo = v('temp_low_f');
  const mix = months.map((m) => (m && m.density_mix ? m.density_mix : null));
  const pct = (k) => mix.map((x) => (x ? Math.round(x[k] * 1000) / 10 : null));

  const f1 = figure('Snowfall by month', 'Average inches of new snow', describe('Snowfall', snow, ' in'));
  const f2 = figure('Powder days and banner days', dayCaption(t), `${describe('Powder days', powder, '')} ${describe('Banner days', banner, '')}`);
  const f3 = figure('Average storm depth', 'Inches of snow per storm', describe('Average storm depth', storm, ' in'));
  const dl = densityLabel(climate);
  const f4 = figure('Snow density mix', 'Share of new snow by water content',
    `Snow density mix by month, dry, average and moist. ${dl ? `Source ${dl}.` : ''}`,
    { tall: true, extra: dl ? h('span', { class: `src-tag ${dl === 'SNOTEL' ? 'src-snotel' : 'src-est'}` }, dl) : null });
  const f5 = figure('Temperatures', 'Average daily high and low, °F', `${describe('High', hi, ' F')} ${describe('Low', lo, ' F')}`);

  root.append(f1.node, f2.node, f3.node);
  const hasMix = mix.some(Boolean);
  if (hasMix) root.append(f4.node);
  root.append(f5.node);

  mount(Chart, f1.canvas, {
    data: { labels: labels(), datasets: [bar('Snowfall', snow, SERIES[0])] },
    options: { scales: axes({ unit: '"' }), plugins: { legend: { display: false }, tooltip: tooltipUnit(' in') } },
  });
  if (!(await nextChart(root))) return;
  mount(Chart, f2.canvas, {
    data: { labels: labels(), datasets: [bar('Powder days', powder, SERIES[0]), bar('Banner days', banner, SERIES[1])] },
    options: { scales: axes(), plugins: { legend: { position: 'top', align: 'start' }, tooltip: tooltipUnit(' days') } },
  });
  if (!(await nextChart(root))) return;
  mount(Chart, f3.canvas, {
    data: { labels: labels(), datasets: [bar('Storm depth', storm, SERIES[0])] },
    options: { scales: axes({ unit: '"' }), plugins: { legend: { display: false }, tooltip: tooltipUnit(' in') } },
  });
  if (!(await nextChart(root))) return;
  if (hasMix) {
    const seg = (label, key, color, top) => bar(label, pct(key), color,
      { borderRadius: top ? { topLeft: 4, topRight: 4 } : 0, borderSkipped: false, borderWidth: { top: 2 }, barPercentage: 0.8 });
    mount(Chart, f4.canvas, {
      data: {
        labels: labels(),
        datasets: [seg(dlabels.dry, 'dry', DENSITY.dry), seg(dlabels.average, 'average', DENSITY.average), seg(dlabels.moist, 'moist', DENSITY.moist, true)],
      },
      options: {
        scales: axes({ unit: '%', stacked: true, max: 100 }),
        plugins: { legend: { position: 'top', align: 'start' }, tooltip: tooltipUnit('%') },
      },
    });
  }
  if (!(await nextChart(root))) return;
  mount(Chart, f5.canvas, {
    data: {
      labels: labels(),
      datasets: [
        { type: 'line', label: 'High', data: hi, borderColor: SERIES[1], backgroundColor: SERIES[1], borderWidth: 2, pointRadius: 4, pointBorderColor: SURFACE, pointBorderWidth: 2, tension: 0.3, spanGaps: false },
        { type: 'line', label: 'Low', data: lo, borderColor: SERIES[0], backgroundColor: SERIES[0], borderWidth: 2, pointRadius: 4, pointBorderColor: SURFACE, pointBorderWidth: 2, tension: 0.3, spanGaps: false },
      ],
    },
    options: {
      scales: { ...axes({ unit: '°' }), y: { ...axes({ unit: '°' }).y, beginAtZero: false } },
      plugins: { legend: { position: 'top', align: 'start' }, tooltip: tooltipUnit('°F', 0) },
    },
  });

  root.append(climateTable(climate));
}

function climateTable(climate) {
  const empty = () => h('td', { class: 'muted' }, h('span', { 'aria-hidden': 'true' }, '\u00b7'), h('span', { class: 'visually-hidden' }, 'No data'));
  const months = seasonMonths(climate);
  const cell = (m, k, digits = 1) => {
    const val = m ? m[k] : null;
    return val == null ? empty() : h('td', null, digits ? fmt1(val) : fmtInt(val));
  };
  const mixCell = (m, k) => (m && m.density_mix ? h('td', null, `${Math.round(m.density_mix[k] * 100)}%`) : empty());
  const rows = [
    ['Snowfall (in)', (m) => cell(m, 'snowfall_in')],
    ['Powder days', (m) => cell(m, 'powder_days')],
    ['Banner days', (m) => cell(m, 'banner_days')],
    ['Storm depth (in)', (m) => cell(m, 'avg_storm_depth_in')],
    ['Dry snow', (m) => mixCell(m, 'dry')],
    ['Average snow', (m) => mixCell(m, 'average')],
    ['Moist snow', (m) => mixCell(m, 'moist')],
    ['High (°F)', (m) => cell(m, 'temp_high_f', 0)],
    ['Low (°F)', (m) => cell(m, 'temp_low_f', 0)],
  ];
  return h('details', { class: 'data-table' },
    h('summary', null, 'Show the numbers as a table'),
    h('div', { class: 'table-scroll' },
      h('table', null,
        h('caption', { class: 'visually-hidden' }, 'Monthly climate averages, October to May'),
        h('thead', null, h('tr', null, h('th', { scope: 'col' }, 'Month'),
          SEASON_MONTHS.map((m) => h('th', { scope: 'col', title: monthLong(m) }, monthShort(m))))),
        h('tbody', null, rows.map(([name, fn]) => h('tr', null, h('th', { scope: 'row' }, name), months.map(fn)))))));
}

/** entries: [{ resort, climate }] where climate may be null. */
export async function renderCompareCharts(root, entries, defs = null) {
  const withData = entries.filter((e) => e.climate);
  if (!withData.length) return;
  const Chart = await loadChartJs();
  const ds = (key) => withData.map((e) => {
    const color = SERIES[entries.indexOf(e)];
    return bar(e.resort.name, seasonMonths(e.climate).map((m) => (m ? m[key] : null)), color, { barPercentage: 0.9, categoryPercentage: 0.82 });
  });
  const names = withData.map((e) => e.resort.name).join(', ');
  const f1 = figure('Snowfall by month', 'Average inches of new snow', `Snowfall by month for ${names}.`);
  const t = thresholds(defs);
  const f2 = figure('Powder days by month', t.powder != null
    ? `Average days per month with ${fmt1(t.powder)} in or more of new snow in 24 hours` : 'Average days per month', `Powder days by month for ${names}.`);
  root.append(f1.node, f2.node);
  mount(Chart, f1.canvas, {
    data: { labels: labels(), datasets: ds('snowfall_in') },
    options: { scales: axes({ unit: '"' }), plugins: { legend: { position: 'top', align: 'start' }, tooltip: tooltipUnit(' in') } },
  });
  mount(Chart, f2.canvas, {
    data: { labels: labels(), datasets: ds('powder_days') },
    options: { scales: axes(), plugins: { legend: { position: 'top', align: 'start' }, tooltip: tooltipUnit(' days') } },
  });
}
