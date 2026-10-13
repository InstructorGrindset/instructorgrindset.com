// Map color modes (Default, Lucrativeness, Professionalism, Season length, Snowfall) and their legend.
// Every mode except Default draws a heat map with the resorts on top. Settings come from definitions.json `heat`.
// Snowfall loads the precomputed NOAA rasters (heat/snowfall.json) when they exist; every other case draws the
// resort only surface from heat.js. Interpolated colors never become numbers: the legend shows the scale, and
// tooltips show each resort's sourced value.
import { RECOLOR_MODES } from './labels.js';
import { buildHeat, legendScale, makeRamp, scaleT } from './heat.js';
import { loadDefinitions, loadHeatRaster, loadStateOutlines } from './data.js';
import { h, clear, fmtInt } from './ui.js';
import { isPhone } from './config.js';

const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const usdShort = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1 });

/** Legend tick labels. */
const TICK = {
  lucrativeness: (v) => usdShort.format(v),
  professionalism: (v) => fmtInt(v),
  season: (v) => fmtInt(v),
  snowfall: (v) => fmtInt(v),
};
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

export async function createRecolor(root, index, { onChange, announce }) {
  const defs = await loadDefinitions().catch(() => null);
  const heat = defs && defs.heat ? defs.heat : null;
  const ramp = heat ? makeRamp(heat.ramp.colors) : null;
  const features = index.geo.resorts.features;
  const info = {};
  const modeCfg = (m) => (heat && heat.modes[m.key]) || {};
  // A mode with a fixed domain (professionalism, 1 to 3) uses only values inside it.
  const inDomain = (m, v) => {
    if (typeof v !== 'number' || !Number.isFinite(v)) return false;
    const d = modeCfg(m).domain;
    return !d || (v >= d[0] && v <= d[1]);
  };
  for (const m of RECOLOR_MODES) {
    if (!m.prop) continue;
    const vals = features.map((f) => f.properties[m.prop]).filter((v) => inDomain(m, v));
    const d = valueDomain(vals);
    info[m.key] = d && heat ? { ...d, legend: legendScale(modeCfg(m), vals) } : d;
  }
  const cache = new Map();
  let seq = 0;

  /** { images, legend, raster, attribution } for a mode, cached. */
  function heatFor(m) {
    if (!cache.has(m.key)) {
      const resortSurface = () => loadStateOutlines(index).catch(() => null).then((outlines) => {
        const pts = features.filter((f) => inDomain(m, f.properties[m.prop]))
          .map((f) => ({ lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1], value: f.properties[m.prop] }));
        const lg = info[m.key].legend;
        return { images: buildHeat(pts, outlines, { ...lg, ramp, field: heat.field, regions: heat.regions }), legend: lg, raster: null };
      });
      const p = m.key === 'snowfall'
        ? loadHeatRaster('snowfall').then((r) => (r ? {
          images: r.rasters.map((x) => ({ url: x.url, coordinates: x.coordinates, width: x.width, height: x.height, region: x.region, ak: x.region === 'alaska' })),
          legend: { scale: r.scale, domain: r.domain, ticks: r.ticks },
          raster: r,
          attribution: r.attribution,
        } : resortSurface()))
        : resortSurface();
      cache.set(m.key, p.catch((err) => { cache.delete(m.key); throw err; }));
    }
    return cache.get(m.key);
  }

  const legend = h('div', { class: 'recolor-legend', id: 'recolor-legend', hidden: true });
  const buttons = RECOLOR_MODES.map((m) => {
    const available = !m.prop || (Boolean(info[m.key]) && Boolean(heat));
    const reason = available ? null : info[m.key] ? 'Heat map settings did not load.' : `No resort has ${m.noun} data yet.`;
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
    let surface;
    try {
      surface = await heatFor(m);
    } catch (err) {
      console.warn('[map] heat map', err.message);
      if (my === seq) { root.removeAttribute('aria-busy'); announce('The heat map could not be drawn.'); }
      return;
    }
    if (my !== seq) return;
    root.removeAttribute('aria-busy');
    onChange({ key, prop: m.prop, images: surface.images, format: SPOKEN[key], attribution: surface.attribution || null });
    drawLegend(m, d, surface);
    const lg = surface.legend;
    announce(modeCfg(m).domain
      ? `Heat map of ${m.label.toLowerCase()}, on a scale of ${lg.ticks.join(', ')}.`
      : d.min === d.max
        ? `Heat map of ${m.label.toLowerCase()}. Every resort with data shows ${SPOKEN[key](d.min)}.`
        : `Heat map of ${m.label.toLowerCase()}. Resorts range from ${SPOKEN[key](d.min)} to ${SPOKEN[key](d.max)}.`);
  }

  function drawLegend(m, d, surface) {
    clear(legend);
    const lg = surface.legend;
    const tick = TICK[m.key];
    const fixed = Boolean(modeCfg(m).domain);
    const km = fmtInt(heat.field.full_opacity_km);
    const ticks = lg.ticks.map((t, i) => {
      const x = scaleT(t, lg.scale, lg.domain) * 100;
      const edge = i === 0 && x < 8 ? 'start' : i === lg.ticks.length - 1 && x > 92 ? 'end' : null;
      return h('span', { class: `heat-tick${edge ? ` heat-tick-${edge}` : ''}`, style: `left:${x.toFixed(1)}%` }, tick(t));
    });
    const notes = surface.raster
      ? [`Background: NOAA station normals, 1991 to 2020. Each resort adds a pocket of its own published snowfall within ${fmtInt(surface.raster.pockets.radius_km)} km.`,
        'Colors between stations and resorts are smoothed estimates. Tooltips and resort pages show only sourced values.']
      : ['Colors between resorts are smoothed estimates. Tooltips and resort pages show only sourced values.',
        `Colors fade where the nearest resort with data is more than ${km} km away.`];
    legend.append(...[
      h('p', { class: 'legend-title' }, m.title, m.unit ? h('span', { class: 'legend-unit' }, m.unit) : null),
      h('div', { class: 'heat-bar', style: `background:${ramp.css}`, 'aria-hidden': 'true' }),
      h('p', { class: 'heat-ticks' }, h('span', { class: 'visually-hidden' }, 'Scale: '), ticks),
      fixed ? null : h('p', { class: 'heat-range' }, d.min === d.max
        ? `Every resort with data: ${FORMAT[m.key](d.min)}`
        : `Resort values: ${FORMAT[m.key](d.min)} to ${FORMAT[m.key](d.max)}`),
      h('ul', { class: 'heat-keys' },
        h('li', null, h('span', { class: 'key-dot key-a', 'aria-hidden': 'true' }), 'Tier A'),
        h('li', null, h('span', { class: 'key-dot key-b', 'aria-hidden': 'true' }), 'Tier B'),
        h('li', null, h('span', { class: 'key-dot key-none', 'aria-hidden': 'true' }), 'No data')),
      h('p', { class: 'ramp-note' }, `${fmtInt(d.count)} ${d.count === 1 ? 'resort has' : 'resorts have'} data.`),
      // Open on wide screens; a tap opens it on phones, where the legend sits over the map.
      h('details', { class: 'heat-about', open: isPhone() ? null : '' },
        h('summary', null, 'About these colors'),
        ...notes.map((t) => h('p', { class: 'ramp-note' }, t))),
    ].filter(Boolean));
    legend.hidden = false;
  }

  root.append(
    h('div', { class: 'recolor-modes', role: 'group', 'aria-label': 'Color the map by' },
      h('span', { class: 'recolor-label', 'aria-hidden': 'true' }, 'Color by'), buttons),
    legend);
  root.hidden = false;
  return { info, select };
}
