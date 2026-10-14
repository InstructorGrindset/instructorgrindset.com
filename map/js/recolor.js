// Map color modes (Default, Lucrativeness, Season length, Snowfall) and their legend.
// Every mode except Default draws a heat map with the resorts on top. Settings come from definitions.json `heat`.
// Snowfall, Season length and Lucrativeness load their precomputed rasters (heat/<mode>.json, pipeline/heat.py and
// pipeline/heat_lucrativeness.py) when they exist, and Snowfall its isolines; every other case draws the resort only
// surface from heat.js. Lucrativeness turns on by itself once any resort has a computed value (pay). Interpolated colors
// never become numbers: the legend shows the scale, and tooltips show each resort's sourced value.
import { RECOLOR_MODES } from './labels.js';
import { buildHeat, legendScale, legendPos, legendCss, makeRamp } from './heat.js';
import { loadDefinitions, loadHeatRaster, loadHeatIsolines, loadStateOutlines } from './data.js';
import { h, clear, fmtInt } from './ui.js';
import { isPhone } from './config.js';

const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const usdShort = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1 });

/** Legend tick labels. */
const TICK = {
  lucrativeness: (v) => usdShort.format(v),
  season: (v) => fmtInt(v),
  snowfall: (v) => fmtInt(v),
};
const FORMAT = {
  lucrativeness: (v) => usd.format(v),
  season: (v) => fmtInt(v),
  snowfall: (v) => fmtInt(v),
};
/** Value with its unit, for announcements and the resort tooltip. */
const SPOKEN = {
  lucrativeness: (v) => `${usd.format(v)} per season`,
  season: (v) => `${fmtInt(v)} days`,
  snowfall: (v) => `${fmtInt(v)} in per season`,
};

