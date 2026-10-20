// EVENTS mode (ROADMAP item 22; docs/map/ARCHITECTURE.md "Events"): every snowsports event, competition and
// instructor event of the season on its own map filter. Loaded only when the viewer turns EVENTS on (the Layers panel
// switch, the header pill, an /map/events/ link or an "Events here" link), together with data/build/events.json.
// It owns the event filters in the Layers panel, the legend items, the panel for the events view and a venue, the
// calendar list view and, once the WebGL map runs, the venue layers (eventsmap.js). Every value renders with its
// confidence badge and source link through the panel's own helpers (panel.js kit), as resort facts do.
// Keep the date wording in step with pipeline/routes.py (event_when, date_span).
import { loadOptional, schemas } from './data.js';
import { hrefFor } from './router.js';
import { isoDay, money } from './panel.js';
import { dateSpan } from './eventshere.js';
import { h, clear, safeUrl, formatIsoDate, plural, fmtInt, monthLong, monthShort, humanize, joinAnd } from './ui.js';

const NEW_TAB = { target: '_blank', rel: 'noopener noreferrer' };
const NEXT_MAX = 8;
const OTHER = 'other';
const SEASON_LONG = 'season';

// ── Rules (pure; the browser test imports them) ──

/** The 2026/27 edition's start and end (end falls back to the start), or nulls. */
const editionStart = (e) => (e.edition_2026_27 ? e.edition_2026_27.start : null);
const editionEnd = (e) => (e.edition_2026_27 ? e.edition_2026_27.end || e.edition_2026_27.start : null);

/** Sign up open: signup.opens is today or earlier, and the deadline (else the 2026/27 start) is today or later. */
export function signupOpen(e, today) {
  const s = e.signup;
  if (!s || !s.opens || s.opens.value > today) return false;
  if (s.deadline) return s.deadline.value >= today;
  const start = editionStart(e);
  return Boolean(start && start >= today);
}

/** Upcoming: a 2026/27 edition that has not ended by `today` (the header pill counts these). */
export function upcoming(e, today) {
  const end = editionEnd(e);
  return Boolean(end && end >= today);
}

/** The month chips an event falls under: its months (a chip each, the rest "other"), plus "season" for a program. */
export function monthKeys(e, chips) {
  const out = new Set();
  for (const m of e.months || []) out.add(chips.includes(m) ? String(m) : OTHER);
  if (e.program) out.add(SEASON_LONG);
  return out;
}

/**
 * Every active part of the filter must match: f.groups (group ids), f.levels (level ids; all of them means no level
 * filter, and an event with no published levels then still shows), f.months (chip keys; empty means every month)
 * and f.signupOpen.
 */
export function matchesEvent(e, f, today, chips, levelCount) {
  if (f.groups && !f.groups.has(e.group)) return false;
  if (f.levels && f.levels.size < levelCount && !e.levels.some((l) => f.levels.has(l))) return false;
  if (f.months && f.months.size && ![...monthKeys(e, chips)].some((k) => f.months.has(k))) return false;
  if (f.signupOpen && !signupOpen(e, today)) return false;
  return true;
}

/** The date line (routes.py event_when). */
export function whenText(e, season) {
  if (e.program) return e.usual_timing ? `Runs ${e.usual_timing.value}` : 'Runs all season';
  const ed = e.edition_2026_27;
  if (ed) return ed.end ? dateSpan(ed.start, ed.end) : `Starts ${dateSpan(ed.start)}`;
  const le = e.last_edition;
  const unpublished = `${season} date not yet published.`;
  if (le && le.start) return `Last held ${dateSpan(le.start, le.end)}. ${unpublished}`;
  if (le && le.season) return `Last held in the ${le.season} season. ${unpublished}`;
  return unpublished;
}

/** The cited block behind the date line: the edition, the last edition, usual_timing or season_long. */
function whenCite(e) {
  if (e.program) return e.usual_timing || e.season_long;
  return e.edition_2026_27 || e.last_edition || null;
}

/** The first source an event cites ("Where this comes from" when it gives no website and no sign up page). */
function firstSource(e) {
  const s = e.signup || {};
  for (const c of [e.edition_2026_27, e.last_edition, e.usual_timing, e.season_long, e.organizer, e.format, s.url, s.opens,
    s.deadline, s.fee_usd, s.platform, s.requirements, e.sanctioning, e.results_url, e.calendar_url]) {
    if (c && c.source_id) return c.source_id;
  }
  return null;
}

function host(url) { try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url; } }

let regionNames = null;
function countryName(code) {
  try {
    regionNames ||= new Intl.DisplayNames(['en'], { type: 'region' });
    return regionNames.of(code) || code;
  } catch { return code; }
}

// ── Data ──

