// Hiring now (docs/map/ARCHITECTURE.md "Hiring now filter"): which resorts are hiring this month, and the chip's
// count. Loaded after the first paint (or on the chip's first click), so the startup path carries none of it.
import { postingsRaw } from './data.js';

export const NONE_TITLE = 'No resort lists an open role or a hiring window for this month.';
const YEAR_MONTH = /^20\d\d-(0[1-9]|1[0-2])$/;
const ids = (v) => (Array.isArray(v) ? v.filter((x) => typeof x === 'string') : []);

/** [start_month, end_month] (YYYY-MM, start first) from the slim index, or null. */
export function monthWindow(v) {
  return Array.isArray(v) && v.length === 2 && YEAR_MONTH.test(v[0]) && YEAR_MONTH.test(v[1]) && v[0] <= v[1] ? v : null;
}

/** True when the viewer's month falls in the window, both ends included. Mirrors pipeline/hiring.py covers(). */
export function windowCovers(w, date) {
  const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
  return Boolean(monthWindow(w)) && w[0] <= key && key <= w[1];
}

/**
 * Ids of resorts with an open role in jobs/postings.json at one of their job_employers that names the resort or no
 * single resort. Only postings of employers with a board and an https url count, as in the panel's open roles.
 */
export function openRoleIds(resorts, postings) {
  const out = new Set();
  const boards = new Set((postings && Array.isArray(postings.boards) ? postings.boards : []).map((b) => b && b.employer_id));
  const named = new Map(); // employer id -> resort ids its postings name ('' for no single resort)
  for (const p of postings && Array.isArray(postings.postings) ? postings.postings : []) {
    if (!p || !boards.has(p.employer_id) || !Array.isArray(p.resort_ids) || typeof p.url !== 'string' || !p.url.startsWith('https://')) continue;
    if (!named.has(p.employer_id)) named.set(p.employer_id, new Set());
    const set = named.get(p.employer_id);
    if (!p.resort_ids.length) set.add('');
    for (const id of p.resort_ids) set.add(id);
  }
  for (const r of resorts) {
    if (ids(r.job_employers).some((e) => named.has(e) && (named.get(e).has(r.id) || named.get(e).has('')))) out.add(r.id);
  }
  return out;
}

/** The whole rule: a hiring window covers `date`, or an open role (openRoleIds). */
export function hiringNowIds(resorts, postings, date) {
  const out = openRoleIds(resorts, postings);
  for (const r of resorts) if (windowCovers(r.hiring_window, date)) out.add(r.id);
  return out;
}

/**
 * Set hiring_now on the slim records and index.hiringIds (sorted), then call update(n) so the controls can say
 * "Hiring now (n)" (app.js: the layers panel option and the header pill; at 0 they stay visible, disabled, with
 * NONE_TITLE). Returns n.
 */
export function markHiring(index, postings, update, date = new Date()) {
  const found = hiringNowIds(index.resortList, postings, date);
  for (const r of index.resortList) r.hiring_now = found.has(r.id);
  index.hiringIds = [...found].sort();
  if (typeof update === 'function') update(found.size, NONE_TITLE);
  return found.size;
}

/** The raw postings file when some resort has job_employers and the feed has postings, else null (no request). */
export function feedPostings(index) {
  if (!index.resortList.some((r) => ids(r.job_employers).length)) return Promise.resolve(null);
  return postingsRaw().then((f) => (f && Array.isArray(f.postings) && f.postings.length ? f : null));
}
