// Live cams and jobs in the resort and cluster panels (docs/map/ARCHITECTURE.md, "Webcams and jobs").
// Links only: cams, streams and job pages open in a new tab and are never embedded. Open roles come from
// jobs/postings.json (public job board APIs, refreshed daily) and load only for resorts whose employers have a feed.
// Keep the markup in step with pipeline/routes.py (render_webcams, render_jobs).
import { loadPostings } from './data.js';
import { DISCIPLINE_LABEL, JOB_GROUPS, WEBCAM_KIND } from './labels.js';
import { h, safeUrl, formatIsoDate, plural, humanize } from './ui.js';

const NEW_TAB = { target: '_blank', rel: 'noopener noreferrer' };
const MAX_TITLES = 5;
const GROUP_ORDER = Object.fromEntries(JOB_GROUPS.map(([k], i) => [k, i]));

function host(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return ''; }
}

function outLink(url, text, cls) {
  return h('a', { href: url, ...NEW_TAB, class: cls }, text);
}

/** "Last verified <date>", plus the source when the source is another page than the link itself. */
function verified(item, ctx) {
  const src = ctx.sources.get(item.source_id);
  const other = !src || src.url !== item.url;
  return [`Last verified ${formatIsoDate(item.as_of)}`, other ? [' · ', ctx.sourceLink(item.source_id)] : null];
}

/**
 * The "Live cams" row: "Open live cams" (the resort's cams page) and its named cams. Null without cams.
 * ctx: { badge, sourceLink, sources }.
 */
export function liveCams(r, ctx) {
  const w = r.webcams;
  if (!w) return null;
  const page = w.cams_page ? safeUrl(w.cams_page.url) : null;
  const cams = w.cams.map((c) => ({ ...c, href: safeUrl(c.url) })).filter((c) => c.href);
  if (!page && !cams.length) return null;
  return h('div', { class: 'live-cams' },
    h('p', { class: 'sub live-cams-title' }, 'Live cams'),
    page ? h('p', { class: 'live-cams-main' },
      outLink(page, 'Open live cams', 'live-cams-link'), ' ', ctx.badge(w.cams_page.confidence), ' ',
      h('span', { class: 'fact-meta' }, `${host(page)} · opens in a new tab · `, verified(w.cams_page, ctx))) : null,
    cams.length ? h('ul', { class: 'cam-list', 'aria-label': 'Named cams' }, cams.map((c) => h('li', null,
      outLink(c.href, c.label, 'cam-link'), ' ', ctx.badge(c.confidence), ' ',
      h('span', { class: 'fact-meta' }, WEBCAM_KIND[c.kind] || humanize(c.kind))))) : null);
}

/**
 * The "Jobs" section: links grouped as Ski school, Coaching and clubs, Management, Parent company and Job boards,
 * with open roles on top once jobs/postings.json has matches. ctx: { badge, sourceLink, sources, current }.
 */
export function jobsSection(r, ctx) {
  const jobs = r.jobs;
  const hw = jobs && jobs.hiring_window;
  // The source's own words on when hiring or training happens (ROADMAP item 6), never reworded.
  const window = hw ? h('p', { class: 'jobs-window' }, h('strong', null, 'Hiring window:'), ` ${hw.text} `, ctx.badge(hw.confidence),
    ' ', h('span', { class: 'fact-meta' }, verified(hw, ctx))) : null;
  if (!jobs || !jobs.links.length) {
    return r.tier === 'A' || window ? h('section', { class: 'panel-section jobs' }, h('h3', null, 'Jobs'), window,
      h('p', { class: 'not-verified' }, 'Jobs links are being collected.')) : null;
  }
  const live = h('div', { class: 'jobs-live', hidden: true });
  const groups = JOB_GROUPS.map(([key, title]) => {
    const items = jobs.links.map((l) => ({ ...l, href: safeUrl(l.url) })).filter((l) => l.role_group === key && l.href);
    if (!items.length) return null;
    return h('div', { class: 'job-group', dataset: { group: key } },
      h('h4', { class: 'sub' }, title),
      h('ul', { class: 'job-links' }, items.map((l) => h('li', null,
        outLink(l.href, l.label, 'job-link'), ' ', ctx.badge(l.confidence),
        l.disciplines.length ? h('span', { class: 'job-tags' }, h('span', { class: 'visually-hidden' }, 'Disciplines: '),
          l.disciplines.map((d) => h('span', { class: 'tag' }, DISCIPLINE_LABEL[d] || humanize(d)))) : null,
        h('span', { class: 'fact-meta' }, verified(l, ctx))))));
  });
  if (jobs.feed) {
    loadPostings().then((file) => {
      if (!file || !ctx.current()) return;
      const block = openRoles(file, r, ctx);
      if (block) { live.append(...block); live.hidden = false; }
    });
  }
  return h('section', { class: 'panel-section jobs' }, h('h3', null, 'Jobs'), window,
    h('p', { class: 'fact-meta' }, "Links open each employer's own site in a new tab."), live, groups);
}

