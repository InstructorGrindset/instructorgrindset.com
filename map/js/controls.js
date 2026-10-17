// Header and layers panel chrome (ROADMAP item 21, World Monitor style) that the first paint does not need: the MAP /
// SATELLITE toggle's buttons, the region jump menu, the Link button, the phone layers sheet and the LIVE jobs feed
// indicator. app.js imports this module after the first paint, so it adds no request before it; the controls are
// on screen from the start (so nothing shifts when they come alive) and work once it has loaded.
import { isPhone, saveTone } from './config.js';
import { hrefFor } from './router.js';
import { h, monthShort } from './ui.js';

const pad2 = (n) => String(n).padStart(2, '0');

/**
 * The jobs feed time (postings.json fetched_at) as "15 Oct 17:19 UTC", or null when the feed has no real fetch time.
 */
export function feedTime(iso) {
  if (typeof iso !== 'string' || !/^\d{4}-\d{2}-\d{2}T/.test(iso)) return null;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return null;
  const d = new Date(t);
  return {
    text: `${pad2(d.getUTCDate())} ${monthShort(d.getUTCMonth() + 1)} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())} UTC`,
    iso: d.toISOString(),
    long: `${monthShort(d.getUTCMonth() + 1)} ${d.getUTCDate()}, ${d.getUTCFullYear()} at ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())} UTC`,
  };
}

/**
 * MAP / SATELLITE: the two pressed buttons in the sub bar (app.js shows them in the viewer's tone at startup). A click
 * saves the choice in this browser (config.saveTone, guarded), calls onChange(tone) and says so.
 */
export function createToneToggle(group, { current, onChange, announce }) {
  if (!group) return;
  for (const b of group.querySelectorAll('button[data-tone]')) {
    b.addEventListener('click', () => {
      const tone = b.dataset.tone;
      if (tone === current()) return;
      saveTone(tone);
      onChange(tone);
      announce(tone === 'satellite' ? 'Basemap set to Satellite, in full color.' : 'Basemap set to Map, in dark monochrome.');
    });
  }
}

/**
 * Region jump menu: a disclosure button with National and every state that has resorts, as real links (the router
 * takes them, like every other in app link). Built on first open.
 */
export function createRegionMenu(root, index) {
  if (!root) return { update() {} };
  const btn = root.querySelector('button');
  const label = root.querySelector('.map-region-label');
  const list = root.querySelector('ul');
  let built = false;
  let current = 'national';
  const items = () => [['national', 'National', { level: 'national' }],
    ...index.stateList.filter((s) => s.resort_count > 0).map((s) => [s.code, s.name, { level: 'state', stateCode: s.code }])];
  function mark() {
    for (const a of list.querySelectorAll('a')) {
      if (a.dataset.region === current) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
    }
  }
  function build() {
    built = true;
    list.append(...items().map(([key, name, view]) => h('li', null, h('a', { href: hrefFor(view), 'data-nav': '', dataset: { region: key } }, name))));
    mark();
  }
  function setOpen(open, { focus = false } = {}) {
    if (open && !built) build();
    list.hidden = !open;
    btn.setAttribute('aria-expanded', String(open));
    if (open && focus) (list.querySelector('[aria-current]') || list.querySelector('a')).focus();
  }
  btn.addEventListener('click', () => setOpen(list.hidden));
  btn.addEventListener('keydown', (e) => { if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true, { focus: true }); } });
  root.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !list.hidden) { e.stopPropagation(); setOpen(false); btn.focus(); return; }
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && e.target.closest('a')) {
      e.preventDefault();
      const links = [...list.querySelectorAll('a')];
      const i = links.indexOf(e.target) + (e.key === 'ArrowDown' ? 1 : -1);
      links[(i + links.length) % links.length].focus();
    }
  });
  list.addEventListener('click', (e) => { if (e.target.closest('a')) setOpen(false); });
  root.addEventListener('focusout', (e) => { if (!list.hidden && !root.contains(e.relatedTarget)) setOpen(false); });
  document.addEventListener('pointerdown', (e) => { if (!list.hidden && !root.contains(e.target)) setOpen(false); });
  root.hidden = false;
  return {
    update(view) {
      current = view.stateCode || 'national';
      label.textContent = view.stateCode ? index.states.get(view.stateCode).name : 'National';
      if (built) mark();
    },
  };
}

/** Link: copies the current deep link and confirms it in the polite status region. */
export function createLinkButton(root, announce) {
  if (!root) return;
  const btn = root.querySelector('button');
  const done = root.querySelector('.mh-copied');
  let timer = null;
  const fallback = (text) => {
    const ta = h('textarea', { class: 'visually-hidden', readonly: '', tabindex: '-1', 'aria-hidden': 'true' });
    ta.value = text;
    document.body.append(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    ta.remove();
    btn.focus();
    return ok;
  };
  btn.addEventListener('click', async () => {
    const url = window.location.href;
    let ok = false;
    try {
      if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(url); ok = true; }
    } catch { ok = false; }
    if (!ok) ok = fallback(url);
    announce(ok ? 'Link to this view copied.' : 'The link could not be copied. Copy it from the address bar.');
    if (ok && done) {
      done.hidden = false;
      clearTimeout(timer);
      timer = setTimeout(() => { done.hidden = true; }, 2000);
    }
  });
}

/**
 * Phones: the LAYERS button opens the layers panel as a sheet over the map (Escape or Close shuts it, focus returns
 * to the button). Wider screens show the panel in place and hide the button.
 */
export function createLayersSheet(panel, toggle, close) {
  if (!panel || !toggle) return { set() {} };
  const title = panel.querySelector('.lp-sheet-title');
  const set = (open, { focus = true } = {}) => {
    panel.classList.toggle('is-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    if (!focus) return;
    if (open) (title || panel).focus({ preventScroll: true }); else toggle.focus();
  };
  toggle.addEventListener('click', () => set(!panel.classList.contains('is-open')));
  if (close) close.addEventListener('click', () => set(false));
  panel.addEventListener('keydown', (e) => { if (e.key === 'Escape' && isPhone() && panel.classList.contains('is-open')) set(false); });
  return { set, get open() { return panel.classList.contains('is-open'); } };
}

/** LIVE: shown only when the jobs feed has a real fetch time, with that time. */
export function showFeedTime(root, iso) {
  const t = feedTime(iso);
  if (!root || !t) return false;
  const time = root.querySelector('time');
  time.textContent = t.text;
  time.dateTime = t.iso;
  root.title = `Jobs feed last updated ${t.long}`;
  root.hidden = false;
  return true;
}
