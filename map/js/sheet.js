// Phone bottom sheet with peek, half and full states. Desktop: inert.
import { isPhone, prefersReducedMotion } from './config.js';

const ORDER = ['peek', 'half', 'full'];
const LABEL = { peek: 'Panel collapsed', half: 'Panel half open', full: 'Panel fully open' };
const PEEK_PX = 132;
const FULL_GAP_PX = 44;

export function createSheet(panel, handle, stage, { onChange } = {}) {
  let state = 'half';
  let dragging = null;

  function heights() {
    const total = stage.clientHeight;
    return { peek: Math.min(PEEK_PX, total), half: Math.round(total * 0.5), full: Math.max(0, total - FULL_GAP_PX), total };
  }

  function visible() {
    if (!isPhone()) return 0;
    return heights()[state];
  }

  function place(px, animate) {
    const { total } = heights();
    panel.style.transition = animate && !prefersReducedMotion() ? '' : 'none';
    panel.style.setProperty('--sheet-offset', `${Math.max(0, total - px)}px`);
  }

  function set(next, { animate = true, silent = false } = {}) {
    state = next;
    panel.dataset.sheet = state;
    handle.setAttribute('aria-label', `${LABEL[state]}. Activate to resize.`);
    handle.setAttribute('aria-expanded', String(state !== 'peek'));
    if (isPhone()) {
      place(heights()[state], animate);
      // The map controls sit just above the sheet (map.css); set on the map only, so a drag restyles nothing else.
      const map = stage.querySelector('#map');
      if (map) map.style.setProperty('--sheet-visible', `${heights()[state]}px`);
    } else panel.style.removeProperty('--sheet-offset');
    if (state === 'peek') {
      const scroller = panel.querySelector('.panel-scroll');
      if (scroller) scroller.scrollTop = 0; // peek always shows the heading
    }
    if (!silent && onChange) onChange(state);
  }

  handle.addEventListener('pointerdown', (e) => {
    if (!isPhone()) return;
    dragging = { y: e.clientY, start: heights()[state], moved: false, id: e.pointerId };
    handle.setPointerCapture(e.pointerId);
  });
  handle.addEventListener('pointermove', (e) => {
    if (!dragging || e.pointerId !== dragging.id) return;
    const dy = dragging.y - e.clientY;
    if (Math.abs(dy) > 4) dragging.moved = true;
    const { peek, full } = heights();
    place(Math.min(full, Math.max(peek * 0.6, dragging.start + dy)), false);
  });
  const end = (e) => {
    if (!dragging || e.pointerId !== dragging.id) return;
    const moved = dragging.moved;
    const dy = dragging.y - e.clientY;
    const target = dragging.start + dy;
    dragging = null;
    if (!moved) return; // click handler below cycles the state
    const hs = heights();
    const nearest = ORDER.reduce((best, k) => (Math.abs(hs[k] - target) < Math.abs(hs[best] - target) ? k : best), 'peek');
    handle.dataset.dragged = '1';
    set(nearest);
  };
  handle.addEventListener('pointerup', end);
  handle.addEventListener('pointercancel', end);

  handle.addEventListener('click', () => {
    if (handle.dataset.dragged) { delete handle.dataset.dragged; return; }
    const i = ORDER.indexOf(state);
    set(i === ORDER.length - 1 ? 'peek' : ORDER[i + 1]);
  });
  handle.addEventListener('keydown', (e) => {
    const i = ORDER.indexOf(state);
    if (e.key === 'ArrowUp') { e.preventDefault(); set(ORDER[Math.min(ORDER.length - 1, i + 1)]); }
    if (e.key === 'ArrowDown') { e.preventDefault(); set(ORDER[Math.max(0, i - 1)]); }
  });

  window.addEventListener('resize', () => set(state, { animate: false, silent: true }));
  set(state, { animate: false, silent: true });

  return {
    get state() { return state; },
    visible,
    set,
    /** After navigation make sure the panel content is readable. */
    reveal() { if (isPhone() && state === 'peek') set('half'); },
  };
}
