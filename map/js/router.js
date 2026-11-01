// History API routing: /map/{state}/{cluster}/{resort}/ plus ?compare=a,b,c on a cluster, and EVENTS mode at
// /map/events/ with ?venue=<id>&event=<id> (the venue and the event the panel shows; checked once events.json loads).
// Overlay state that holds across views rides along in the address bar: ?jet=<layer>,<layer> (the jet stream paths
// on, the active one last; docs/map/JETSTREAM.md). Links in the page leave it out; the router adds it to every
// history entry, so the Link button and the address bar carry it.
import { config } from './config.js';

/** The first path segment of EVENTS mode (pipeline/routes.py EVENTS_PATH). */
export const EVENTS_SEGMENT = 'events';

const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export function parsePath(pathname) {
  let p = pathname;
  if (p.startsWith(config.mapBase)) p = p.slice(config.mapBase.length);
  else if (p === config.mapBase.replace(/\/$/, '')) p = '';
  p = p.replace(/index\.html$/, '');
  return p.split('/').map((s) => {
    try { return decodeURIComponent(s).toLowerCase(); } catch { return ''; }
  }).filter(Boolean);
}

/**
 * Resolve URL segments to the nearest valid view.
 * Returns { level, stateCode, clusterId, resortId, compare, fellBack }.
 */
export function resolve(index, segments, search = '') {
  const [s, c, r] = segments;
  const view = { level: 'national', stateCode: null, clusterId: null, resortId: null, compare: [], venueId: null, eventId: null, fellBack: false };
  if (segments.length > 3) view.fellBack = true;
  if (!s) return view;
  if (s === EVENTS_SEGMENT) {
    // EVENTS mode. The ids are only shape checked here: events.json loads with the mode, then events.js checks them.
    view.level = 'events';
    if (segments.length > 1) view.fellBack = true;
    const q = new URLSearchParams(search);
    const slug = (k) => { const v = (q.get(k) || '').trim().toLowerCase(); return SLUG.test(v) ? v : null; };
    view.venueId = slug('venue');
    view.eventId = slug('event');
    return view;
  }

  const code = SLUG.test(s) ? s.toUpperCase() : null;
  if (!code || !index.states.has(code)) { view.fellBack = true; return view; }
  view.level = 'state';
  view.stateCode = code;
  if (!c) return view;

  const cluster = SLUG.test(c) ? index.clusters.get(c) : null;
  if (!cluster) { view.fellBack = true; return view; }
  if (!cluster.states.includes(code)) {
    // Right cluster, wrong state segment: correct the state.
    view.stateCode = index.states.has(cluster.states[0]) ? cluster.states[0] : code;
    view.fellBack = true;
  }
  view.level = 'cluster';
  view.clusterId = cluster.id;

  if (r) {
    const resort = SLUG.test(r) ? index.resorts.get(r) : null;
    if (!resort || resort.cluster_id !== cluster.id) { view.fellBack = true; return view; }
    view.level = 'resort';
    view.resortId = resort.id;
    return view;
  }

  const raw = new URLSearchParams(search).get('compare');
  if (raw) {
    const ids = [...new Set(raw.split(',').map((x) => x.trim().toLowerCase()))]
      .filter((id) => cluster.resort_ids.includes(id))
      .slice(0, config.compare.max);
    if (ids.length >= config.compare.min) {
      view.level = 'compare';
      view.compare = ids;
    } else view.fellBack = true;
  }
  return view;
}

export function pathFor(view) {
  if (view.level === 'events') return `${config.mapBase}${EVENTS_SEGMENT}/`;
  const parts = [];
  if (view.stateCode) parts.push(view.stateCode.toLowerCase());
  if (view.stateCode && view.clusterId) parts.push(view.clusterId);
  if (view.stateCode && view.clusterId && view.resortId) parts.push(view.resortId);
  return config.mapBase + (parts.length ? parts.join('/') + '/' : '');
}

