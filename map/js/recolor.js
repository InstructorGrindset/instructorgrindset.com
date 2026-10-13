// Map color modes (Default, Lucrativeness, Professionalism, Season length, Snowfall) and their legend.
// Every mode except Default draws a value heat surface (heat.js) with the resorts on top.
import { RECOLOR_MODES } from './labels.js';
import { buildHeat, rampCss } from './heat.js';
import { loadStateOutlines } from './data.js';
import { config } from './config.js';
import { h, clear, fmtInt } from './ui.js';

const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });

const FORMAT = {
  lucrativeness: (v) => usd.format(v),
  professionalism: (v) => fmtInt(v),
  season: (v) => fmtInt(v),
  snowfall: (v) => fmtInt(v),
};
/** Value with its unit, for announcements and the resort tooltip. */
const SPOKEN = {
  lucrativeness: (v) => `${usd.format(v)} per season`,
  professionalism: (v) => `${fmtInt(v)} of 3`,
  season: (v) => `${fmtInt(v)} days`,
  snowfall: (v) => `${fmtInt(v)} in per season`,
};

/** Min, max and count over the finite values of a property; null when no resort has one. */
export function valueDomain(values) {
  const v = values.filter((x) => typeof x === 'number' && Number.isFinite(x));
  if (!v.length) return null;
  return { min: Math.min(...v), max: Math.max(...v), count: v.length };
}

export function createRecolor(root, index, { onChange, announce }) {
  const features = index.geo.resorts.features;
  const info = {};
  // A mode with a fixed domain (professionalism, 1 to 3) uses only values inside it.
  const inDomain = (m, v) => typeof v === 'number' && Number.isFinite(v) && (!m.domain || (v >= m.domain.min && v <= m.domain.max));
  for (const m of RECOLOR_MODES) {
    if (!m.prop) continue;
    const d = valueDomain(features.map((f) => f.properties[m.prop]).filter((v) => inDomain(m, v)));
    info[m.key] = d && m.domain ? { ...d, min: m.domain.min, max: m.domain.max } : d;
  }
  const cache = new Map();
  let seq = 0;

  async function heatFor(m) {
    if (!cache.has(m.key)) {
      cache.set(m.key, loadStateOutlines(index).catch(() => null).then((outlines) => {
        const pts = features.filter((f) => inDomain(m, f.properties[m.prop]))
          .map((f) => ({ lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1], value: f.properties[m.prop] }));
        return buildHeat(pts, info[m.key], outlines);
      }));
    }
    return cache.get(m.key);
  }

  const legend = h('div', { class: 'recolor-legend', id: 'recolor-legend', hidden: true });
  const buttons = RECOLOR_MODES.map((m) => {
    const available = !m.prop || Boolean(info[m.key]);
    const reason = available ? null : `No resort has ${m.noun} data yet.`;
    const descId = `recolor-why-${m.key}`;
    return h('button', {
      type: 'button', class: 'filter-btn', dataset: { mode: m.key },
      'aria-pressed': String(m.key === 'default'),
      'aria-disabled': available ? null : 'true',
      'aria-describedby': available ? null : descId,
      title: reason || (m.title ? `${m.title}, shown as a heat map` : 'Tier colors'),
      onclick: () => {
        if (!available) { announce(reason); return; }
        select(m.key);
      },
    }, m.label, reason ? h('span', { class: 'visually-hidden', id: descId }, reason) : null);
  });

  async function select(key) {
    const my = ++seq;
    for (const b of buttons) b.setAttribute('aria-pressed', String(b.dataset.mode === key));
    const m = RECOLOR_MODES.find((x) => x.key === key);
    if (!m.prop) {
      legend.hidden = true;
      root.removeAttribute('aria-busy');
      onChange(null);
      announce('Map colors show Tier A and Tier B.');
      return;
    }
    const d = info[key];
    root.setAttribute('aria-busy', 'true');
    const images = await heatFor(m);
    if (my !== seq) return;
    root.removeAttribute('aria-busy');
    onChange({ key, prop: m.prop, images, format: SPOKEN[key] });
    drawLegend(m, d);
    announce(m.ticks
      ? `Heat map of ${m.label.toLowerCase()}, on a scale of ${m.ticks.join(', ')}.`
      : d.min === d.max
      ? `Heat map of ${m.label.toLowerCase()}. Every resort with data shows ${SPOKEN[key](d.min)}.`
      : `Heat map of ${m.label.toLowerCase()}, from ${SPOKEN[key](d.min)} to ${SPOKEN[key](d.max)}.`);
  }

  function drawLegend(m, d) {
    clear(legend);
    const f = FORMAT[m.key];
    const km = fmtInt(config.heat.radiusKm);
    legend.append(
      h('p', { class: 'legend-title' }, m.title, m.unit ? h('span', { class: 'legend-unit' }, m.unit) : null),
      h('div', { class: 'heat-bar', style: `background:${rampCss()}`, 'aria-hidden': 'true' }),
      m.ticks
        ? h('p', { class: 'heat-scale heat-ticks' }, m.ticks.map((t) => h('span', null, f(t))))
        : d.min === d.max
          ? h('p', { class: 'heat-scale' }, h('span', null, `Every resort with data: ${f(d.min)}`))
          : h('p', { class: 'heat-scale' }, h('span', null, `Min ${f(d.min)}`), h('span', null, `Max ${f(d.max)}`)),
      h('p', { class: 'ramp-note' }, `Each color blends the values of resorts within ${km} km, weighted by distance. Areas farther than ${km} km from a resort with data stay empty.`),
      h('ul', { class: 'heat-keys' },
        h('li', null, h('span', { class: 'key-dot key-a', 'aria-hidden': 'true' }), 'Tier A'),
        h('li', null, h('span', { class: 'key-dot key-b', 'aria-hidden': 'true' }), 'Tier B'),
        h('li', null, h('span', { class: 'key-dot key-none', 'aria-hidden': 'true' }), 'No data')),
      h('p', { class: 'ramp-note' }, `${fmtInt(d.count)} ${d.count === 1 ? 'resort has' : 'resorts have'} data.`));
    legend.hidden = false;
  }

  root.append(
    h('div', { class: 'recolor-modes', role: 'group', 'aria-label': 'Color the map by' },
      h('span', { class: 'recolor-label', 'aria-hidden': 'true' }, 'Color by'), buttons),
    legend);
  root.hidden = false;
  return { info, select };
}
