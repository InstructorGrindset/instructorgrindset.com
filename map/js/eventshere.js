// "Events here" on resort panels (ROADMAP item 22) and the date wording EVENTS mode shares. panel.js loads this the
// first time a resort with events renders, so the startup path carries none of it. Matches pipeline/routes.py
// render_resort_events, date_span and resort_events_when.
import { hrefFor } from './router.js';
import { h, formatIsoDate, fmtInt } from './ui.js';

const SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const pad2 = (n) => String(n).padStart(2, '0');
const isoDay = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;

/** "Feb 25 to 27, 2027", "Nov 28 to Dec 2, 2026", "Dec 30, 2026 to Jan 2, 2027", "Feb 25, 2027". */
export function dateSpan(start, end) {
  const a = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(start || ''));
  if (!a) return '';
  const b = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(end || ''));
  const [ya, ma, da] = [a[1], SHORT[Number(a[2]) - 1], Number(a[3])];
  if (!b || b[0] === a[0]) return `${ma} ${da}, ${ya}`;
  const [yb, mb, db] = [b[1], SHORT[Number(b[2]) - 1], Number(b[3])];
  if (ya === yb && ma === mb) return `${ma} ${da} to ${db}, ${ya}`;
  if (ya === yb) return `${ma} ${da} to ${mb} ${db}, ${ya}`;
  return `${ma} ${da}, ${ya} to ${mb} ${db}, ${yb}`;
}

/** The date line of a Resort.events item. */
export function eventWhenText(it, season) {
  if (it.published) return it.end ? dateSpan(it.start, it.end) : `Starts ${dateSpan(it.start)}`;
  if (it.season_long) return it.timing ? `Runs ${it.timing}` : 'Runs all season';
  const unpublished = `${season} date not yet published.`;
  if (it.start) return `Last held ${dateSpan(it.start, it.end)}. ${unpublished}`;
  if (it.season) return `Last held in the ${it.season} season. ${unpublished}`;
  return unpublished;
}

/**
 * The section: the resort's next events by the viewer's date (dated editions not yet over, then the others; up to
 * definitions events.resort_panel_max), each linked into EVENTS mode, then the venue on the events map.
 * kit: panel.js kit() (badge, sourceLink, section); definitions: definitions.json or null.
 */
export function eventsHere(r, kit, definitions, now = new Date()) {
  const ev = r.events;
  if (!ev || !Array.isArray(ev.items) || !ev.items.length) return null;
  const defs = (definitions && definitions.events) || {};
  const max = defs.resort_panel_max || 3;
  const season = (defs.season && defs.season.label) || '2026/27';
  const today = isoDay(now);
  const ahead = ev.items.filter((it) => !it.published || (it.end || it.start || '') >= today);
  const shown = (ahead.length ? ahead : ev.items).slice(0, max);
  const box = kit.section('Events here',
    h('ul', { class: 'ev-here' }, shown.map((it) => h('li', null,
      h('a', { class: 'ev-here-link', href: hrefFor({ level: 'events', venueId: r.id, eventId: it.id }), 'data-nav': '' }, it.name),
      ' ', h('span', { class: 'nav-meta' }, it.discipline_label || ''), ' ',
      h('span', { class: 'fact-meta' }, `${eventWhenText(it, season)} `, it.confidence ? [kit.badge(it.confidence), ' '] : null,
        it.as_of ? `Last verified ${formatIsoDate(it.as_of)}` : null, it.source_id ? [' · ', kit.sourceLink(it.source_id)] : null)))),
    h('p', { class: 'ev-here-all' }, h('a', { href: hrefFor({ level: 'events', venueId: r.id }), 'data-nav': '' },
      `${ev.count === 1 ? 'See the event' : `See all ${fmtInt(ev.count)} events`} at ${r.name} on the events map`)));
  box.classList.add('events-here');
  return box;
}
