// Side panel (desktop) / bottom sheet (phone) content for each level.
import { config } from './config.js';
import { clusterResorts, clusterOrder, loadClimate, loadDetail, facilityCounts } from './data.js';
import {
  FACILITY_LABEL, criterionLabel, facilityPhrase, CERT_LABEL, CERT_LEVELS, RATE_UNIT, ACCESS_LABEL, tagLabel,
} from './labels.js';
import { hrefFor } from './router.js';
import {
  h, clear, safeUrl, formatIsoDate, formatMonthDay, fmtInt, fmt1, plural, humanize, joinAnd, markdown,
} from './ui.js';

// Chart.js and the chart code load only when a resort or comparison shows climate data.
let chartsMod = null;
let chartsPromise = null;
function charts() {
  if (!chartsPromise) chartsPromise = import('./charts.js').then((m) => { chartsMod = m; return m; });
  return chartsPromise;
}
function destroyCharts() { if (chartsMod) chartsMod.destroyCharts(); }

// Same links as the static pages written by pipeline/routes.py.
const RELATED = [
  ['/blog', 'Blog'], ['/reading-list', 'The Reading List'], ['/income-calculator', 'Season Income Calculator'],
  ['/career-plan', 'Plan Your Career'], ['/priority-predictor', 'Priority Predictor'],
];

const CONFIDENCE_LABEL = { published: 'Published', reported: 'Reported', estimated: 'Estimated' };
const CONFIDENCE_HELP = {
  published: 'Published by the resort or another primary source',
  reported: 'Aggregated from three or more approved reports',
  estimated: 'Estimated from a stated model',
};

export const fmt = {
  int: (v) => fmtInt(v),
  ft: (v) => `${fmtInt(v)} ft`,
  days: (v) => `${fmtInt(v)} days`,
  pct: (v) => `${fmt1(v)}%`,
  date: (v) => formatMonthDay(v),
  text: (v) => String(v),
};

const nf2 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 });
const usd0 = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const usd2 = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 });
/** Whole dollars, or cents when digits is set and the value has a fraction. */
export function money(v, digits = 0) { return (digits && !Number.isInteger(v) ? usd2 : usd0).format(v); }
export function moneyRange(low, high, digits = 0) {
  if (low == null && high == null) return 'Not yet verified';
  if (low == null || high == null || low === high) return money(low ?? high, digits);
  return `${money(low, digits)} to ${money(high, digits)}`;
}

/**
 * Operator scale 1, 2 or 3 (owner decision 5): resort.operator_scale.value, else career.professionalism.score when
 * the build carries the scale there. null when unrated.
 */
export function operatorScale(r) {
  const ok = (v) => (v === 1 || v === 2 || v === 3 ? v : null);
  const o = r.operator_scale && ok(r.operator_scale.value);
  if (o) return o;
  const p = r.career && r.career.professionalism;
  return p ? ok(p.score) : null;
}

export function badge(confidence) {
  return h('span', { class: `badge badge-${confidence}`, title: CONFIDENCE_HELP[confidence] }, CONFIDENCE_LABEL[confidence] || confidence);
}

function navLink(view, attrs, ...children) {
  return h('a', { href: hrefFor(view), 'data-nav': '', ...attrs }, ...children);
}

