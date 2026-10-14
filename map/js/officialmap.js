// Official trail map viewer for the trail map dialog (map/js/trailmap.js), loaded with it on first open.
// The resort's own map file is shown straight from the resort's servers (hotlinked, never copied or modified):
// an image in a zoomable, pannable viewer, or a PDF in an <object>. Contract: docs/map/ARCHITECTURE.md,
// "Official trail maps".
import { h } from './ui.js';

const ZOOM_STEP = 1.5;
const PAN_STEP = 0.12;          // share of the viewport per arrow key press (three times with Shift)
const PDF_TIMEOUT_MS = 15000;   // a PDF that has not loaded by then counts as blocked

/**
 * Zoomable, pannable image: mouse wheel, pinch, drag, double click, plus and minus buttons, reset and the arrow,
 * plus, minus and 0 keys. onLoad and onError fire once. state() reports { scale, fit, x, y } for tests.
 */
export function createImageViewer({ url, alt, label, helpId, onLoad = () => {}, onError = () => {} }) {
  const img = h('img', { class: 'tmo-img', alt, loading: 'lazy', decoding: 'async', referrerpolicy: 'no-referrer', draggable: 'false' });
  const viewport = h('div', {
    class: 'tmo-viewport', tabindex: '0', role: 'region', 'aria-roledescription': 'zoomable image',
    'aria-label': label, 'aria-describedby': helpId,
  }, img);
  const btn = (text, aria, fn) => h('button', { type: 'button', class: 'btn btn-sm tmo-btn', 'aria-label': aria, title: aria, onclick: fn }, text);
  const zoomIn = btn('+', 'Zoom in', () => zoomBy(ZOOM_STEP));
  const zoomOut = btn('−', 'Zoom out', () => zoomBy(1 / ZOOM_STEP));
  const reset = btn('Reset', 'Reset the view', () => fit());
  const tools = h('div', { class: 'tmo-tools', role: 'group', 'aria-label': 'Zoom' }, zoomIn, zoomOut, reset);
  const el = h('div', { class: 'tmo-viewer' }, viewport, tools);

  let nw = 0; let nh = 0;            // natural size of the image
  let s = 1; let x = 0; let y = 0;   // scale and offset of the image's top left corner in viewport pixels
  let fitScale = 1; let atFit = true; let ready = false; let done = false;
  const pointers = new Map();
  let pinch = null;

  const size = () => ({ w: viewport.clientWidth, h: viewport.clientHeight });
  const limits = () => ({ min: fitScale, max: Math.max(fitScale * 6, 2) });

  function clamp() {
    const { w, h: vh } = size();
    const sw = nw * s; const sh = nh * s;
    x = sw <= w ? (w - sw) / 2 : Math.min(0, Math.max(w - sw, x));
    y = sh <= vh ? (vh - sh) / 2 : Math.min(0, Math.max(vh - sh, y));
  }
  function apply() {
    clamp();
    img.style.transform = `translate(${x}px, ${y}px) scale(${s})`;
    const { min, max } = limits();
    zoomOut.disabled = s <= min + 1e-6;
    zoomIn.disabled = s >= max - 1e-6;
  }
  function fit() {
    if (!ready) return;
    const { w, h: vh } = size();
    if (!w || !vh) return;
    fitScale = Math.min(w / nw, vh / nh);
    s = fitScale; x = 0; y = 0; atFit = true;
    apply();
  }
  /** Zoom by factor f about viewport point (cx, cy); the center by default. */
  function zoomBy(f, cx, cy) {
    if (!ready) return;
    const { w, h: vh } = size();
    const px = cx ?? w / 2; const py = cy ?? vh / 2;
    const { min, max } = limits();
    const next = Math.min(max, Math.max(min, s * f));
    x = px - (px - x) * (next / s);
    y = py - (py - y) * (next / s);
    s = next; atFit = Math.abs(s - fitScale) < 1e-6;
    apply();
  }
  function panBy(dx, dy) { if (!ready) return; x += dx; y += dy; apply(); }
  const local = (e) => { const r = viewport.getBoundingClientRect(); return { px: e.clientX - r.left, py: e.clientY - r.top }; };

  viewport.addEventListener('wheel', (e) => {
    if (!ready) return;
    e.preventDefault();
    const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaMode === 2 ? e.deltaY * 400 : e.deltaY;
    const { px, py } = local(e);
    zoomBy(Math.exp(-dy * 0.0015), px, py);
  }, { passive: false });
  viewport.addEventListener('dblclick', (e) => { const { px, py } = local(e); zoomBy(e.shiftKey ? 1 / 2 : 2, px, py); });
  viewport.addEventListener('pointerdown', (e) => {
    if (!ready || (e.pointerType === 'mouse' && e.button !== 0)) return;
    try { viewport.setPointerCapture(e.pointerId); } catch { /* a pointer the browser no longer tracks */ }
    pointers.set(e.pointerId, local(e));
    viewport.classList.add('is-dragging');
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      pinch = { d: Math.hypot(a.px - b.px, a.py - b.py) || 1, mx: (a.px + b.px) / 2, my: (a.py + b.py) / 2 };
    }
  });
  viewport.addEventListener('pointermove', (e) => {
    if (!pointers.has(e.pointerId)) return;
    const prev = pointers.get(e.pointerId);
    const now = local(e);
    pointers.set(e.pointerId, now);
    if (pointers.size >= 2 && pinch) {
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a.px - b.px, a.py - b.py) || 1;
      const mx = (a.px + b.px) / 2; const my = (a.py + b.py) / 2;
      x += mx - pinch.mx; y += my - pinch.my;
      zoomBy(d / pinch.d, mx, my);
      pinch = { d, mx, my };
    } else if (pointers.size === 1) {
      panBy(now.px - prev.px, now.py - prev.py);
    }
  });
  const release = (e) => {
    pointers.delete(e.pointerId);
    if (pointers.size < 2) pinch = null;
    if (!pointers.size) viewport.classList.remove('is-dragging');
  };
  viewport.addEventListener('pointerup', release);
  viewport.addEventListener('pointercancel', release);
  viewport.addEventListener('keydown', (e) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const { w, h: vh } = size();
    const k = PAN_STEP * (e.shiftKey ? 3 : 1);
    const keys = {
      ArrowLeft: () => panBy(w * k, 0), ArrowRight: () => panBy(-w * k, 0),
      ArrowUp: () => panBy(0, vh * k), ArrowDown: () => panBy(0, -vh * k),
      '+': () => zoomBy(ZOOM_STEP), '=': () => zoomBy(ZOOM_STEP), '-': () => zoomBy(1 / ZOOM_STEP), _: () => zoomBy(1 / ZOOM_STEP),
      0: () => fit(),
    };
    const fn = keys[e.key];
    if (!fn) return;
    e.preventDefault();
    fn();
  });
  // Refit on resize while the view is at fit; otherwise keep the view inside the frame.
  const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(() => { if (!ready) return; if (atFit) fit(); else apply(); }) : null;
  if (ro) ro.observe(viewport);

  img.addEventListener('load', () => {
    if (done) return;
    nw = img.naturalWidth; nh = img.naturalHeight;
    if (!nw || !nh) { done = true; onError(); return; }
    done = true; ready = true;
    el.classList.add('is-ready');
    fit();
    onLoad();
  });
  img.addEventListener('error', () => { if (done) return; done = true; onError(); });
  img.src = url; // after referrerpolicy and loading, so the first request already has them

  apply();
  return {
    el,
    viewport,
    img,
    fit,
    zoomBy,
    panBy,
    /** Re-measure after the panel was hidden. */
    resize() { if (ready) { if (atFit) fit(); else apply(); } },
    state: () => ({ scale: s, fit: fitScale, x, y, ready }),
    destroy() { if (ro) ro.disconnect(); img.removeAttribute('src'); },
  };
}