/** Full href for a view, keeping dev params such as ?data=. */
export function hrefFor(view) {
  const q = new URLSearchParams(config.preservedParams);
  if (view.level === 'compare' && view.compare && view.compare.length) q.set('compare', view.compare.join(','));
  if (view.level === 'events') {
    if (view.venueId) q.set('venue', view.venueId);
    if (view.eventId) q.set('event', view.eventId);
  }
  const qs = q.toString().replace(/%2C/g, ',').replace(/%2F/g, '/');
  return pathFor(view) + (qs ? `?${qs}` : '');
}

const LAYER_ID = /^[a-z0-9]+(?:_[a-z0-9]+)*$/;
/** The jet stream layer ids of a query string (?jet=el_nino,la_nina), shape checked only; jetstream.js checks them against the data. */
export function parseJet(search) {
  let raw = '';
  try { raw = new URLSearchParams(search).get('jet') || ''; } catch { raw = ''; }
  return [...new Set(raw.split(',').map((x) => x.trim().toLowerCase()).filter((x) => LAYER_ID.test(x)))].slice(0, 12);
}
let jetParam = '';
/** The jet stream layers on, in the order they were turned on (the active one last). */
export function jetLayers() { return jetParam ? jetParam.split(',') : []; }
export function setJetLayers(ids) { jetParam = (ids || []).filter((x) => LAYER_ID.test(x)).join(','); }

/** The address bar URL of a view: hrefFor plus the overlay state. */
export function locationFor(view) {
  const href = hrefFor(view);
  return jetParam ? `${href}${href.includes('?') ? '&' : '?'}jet=${jetParam}` : href;
}

export function sameView(a, b) {
  return a && b && a.level === b.level && a.stateCode === b.stateCode && a.clusterId === b.clusterId
    && a.resortId === b.resortId && (a.compare || []).join() === (b.compare || []).join()
    && (a.venueId || null) === (b.venueId || null) && (a.eventId || null) === (b.eventId || null);
}

export function createRouter(index, onChange) {
  let current = null;
  setJetLayers(parseJet(window.location.search));

  function fromLocation() {
    return resolve(index, parsePath(window.location.pathname), window.location.search);
  }

  function apply(view, { initial = false, fromPop = false } = {}) {
    if (initial || fromPop) {
      // Rewrite unknown or non canonical paths (and /map/index.html) to the resolved view. Back and forward keep
      // the overlays as they are, so an older entry's ?jet= is rewritten to the current one.
      const href = locationFor(view);
      if (view.fellBack || window.location.pathname !== href.split('?')[0]
        || (view.level === 'events' && window.location.search !== (href.includes('?') ? href.slice(href.indexOf('?')) : ''))
        || parseJet(window.location.search).join(',') !== jetParam) {
        window.history.replaceState({ href }, '', href);
      }
    }
    const prev = current;
    current = { ...view };
    onChange(current, { initial, fromPop, prev, fellBack: view.fellBack });
  }

  window.addEventListener('popstate', () => apply(fromLocation(), { fromPop: true }));

  return {
    start() { apply(fromLocation(), { initial: true }); },
    get current() { return current; },
    /** Navigate to a view object. */
    go(view, { replace = false } = {}) {
      const next = { ...view, fellBack: false };
      if (sameView(next, current)) return;
      const href = locationFor(next);
      if (replace) window.history.replaceState({ href }, '', href);
      else window.history.pushState({ href }, '', href);
      apply(next);
    },
    /** Rewrite the address bar for the current view after an overlay change (no new history entry). */
    syncUrl() {
      if (!current) return;
      const href = locationFor(current);
      if (window.location.pathname + window.location.search !== href) window.history.replaceState({ href }, '', href);
    },
    /** Navigate to an in-app href (from a link). */
    goHref(href) {
      const u = new URL(href, window.location.origin);
      const view = resolve(index, parsePath(u.pathname), u.search);
      this.go(view);
    },
  };
}