export function createPanel(root, index, {
  onCompare, onDetail = () => {}, onSiteSelect = () => {}, onTrailMap = null, onResort = () => {},
}) {
  let token = 0;
  let siteButtons = new Map();
  let currentDetail = null;
  let compareState = { clusterId: null, open: false, selected: [] };

  function sourceLink(id) {
    // Sources arrive with the full data after the first paint; fillSources() swaps these placeholders.
    if (!index.detailsReady && !index.sources.has(id)) return h('span', { class: 'muted src-pending', dataset: { src: id } }, 'Loading source');
    const s = index.sources.get(id);
    if (!s) return h('span', { class: 'muted' }, 'Source record pending');
    const url = safeUrl(s.url);
    const link = url ? h('a', { href: url, target: '_blank', rel: 'noopener noreferrer', class: 'src-link' }, s.title || id)
      : h('span', null, s.title || id);
    return s.note ? h('span', null, link, h('span', { class: 'src-note' }, ` (${s.note})`)) : link;
  }

  /** Value + confidence badge + last verified + source, or "Not yet verified". */
  function sourcedValue(src, format) {
    if (!src) return h('span', { class: 'not-verified' }, 'Not yet verified');
    return h('span', { class: 'sourced' },
      h('span', { class: 'sourced-main' }, h('span', { class: 'fact-value' }, format(src.value)), badge(src.confidence)),
      src.note ? h('span', { class: 'fact-note' }, src.note) : null,
      h('span', { class: 'fact-meta' }, `Last verified ${formatIsoDate(src.as_of)} · `, sourceLink(src.source_id)));
  }

  function fact(label, src, format, { stale = false } = {}) {
    const dd = h('dd', null, sourcedValue(src, format));
    if (src && stale) dd.querySelector('.sourced-main').append(staleTag());
    return h('div', { class: 'fact' }, h('dt', null, label), dd);
  }

  function staleTag() {
    return h('span', { class: 'tier tier-stale', title: 'Last verified more than the staleness limit ago' }, 'Needs update');
  }

  function section(title, ...body) {
    return h('section', { class: 'panel-section' }, h('h3', null, title), ...body);
  }

  function tags(list) {
    return h('ul', { class: 'tag-list' }, list.map((t) => h('li', { class: 'tag' }, tagLabel(t))));
  }

  function breadcrumb(view) {
    const items = [{ label: 'United States', view: { level: 'national' } }];
    if (view.stateCode) items.push({ label: index.states.get(view.stateCode).name, view: { level: 'state', stateCode: view.stateCode } });
    if (view.clusterId) items.push({ label: index.clusters.get(view.clusterId).name, view: { level: 'cluster', stateCode: view.stateCode, clusterId: view.clusterId } });
    if (view.resortId) items.push({ label: index.resorts.get(view.resortId).name, view });
    if (view.level === 'compare') items.push({ label: 'Compare', view });
    return h('nav', { class: 'crumbs', 'aria-label': 'Breadcrumb' },
      h('ol', null, items.map((it, i) => h('li', null, i === items.length - 1
        ? h('span', { 'aria-current': 'page' }, it.label)
        : navLink(it.view, null, it.label)))));
  }

  function heading(eyebrow, text) {
    return [h('span', { class: 'eyebrow' }, eyebrow), h('h2', { id: 'panel-heading', tabindex: '-1' }, text)];
  }

  function stats(items) {
    return h('dl', { class: 'stats' }, items.map(([label, value]) => h('div', { class: 'stat' }, h('dt', null, label), h('dd', null, value))));
  }

  /** Compact tags for lists: tier, facility type (when not a ski area), draft status. */
  function tierTags(r) {
    return [
      r.tier === 'A' ? h('span', { class: 'tier tier-a' }, 'Tier A') : h('span', { class: 'tier tier-b' }, 'Tier B'),
      r.facility_type !== 'ski_area' ? h('span', { class: `tier tier-facility fac-${r.facility_type}` }, FACILITY_LABEL[r.facility_type]) : null,
      r.tier === 'A' && r.tier_a_status === 'draft' ? h('span', { class: 'tier tier-draft', title: 'Tier A list awaiting approval' }, 'Draft profile') : null,
    ];
  }

  /** Full tags for the resort panel: adds the facility type for ski areas and the Approved state. */
  function resortTags(r) {
    const approved = r.tier === 'A' && r.tier_a_status === 'approved';
    const by = r.approval && r.approval.by ? ` by ${r.approval.by}` : '';
    const on = r.approval && r.approval.date ? ` on ${formatIsoDate(r.approval.date)}` : '';
    return [
      r.tier === 'A' ? h('span', { class: 'tier tier-a' }, 'Tier A') : h('span', { class: 'tier tier-b' }, 'Tier B'),
      h('span', { class: `tier tier-facility fac-${r.facility_type}` }, FACILITY_LABEL[r.facility_type]),
      approved ? h('span', { class: 'tier tier-approved', title: `Tier A profile approved${by}${on}` }, 'Approved') : null,
      r.tier === 'A' && r.tier_a_status === 'draft' ? h('span', { class: 'tier tier-draft', title: 'Tier A list awaiting approval' }, 'Draft profile') : null,
    ];
  }

  function membersOf(ids) { return ids.map((id) => index.resorts.get(id)).filter(Boolean); }
  function stateMembers(code) { return index.resortList.filter((r) => r.state === code); }

  // ── Levels ──

  function national() {
    const tierA = index.resortList.filter((r) => r.tier === 'A').length;
    const out = [
      ...heading('National view', 'United States'),
      h('p', { class: 'intro' }, 'Pick a state to see its resort clusters, then a cluster to see its resorts. Every number shows its source, its confidence level, and the date it was last verified.'),
    ];
    if (!index.stateList.length) {
      out.push(h('p', { class: 'empty' }, 'Resort data is being compiled. Check back soon.'));
      return out;
    }
    out.push(stats([
      ['States', fmtInt(index.stateList.length)],
      ['Clusters', fmtInt(index.clusters.size)],
      ['Resorts', fmtInt(index.resortList.length)],
      ['Tier A', fmtInt(tierA)],
    ]));
    out.push(h('p', { class: 'stats-note' }, `Includes ${facilityPhrase(facilityCounts(index.resortList))}.`));
    out.push(legend());
    out.push(section('States', h('ul', { class: 'nav-list' }, index.stateList.map((s) => h('li', null,
      navLink({ level: 'state', stateCode: s.code }, { class: 'nav-item' },
        h('span', { class: 'nav-name' }, s.name),
        h('span', { class: 'nav-meta' }, `${plural(s.cluster_ids.length, 'cluster', 'clusters')} · ${facilityPhrase(facilityCounts(stateMembers(s.code)))}`)))))));
    return out;
  }

  function legend() {
    return h('ul', { class: 'legend', 'aria-label': 'Map legend' },
      h('li', null, h('span', { class: 'swatch swatch-a', 'aria-hidden': 'true' }), 'Tier A deep profile'),
      h('li', null, h('span', { class: 'swatch swatch-b', 'aria-hidden': 'true' }), 'Tier B open data profile'),
      h('li', null, h('span', { class: 'swatch swatch-indoor', 'aria-hidden': 'true' }), 'Indoor ski facility'),
      h('li', null, h('span', { class: 'swatch swatch-training', 'aria-hidden': 'true' }), 'Year round training center'),
      h('li', null, h('span', { class: 'swatch swatch-cluster', 'aria-hidden': 'true' }), 'Cluster'),
      h('li', { class: 'legend-note' }, 'Amber marks Tier A and blue marks Tier B. Larger circles mean more skiable acres.'));
  }

  function state(view) {
    const s = index.states.get(view.stateCode);
    const clusters = s.cluster_ids.map((id) => index.clusters.get(id)).sort(clusterOrder);
    return [
      breadcrumb(view),
      ...heading('State', s.name),
      h('p', { class: 'intro' }, `${plural(clusters.length, 'cluster', 'clusters')} with ${facilityPhrase(facilityCounts(stateMembers(s.code)))}. Clusters are the labor markets instructors choose between, so some cross state lines.`),
      section('Clusters', h('ul', { class: 'nav-list' }, clusters.map((c) => {
        const others = c.states.filter((x) => x !== s.code).map((x) => (index.states.get(x) || { name: x }).name);
        return h('li', null, navLink({ level: 'cluster', stateCode: s.code, clusterId: c.id }, { class: 'nav-item' },
          h('span', { class: 'nav-name' }, c.name),
          h('span', { class: 'nav-meta' }, `${facilityPhrase(facilityCounts(membersOf(c.resort_ids)))} · ${fmtInt(c.tier_a_count)} Tier A`),
          others.length ? h('span', { class: 'nav-meta' }, `Shared with ${joinAnd(others)}`) : null));
      }))),
    ];
  }

  function cluster(view) {
    const c = index.clusters.get(view.clusterId);
    const resorts = clusterResorts(index, c);
    if (compareState.clusterId !== c.id) compareState = { clusterId: c.id, open: false, selected: [] };
    const list = h('ul', { class: 'nav-list resort-list' });
    const status = h('p', { class: 'compare-status', 'aria-live': 'polite' });
    const submit = h('button', { type: 'button', class: 'btn btn-sm', disabled: true }, 'Compare selected');
    const toggle = h('button', { type: 'button', class: 'btn btn-sm btn-ghost', 'aria-expanded': String(compareState.open), 'aria-controls': 'compare-bar' }, 'Compare resorts');
    const bar = h('div', { class: 'compare-bar', id: 'compare-bar', hidden: !compareState.open }, status, submit);

    function syncCompare() {
      const n = compareState.selected.length;
      status.textContent = n < config.compare.min
        ? `Pick ${config.compare.min} to ${config.compare.max} resorts. ${n} selected.`
        : `${n} of ${config.compare.max} selected.`;
      submit.disabled = n < config.compare.min;
      for (const box of list.querySelectorAll('input[type=checkbox]')) {
        box.disabled = !box.checked && n >= config.compare.max;
      }
    }

    function draw() {
      clear(list);
      for (const r of resorts) {
        const link = navLink({ level: 'resort', stateCode: view.stateCode, clusterId: c.id, resortId: r.id }, { class: 'nav-item' },
          h('span', { class: 'nav-name' }, r.name, ' ', tierTags(r)),
          h('span', { class: 'nav-meta' }, r.acres != null ? `${fmtInt(r.acres)} skiable acres` : 'Size not yet verified',
            r.climate_available ? ' · Climate charts' : ''));
        const li = h('li', { class: compareState.open ? 'with-check' : null });
        if (compareState.open) {
          const box = h('input', {
            type: 'checkbox', id: `cmp-${r.id}`, value: r.id, checked: compareState.selected.includes(r.id),
            onchange: (e) => {
              if (e.target.checked) compareState.selected = [...compareState.selected, r.id].slice(0, config.compare.max);
              else compareState.selected = compareState.selected.filter((x) => x !== r.id);
              syncCompare();
            },
          });
          li.append(h('label', { class: 'check', for: `cmp-${r.id}` }, box, h('span', { class: 'visually-hidden' }, `Select ${r.name} for comparison`)));
        }
        li.append(link);
        list.append(li);
      }
      syncCompare();
    }

    toggle.addEventListener('click', () => {
      compareState.open = !compareState.open;
      toggle.setAttribute('aria-expanded', String(compareState.open));
      toggle.textContent = compareState.open ? 'Cancel comparison' : 'Compare resorts';
      bar.hidden = !compareState.open;
      draw();
      if (compareState.open) { const first = list.querySelector('input'); if (first) first.focus(); }
    });
    if (compareState.open) toggle.textContent = 'Cancel comparison';
    submit.addEventListener('click', () => onCompare({ level: 'compare', stateCode: view.stateCode, clusterId: c.id, compare: [...compareState.selected] }));
    draw();

    const stateNames = c.states.map((x) => (index.states.get(x) || { name: x }).name);
    const counts = facilityCounts(resorts);
    return [
      breadcrumb(view),
      ...heading('Cluster', c.name),
      stats([['States', joinAnd(stateNames)], ['Resorts', fmtInt(resorts.length)], ['Tier A', fmtInt(c.tier_a_count)]]),
      h('p', { class: 'stats-note' }, `Includes ${facilityPhrase(counts)}.`),
      c.overview_md || c.alexandre_take_md
        ? editorialSection('About this cluster', [['Overview', c.overview_md], ["Alexandre's take", c.alexandre_take_md]], c.sources, null)
        : (c.sources.length ? h('p', { class: 'fact-meta' }, 'Cluster sources: ', c.sources.map((id, i) => [i ? ', ' : '', sourceLink(id)])) : null),
      h('section', { class: 'panel-section' },
        h('div', { class: 'section-head' }, h('h3', null, 'Resorts'), resorts.length >= config.compare.min ? toggle : null),
        bar, list),
    ];
  }

  /** Breadcrumb and heading from the slim index while the full record loads, then the full view. */
  function waitForDetails(view, my, eyebrow, title) {
    const load = view.level === 'resort' ? index.loadResort(view.resortId) : index.loadDetails();
    load.catch(() => null).then(() => {
      if (my !== token) return;
      const hadFocus = document.activeElement && document.activeElement.id === 'panel-heading';
      const notice = root.querySelector(':scope > .notice');
      const el = api.render(view, { keepToken: true });
      root.removeAttribute('aria-busy');
      performance.mark('careermap:panel-full');
      if (notice) root.prepend(notice);
      if (hadFocus && el) el.focus({ preventScroll: true });
    });
    return [breadcrumb(view), ...heading(eyebrow, title), h('p', { class: 'loading' }, 'Loading resort details')];
  }

  function unavailable(view, eyebrow, title) {
    return [breadcrumb(view), ...heading(eyebrow, title),
      h('p', { class: 'not-verified' }, 'The details for this resort could not load. Try reloading the page.')];
  }

  function resort(view, my) {
    const slim = index.resorts.get(view.resortId);
    const c = index.clusters.get(view.clusterId);
    const eyebrow = `Resort in ${c.name}, ${slim.state}`;
    if (!index.fullReady(view.resortId)) return waitForDetails(view, my, eyebrow, slim.name);
    const r = index.full(view.resortId);
    if (!r) return unavailable(view, eyebrow, slim.name);
    onResort(r);
    const climateRoot = h('div', { class: 'climate' });
    const out = [
      breadcrumb(view),
      ...heading(eyebrow, r.name),
      h('div', { class: 'tier-row' }, resortTags(r)),
      r.trailmap_available && onTrailMap
        ? h('p', { class: 'trailmap-open' }, h('button', {
          type: 'button', class: 'btn btn-sm btn-ghost', 'aria-haspopup': 'dialog',
          onclick: (e) => onTrailMap(r, e.currentTarget),
        }, 'Open the trail map'))
        : null,
      h('dl', { class: 'facts' }, fact('Owner', r.owner, fmt.text)),
    ];
    if (r.tier === 'A' && r.tier_a_criteria.length) {
      const approval = r.tier_a_status === 'approved' && r.approval
        ? h('p', { class: 'fact-meta' }, `Approved${r.approval.by ? ` by ${r.approval.by}` : ''}${r.approval.date ? ` on ${formatIsoDate(r.approval.date)}` : ''}.`)
        : null;
      out.push(section('Why Tier A', h('ul', { class: 'criteria' }, r.tier_a_criteria.map((k) => h('li', null,
        h('span', { class: 'criterion' }, criterionLabel(k.criterion)),
        k.rationale ? h('span', { class: 'criterion-why' }, k.rationale) : null,
        h('span', { class: 'fact-meta' }, sourceLink(k.source_id))))), approval));
    }
    if (r.editorial) out.push(editorialSection(`About ${r.name}`, [
      ['Overview', r.editorial.overview_md], ['Culture', r.editorial.culture_md], ["Alexandre's take", r.editorial.alexandre_take_md],
    ], r.editorial.source_ids, r.editorial.reviewed_date));
    out.push(careerSection(r));
    out.push(section('Operation size', h('dl', { class: 'facts' },
      fact('Skiable acres', r.size.acres, fmt.int),
      fact('Vertical drop', r.size.vertical_ft, fmt.ft),
      fact('Base elevation', r.elevation.base_ft, fmt.ft),
      fact('Summit elevation', r.elevation.summit_ft, fmt.ft),
      fact('Lifts', r.size.lifts, fmt.int),
      fact('Ski school staff', r.size.ski_school_staff_est, fmt.int))));
    out.push(section('Season', h('dl', { class: 'facts' },
      fact('Typical opening', r.season.open_typical, fmt.date),
      fact('Typical closing', r.season.close_typical, fmt.date),
      fact('Season length', r.season.length_days, fmt.days),
      fact('Staff training starts', r.season.training_start, fmt.date))));
    out.push(section('Terrain', h('dl', { class: 'facts' },
      fact('Beginner', r.terrain.beginner_pct, fmt.pct),
      fact('Intermediate', r.terrain.intermediate_pct, fmt.pct),
      fact('Advanced', r.terrain.advanced_pct, fmt.pct),
      fact('Expert', r.terrain.expert_pct, fmt.pct)),
      r.terrain.character_tags.length ? tags(r.terrain.character_tags) : null));
    const fitBits = [
      ['Client profile', r.clients.profile_tags],
      ['Best suited for skiers and riders', r.fit.enthusiast_types],
      ['Best suited for professionals', r.fit.professional_types],
    ].filter(([, l]) => l.length);
    if (fitBits.length) {
      out.push(section('Clients and fit', fitBits.map(([label, l]) => [h('p', { class: 'sub' }, label), tags(l)])));
    }
    out.push(housingSection(r, my));
    out.push(reportSection(r));
    out.push(section('Snow and climate', climateRoot));
    out.push(h('p', { class: 'fact-meta location' }, `Base area ${r.lat.toFixed(3)}, ${r.lon.toFixed(3)}`,
      r.location_source_id ? [' · ', sourceLink(r.location_source_id)] : null));

    if (!r.climate_available) {
      climateRoot.append(h('p', { class: 'not-verified' }, 'Climate data is being compiled.'));
    } else {
      climateRoot.append(h('p', { class: 'loading' }, 'Loading climate data'));
      loadClimate(r.id).then(async (climate) => {
        if (my !== token) return;
        clear(climateRoot);
        if (!climate) { climateRoot.append(h('p', { class: 'not-verified' }, 'Climate data is being compiled.')); return; }
        try {
          const ch = await charts();
          if (my !== token) return;
          climateRoot.append(climateHeader(climate, ch));
          await ch.renderResortCharts(climateRoot, climate, index.definitions);
        } catch (err) {
          console.warn('[map] charts', err.message);
          climateRoot.append(h('p', { class: 'not-verified' }, 'Charts could not load. Try reloading the page.'));
        }
      });
    }
    return out;
  }

  // ── Career (Phase 4) ──

  /** Professionalism (owner decision 5): the operator scale value 1 to 3, shown as the value only. */
  function professionalismBlock(r) {
    const v = operatorScale(r);
    return h('div', { class: 'score-block professionalism' },
      h('p', { class: 'sub' }, 'Professionalism'),
      v == null ? h('p', { class: 'not-verified' }, 'Not yet rated')
        : h('p', { class: 'fact-value score-value', 'aria-label': `Professionalism: ${v} of 3` }, `${v} of 3`));
  }

  function careerSection(r) {
    const c = r.career;
    if (!c) return section('Career', h('p', { class: 'not-verified' }, 'Career data is being collected.'), professionalismBlock(r));
    const stale = new Set(c.stale_fields);
    return section('Career',
      h('p', { class: 'sub' }, 'Hourly pay by certification'),
      payTable(c.pay, stale),
      h('dl', { class: 'facts career-facts' },
        fact('Private lesson rate', c.private_rate, fmtRate, { stale: stale.has('private_rate') }),
        fact('Instructor split', c.private_split, (v) => `${fmt1(v)}% to the instructor`, { stale: stale.has('private_split') })),
      professionalismBlock(r),
      h('div', { class: 'score-block' }, h('p', { class: 'sub' }, 'Lucrativeness, estimated savings per season'), lucrativeness(c.lucrativeness)));
  }

  function fmtRate(v) {
    const unit = RATE_UNIT[v.unit] || '';
    return `${moneyRange(v.low, v.high)} ${unit}`.trim();
  }

  function payTable(rows, stale = new Set()) {
    if (!rows.length) return h('p', { class: 'not-verified' }, 'Not yet verified');
    const order = (x) => CERT_LEVELS.indexOf(x.cert_level);
    return h('table', { class: 'pay-table' },
      h('caption', { class: 'visually-hidden' }, 'Hourly pay by certification level'),
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, 'Certification'), h('th', { scope: 'col' }, 'Hourly pay'))),
      h('tbody', null, [...rows].sort((a, b) => order(a) - order(b)).map((row) => h('tr', { dataset: { level: row.cert_level } },
        h('th', { scope: 'row' }, CERT_LABEL[row.cert_level] || row.cert_level),
        h('td', null,
          h('span', { class: 'sourced-main' },
            h('span', { class: 'fact-value' }, moneyRange(row.hourly_low, row.hourly_high, 2)), badge(row.confidence),
            row.stale || stale.has(`pay.${row.cert_level}`) ? staleTag() : null),
          row.confidence === 'reported' && row.aggregate_count
            ? h('span', { class: 'fact-note' }, `Range from ${fmtInt(row.aggregate_count)} reports`) : null,
          h('span', { class: 'fact-meta' },
            row.season ? `${String(row.season).replace('-', '/')} season \u00b7 ` : '',
            `Last verified ${formatIsoDate(row.as_of)} \u00b7 `, sourceLink(row.source_id)))))));
  }

  function statusText(status, unsetKey) {
    if (status === 'insufficient_data') return 'Not enough verified data yet';
    if (status === unsetKey) return 'Scoring model is being finalized';
    return 'Scoring model is being finalized';
  }

  function lucrativeness(l) {
    if (!l) return h('p', { class: 'not-verified' }, 'Not enough verified data yet');
    const body = [];
    if (l.status !== 'computed' || l.mid == null) body.push(h('p', { class: 'not-verified' }, statusText(l.status, 'assumptions_unset')));
    else {
      // Owner decision 4: the headline is the top of the posted wage scale; the range runs bottom to top.
      body.push(h('div', { class: 'sourced-main' },
        h('span', { class: 'fact-value' }, money(l.mid)), badge('estimated')));
      body.push(h('span', { class: 'fact-meta' }, 'At the top of the posted wage scale, before tax.',
        l.low != null && l.high != null && l.low !== l.high
          ? ` Range ${money(l.low)} at the bottom of the scale to ${money(l.high)} at the top.` : ''));
    }
    // rent_basis says which rent fed the model: "employee_housing:<site id>" or a baseline key (fmr_studio, acs_median);
    // rent_source_id is its source.
    const basis = l.assumptions.find((a) => a.key === 'rent_basis' && a.value != null);
    const basisSource = l.assumptions.find((a) => a.key === 'rent_source_id' && a.value != null);
    const listed = l.assumptions.filter((a) => a.key !== 'rent_basis' && a.key !== 'rent_source_id');
    if (basis) {
      body.push(h('p', { class: 'fact-meta' },
        h('span', { class: 'rent-used', dataset: { source: String(basis.value) } }, rentUsedText(String(basis.value), currentDetail)),
        basisSource ? [' ', sourceLink(String(basisSource.value))] : null));
    }
    if (listed.length) {
      body.push(h('p', { class: 'sub' }, 'Assumptions'),
        h('ul', { class: 'assumptions' }, listed.map((a) => h('li', null,
          h('span', null, a.label), ' ',
          a.value == null || (a.key.startsWith('tips') && a.value === 0)
            ? h('span', { class: 'not-verified' }, a.key.startsWith('tips') ? 'Tips are not included yet' : 'Not set yet')
            : h('span', { class: 'assumption-value' }, fmtAssumption(a.key, a.value))))));
    }
    return h('div', null, body);
  }

  // ── Editorial (Phase 6): rendered only when the build carries reviewed text ──
  function editorialSection(title, blocks, sourceIds, reviewedDate) {
    const parts = blocks.filter(([, text]) => text && String(text).trim());
    if (!parts.length) return null;
    return h('section', { class: 'panel-section editorial' },
      h('h3', null, title),
      parts.map(([label, text]) => [h('p', { class: 'sub' }, label), h('div', { class: 'editorial-text' }, markdown(text))]),
      sourceIds && sourceIds.length ? h('p', { class: 'fact-meta' }, 'Sources: ', sourceIds.map((id, i) => [i ? ', ' : '', sourceLink(id)])) : null,
      reviewedDate ? h('p', { class: 'fact-meta' }, `Reviewed ${formatIsoDate(reviewedDate)}.`) : null);
  }

  // ── Pay and rent reports (Phase 7): the form posts to the configured endpoint; reports are never displayed ──
  function reportSection(r) {
    const endpoint = index.definitions && index.definitions.submissions && index.definitions.submissions.endpoint;
    const url = endpoint && /^https:\/\//.test(endpoint) ? safeUrl(endpoint) : null;
    if (!url) return section('Share your pay or rent', h('p', { class: 'fact-meta' }, 'Pay and rent reports open soon.'));
    return section('Share your pay or rent', h('div', { class: 'report-slot', dataset: { resort: r.id } }, reportForm(r, url)));
  }

  /** Housing sites arrive with the detail file; rebuild the form so it can offer them. */
  function refreshReportForm(r) {
    const slot = root.querySelector('.report-slot');
    if (!slot || slot.dataset.resort !== r.id) return;
    const url = safeUrl(index.definitions.submissions.endpoint);
    clear(slot);
    slot.append(reportForm(r, url));
  }

  function seasonOptions(now = new Date()) {
    const y = now.getMonth() >= 6 ? now.getFullYear() : now.getFullYear() - 1;   // seasons turn over in July
    return [0, 1, 2].map((k) => {
      const a = y - k;
      return { value: `${a}-${String((a + 1) % 100).padStart(2, '0')}`, label: `${a}/${String((a + 1) % 100).padStart(2, '0')} season` };
    });
  }

  let formSeq = 0;
  function reportForm(r, url) {
    formSeq += 1;
    const id = (k) => `report-${formSeq}-${k}`;
    const min = (index.definitions.reports && index.definitions.reports.aggregation_min) || null;
    const field = (key, label, control, hint) => h('div', { class: 'form-field', dataset: { field: key } },
      h('label', { for: id(key) }, label), control,
      hint ? h('p', { class: 'form-hint', id: id(`${key}-hint`) }, hint) : null,
      h('p', { class: 'form-error', id: id(`${key}-error`), 'aria-live': 'polite' }));
    const select = (key, options, attrs = {}) => h('select', { id: id(key), name: key, ...attrs },
      options.map((o) => h('option', { value: o.value }, o.label)));
    const sites = (currentDetail && currentDetail.resort_id === r.id ? currentDetail.housing : []);
    const kinds = [{ value: 'pay', label: 'Hourly pay' }, { value: 'rent', label: 'Rent I paid near the resort' }]
      .concat(sites.length ? [{ value: 'housing', label: 'Rent at a listed housing site' }] : []);
    const kind = select('kind', kinds);
    const season = select('season', seasonOptions());
    const cert = select('cert_level', [{ value: '', label: 'Choose a level' }].concat(CERT_LEVELS.map((v) => ({ value: v, label: CERT_LABEL[v] }))));
    const site = select('housing_site_id', [{ value: '', label: 'Choose a site' }].concat(sites.map((x) => ({ value: x.id, label: x.name }))));
    const amount = h('input', { id: id('amount'), name: 'amount', type: 'number', inputmode: 'decimal', step: '0.01', min: '0', 'aria-describedby': id('amount-hint') });
    const proof = h('input', { id: id('proof_url'), name: 'proof_url', type: 'url', placeholder: 'https://', 'aria-describedby': id('proof_url-hint') });
    const status = h('p', { class: 'form-status', role: 'status', 'aria-live': 'polite' });
    const submit = h('button', { type: 'submit', class: 'btn btn-sm' }, 'Send report');
    const fields = {
      kind: field('kind', 'What are you reporting', kind),
      season: field('season', 'Season', season),
      cert_level: field('cert_level', 'Certification level', cert),
      housing_site_id: field('housing_site_id', 'Housing site', site),
      amount: field('amount', 'Hourly pay in USD', amount, 'Your base hourly rate before tips.'),
      proof_url: field('proof_url', 'Proof link (optional)', proof, 'A pay stub, offer letter or listing helps moderation. Links stay private.'),
    };
    const form = h('form', { class: 'report-form', novalidate: true },
      h('p', { class: 'fact-meta' }, `Reports stay private. A value appears on the map only as a range once ${min ? fmtInt(min) : 'several'} or more approved reports cover the same season.`),
      Object.values(fields), submit, status);

    function sync() {
      const k = kind.value;
      fields.cert_level.hidden = k !== 'pay';
      fields.housing_site_id.hidden = k !== 'housing';
      fields.amount.querySelector('label').textContent = k === 'pay' ? 'Hourly pay in USD' : 'Monthly rent in USD';
      fields.amount.querySelector('.form-hint').textContent = k === 'pay' ? 'Your base hourly rate before tips.' : 'Your share of the monthly rent.';
    }
    kind.addEventListener('change', sync);
    sync();

    function setError(key, msg) {
      const f = fields[key];
      const ctl = f.querySelector('input, select');
      f.querySelector('.form-error').textContent = msg || '';
      if (msg) { ctl.setAttribute('aria-invalid', 'true'); ctl.setAttribute('aria-describedby', id(`${key}-error`)); } else {
        ctl.removeAttribute('aria-invalid');
        if (f.querySelector('.form-hint')) ctl.setAttribute('aria-describedby', id(`${key}-hint`)); else ctl.removeAttribute('aria-describedby');
      }
      return !msg;
    }

    function validate() {
      const k = kind.value;
      const v = Number(amount.value);
      const errors = {
        season: season.value ? '' : 'Choose a season.',
        cert_level: k === 'pay' && !cert.value ? 'Choose your certification level.' : '',
        housing_site_id: k === 'housing' && !site.value ? 'Choose the housing site.' : '',
        amount: amount.value === '' || !Number.isFinite(v) ? 'Enter an amount in dollars.'
          : k === 'pay' ? (v < 5 || v > 300 ? 'Enter an hourly rate between $5 and $300.' : '')
            : (v < 100 || v > 10000 ? 'Enter a monthly rent between $100 and $10,000.' : ''),
        proof_url: proof.value && !/^https:\/\/[^\s]+\.[^\s]+$/.test(proof.value.trim()) ? 'Use a full link that starts with https://' : '',
      };
      let first = null;
      for (const [key, msg] of Object.entries(errors)) if (!setError(key, msg) && !first) first = fields[key].querySelector('input, select');
      return first;
    }

    // Owner decision 11: JSON to the configured endpoint (the site's Formspree form). Flat fields match the
    // `python -m pipeline.reports import` columns. Nothing submitted is ever shown on the map.
    const retry = h('button', { type: 'button', class: 'btn btn-sm btn-ghost report-retry', hidden: true }, 'Try again');
    form.append(retry);
    let lastPayload = null;

    async function send(payload) {
      submit.disabled = true;
      retry.hidden = true;
      status.textContent = 'Sending your report';
      form.classList.remove('is-sent', 'is-error');
      try {
        const res = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify(payload),
          credentials: 'omit',
        });
        if (!res.ok) throw new Error(String(res.status));
        form.reset();
        sync();
        lastPayload = null;
        form.classList.add('is-sent');
        status.textContent = 'Thanks. Your report goes to review before anything appears on the map.';
      } catch {
        form.classList.add('is-error');
        status.textContent = 'The report could not be sent. Check your connection and try again.';
        retry.hidden = false;
      } finally {
        submit.disabled = false;
      }
    }
    retry.addEventListener('click', () => { if (lastPayload) send(lastPayload); });

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      status.textContent = '';
      const bad = validate();
      if (bad) { bad.focus(); status.textContent = 'Please fix the highlighted fields.'; return; }
      const k = kind.value;
      lastPayload = {
        _subject: `Career Map report: ${r.name}`,
        resort_id: r.id, kind: k, season: season.value,
        ...(k === 'pay' ? { cert_level: cert.value, hourly_usd: Number(amount.value) } : { monthly_usd: Number(amount.value) }),
        ...(k === 'housing' ? { housing_site_id: site.value } : {}),
        ...(proof.value.trim() ? { proof_url: proof.value.trim() } : {}),
        submitted_at: new Date().toISOString(),
      };
      await send(lastPayload);
    });
    return form;
  }

  function rentUsedText(value, detail) {
    if (value === 'reported_area') return 'Rent used: the range of approved rent reports near the resort.';
    if (value === 'fmr_studio') return 'Rent used: HUD Fair Market Rent for a studio in the county.';
    if (value === 'fmr_one_br') return 'Rent used: HUD Fair Market Rent for one bedroom in the county.';
    if (value === 'acs_median') return 'Rent used: Census ACS median gross rent in the county.';
    if (value === 'employee_housing' || value === 'employee_housing_mean' || value === 'employee_housing_average') {
      return 'Rent used: the average of the employee housing rents listed for this resort.';
    }
    if (!value.startsWith('employee_housing:')) return `Rent used: ${humanize(value)}.`;
    const id = value.slice('employee_housing:'.length);
    const site = detail && detail.housing.find((x) => x.id === id);
    return `Rent used: ${site ? site.name : humanize(id)}, the cheapest employee housing listed.`;
  }

  // ── Housing and commute (Phase 5) ──

  const HOUSING_TYPE = { resort_owned: 'Resort owned', master_lease: 'Master lease', market: 'Market rental' };
  const PRECISION = { building: 'Listed building', neighborhood: 'Neighborhood area' };
  const OCCUPANCY = { shared: 'Shared rooms', private: 'Private rooms', mixed: 'Shared and private rooms' };
  const MODE = { drive: 'Drive', bus: 'Bus', walk: 'Walk', shuttle: 'Shuttle' };
  const TYPE_ORDER = { resort_owned: 0, master_lease: 1, market: 2 };

  /** Access rating and its one sentence summary (owner decision 6), with its source. */
  function accessBlock(a) {
    if (!a) return null;
    return h('div', { class: 'access' },
      h('p', { class: 'sub' }, 'Getting to the mountain'),
      h('div', { class: 'sourced-main' }, h('span', { class: 'fact-value access-rating' }, ACCESS_LABEL[a.rating]), badge(a.confidence)),
      a.summary_md ? h('div', { class: 'access-summary' }, markdown(a.summary_md)) : null,
      h('p', { class: 'fact-meta' }, `Last verified ${formatIsoDate(a.as_of)} · `, sourceLink(a.source_id)));
  }

  function housingSection(r, my) {
    const accessSlot = h('div', { class: 'access-slot' }, accessBlock(r.access));
    const body = h('div', { class: 'housing' });
    siteButtons = new Map();
    currentDetail = null;
    if (!r.detail_available) {
      body.append(h('p', { class: 'not-verified' }, 'Housing data is being collected.'));
      onDetail(r.id, null);
    } else {
      body.append(h('p', { class: 'loading' }, 'Loading housing and commute data'));
      loadDetail(r.id).then((detail) => {
        if (my !== token) return;
        currentDetail = detail;
        if (!r.access && detail && detail.access) { clear(accessSlot); accessSlot.append(accessBlock(detail.access)); }
        clear(body);
        renderHousing(body, detail);
        for (const el of root.querySelectorAll('.rent-used')) el.textContent = rentUsedText(el.dataset.source, detail);
        if (detail && detail.housing.length) refreshReportForm(r);
        onDetail(r.id, detail);
      });
    }
    return section('Housing and commute', accessSlot, body);
  }

  function renderHousing(body, detail) {
    const sites = detail ? [...detail.housing].sort((a, b) => (TYPE_ORDER[a.type] - TYPE_ORDER[b.type])
      || ((a.rent_monthly && a.rent_monthly.value.low) ?? Infinity) - ((b.rent_monthly && b.rent_monthly.value.low) ?? Infinity)) : [];
    if (!sites.length) body.append(h('p', { class: 'not-verified' }, 'Housing data is being collected.'));
    else {
      body.append(h('p', { class: 'fact-meta' }, 'Select a site to see it on the map with its commute.'));
      body.append(h('ul', { class: 'site-list' }, sites.map((site) => h('li', null, siteCard(site, detail)))));
    }
    if (detail && detail.rent_reported) {
      // The note carries the report count; show it the same way as reported pay rows.
      const rr = detail.rent_reported;
      const n = rr.note && /(\d+)/.exec(rr.note);
      const shown = n ? { ...rr, note: `Range from ${fmtInt(Number(n[1]))} reports` } : rr;
      body.append(h('dl', { class: 'facts' }, fact('Reported rent near the resort', shown, (v) => `${moneyRange(v.low, v.high)} per month`)));
    }
    const b = detail && detail.rent_baseline;
    if (b && [b.fmr.studio, b.fmr.one_br, b.fmr.two_br, b.acs_median_gross_rent].some(Boolean)) {
      body.append(h('p', { class: 'sub' }, `Rent baseline${b.county_name ? ` for ${b.county_name}` : ''}`),
        h('dl', { class: 'facts' },
          fact('HUD FMR studio', b.fmr.studio, monthly),
          fact('HUD FMR one bedroom', b.fmr.one_br, monthly),
          fact('HUD FMR two bedroom', b.fmr.two_br, monthly),
          fact('ACS median gross rent', b.acs_median_gross_rent, monthly)));
    }
  }

  const monthly = (v) => `${money(v)} per month`;

  function siteCard(site, detail) {
    const btn = h('button', {
      type: 'button', class: 'site-select', 'aria-pressed': 'false', dataset: { site: site.id },
      onclick: () => selectSite(btn.getAttribute('aria-pressed') === 'true' ? null : site.id),
    },
    h('span', { class: `site-dot site-${site.type} prec-${site.precision}`, 'aria-hidden': 'true' }),
    h('span', { class: 'nav-name' }, site.name),
    h('span', { class: 'nav-meta' }, `${HOUSING_TYPE[site.type]} \u00b7 ${PRECISION[site.precision]}`));
    siteButtons.set(site.id, btn);
    const routes = new Map(((detail.transit && detail.transit.routes) || []).map((x) => [x.id, x.short_name || x.long_name || x.id]));
    return h('div', { class: 'site', id: `site-${site.id}` },
      btn,
      h('dl', { class: 'facts site-facts' },
        site.address ? fact('Address', site.address, (v) => v) : null,
        fact('Rent', site.rent_monthly, (v) => `${moneyRange(v.low, v.high)} per month`),
        h('div', { class: 'fact' }, h('dt', null, 'Occupancy'),
          h('dd', null, site.occupancy ? OCCUPANCY[site.occupancy] : h('span', { class: 'not-verified' }, 'Not yet verified'))),
        fact('Deposit', site.deposit, (v) => money(v))),
      site.eligibility_note ? h('p', { class: 'site-note' }, site.eligibility_note) : null,
      site.commutes.length
        ? h('table', { class: 'commute-table' },
          h('caption', { class: 'visually-hidden' }, `Commute from ${site.name} to the base area`),
          h('thead', null, h('tr', null, ['Mode', 'Time', 'Distance', 'Routes'].map((t) => h('th', { scope: 'col' }, t)))),
          h('tbody', null, site.commutes.map((c) => h('tr', null,
            h('th', { scope: 'row' }, MODE[c.mode]),
            h('td', null, c.minutes == null ? h('span', { class: 'not-verified' }, 'Being calculated') : `${fmtInt(c.minutes)} min`),
            h('td', null, c.distance_mi == null ? '' : `${fmt1(c.distance_mi)} mi`),
            h('td', null, c.transit_route_ids.map((id) => routes.get(id) || id).join(', '))))))
        : h('p', { class: 'not-verified' }, 'Commute times are being calculated.'));
  }

  function selectSite(id, { scroll = false } = {}) {
    for (const [sid, b] of siteButtons) b.setAttribute('aria-pressed', String(sid === id));
    onSiteSelect(id);
    if (id && scroll) {
      const el = root.querySelector(`#site-${CSS.escape(id)}`);
      if (el) el.scrollIntoView({ block: 'nearest' });
    }
  }

  function fmtAssumption(key, v) {
    if (key === 'cert_level' && CERT_LABEL[v]) return CERT_LABEL[v];
    if (typeof v === 'number' && key.endsWith('_usd')) return money(v, Number.isInteger(v) ? 0 : 2);
    if (typeof v === 'number' && key.startsWith('range_spread')) return `${fmt1(v * 100)}% of earnings`;
    if (typeof v === 'number' && key.endsWith('_days')) return `${fmtInt(v)} days`;
    if (typeof v === 'string') return humanize(v);
    if (typeof v === 'number') return Math.abs(v) >= 100 ? fmtInt(v) : nf2.format(v);
    if (typeof v === 'boolean') return v ? 'Yes' : 'No';
    return String(v);
  }

  function climateHeader(climate, ch) {
    const total = ch.seasonTotal(climate, 'snowfall_in');
    const powder = ch.seasonTotal(climate, 'powder_days');
    return h('div', { class: 'climate-head' },
      stats([
        ['Snowfall, Oct to May', total == null ? h('span', { class: 'not-verified' }, 'Not yet verified') : `${fmtInt(total)} in`],
        ['Powder days', powder == null ? h('span', { class: 'not-verified' }, 'Not yet verified') : fmtInt(powder)],
      ]),
      h('p', { class: 'fact-meta' }, badge('estimated'),
        ` Modeled monthly averages, ${climate.period.start_year} to ${climate.period.end_year}. Last computed ${formatIsoDate(climate.generated_at)} · `,
        climate.source_ids.map((id, i) => [i ? ', ' : '', sourceLink(id)])));
  }

  function compare(view, my) {
    const c = index.clusters.get(view.clusterId);
    if (!index.detailsReady || !chartsMod) {
      if (!index.detailsReady) return waitForDetails(view, my, 'Cluster comparison', `Compare resorts in ${c.name}`);
      charts().catch(() => null).then(() => { if (my === token) api.render(view, { keepToken: true }); });
      return [breadcrumb(view), ...heading('Cluster comparison', `Compare resorts in ${c.name}`), h('p', { class: 'loading' }, 'Loading the comparison')];
    }
    const { SERIES, seasonTotal, renderCompareCharts } = chartsMod;
    const picks = view.compare.map((id) => index.full(id)).filter(Boolean);
    const chartsRoot = h('div', { class: 'climate' }, h('p', { class: 'loading' }, 'Loading climate data'));
    const tableRoot = h('div', { class: 'table-scroll' });
    const noteRoot = h('div');
    const out = [
      breadcrumb(view),
      ...heading('Cluster comparison', `Compare resorts in ${c.name}`),
      h('ul', { class: 'compare-keys', 'aria-label': 'Resorts in this comparison' }, picks.map((r, i) => h('li', null,
        h('span', { class: 'key', style: `background:${SERIES[i]}`, 'aria-hidden': 'true' }),
        navLink({ level: 'resort', stateCode: view.stateCode, clusterId: c.id, resortId: r.id }, null, r.name)))),
      section('Snow by month', chartsRoot, noteRoot),
      section('Side by side', tableRoot),
      h('p', { class: 'compare-actions' },
        h('button', {
          type: 'button', class: 'btn btn-sm btn-ghost',
          onclick: () => {
            compareState = { clusterId: c.id, open: true, selected: [...view.compare] };
            onCompare({ level: 'cluster', stateCode: view.stateCode, clusterId: c.id });
          },
        }, 'Change selection'),
        ' ', navLink({ level: 'cluster', stateCode: view.stateCode, clusterId: c.id }, { class: 'back-link' }, `Back to ${c.name}`)),
    ];

    Promise.all(picks.map((r) => (r.climate_available ? loadClimate(r.id) : Promise.resolve(null)))).then(async (climates) => {
      if (my !== token) return;
      const entries = picks.map((r, i) => ({ resort: r, climate: climates[i] }));
      tableRoot.append(compareTable(entries, seasonTotal));
      const missing = entries.filter((e) => !e.climate).map((e) => e.resort.name);
      if (missing.length) noteRoot.append(h('p', { class: 'not-verified' }, `Climate data is being compiled for ${joinAnd(missing)}.`));
      clear(chartsRoot);
      try {
        await renderCompareCharts(chartsRoot, entries, index.definitions);
      } catch (err) {
        console.warn('[map] charts', err.message);
        chartsRoot.append(h('p', { class: 'not-verified' }, 'Charts could not load. Try reloading the page.'));
      }
    });
    return out;
  }

  function compareTable(entries, seasonTotal) {
    const mini = (src, format) => (src
      ? h('span', { class: 'cell-sourced' }, format(src.value), ' ', badge(src.confidence))
      : h('span', { class: 'not-verified' }, 'Not yet verified'));
    const climateCell = (e, key, unit) => {
      const v = e.climate ? seasonTotal(e.climate, key) : null;
      return v == null ? h('span', { class: 'not-verified' }, 'Being compiled')
        : h('span', { class: 'cell-sourced' }, `${fmtInt(v)}${unit}`, ' ', badge('estimated'));
    };
    const rows = [
      ['Snowfall, Oct to May', (e) => climateCell(e, 'snowfall_in', ' in')],
      ['Powder days', (e) => climateCell(e, 'powder_days', '')],
      ['Banner days', (e) => climateCell(e, 'banner_days', '')],
      ['Skiable acres', (e) => mini(e.resort.size.acres, fmt.int)],
      ['Vertical drop', (e) => mini(e.resort.size.vertical_ft, fmt.ft)],
      ['Lifts', (e) => mini(e.resort.size.lifts, fmt.int)],
      ['Season length', (e) => mini(e.resort.season.length_days, fmt.days)],
    ];
    return h('table', { class: 'compare-table' },
      h('caption', { class: 'visually-hidden' }, 'Resort comparison'),
      h('thead', null, h('tr', null, h('td', null), entries.map((e, i) => h('th', { scope: 'col' },
        h('span', { class: 'key', style: `background:${chartsMod.SERIES[i]}`, 'aria-hidden': 'true' }), e.resort.name)))),
      h('tbody', null, rows.map(([label, fn]) => h('tr', null, h('th', { scope: 'row' }, label), entries.map((e) => h('td', null, fn(e)))))));
  }

  function footer() {
    return h('footer', { class: 'panel-footer' },
      h('p', null, 'Choosing your next mountain? Talk it through with Alexandre.'),
      h('a', { class: 'btn btn-sm', href: config.consultUrl }, 'Book a Free Consultation'),
      h('nav', { class: 'related', 'aria-label': 'Related tools and reading' },
        h('p', { class: 'sub' }, 'Keep planning'),
        h('ul', null, RELATED.map(([href, label]) => h('li', null, h('a', { href }, label))))),
      h('p', { class: 'fact-meta' }, index && index.generatedAt ? `Map data compiled ${formatIsoDate(index.generatedAt)}.` : '',
        config.isDevData ? ' Test fixture data loaded.' : ''));
  }

  /** Swap the source placeholders once sources.json has loaded. */
  function fillSources() {
    for (const el of root.querySelectorAll('.src-pending')) el.replaceWith(sourceLink(el.dataset.src));
  }

  let rendered = false;
  const api = {
    /** Render a view. Returns the heading element. */
    render(view, { keepToken = false } = {}) {
      if (!keepToken) token += 1;
      const my = token;
      const first = !rendered;
      rendered = true;
      root.removeAttribute('aria-busy');
      // A route stub carries a prerendered summary of its resort. Keep it on screen while the full record loads.
      if (first && (view.level === 'resort' ? !index.fullReady(view.resortId) : (view.level === 'compare' && !index.detailsReady))) {
        const pre = root.querySelector('#panel-heading');
        if (pre && pre.textContent.trim() === (view.level === 'resort' ? index.resorts.get(view.resortId).name : pre.textContent.trim())) {
          root.setAttribute('aria-busy', 'true');
          waitForDetails(view, my, '', '');
          return pre;
        }
      }
      destroyCharts();
      clear(root);
      let body;
      switch (view.level) {
        case 'state': body = state(view); break;
        case 'cluster': body = cluster(view); break;
        case 'compare': body = compare(view, my); break;
        case 'resort': body = resort(view, my); break;
        default: body = national();
      }
      root.append(...body.flat().filter(Boolean), footer());
      return root.querySelector('#panel-heading');
    },
    /** Select a housing site from the map. */
    selectSite(id) { selectSite(id, { scroll: true }); },
    fillSources,
    renderError(message) {
      destroyCharts();
      clear(root);
      root.append(...heading('Snowsports Career Map', 'Map data is unavailable'),
        h('p', { class: 'intro' }, 'The resort data could not be loaded right now. Please try again in a few minutes.'),
        h('p', { class: 'fact-meta' }, message), footer());
    },
  };
  return api;
}