/** events.json validated (Zod, per item), indexed. Resolves to the model, or a model with no events and `error`. */
async function loadModel() {
  const [raw, sch] = await Promise.all([loadOptional('events.json'), schemas()]);
  const r = raw ? sch.EventsFile.safeParse(raw) : null;
  if (!r || !r.success) {
    if (r) console.warn('[map] events.json failed validation', r.error.issues.slice(0, 5));
    return { error: raw ? 'invalid' : 'missing', file: null, events: [], venues: new Map(), byVenue: new Map(), calendars: [] };
  }
  const file = r.data;
  const venues = new Map(file.venues.map((v) => [v.id, v]));
  const groups = new Map(file.groups.map((g, i) => [g.id, { ...g, order: i }]));
  const events = file.events.filter((e) => venues.has(e.venue_id) && groups.has(e.group));
  const byVenue = new Map();
  for (const e of events) {
    if (!byVenue.has(e.venue_id)) byVenue.set(e.venue_id, []);
    byVenue.get(e.venue_id).push(e);
  }
  const disc = new Map(file.groups.flatMap((g) => g.disciplines.map((d) => [d.id, d.label])));
  return {
    file, events, venues, byVenue, groups, disc,
    eventById: new Map(events.map((e) => [e.id, e])),
    levels: file.levels, levelLabel: new Map(file.levels.map((x) => [x.id, x.label])),
    method: new Map(file.signup_methods.map((x) => [x.id, x.label])),
    kind: new Map(file.venue_kinds.map((x) => [x.id, x.label])),
    chips: file.month_chips,
    season: file.season,
    calendars: file.calendars.filter((c) => groups.has(c.group)),
  };
}

/**
 * The events layer. opts: { index, layersBox, legend, stage, announce(text), go(view, opts), current() -> view,
 * onFilter() (the 2D picture redraws), onList(on) (List view), isPhone() }.
 */
