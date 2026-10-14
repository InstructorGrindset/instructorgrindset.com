// Housing links in the resort panel (docs/map/ARCHITECTURE.md, "Housing search"). Loaded on the first resort view,
// so the national map's startup path carries none of it.
// Links only: Furnished Finder listings and every housing tool open in a new tab and are never copied or embedded.
// Keep the markup in step with pipeline/routes.py render_housing.
import { h, safeUrl, formatIsoDate, plural, humanize } from './ui.js';

const NEW_TAB = { target: '_blank', rel: 'noopener noreferrer' };

export const TOOL_KIND = {
  employer_portal: 'Employer portal',
  housing_authority: 'Housing authority',
  listing_site: 'Listing site',
  facebook_group: 'Facebook group',
  craigslist: 'Craigslist',
  property_manager: 'Property manager',
  app: 'App',
  other: 'Other',
};

function host(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return ''; }
}

/** "Last verified <date>", plus the source when the source is another page than the link itself. */
function verified(item, ctx) {
  const src = ctx.sources.get(item.source_id);
  const other = !src || src.url !== item.url;
  return [`Last verified ${formatIsoDate(item.as_of)}`, other ? [' · ', ctx.sourceLink(item.source_id)] : null];
}

/**
 * "Furnished rentals": a link to the Furnished Finder listings near the resort's town. Only with a sourced https url.
 * ctx: { badge, sourceLink, sources }.
 */
export function furnishedRentals(r, ctx) {
  const ff = r.housing_search && r.housing_search.furnished_finder;
  const url = ff && /^https:\/\//.test(ff.url) ? safeUrl(ff.url) : null;
  if (!url) return null;
  return h('div', { class: 'housing-block ff' },
    h('h4', { class: 'sub' }, 'Furnished rentals'),
    h('p', { class: 'ff-main' },
      h('a', { href: url, ...NEW_TAB, class: 'ff-link' }, `Furnished Finder listings near ${ff.town}`), ' ', ctx.badge(ff.confidence), ' ',
      h('span', { class: 'fact-meta' }, `${host(url)} · opens in a new tab · `, verified(ff, ctx))),
    ff.note ? h('p', { class: 'fact-note' }, ff.note) : null);
}

/**
 * "Where people find housing here": the tools the sources name for this resort, then for its cluster, ranked by how
 * many independent sources name each one (pipeline/housing_search.py). Each shows its kind, the sources' reason,
 * "Named by N sources" and the source links. Tier A resorts without tools say they are being collected.
 */
export function housingTools(r, ctx) {
  const tools = ((r.housing_search && r.housing_search.tools) || [])
    .map((t) => ({ ...t, href: /^https:\/\//.test(t.url) ? safeUrl(t.url) : null })).filter((t) => t.href);
  if (!tools.length) {
    return r.tier === 'A' ? h('div', { class: 'housing-block tools' },
      h('h4', { class: 'sub' }, 'Where people find housing here'),
      h('p', { class: 'not-verified' }, 'Housing search tools are being collected.')) : null;
  }
  return h('div', { class: 'housing-block tools' },
    h('h4', { class: 'sub' }, 'Where people find housing here'),
    h('p', { class: 'fact-meta' }, 'Ranked by how many independent sources name each one. Links open in a new tab.'),
    h('ol', { class: 'housing-tools' }, tools.map((t) => h('li', { class: 'housing-tool', dataset: { scope: t.scope || '' } },
      h('p', { class: 'tool-head' },
        h('a', { href: t.href, ...NEW_TAB, class: 'tool-link' }, t.name), ' ',
        h('span', { class: 'tag tool-kind' }, TOOL_KIND[t.kind] || humanize(t.kind)), ' ', ctx.badge(t.confidence)),
      h('p', { class: 'tool-why' }, t.why),
      t.note ? h('p', { class: 'fact-note' }, t.note) : null,
      // named_by counts publishers; two pages of one site count once.
      h('p', { class: 'fact-meta tool-sources' }, `Named by ${plural(t.named_by, 'source', 'sources')}`,
        t.source_ids.length > t.named_by ? ` across ${plural(t.source_ids.length, 'page', 'pages')}` : '', ': ',
        t.source_ids.map((id, i) => [i ? ', ' : '', ctx.sourceLink(id)]),
        ` · Last verified ${formatIsoDate(t.as_of)}`)))));
}
