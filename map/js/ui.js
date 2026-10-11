// Small DOM and formatting helpers. All data text goes through textContent.

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, String(v));
    }
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

/** Only http(s) URLs become links. */
export function safeUrl(url) {
  try {
    const u = new URL(url, window.location.origin);
    return u.protocol === 'https:' || u.protocol === 'http:' ? u.href : null;
  } catch {
    return null;
  }
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MONTHS_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
  'September', 'October', 'November', 'December'];

export function monthShort(m) { return MONTHS[m - 1] || String(m); }
export function monthLong(m) { return MONTHS_LONG[m - 1] || String(m); }

/** "2026-10-11" -> "Oct 11, 2026" without timezone drift. */
export function formatIsoDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
  if (!m) return String(iso || '');
  return `${MONTHS[Number(m[2]) - 1]} ${Number(m[3])}, ${m[1]}`;
}

/** "11-20" (MM-DD) -> "Nov 20". */
export function formatMonthDay(mmdd) {
  const m = /^(\d{2})-(\d{2})$/.exec(String(mmdd || ''));
  if (!m) return String(mmdd);
  return `${MONTHS[Number(m[1]) - 1]} ${Number(m[2])}`;
}

const nf0 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });

export function fmtInt(n) { return nf0.format(n); }
export function fmt1(n) { return nf1.format(n); }

export function plural(n, one, many) { return `${fmtInt(n)} ${n === 1 ? one : many}`; }

/** "powder_hound" -> "Powder hound" */
export function humanize(tag) {
  const s = String(tag).replace(/[_-]+/g, ' ').trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** ["California", "Nevada", "Utah"] -> "California, Nevada and Utah" */
export function joinAnd(items) {
  if (items.length <= 1) return items.join('');
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}

export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