/** Whether this browser can show a PDF inside the page (false on most phones and in headless Chromium). */
export function canEmbedPdf() {
  return typeof navigator !== 'undefined' && navigator.pdfViewerEnabled !== false;
}

/**
 * The PDF in an <object> with a fallback link. onError fires once when the object reports an error (an HTTP error,
 * a refused frame) or has not loaded after PDF_TIMEOUT_MS; onLoad when it has loaded.
 */
export function createPdfViewer({ url, label, linkText, onLoad = () => {}, onError = () => {} }) {
  let done = false;
  const finish = (ok) => { if (done) return; done = true; clearTimeout(timer); (ok ? onLoad : onError)(); };
  const obj = h('object', { class: 'tmo-pdf', type: 'application/pdf', 'aria-label': label },
    h('p', { class: 'tmo-pdf-fallback' }, 'This browser cannot show the PDF here. ',
      h('a', { href: url, target: '_blank', rel: 'noopener noreferrer' }, linkText)));
  obj.addEventListener('load', () => finish(true));
  obj.addEventListener('error', () => finish(false));
  const timer = setTimeout(() => finish(false), PDF_TIMEOUT_MS);
  obj.data = url; // after the listeners
  return { el: h('div', { class: 'tmo-viewer tmo-viewer-pdf' }, obj), obj, destroy() { clearTimeout(timer); done = true; } };
}
