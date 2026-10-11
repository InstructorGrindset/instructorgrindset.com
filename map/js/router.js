// History API routing: /map/{state}/{cluster}/{resort}/ plus ?compare=a,b,c on a cluster.
import { config } from './config.js';

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
  const view = { level: 'national', stateCode: null, clusterId: null, resortId: null, compare: [], fellBack: false };
  if (segments.length > 3) view.fellBack = true;
  if (!s) return view;

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
  const qs = q.toString().replace(/%2C/g, ',').replace(/%2F/g, '/');
  return pathFor(view) + (qs ? `?${qs}` : '');
}

export function sameView(a, b) {
  return a && b && a.level === b.level && a.stateCode === b.stateCode && a.clusterId === b.clusterId
    && a.resortId === b.resortId && (a.compare || []).join() === (b.compare || []).join();
}

export function createRouter(index, onChange) {
  let current = null;

  function fromLocation() {
    return resolve(index, parsePath(window.location.pathname), window.location.search);
  }

  function apply(view, { initial = false, fromPop = false } = {}) {
    if (initial || fromPop) {
      // Rewrite unknown or non canonical paths (and /map/index.html) to the resolved view.
      const href = hrefFor(view);
      if (view.fellBack || window.location.pathname !== href.split('?')[0]) {
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
      const href = hrefFor(next);
      if (replace) window.history.replaceState({ href }, '', href);
      else window.history.pushState({ href }, '', href);
      apply(next);
    },
    /** Navigate to an in-app href (from a link). */
    goHref(href) {
      const u = new URL(href, window.location.origin);
      const view = resolve(index, parsePath(u.pathname), u.search);
      this.go(view);
    },
  };
}