/** Precomputed rasters per mode (heat/<mode>.json); without a manifest a mode draws in the browser. */
const RASTER_MODES = new Set(['snowfall', 'season', 'lucrativeness']);
/** The heat.season keys that set the season fade (the browser keeps its own resolution). */
const SEASON_FADE = ['full_opacity_km', 'fade_end_km', 'floor_alpha', 'fade_to', 'no_data_reach_km'];

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
  // A mode with a fixed domain uses only values inside it.
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

  /** Field settings of a mode: heat.field, with the season fade for Season length. */
  const fieldFor = (m) => {
    if (m.key !== 'season' || !heat.season) return heat.field;
    const f = { ...heat.field };
    for (const k of SEASON_FADE) if (heat.season[k] != null) f[k] = heat.season[k];
    return f;
  };

  /** { images, legend, raster, attribution, isolines (a promise or null) } for a mode, cached. */
  function heatFor(m) {
    if (!cache.has(m.key)) {
      const resortSurface = () => loadStateOutlines(index).catch(() => null).then((outlines) => {
        const feats = features.filter((f) => inDomain(m, f.properties[m.prop]));
        const pts = feats.map((f) => ({ lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1], value: f.properties[m.prop] }));
        const lg = info[m.key].legend;
        const field = fieldFor(m);
        // States with a resort value; the others keep color only near their border (season length).
        const dataStates = field.no_data_reach_km != null ? new Set(feats.map((f) => String(f.properties.state || '').toUpperCase())) : null;
        return {
          images: buildHeat(pts, outlines, { ...lg, ramp, field, regions: heat.regions, neutral: heat.neutral, dataStates }),
          legend: lg, raster: null, isolines: null,
        };
      });
      const fromRaster = (r) => ({
        images: r.rasters.map((x) => ({ url: x.url, coordinates: x.coordinates, width: x.width, height: x.height, region: x.region })),
        legend: r.scale === 'classed'
          ? { scale: 'classed', domain: r.domain, ticks: (r.legend && r.legend.ticks) || r.ticks, breaks: r.breaks, colors: r.colors,
            compressBelow: r.legend ? r.legend.compress_below ?? null : null, fixed: Boolean(modeCfg(m).domain) }
          : { scale: r.scale, domain: r.domain, ticks: r.ticks },
        raster: r,
        attribution: r.attribution || null,
        // Snowfall isolines load with the mode, after the manifest; the rasters draw without waiting for them.
        isolines: r.isolines ? loadHeatIsolines(r) : null,
      });
      const p = RASTER_MODES.has(m.key)
        ? loadHeatRaster(m.key).then((r) => (r ? fromRaster(r) : resortSurface()))
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
    onChange({ key, prop: m.prop, images: surface.images, format: SPOKEN[key], attribution: surface.attribution || null,
      isolines: surface.isolines || null });
    drawLegend(m, d, surface);
    const lg = surface.legend;
    announce(modeCfg(m).domain
      ? `Heat map of ${m.label.toLowerCase()}, on a scale of ${lg.ticks.join(', ')}.`
      : d.min === d.max
        ? `Heat map of ${m.label.toLowerCase()}. Every resort with data shows ${SPOKEN[key](d.min)}.`
        : `Heat map of ${m.label.toLowerCase()}. Resorts range from ${SPOKEN[key](d.min)} to ${SPOKEN[key](d.max)}.${surface.raster && surface.raster.background ? (surface.raster.mountain_stations ? ' The background comes from NOAA station normals and NRCS SNOTEL mountain stations.' : ' The background comes from NOAA station normals.') : ''}`);
  }

  function drawLegend(m, d, surface) {
    clear(legend);
    const lg = surface.legend;
    const tick = TICK[m.key];
    const fixed = Boolean(modeCfg(m).domain);
    const field = fieldFor(m);
    const km = fmtInt(field.full_opacity_km);
    const ticks = lg.ticks.map((t, i) => {
      const x = legendPos(t, lg) * 100;
      const edge = i === 0 && x < 8 ? 'start' : i === lg.ticks.length - 1 && x > 92 ? 'end' : null;
      return h('span', { class: `heat-tick${edge ? ` heat-tick-${edge}` : ''}`, style: `left:${x.toFixed(1)}%` }, tick(t));
    });
    const noaa = surface.raster && surface.raster.background;
    // Snowfall: NOAA normals, plus NRCS SNOTEL mountain stations and the terrain when the manifest has them.
    const snotel = noaa && surface.raster.mountain_stations;
    const shaped = snotel && surface.raster.terrain ? ', shaped by the terrain' : '';
    const notes = noaa
      ? [snotel
        ? `Background: NOAA station normals, 1991 to 2020, and NRCS SNOTEL mountain stations${shaped}, with a ${fmtInt(surface.raster.pockets.radius_km)} km pocket of each resort's published snowfall.`
        : `Background: NOAA station normals, 1991 to 2020, with a ${fmtInt(surface.raster.pockets.radius_km)} km pocket of each resort's published snowfall.`,
        'Colors between stations and resorts are smoothed estimates. Tooltips and resort pages show only sourced values.']
      : ['Colors between resorts are smoothed estimates. Tooltips and resort pages show only sourced values.',
        field.fade_to === 'neutral'
          ? `Colors turn grey where the nearest resort with data is more than ${km} km away.`
          : `Colors fade where the nearest resort with data is more than ${km} km away.`];
    if (m.key === 'season' && field.no_data_reach_km != null) notes.push('States with no resort data stay clear.');
    // Classed snowfall: the grey class and the isolines get a line of their own.
    const lowest = lg.scale === 'classed' && m.key === 'snowfall' ? lg.colors[0] : null;
    const iso = surface.raster && surface.raster.isolines;
    legend.append(...[
      h('p', { class: 'legend-title' }, m.title, m.unit ? h('span', { class: 'legend-unit' }, m.unit) : null),
      h('div', { class: `heat-bar${lg.scale === 'classed' ? ' heat-bar-classed' : ''}`, style: `background:${legendCss(lg, ramp)}`, 'aria-hidden': 'true' }),
      h('p', { class: 'heat-ticks' }, h('span', { class: 'visually-hidden' }, 'Scale: '), ticks),
      lowest ? h('p', { class: 'heat-low' }, h('span', { class: 'key-swatch', style: `background:${lowest}`, 'aria-hidden': 'true' }),
        `Grey: under ${fmtInt(lg.breaks[0])} in, little or no snow`) : null,
      iso && iso.levels.length ? h('p', { class: 'heat-low' }, h('span', { class: 'key-line', style: `--iso-under:${lg.colors ? lg.colors[Math.floor(lg.colors.length / 2)] : '#79d26a'}`, 'aria-hidden': 'true' }),
        `Lines every ${fmtInt(iso.levels[1] - iso.levels[0] || iso.major_step)} in from ${fmtInt(iso.levels[0])}, bolder every ${fmtInt(iso.major_step)}`) : null,
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