/** Boards keyed by employer, and the earliest fetch date among the boards used ("Updated <date>"). */
function boardsOf(file) { return new Map(file.boards.map((b) => [b.employer_id, b])); }
function updated(boards, ids) {
  const dates = [...ids].map((id) => boards.get(id)).filter(Boolean)
    .map((b) => b.as_of || String(b.fetched_at || '').slice(0, 10)).filter(Boolean).sort();
  return dates.length ? formatIsoDate(dates[0]) : null;
}

/** "N open roles" with up to five titles, roles of the resort's multi resort employers, and the date. */
export function openRoles(file, r, ctx) {
  const employers = new Set(r.jobs.links.map((l) => l.employer_id));
  const boards = boardsOf(file);
  const mine = file.postings.filter((p) => employers.has(p.employer_id) && boards.has(p.employer_id) && safeUrl(p.url));
  const here = mine.filter((p) => p.resort_ids.includes(r.id))
    .sort((a, b) => ((GROUP_ORDER[a.role_group] ?? 9) - (GROUP_ORDER[b.role_group] ?? 9)) || a.title.localeCompare(b.title));
  const wide = new Map();
  for (const p of mine) if (!p.resort_ids.length) wide.set(p.employer_id, (wide.get(p.employer_id) || 0) + 1);
  if (!here.length && !wide.size) return null;
  const out = [];
  if (here.length) {
    out.push(h('p', { class: 'jobs-open sourced-main' },
      h('span', { class: 'fact-value' }, plural(here.length, 'open role', 'open roles')), ctx.badge('published')));
    out.push(h('ul', { class: 'posting-list' }, here.slice(0, MAX_TITLES).map((p) => h('li', null,
      outLink(safeUrl(p.url), p.title, 'posting-link'),
      p.location ? h('span', { class: 'fact-meta' }, ` ${p.location}`) : null))));
    if (here.length > MAX_TITLES) {
      out.push(h('p', { class: 'fact-meta' }, `${plural(here.length - MAX_TITLES, 'more role', 'more roles')} on the job boards below.`));
    }
  }
  for (const [id, n] of wide) {
    const b = boards.get(id);
    out.push(h('p', { class: 'fact-meta jobs-wide' }, `${here.length ? 'Plus ' : ''}${plural(n, 'role', 'roles')} at `,
      outLink(safeUrl(b.url), b.employer_name, 'src-link'), ' with no single resort named.'));
  }
  const used = new Set([...here.map((p) => p.employer_id), ...wide.keys()]);
  const when = updated(boards, used);
  out.push(h('p', { class: 'fact-meta jobs-updated' }, when ? `Updated ${when} from ` : 'From ',
    [...used].map((id, i) => [i ? ', ' : '', outLink(safeUrl(boards.get(id).url), `${boards.get(id).employer_name} job board`, 'src-link')]),
    '.'));
  return out;
}

/**
 * Compact "Hiring" section for a cluster: employers with open roles at its resorts. Hidden until postings load
 * with matches. ctx: { badge, current }.
 */
export function clusterHiring(resorts, ctx) {
  if (!resorts.some((r) => r.jobs_available)) return null;
  const el = h('section', { class: 'panel-section hiring', hidden: true });
  loadPostings().then((file) => {
    if (!file || !ctx.current()) return;
    const ids = new Set(resorts.map((r) => r.id));
    const boards = boardsOf(file);
    const counts = new Map();
    for (const p of file.postings) {
      if (boards.has(p.employer_id) && p.resort_ids.some((id) => ids.has(id))) counts.set(p.employer_id, (counts.get(p.employer_id) || 0) + 1);
    }
    if (!counts.size) return;
    const rows = [...counts].sort((a, b) => b[1] - a[1] || boards.get(a[0]).employer_name.localeCompare(boards.get(b[0]).employer_name));
    const when = updated(boards, counts.keys());
    el.append(h('h3', null, 'Hiring'),
      h('ul', { class: 'hiring-list' }, rows.map(([id, n]) => h('li', null,
        outLink(safeUrl(boards.get(id).url), boards.get(id).employer_name, 'job-link'), ' ', ctx.badge('published'),
        h('span', { class: 'fact-meta' }, ` ${plural(n, 'open role', 'open roles')}`)))),
      h('p', { class: 'fact-meta' }, 'Instructor, coaching and management roles from public job boards.',
        when ? ` Updated ${when}.` : ''));
    el.hidden = false;
  });
  return el;
}
