// Map recolor control (Default, Lucrativeness, Professionalism, Season length, Snowfall) and its legend.
import { RECOLOR_MODES } from './labels.js';
import { RAMP, NO_DATA, classBreaks } from './map.js';
import { h, clear, fmtInt } from './ui.js';

const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1 });

const FORMAT = {
  lucrativeness: (v) => usd.format(v),
  professionalism: (v) => fmtInt(v),
  season: (v) => `${fmtInt(v)} days`,
  snowfall: (v) => `${fmtInt(v)} in`,
};
const ROUND = { lucrativeness: 100, professionalism: 1, season: 1, snowfall: 1 };

export function createRecolor(root, index, { onChange, announce }) {
  const features = index.geo.resorts.features;
  const info = {};
  for (const m of RECOLOR_MODES) {
    if (!m.prop) continue;
    info[m.key] = classBreaks(features.map((f) => f.properties[m.prop]), ROUND[m.key]);
  }

  const legend = h('div', { class: 'recolor-legend', id: 'recolor-legend', hidden: true, 'aria-live': 'polite' });
  const buttons = RECOLOR_MODES.map((m) => {
    const available = !m.prop || Boolean(info[m.key]);
    const reason = available ? null : `No resort has ${m.noun} data yet.`;
    const descId = `recolor-why-${m.key}`;
    const btn = h('button', {
      type: 'button', class: 'filter-btn', dataset: { mode: m.key },
      'aria-pressed': String(m.key === 'default'),
      'aria-disabled': available ? null : 'true',
      'aria-describedby': available ? null : descId,
      title: reason || m.title || 'Tier colors',
      onclick: () => {
        if (!available) { announce(reason); return; }
        select(m.key);
      },
    }, m.label, reason ? h('span', { class: 'visually-hidden', id: descId }, reason) : null);
    return btn;
  });

  function select(key) {
    for (const b of buttons) b.setAttribute('aria-pressed', String(b.dataset.mode === key));
    const m = RECOLOR_MODES.find((x) => x.key === key);
    if (!m.prop) {
      legend.hidden = true;
      onChange(null);
      announce('Map colors show Tier A and Tier B.');
      return;
    }
    const br = info[key];
    onChange({ prop: m.prop, stops: br.stops });
    drawLegend(m, br);
    announce(`Map colored by ${m.label.toLowerCase()}, from ${FORMAT[key](br.min)} to ${FORMAT[key](br.max)}.`);
  }

  function drawLegend(m, br) {
    clear(legend);
    const f = FORMAT[m.key];
    const edges = [br.min, ...br.stops, br.max];
    const items = br.stops.length
      ? edges.slice(0, -1).map((lo, i) => ({ color: RAMP[i], label: `${f(lo)} to ${f(edges[i + 1])}` }))
      : [{ color: RAMP[2], label: f(br.min) }];
    legend.append(
      h('p', { class: 'legend-title' }, m.title),
      h('ul', { class: 'ramp', 'aria-label': `${m.label} legend` },
        items.map((it) => h('li', null, h('span', { class: 'chip', style: `background:${it.color}`, 'aria-hidden': 'true' }), it.label)),
        h('li', null, h('span', { class: 'chip', style: `background:${NO_DATA}`, 'aria-hidden': 'true' }), 'No data')),
      h('p', { class: 'ramp-note' }, `${fmtInt(br.count)} resorts with data. Amber outline marks Tier A.`));
    legend.hidden = false;
  }

  root.append(
    h('div', { class: 'recolor-modes', role: 'group', 'aria-label': 'Color the map by' },
      h('span', { class: 'recolor-label', 'aria-hidden': 'true' }, 'Color by'), buttons),
    legend);
  root.hidden = false;
  return { info, select };
}
