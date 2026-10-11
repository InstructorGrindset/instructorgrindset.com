// Side panel (desktop) / bottom sheet (phone) content for each level.
import { config } from './config.js';
import { clusterResorts, clusterOrder, loadClimate, facilityCounts } from './data.js';
import {
  FACILITY_LABEL, criterionLabel, facilityPhrase, CERT_LABEL, COMPONENT_LABEL, RATE_UNIT, tagLabel,
} from './labels.js';
import { CERT_LEVELS } from './schemas.js';
import { hrefFor } from './router.js';
import {
  h, clear, safeUrl, formatIsoDate, formatMonthDay, fmtInt, fmt1, plural, humanize, joinAnd,
} from './ui.js';
import {
  destroyCharts, renderResortCharts, renderCompareCharts, seasonTotal, SERIES,
} from './charts.js';

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

export function badge(confidence) {
  return h('span', { class: `badge badge-${confidence}`, title: CONFIDENCE_HELP[confidence] }, CONFIDENCE_LABEL[confidence] || confidence);
}

function navLink(view, attrs, ...children) {
  return h('a', { href: hrefFor(view), 'data-nav': '', ...attrs }, ...children);
}

export function createPanel(root, index, { onCompare }) {
  let token = 0;
  let compareState = { clusterId: null, open: false, selected: [] };

  function sourceLink(id) {
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
        const acres = r.size.acres;
        const link = navLink({ level: 'resort', stateCode: view.stateCode, clusterId: c.id, resortId: r.id }, { class: 'nav-item' },
          h('span', { class: 'nav-name' }, r.name, ' ', tierTags(r)),
          h('span', { class: 'nav-meta' }, acres ? `${fmtInt(acres.value)} skiable acres` : 'Size not yet verified',
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
      c.sources.length ? h('p', { class: 'fact-meta' }, 'Cluster sources: ', c.sources.map((id, i) => [i ? ', ' : '', sourceLink(id)])) : null,
      h('section', { class: 'panel-section' },
        h('div', { class: 'section-head' }, h('h3', null, 'Resorts'), resorts.length >= config.compare.min ? toggle : null),
        bar, list),
    ];
  }

  function resort(view, my) {
    const r = index.resorts.get(view.resortId);
    const c = index.clusters.get(view.clusterId);
    const climateRoot = h('div', { class: 'climate' });
    const out = [
      breadcrumb(view),
      ...heading(`Resort in ${c.name}, ${r.state}`, r.name),
      h('div', { class: 'tier-row' }, resortTags(r)),
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
        climateRoot.append(climateHeader(climate));
        try {
          await renderResortCharts(climateRoot, climate, index.definitions);
        } catch (err) {
          console.warn('[map] charts', err.message);
          climateRoot.append(h('p', { class: 'not-verified' }, 'Charts could not load. Try reloading the page.'));
        }
      });
    }
    return out;
  }

  // ── Career (Phase 4) ──

  function careerSection(r) {
    const c = r.career;
    if (!c) return section('Career', h('p', { class: 'not-verified' }, 'Career data is being collected.'));
    const stale = new Set(c.stale_fields);
    return section('Career',
      h('p', { class: 'sub' }, 'Hourly pay by certification'),
      payTable(c.pay, stale),
      h('dl', { class: 'facts career-facts' },
        fact('Private lesson rate', c.private_rate, fmtRate, { stale: stale.has('private_rate') }),
        fact('Instructor split', c.private_split, (v) => `${fmt1(v)}% to the instructor`, { stale: stale.has('private_split') })),
      h('div', { class: 'score-block' }, h('p', { class: 'sub' }, 'Professionalism'), professionalism(c.professionalism, stale)),
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

  let tipSeq = 0;
  function professionalism(p, stale = new Set()) {
    if (!p) return h('p', { class: 'not-verified' }, 'Not enough verified data yet');
    if (p.status !== 'computed' || p.score == null) return h('p', { class: 'not-verified' }, statusText(p.status, 'weights_unset'));
    tipSeq += 1;
    const id = `breakdown-${tipSeq}`;
    const fmtW = (v) => (v == null ? 'No data' : nf2.format(v));
    const tip = h('div', { class: 'breakdown', role: 'tooltip', id, hidden: true },
      h('table', null,
        h('caption', null, 'How the score is built'),
        h('thead', null, h('tr', null, ['Component', 'Weight', 'Value', 'Points'].map((t) => h('th', { scope: 'col' }, t)))),
        h('tbody', null, p.breakdown.map((b) => h('tr', null,
          h('th', { scope: 'row' }, COMPONENT_LABEL[b.component] || humanize(b.component),
            stale.has(`professionalism.${b.component}`) ? h('span', { class: 'stale-mark' }, ' (needs update)') : null),
          h('td', null, fmtW(b.weight)), h('td', null, fmtW(b.value)),
          h('td', null, b.points == null ? 'No data' : fmt1(b.points)))))));
    const trigger = h('button', {
      type: 'button', class: 'score-trigger', 'aria-describedby': id, 'aria-expanded': 'false',
      'aria-label': `Professionalism score ${fmtInt(p.score)} out of 100. Focus or hover to see the breakdown.`,
    }, h('span', { class: 'fact-value score-value' }, fmtInt(p.score)), h('span', { class: 'score-max' }, ' / 100'));
    const wrap = h('div', { class: 'score-wrap' }, trigger, tip);
    const show = (on) => { tip.hidden = !on; trigger.setAttribute('aria-expanded', String(on)); };
    wrap.addEventListener('mouseenter', () => show(true));
    wrap.addEventListener('mouseleave', () => { if (document.activeElement !== trigger) show(false); });
    trigger.addEventListener('focus', () => show(true));
    trigger.addEventListener('blur', () => show(false));
    trigger.addEventListener('click', () => show(tip.hidden));
    trigger.addEventListener('keydown', (e) => { if (e.key === 'Escape') show(false); });
    const srcIds = [...new Set(p.breakdown.map((b) => b.source_id).filter(Boolean))];
    const staleParts = p.breakdown.filter((b) => stale.has(`professionalism.${b.component}`));
    return h('div', null,
      h('div', { class: 'sourced-main' }, wrap, badge('estimated'), staleParts.length ? staleTag() : null),
      h('span', { class: 'fact-meta' }, 'Model score from weighted components in config.',
        srcIds.length ? [' Sources: ', srcIds.map((sid, i) => [i ? ', ' : '', sourceLink(sid)])] : null));
  }

  function lucrativeness(l) {
    if (!l) return h('p', { class: 'not-verified' }, 'Not enough verified data yet');
    const body = [];
    if (l.status !== 'computed' || l.mid == null) body.push(h('p', { class: 'not-verified' }, statusText(l.status, 'assumptions_unset')));
    else {
      body.push(h('div', { class: 'sourced-main' },
        h('span', { class: 'fact-value' }, moneyRange(l.low, l.high)), badge('estimated')));
      body.push(h('span', { class: 'fact-meta' }, `Mid case ${money(l.mid)}. Low and high cases bracket the mid case.`));
    }
    if (l.assumptions.length) {
      body.push(h('p', { class: 'sub' }, 'Assumptions'),
        h('ul', { class: 'assumptions' }, l.assumptions.map((a) => h('li', null,
          h('span', null, a.label), ' ',
          a.value == null ? h('span', { class: 'not-verified' }, 'Not set yet') : h('span', { class: 'assumption-value' }, fmtAssumption(a.key, a.value))))));
    }
    return h('div', null, body);
  }

  function fmtAssumption(key, v) {
    if (key === 'cert_level' && CERT_LABEL[v]) return CERT_LABEL[v];
    if (typeof v === 'number' && key.endsWith('_usd')) return money(v, Number.isInteger(v) ? 0 : 2);
    if (typeof v === 'number' && key.startsWith('range_spread')) return `${fmt1(v * 100)}% of earnings`;
    if (typeof v === 'number' && key.endsWith('_days')) return `${fmtInt(v)} days`;
    if (typeof v === 'string') return humanize(v);
    if (typeof v === 'number') return Math.abs(v) >= 100 ? fmtInt(v) : fmt1(v);
    if (typeof v === 'boolean') return v ? 'Yes' : 'No';
    return String(v);
  }

  function climateHeader(climate) {
    const total = seasonTotal(climate, 'snowfall_in');
    const powder = seasonTotal(climate, 'powder_days');
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
    const picks = view.compare.map((id) => index.resorts.get(id)).filter(Boolean);
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
      tableRoot.append(compareTable(entries));
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

  function compareTable(entries) {
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
        h('span', { class: 'key', style: `background:${SERIES[i]}`, 'aria-hidden': 'true' }), e.resort.name)))),
      h('tbody', null, rows.map(([label, fn]) => h('tr', null, h('th', { scope: 'row' }, label), entries.map((e) => h('td', null, fn(e)))))));
  }

  function footer() {
    return h('footer', { class: 'panel-footer' },
      h('p', null, 'Choosing your next mountain? Talk it through with Alexandre.'),
      h('a', { class: 'btn btn-sm', href: config.consultUrl }, 'Book a Free Consultation'),
      h('p', { class: 'fact-meta' }, index && index.generatedAt ? `Map data compiled ${formatIsoDate(index.generatedAt)}.` : '',
        config.isDevData ? ' Test fixture data loaded.' : ''));
  }

  return {
    /** Render a view. Returns a short summary for the live region. */
    render(view) {
      token += 1;
      const my = token;
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
    renderError(message) {
      destroyCharts();
      clear(root);
      root.append(...heading('Snowsports Career Map', 'Map data is unavailable'),
        h('p', { class: 'intro' }, 'The resort data could not be loaded right now. Please try again in a few minutes.'),
        h('p', { class: 'fact-meta' }, message), footer());
    },
  };
}