export function createEvents(opts) {
  const { index } = opts;
  let model = null;
  let mapLayers = null;
  let mapCtl = null;
  let active = false;
  let panelNext = null; // the "Coming up" list in the events panel, refreshed when the filters change
  const f = { groups: null, levels: null, months: new Set(), signupOpen: false };
  const today = () => isoDay(new Date());

  const ready = loadModel().then((m) => {
    model = m;
    f.groups = new Set(m.groups ? m.groups.keys() : []);
    f.levels = new Set((m.levels || []).map((x) => x.id));
    // camera.js frames a selected venue through this (mapped venues only).
    index.events = {
      venuePoint(id) {
        const v = m.venues.get(id);
        return v && v.mapped ? { lat: v.location.lat, lon: v.location.lon, precision: v.location.precision } : null;
      },
    };
    buildFilters();
    buildLegend();
    buildList();
    return api;
  });

  const levelCount = () => (model.levels || []).length;
  const keep = (e, except = null) => matchesEvent(e, {
    groups: except === 'groups' ? null : f.groups,
    levels: except === 'levels' ? null : f.levels,
    months: except === 'months' ? null : f.months,
    signupOpen: except === 'signupOpen' ? false : f.signupOpen,
  }, today(), model.chips, levelCount());
  const shown = () => model.events.filter((e) => keep(e));
  const isFiltered = () => f.signupOpen || f.months.size > 0 || f.groups.size < model.groups.size || f.levels.size < levelCount();
  const onlyOpen = () => f.levels.size === 1 && f.levels.has('open_amateur');

  // ── Layers panel filters ──
  let filterRoot = null;
  const inputs = []; // { input, dim, key, count }
  function option(dim, key, label, { swatch = null, chip = false, title = null } = {}) {
    const id = `evf-${dim}-${key}`;
    const input = h('input', { type: 'checkbox', id, dataset: { dim, key }, title });
    const count = h('span', { class: 'ev-opt-count' });
    inputs.push({ input, dim, key, count });
    return h('label', { class: chip ? 'ev-chip' : 'lp-opt lp-checkbox', for: id, title },
      input, swatch ? h('span', { class: 'ev-swatch', style: `--g:${swatch}`, 'aria-hidden': 'true' }) : null,
      h('span', { class: 'lp-text' }, label), count);
  }
  function group(legend, cls, ...children) {
    return h('fieldset', { class: `lp-group ${cls}` }, h('legend', { class: 'lp-legend' }, legend), ...children);
  }
  let resetBtn = null;
  function buildFilters() {
    if (!opts.layersBox || !model.file) return;
    // Groups with no event in the data have nothing to filter; they join once research adds events.
    const present = new Set(model.events.map((e) => e.group));
    const g = [...model.groups.values()].filter((x) => present.has(x.id));
    const chipLabel = (m) => monthShort(m);
    filterRoot = h('div', { id: 'events-filter', class: 'events-filter', role: 'group', 'aria-label': 'Filter events', hidden: !active },
      group('Show only', 'ev-quick',
        option('quick', 'signup', 'Sign up open', { title: 'Sign up has opened and the deadline or start date is still ahead' }),
        option('quick', 'anyone', 'Open to anyone', { title: 'Events anyone can enter, community competitions included' })),
      group('Discipline', 'ev-groups', h('div', { class: 'ev-grid' }, g.map((x) => option('groups', x.id, x.label, { swatch: x.color })))),
      group('Month', 'ev-months', h('div', { class: 'ev-chips' },
        model.chips.map((m) => option('months', String(m), chipLabel(m), { chip: true, title: monthLong(m) })),
        option('months', OTHER, 'Other months', { chip: true }),
        option('months', SEASON_LONG, 'Season long', { chip: true, title: 'Season long programs: race leagues, NASTAR, weekly rail jams' }))),
      group('Level', 'ev-levels', model.levels.map((x) => option('levels', x.id, x.label))),
      resetBtn = h('button', { type: 'button', class: 'lp-reset', hidden: true, onclick: () => reset() }, 'Show every event'));
    filterRoot.addEventListener('change', (ev) => {
      const input = ev.target.closest('input[data-dim]');
      if (!input) return;
      const { dim, key } = input.dataset;
      const on = input.checked;
      if (dim === 'quick' && key === 'signup') f.signupOpen = on;
      else if (dim === 'quick' && key === 'anyone') f.levels = on ? new Set(['open_amateur']) : new Set(model.levels.map((x) => x.id));
      else if (dim === 'groups' || dim === 'levels') { if (on) f[dim].add(key); else f[dim].delete(key); }
      else if (dim === 'months') { if (on) f.months.add(key); else f.months.delete(key); }
      changed();
    });
    opts.layersBox.append(filterRoot);
    syncFilters();
  }
  function syncFilters() {
    if (!filterRoot) return;
    const counts = {
      groups: (k) => model.events.filter((e) => e.group === k && keep(e, 'groups')).length,
      levels: (k) => model.events.filter((e) => e.levels.includes(k) && keep(e, 'levels')).length,
      months: (k) => model.events.filter((e) => monthKeys(e, model.chips).has(k) && keep(e, 'months')).length,
      quick: (k) => (k === 'signup' ? model.events.filter((e) => signupOpen(e, today()) && keep(e, 'signupOpen')).length
        : model.events.filter((e) => e.levels.includes('open_amateur') && keep(e, 'levels')).length),
    };
    for (const o of inputs) {
      o.input.checked = o.dim === 'quick' ? (o.key === 'signup' ? f.signupOpen : onlyOpen())
        : o.dim === 'months' ? f.months.has(o.key) : f[o.dim].has(o.key);
      o.count.textContent = fmtInt(counts[o.dim](o.key));
    }
    resetBtn.hidden = !isFiltered();
  }
  function reset() {
    f.groups = new Set(model.groups.keys());
    f.levels = new Set(model.levels.map((x) => x.id));
    f.months = new Set();
    f.signupOpen = false;
    changed();
    const first = filterRoot && filterRoot.querySelector('input');
    if (first) first.focus();
  }
  function changed() {
    syncFilters();
    const n = shown().length;
    opts.announce(isFiltered() ? `Showing ${plural(n, 'event', 'events')} of ${fmtInt(model.events.length)}.` : `Showing every event, ${plural(n, 'event', 'events')}.`);
    refreshMap();
    renderList();
    refreshNext();
    opts.onFilter();
  }

  // ── Legend strip ──
  let legendItems = null;
  function buildLegend() {
    if (!opts.legend || !model.file) return;
    const present = new Set(model.events.map((e) => e.group));
    legendItems = h('ul', { class: 'ml-items ml-events', hidden: !active },
      [...model.groups.values()].filter((g) => present.has(g.id)).map((g) => h('li', null,
        h('span', { class: 'ml-dot ml-ev', style: `--g:${g.color}`, 'aria-hidden': 'true' }), g.label)),
      h('li', { class: 'ml-ev-size' }, h('span', { class: 'ml-ev-sizes', 'aria-hidden': 'true' }, h('span'), h('span')), 'Size: number of events'));
    if (!present.size) legendItems.prepend(h('li', null, 'No events yet'));
    opts.legend.querySelector('.ml-items').after(legendItems);
  }

  // ── Map ──
  function venueFeatures() {
    const per = new Map();
    for (const e of shown()) {
      const v = model.venues.get(e.venue_id);
      if (!v || !v.mapped) continue;
      if (!per.has(v.id)) per.set(v.id, { v, n: 0, groups: new Map() });
      const p = per.get(v.id);
      p.n += 1;
      p.groups.set(e.group, (p.groups.get(e.group) || 0) + 1);
    }
    return [...per.values()].map(({ v, n, groups }) => {
      const top = [...groups.entries()].sort((a, b) => (b[1] - a[1]) || (model.groups.get(a[0]).order - model.groups.get(b[0]).order))[0][0];
      const g = model.groups.get(top);
      const mix = groups.size > 1 ? `, mostly ${g.label}` : `, ${g.label}`;
      return { v, n, color: g.color, tip: `${v.name}: ${plural(n, 'event', 'events')}${mix}` };
    });
  }
  function refreshMap() {
    if (!mapLayers) return;
    mapLayers.setData({
      type: 'FeatureCollection',
      features: venueFeatures().map(({ v, n, color, tip }) => ({
        type: 'Feature', geometry: { type: 'Point', coordinates: [v.location.lon, v.location.lat] },
        properties: { id: v.id, name: v.name, count: n, color, tip },
      })),
    });
  }

  // ── Panel ──
  function stateName(code) { const s = code && index.states.get(code); return s ? s.name : code; }
  /** "Fixture Town, MN" ("MN" alone when the venue is named after its town), "Fjord, Norway", or the state of a resort. */
  function place(v) {
    if (v.resort_id) return stateName(v.state) || '';
    return [v.town && v.town !== v.name ? v.town : null, v.state || (v.country ? countryName(v.country) : null)].filter(Boolean).join(', ');
  }
  function eyebrow(v) {
    if (v.resort_id) return `Ski area in ${stateName(v.state)}`;
    const k = model.kind.get(v.kind) || humanize(v.kind);
    const p = place(v);
    return p ? `${k} in ${p}` : k;
  }
  const navTo = (view, attrs, ...children) => h('a', { href: hrefFor(view), 'data-nav': '', ...attrs }, ...children);
  const eventView = (e) => ({ level: 'events', venueId: e.venue_id, eventId: e.id });

  function eventMeta(e, { venue = true } = {}) {
    const v = model.venues.get(e.venue_id);
    const bits = [whenText(e, model.season.label), model.disc.get(e.discipline) || humanize(e.discipline)];
    if (venue && v) bits.push([v.name, place(v)].filter(Boolean).join(', '));
    return bits.join(' · ');
  }

  function nationalPanel(view, kit, notice) {
    const evs = model.events;
    const out = [...kit.heading('Events', 'Events and competitions')];
    if (notice) out.unshift(h('p', { class: 'notice', role: 'note' }, notice));
    out.push(h('p', { class: 'intro' }, `Races, competitions and instructor events across the United States for the ${model.season.label} season, with dates, venues and how to sign up. Pick a dot on the map or open the list view for the calendar. Every detail shows its source, its confidence level and the date it was last verified.`));
    if (model.error) {
      out.push(h('p', { class: 'not-verified' }, 'The events could not load. Try reloading the page.'));
      return out;
    }
    if (!evs.length) {
      out.push(h('p', { class: 'empty' }, 'Events are being compiled. Check back soon.'));
      return out;
    }
    const t = today();
    const venuesMapped = [...model.venues.values()].filter((v) => v.mapped).length;
    out.push(h('dl', { class: 'stats' }, [
      ['Events', fmtInt(evs.length)], ['Venues', fmtInt(venuesMapped)],
      [`${model.season.label} dates`, fmtInt(evs.filter((e) => e.edition_2026_27).length)],
      ['Sign up open', fmtInt(evs.filter((e) => signupOpen(e, t)).length)],
    ].map(([k, v]) => h('div', { class: 'stat' }, h('dt', null, k), h('dd', null, v)))));
    const programs = evs.filter((e) => e.program).length;
    const abroad = evs.filter((e) => { const v = model.venues.get(e.venue_id); return v && !v.mapped; }).length;
    const notes = [programs ? plural(programs, 'season long program', 'season long programs') : null,
      abroad ? `${plural(abroad, 'event', 'events')} outside the United States, shown in the list` : null].filter(Boolean);
    if (notes.length) out.push(h('p', { class: 'stats-note' }, `Includes ${joinAnd(notes)}.`));
    panelNext = h('div', { class: 'ev-next' });
    fillNext(panelNext);
    out.push(kit.section('Coming up', panelNext));
    const counts = new Map();
    for (const e of evs) counts.set(e.group, (counts.get(e.group) || 0) + 1);
    out.push(kit.section('By discipline', h('ul', { class: 'ev-group-list' }, [...model.groups.values()].filter((g) => counts.get(g.id)).map((g) =>
      h('li', null, h('span', { class: 'ev-swatch', style: `--g:${g.color}`, 'aria-hidden': 'true' }), h('span', { class: 'ev-group-name' }, g.label),
        h('span', { class: 'nav-meta' }, plural(counts.get(g.id), 'event', 'events')))))));
    out.push(kit.section('Calendars',
      h('p', null, `The list view holds the calendar by month and ${plural(model.calendars.length, 'calendar', 'calendars')} from the sanctioning bodies, for series too large to list race by race.`),
      h('button', { type: 'button', class: 'btn btn-sm btn-ghost', onclick: () => opts.onList(true) }, 'Open the calendar list')));
    return out;
  }

  function refreshNext() {
    if (!panelNext || !panelNext.isConnected) { panelNext = null; return; }
    fillNext(panelNext);
  }
  function fillNext(box) {
    const t = today();
    const next = shown().filter((e) => editionStart(e) && editionEnd(e) >= t)
      .sort((a, b) => editionStart(a).localeCompare(editionStart(b)) || a.name.localeCompare(b.name)).slice(0, NEXT_MAX);
    clear(box);
    box.append(next.length
      ? h('ul', { class: 'nav-list ev-next-list' }, next.map((e) => h('li', null, navTo(eventView(e), { class: 'nav-item' },
        h('span', { class: 'nav-name' }, h('span', { class: 'ev-swatch', style: `--g:${model.groups.get(e.group).color}`, 'aria-hidden': 'true' }), e.name),
        h('span', { class: 'nav-meta' }, eventMeta(e))))))
      : h('p', { class: 'empty' }, isFiltered() ? 'Nothing matches these filters yet.' : 'Dated events of the season appear here.'));
    if (isFiltered()) box.append(h('p', { class: 'fact-meta' }, `${plural(shown().length, 'event matches', 'events match')} the filters.`));
  }

  /** Value, badge, note, "Last verified" and source, as resort facts show them (panel.js sourcedValue). */
  function cited(kit, label, src, format = (v) => v) {
    return src ? kit.fact(label, src, format) : null;
  }
  const linkTo = (url, text, cls) => { const u = safeUrl(url); return u ? h('a', { href: u, ...NEW_TAB, class: cls }, text) : text; };

  function locationFact(kit, v) {
    const loc = v.location;
    if (v.resort_id) {
      return h('div', { class: 'fact' }, h('dt', null, 'Location'), h('dd', null, h('span', { class: 'sourced' },
        h('span', { class: 'sourced-main' }, h('span', { class: 'fact-value' }, `Base area of ${v.name}`)),
        h('span', { class: 'fact-meta' }, 'The resort profile\'s point · ', kit.sourceLink(loc.source_id)))));
    }
    const text = loc.precision === 'town' ? `Town center of ${v.town || v.name}` : loc.precision === 'region' ? 'The region around the venue' : 'The venue itself';
    return loc.confidence && loc.as_of ? kit.fact('Location', { ...loc, value: text }, (x) => x)
      : h('div', { class: 'fact' }, h('dt', null, 'Location'), h('dd', null, text, ' · ', kit.sourceLink(loc.source_id)));
  }

  /** The Venue fact of a card: the venue and its place, with the location's precision, badge and source. A resort
   * venue sits on the resort profile's own point, which carries a source but no badge (as on the resort page). */
  function venueFact(kit, v) {
    const text = [v.name, place(v)].filter(Boolean).join(', ');
    const loc = v.location;
    if (!loc.confidence || !loc.as_of) {
      return h('div', { class: 'fact' }, h('dt', null, 'Venue'), h('dd', null, h('span', { class: 'sourced' },
        h('span', { class: 'sourced-main' }, h('span', { class: 'fact-value' }, text)),
        h('span', { class: 'fact-meta' }, v.resort_id ? 'At the resort profile point · ' : '', kit.sourceLink(loc.source_id)))));
    }
    const note = loc.precision === 'town' ? `Mapped at the town center of ${v.town || 'the town'}`
      : loc.precision === 'region' ? 'Mapped to the region' : null;
    return kit.fact('Venue', { ...loc, value: text, note: [note, loc.note].filter(Boolean).join('. ') || null }, (x) => x);
  }

  function signupBlock(e, kit) {
    const s = e.signup || {};
    const url = s.url ? safeUrl(s.url.value) : null;
    const method = s.method ? model.method.get(s.method) || humanize(s.method) : null;
    const box = h('div', { class: 'ev-signup' }, h('p', { class: 'ev-signup-head' }, h('span', { class: 'ev-signup-label' }, 'Sign up'),
      method ? h('span', { class: 'ev-method' }, method) : null));
    if (url) {
      box.append(h('p', { class: 'ev-signup-cta' },
        h('a', { href: url, ...NEW_TAB, class: 'btn ev-signup-btn' }, 'Sign up', h('span', { class: 'visually-hidden' }, ` for ${e.name}, opens in a new tab`)),
        h('span', { class: 'fact-meta' }, kit.badge(s.url.confidence), ' ', host(url), ` · Last verified ${formatIsoDate(s.url.as_of)} · `, kit.sourceLink(s.url.source_id),
          s.url.note ? h('span', { class: 'src-note' }, ` (${s.url.note})`) : null)));
    }
    const facts = [
      cited(kit, 'Platform', s.platform),
      cited(kit, 'Opens', s.opens, formatIsoDate),
      cited(kit, 'Deadline', s.deadline, formatIsoDate),
      cited(kit, 'Entry fee', s.fee_usd, (v) => money(v, 2)),
      cited(kit, 'Requirements', s.requirements),
    ].filter(Boolean);
    if (facts.length) box.append(h('dl', { class: 'facts' }, facts));
    if (!method && !url) {
      const web = e.website ? safeUrl(e.website.value) : null;
      box.append(h('p', { class: 'ev-signup-none' }, 'Sign up details not published yet.',
        web ? [' ', h('a', { href: web, ...NEW_TAB, class: 'src-link' }, 'See the event website')] : null));
    }
    return box;
  }

  function eventCard(e, kit, t) {
    const g = model.groups.get(e.group);
    const v = model.venues.get(e.venue_id);
    const levels = e.levels.map((l) => model.levelLabel.get(l) || humanize(l));
    const when = whenCite(e);
    const card = h('article', { class: 'ev-card', id: `ev-${e.id}`, dataset: { event: e.id }, style: `--g:${g.color}`, 'aria-labelledby': `ev-h-${e.id}` },
      h('h3', { class: 'ev-title', id: `ev-h-${e.id}`, tabindex: '-1' }, e.name),
      h('div', { class: 'ev-tags' },
        h('span', { class: 'tier ev-disc' }, model.disc.get(e.discipline) || humanize(e.discipline)),
        e.disciplines_also.map((d) => h('span', { class: 'tier ev-also' }, model.disc.get(d) || humanize(d))),
        levels.length ? levels.map((l) => h('span', { class: 'tier ev-level' }, l)) : h('span', { class: 'tier ev-level ev-level-none' }, 'Who can enter: not published'),
        signupOpen(e, t) ? h('span', { class: 'tier ev-open' }, 'Sign up open') : null),
      e.series ? h('p', { class: 'fact-meta ev-series' }, `Part of ${e.series.name}`) : null,
      h('dl', { class: 'facts' },
        when ? kit.fact('Dates', { ...when, value: whenText(e, model.season.label) }, (x) => x)
          : h('div', { class: 'fact' }, h('dt', null, 'Dates'), h('dd', null, h('span', { class: 'not-verified' }, whenText(e, model.season.label)))),
        !e.program && e.usual_timing ? cited(kit, 'Usual timing', e.usual_timing) : null,
        v ? venueFact(kit, v) : null,
        cited(kit, 'Organizer', e.organizer && { ...e.organizer, value: e.organizer.name }, (x) => (e.organizer.url ? linkTo(e.organizer.url, x, 'src-link') : x)),
        cited(kit, 'Sanctioning', e.sanctioning && { ...e.sanctioning, value: e.sanctioning.name }, (x) => (e.sanctioning.url ? linkTo(e.sanctioning.url, x, 'src-link') : x)),
        cited(kit, 'Format', e.format)),
      signupBlock(e, kit));
    const links = [
      cited(kit, 'Website', e.website, (u) => linkTo(u, host(u), 'ev-web-link')),
      cited(kit, 'Results', e.results_url, (u) => linkTo(u, host(u), 'ev-web-link')),
      cited(kit, 'Calendar', e.calendar_url, (u) => linkTo(u, host(u), 'ev-web-link')),
    ].filter(Boolean);
    if (links.length) card.append(h('dl', { class: 'facts ev-links' }, links));
    if (!e.website && !(e.signup && e.signup.url)) {
      const src = firstSource(e);
      if (src) card.append(h('p', { class: 'fact-meta ev-origin' }, 'Where this comes from: ', kit.sourceLink(src)));
    }
    return card;
  }

  function venuePanel(view, kit, v) {
    const evs = model.byVenue.get(v.id) || [];
    const t = today();
    const out = [
      h('nav', { class: 'crumbs', 'aria-label': 'Breadcrumb' }, h('ol', null,
        h('li', null, navTo({ level: 'events' }, null, 'Events')), h('li', null, h('span', { 'aria-current': 'page' }, v.name)))),
      ...kit.heading(eyebrow(v), v.name),
      h('div', { class: 'tier-row' }, h('span', { class: 'tier tier-facility' }, model.kind.get(v.kind) || humanize(v.kind)),
        !v.mapped ? h('span', { class: 'tier ev-abroad' }, 'Listed only') : null),
      !v.mapped ? h('p', { class: 'fact-note ev-abroad-note' }, `In ${countryName(v.country)}, outside the United States, so it shows in the list and here, never as a map dot.`) : null,
      h('dl', { class: 'facts' }, locationFact(kit, v)),
    ];
    const r = v.resort_id && index.resorts.get(v.resort_id);
    if (r) {
      const c = index.clusters.get(r.cluster_id);
      if (c) {
        const st = c.states.includes(r.state) ? r.state : c.states[0];
        out.push(h('p', { class: 'ev-resort-link' }, navTo({ level: 'resort', stateCode: st, clusterId: c.id, resortId: r.id }, { class: 'src-link' }, `Open the resort profile for ${r.name}`)));
      }
    }
    out.push(kit.section(evs.length === 1 ? 'The event here' : `${fmtInt(evs.length)} events here`,
      h('p', { class: 'fact-meta' }, 'In date order. Sign up buttons open the organizer\'s page in a new tab.'),
      evs.map((e) => eventCard(e, kit, t))));
    return out;
  }

  // ── List view: the calendar by month, then the calendars ──
  let listEl = null;
  let listBody = null;
  let listCount = null;
  function buildList() {
    if (!opts.stage) return;
    listEl = h('section', { id: 'events-list', class: 'list-view events-list', 'aria-label': 'Event calendar and calendars', hidden: true },
      h('h2', { class: 'list-title' }, `Event calendar ${model.season ? model.season.label : ''}`),
      listCount = h('p', { class: 'ev-list-count' }),
      listBody = h('div', { class: 'ev-list-body' }));
    if (model.calendars && model.calendars.length) listEl.append(calendarsSection());
    const anchor = opts.stage.querySelector('#list-view');
    if (anchor) anchor.after(listEl); else opts.stage.append(listEl);
  }
  let listBuilt = false;
  function renderList() {
    if (!listEl || listEl.hidden) { listBuilt = false; return; }
    listBuilt = true;
    clear(listBody);
    const evs = shown();
    listCount.textContent = model.events.length
      ? `${isFiltered() ? `${plural(evs.length, 'event matches', 'events match')} the filters, of ${fmtInt(model.events.length)}.` : `${plural(evs.length, 'event', 'events')}.`}`
      : 'Events are being compiled. Check back soon.';
    const months = new Map();
    const programs = [];
    const undated = [];
    for (const e of evs) {
      const s = editionStart(e);
      if (s) { const k = s.slice(0, 7); if (!months.has(k)) months.set(k, []); months.get(k).push(e); }
      else (e.program ? programs : undated).push(e);
    }
    const sel = opts.current();
    const row = (e) => h('li', null, navTo(eventView(e), { class: 'ev-row', 'aria-current': sel && sel.eventId === e.id ? 'page' : null },
      h('span', { class: 'ev-swatch', style: `--g:${model.groups.get(e.group).color}`, 'aria-hidden': 'true' }),
      h('span', { class: 'ev-row-name' }, e.name),
      signupOpen(e, today()) ? h('span', { class: 'tier ev-open' }, 'Sign up open') : null,
      h('span', { class: 'nav-meta ev-row-meta' }, eventMeta(e))));
    const block = (title, list) => h('section', { class: 'ev-month' }, h('h3', { class: 'ev-month-title' }, title,
      h('span', { class: 'tree-meta' }, ` ${plural(list.length, 'event', 'events')}`)), h('ul', { class: 'ev-rows' }, list.map(row)));
    for (const k of [...months.keys()].sort()) listBody.append(block(`${monthLong(Number(k.slice(5)))} ${k.slice(0, 4)}`, months.get(k)));
    if (programs.length) listBody.append(block('Season long', programs));
    if (undated.length) listBody.append(block(`${model.season.label} date not yet published`, undated));
    if (model.events.length && !evs.length) listBody.append(h('p', { class: 'empty' }, 'Nothing matches these filters yet.'));
  }
  function calendarsSection() {
    const byGroup = new Map();
    for (const c of model.calendars) { if (!byGroup.has(c.group)) byGroup.set(c.group, []); byGroup.get(c.group).push(c); }
    return h('section', { class: 'ev-cals', 'aria-labelledby': 'ev-cals-title' },
      h('h2', { class: 'list-title', id: 'ev-cals-title' }, 'Calendars'),
      h('p', { class: 'ev-list-count' }, 'Calendars from the sanctioning bodies, for series too large to list race by race. Each opens in a new tab.'),
      [...model.groups.values()].filter((g) => byGroup.has(g.id)).map((g) => h('section', { class: 'ev-month' },
        h('h3', { class: 'ev-month-title' }, h('span', { class: 'ev-swatch', style: `--g:${g.color}`, 'aria-hidden': 'true' }), g.label),
        h('ul', { class: 'ev-rows' }, byGroup.get(g.id).map((c) => h('li', { class: 'ev-cal' },
          linkTo(c.url.value, c.name, 'ev-cal-link'),
          h('span', { class: 'nav-meta' }, ` ${model.disc.get(c.discipline) || humanize(c.discipline)} · ${c.region === 'national' ? 'National' : stateName(c.region)}`),
          h('span', { class: 'fact-meta' }, opts.kit().badge(c.url.confidence), ` Last verified ${formatIsoDate(c.url.as_of)} · `, opts.kit().sourceLink(c.url.source_id))))))));
  }

  const api = {
    ready,
    get loaded() { return Boolean(model); },
    get model() { return model; },
    /** Panel body for an events view (panel.js renderEvents), or null until events.json has loaded. */
    renderPanel(view, kit) {
      if (!model) return null;
      panelNext = null;
      if (view.eventId && !model.eventById.has(view.eventId)) {
        return nationalPanel(view, kit, 'That link points to an event we could not find, so here are all the events.');
      }
      const vid = view.venueId || (view.eventId ? model.eventById.get(view.eventId).venue_id : null);
      if (vid && !model.venues.has(vid)) return nationalPanel(view, kit, 'That link points to a venue we could not find, so here are all the events.');
      return vid ? venuePanel(view, kit, model.venues.get(vid)) : nationalPanel(view, kit);
    },
    /** The venue of a view (an event link names the venue too), for the map and the sub bar. */
    venueOf(view) {
      if (!model || view.level !== 'events') return null;
      const vid = view.venueId || (view.eventId && model.eventById.has(view.eventId) ? model.eventById.get(view.eventId).venue_id : null);
      return vid ? model.venues.get(vid) || null : null;
    },
    /** Scroll the event's card into view, mark it and (unless focus is false) focus its heading. */
    focusEvent(id, { focus = true } = {}) {
      const card = document.getElementById(`ev-${id}`);
      if (!card) return false;
      for (const c of document.querySelectorAll('.ev-card.is-selected')) c.classList.remove('is-selected');
      card.classList.add('is-selected');
      const scroller = card.closest('.panel-scroll');
      if (scroller) scroller.scrollTop = Math.max(0, card.offsetTop - 12);
      if (focus) card.querySelector('.ev-title').focus({ preventScroll: true });
      return true;
    },
    /** Swap the list's source placeholders once sources.json is in (the panel does its own). */
    fillSources() {
      if (!listEl) return;
      for (const el of listEl.querySelectorAll('.src-pending')) el.replaceWith(opts.kit().sourceLink(el.dataset.src));
    },
    /** EVENTS mode on or off: the filters, the legend items and the venue layers. */
    setActive(on) {
      active = on;
      if (filterRoot) filterRoot.hidden = !on;
      if (legendItems) legendItems.hidden = !on;
      if (!on && listEl) listEl.hidden = true;
      if (mapLayers) mapLayers.setVisible(on);
      if (mapCtl) mapCtl.setMode(on ? 'events' : 'resorts', mapLayers ? { pick: mapLayers.pick } : null);
    },
    /** The calendar list in place of the resort list view. */
    showList(on) {
      if (!listEl) return;
      listEl.hidden = !on;
      if (on && !listBuilt) renderList();
    },
    /** The selected venue: the ring on the map, the list highlight. */
    select(view) {
      const v = api.venueOf(view);
      if (mapLayers) mapLayers.setSelected(v && v.mapped ? v.id : null);
      if (listEl && !listEl.hidden) {
        for (const a of listEl.querySelectorAll('[aria-current]')) a.removeAttribute('aria-current');
        const a = view.eventId && listEl.querySelector(`a[href="${CSS.escape(hrefFor({ level: 'events', venueId: v && v.id, eventId: view.eventId }))}"]`);
        if (a) a.setAttribute('aria-current', 'page');
      }
    },
    /** Venue dots on the WebGL map (eventsmap.js), once it runs. */
    attachMap(ctl) {
      mapCtl = ctl;
      ctl.whenReady(() => import('./eventsmap.js').then(({ attachVenueLayers }) => {
        if (mapLayers) return;
        mapLayers = attachVenueLayers(ctl);
        refreshMap();
        mapLayers.setVisible(active);
        const cur = opts.current();
        if (cur) api.select(cur);
        ctl.setMode(active ? 'events' : 'resorts', { pick: mapLayers.pick });
      }).catch((err) => console.warn('[map] events map', err.message)));
    },
    /** The 2D picture before the WebGL map starts: the venue dots the filters keep. */
    previewPoints() {
      if (!model) return [];
      return venueFeatures().map(({ v, n, color }) => ({ lon: v.location.lon, lat: v.location.lat, color, r: 2.6 + 2.2 * Math.sqrt(n) }));
    },
    /** Events of the season still ahead by the viewer's date (the header pill). */
    upcomingCount(t = today()) { return model ? model.events.filter((e) => upcoming(e, t)).length : 0; },
    /** "Showing events at Vail." for the status region. */
    summary(view) {
      const v = api.venueOf(view);
      if (v) return `Showing ${plural((model.byVenue.get(v.id) || []).length, 'event', 'events')} at ${v.name}.`;
      return `Showing ${plural(model ? shown().length : 0, 'event', 'events')}.`;
    },
  };
  return api;
}
