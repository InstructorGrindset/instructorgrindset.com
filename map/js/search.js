// Resort search: an ARIA combobox with a list of suggestions (WAI-ARIA APG "list autocomplete") over the slim
// index the map already holds (map-index.json, clusters.geojson, states.json). No extra fetch.
// app.js loads this module on first focus of the search box. Choosing a result calls onSelect(view), which goes
// through the router exactly as a click does, so it works before the WebGL map starts and in the list view.
import { h, clear, plural, joinAnd } from './ui.js';
import { FACILITY_LABEL } from './labels.js';

const MAX_RESULTS = 8;
// Whole words treated as the same: "Mount Hood" finds "Mt. Hood Meadows" and the other way round.
const SHORT = { mount: 'mt', saint: 'st', fort: 'ft' };

/** Lower case, accents and punctuation dropped: "Smugglers' Notch" -> "smugglers notch". */
export function normalize(text) {
  return String(text || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
    .replace(/['‘’`´.]/g, '').replace(/&/g, ' and ').replace(/[^a-z0-9]+/g, ' ').trim();
}

function shorten(norm) { return norm.split(' ').map((w) => SHORT[w] || w).join(' '); }

/** The forms of a name a query is compared with. */
function keysOf(name) {
  const n = normalize(name);
  const s = shorten(n);
  return s === n ? [n] : [n, s];
}

/** 0 exact, 1 prefix, 2 word prefix, 3 every word matches a word start, 4 anywhere; null for no match. */
export function score(key, q) {
  if (!q || !key) return null;
  if (key === q) return 0;
  if (key.startsWith(q) || key.replace(/ /g, '').startsWith(q.replace(/ /g, ''))) return 1;
  if (` ${key}`.includes(` ${q}`)) return 2;
  const words = key.split(' ');
  if (q.split(' ').every((t) => words.some((w) => w.startsWith(t)))) return 3;
  return key.includes(q) ? 4 : null;
}

const KIND_ORDER = { state: 0, cluster: 1, resort: 2 };

/** Every state, cluster and resort the router can show, with the text each result displays. */
export function buildItems(index) {
  const stateName = (code) => (index.states.get(code) || { name: code }).name;
  const items = [];
  for (const s of index.stateList) {
    items.push({ kind: 'state', id: s.code, name: s.name, keys: keysOf(s.name), code: s.code.toLowerCase(),
      meta: `State with ${plural(s.resort_count, 'resort', 'resorts')}` });
  }
  for (const c of index.clusters.values()) {
    if (!c.states.some((st) => index.states.has(st))) continue;
    items.push({ kind: 'cluster', id: c.id, name: c.name, keys: keysOf(c.name), fallback: c.fallback,
      meta: `Cluster of ${plural(c.resort_ids.length, 'resort', 'resorts')} in ${joinAnd(c.states.map(stateName))}` });
  }
  for (const r of index.resortList) {
    if (!index.clusters.has(r.cluster_id)) continue;
    const noun = r.facility_type === 'ski_area' ? 'resort' : FACILITY_LABEL[r.facility_type].toLowerCase();
    items.push({ kind: 'resort', id: r.id, name: r.name, keys: keysOf(r.name), tier: r.tier, state: r.state,
      clusterId: r.cluster_id, meta: `Tier ${r.tier} ${noun} in ${stateName(r.state)}` });
  }
  return items;
}

/** Ranked matches for a query: prefix matches first, then states, clusters and resorts, Tier A first, then by name. */
export function search(items, query, limit = MAX_RESULTS) {
  const q = normalize(query);
  if (!q) return [];
  const qs = [...new Set([q, shorten(q)])];
  const hits = [];
  for (const it of items) {
    let best = it.kind === 'state' && qs.includes(it.code) ? 0 : null;
    for (const k of it.keys) {
      for (const x of qs) {
        const s = score(k, x);
        if (s != null && (best == null || s < best)) best = s;
      }
    }
    // One letter matches only at the start of a word.
    if (best != null && (q.length > 1 || best <= 2)) hits.push({ it, s: best });
  }
  hits.sort((a, b) => (a.s - b.s) || (KIND_ORDER[a.it.kind] - KIND_ORDER[b.it.kind])
    || ((a.it.fallback ? 1 : 0) - (b.it.fallback ? 1 : 0)) || ((a.it.tier === 'A' ? 0 : 1) - (b.it.tier === 'A' ? 0 : 1))
    || (a.it.name.length - b.it.name.length) || a.it.name.localeCompare(b.it.name));
  return hits.slice(0, limit).map((x) => x.it);
}

/** The view a result opens, with the same state rule as a click on the map (keep the current state when it fits). */
export function viewFor(index, item, cur) {
  if (item.kind === 'state') return { level: 'state', stateCode: item.id };
  const c = index.clusters.get(item.kind === 'cluster' ? item.id : item.clusterId);
  const keep = cur && cur.stateCode && c.states.includes(cur.stateCode) ? cur.stateCode : null;
  if (item.kind === 'cluster') return { level: 'cluster', stateCode: keep || c.states[0], clusterId: c.id };
  return { level: 'resort', stateCode: keep || (c.states.includes(item.state) ? item.state : c.states[0]),
    clusterId: c.id, resortId: item.id };
}

/**
 * Wire the combobox in `root` (an input[role=combobox], a ul[role=listbox], an empty message and a status region).
 * opts: onSelect(view, item), current() for the current view, listBottom() for the lowest y the list may reach.
 */
export function createSearch(root, index, { onSelect, current = () => null, listBottom = () => window.innerHeight - 16 } = {}) {
  const input = root.querySelector('[role="combobox"]');
  const list = root.querySelector('[role="listbox"]');
  const empty = root.querySelector('.map-search-empty');
  const status = root.querySelector('[role="status"]');
  const items = buildItems(index);
  let results = [];
  let active = -1;
  let statusTimer = null;

  const optId = (i) => `${list.id}-opt-${i}`;
  const isOpen = () => !list.hidden;

  function say(text) {
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => { status.textContent = text; }, 450);
  }

  function fit() {
    if (!isOpen()) return;
    const top = list.getBoundingClientRect().top;
    list.style.maxHeight = `${Math.max(132, Math.floor(listBottom() - top - 8))}px`;
  }

  function setActive(i) {
    active = i;
    for (const [j, li] of [...list.children].entries()) li.setAttribute('aria-selected', String(j === i));
    if (i >= 0) {
      input.setAttribute('aria-activedescendant', optId(i));
      list.children[i].scrollIntoView({ block: 'nearest' });
    } else input.removeAttribute('aria-activedescendant');
  }

  function close() {
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    setActive(-1);
    window.removeEventListener('resize', fit);
  }

  function open() {
    if (!results.length) return;
    if (!isOpen()) {
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      window.addEventListener('resize', fit);
    }
    fit();
  }

  function update() {
    const q = input.value;
    results = search(items, q);
    clear(list);
    results.forEach((it, i) => list.append(h('li', { id: optId(i), role: 'option', class: `map-search-option kind-${it.kind}`, 'aria-selected': 'false', dataset: { index: i } },
      h('span', { class: 'map-search-name' }, it.name), h('span', { class: 'map-search-meta' }, it.meta))));
    active = -1;
    input.removeAttribute('aria-activedescendant');
    const none = Boolean(normalize(q)) && !results.length;
    empty.hidden = !none;
    if (results.length) {
      open();
      say(`${plural(results.length, 'result', 'results')}. Use the up and down arrow keys to choose one.`);
    } else {
      close();
      say(none ? empty.textContent : '');
    }
  }

  function choose(i) {
    const it = results[i];
    if (!it) return;
    const view = viewFor(index, it, current());
    input.value = '';
    results = [];
    clear(list);
    empty.hidden = true;
    close();
    clearTimeout(statusTimer);
    status.textContent = '';
    onSelect(view, it);
  }

  input.addEventListener('input', update);
  input.addEventListener('focus', () => { if (input.value && !isOpen()) update(); });
  input.addEventListener('blur', () => { close(); empty.hidden = true; });
  input.addEventListener('keydown', (e) => {
    if (e.isComposing) return;
    const n = results.length;
    switch (e.key) {
      case 'ArrowDown':
        if (!n) return;
        e.preventDefault();
        if (!isOpen()) { open(); if (!e.altKey) setActive(0); } else setActive(active + 1 >= n ? 0 : active + 1);
        break;
      case 'ArrowUp':
        if (!n) return;
        e.preventDefault();
        if (!isOpen()) open();
        setActive(active <= 0 ? n - 1 : active - 1);
        break;
      case 'Enter':
        if (!n) return;
        e.preventDefault();
        choose(active >= 0 ? active : 0);
        break;
      case 'Escape':
        if (isOpen()) { e.preventDefault(); close(); } else if (input.value) { e.preventDefault(); input.value = ''; update(); }
        break;
      case 'Tab':
        close();
        break;
      default:
    }
  });
  // Keep focus in the input while a pointer picks an option, then choose it on click.
  list.addEventListener('mousedown', (e) => e.preventDefault());
  list.addEventListener('click', (e) => {
    const li = e.target.closest('[role="option"]');
    if (li) choose(Number(li.dataset.index));
  });

  // Text typed while this module loaded.
  if (document.activeElement === input && input.value) update();
  return { input, update, close, get results() { return results.slice(); } };
}
