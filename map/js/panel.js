// Side panel (desktop) / bottom sheet (phone) content for each level.
import { config } from './config.js';
import { clusterResorts, loadClimate } from './data.js';
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
const CRITERIA = {
  diehard: 'Diehard following', luxury: 'Luxury clientele',
  athlete_development: 'Athlete development', heritage: 'Heritage',
};

export const fmt = {
  int: (v) => fmtInt(v),
  ft: (v) => `${fmtInt(v)} ft`,
  days: (v) => `${fmtInt(v)} days`,
  pct: (v) => `${fmt1(v)}%`,
  date: (v) => formatMonthDay(v),
  text: (v) => String(v),
};

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
    return url ? h('a', { href: url, target: '_blank', rel: 'noopener noreferrer', class: 'src-link' }, s.title || id)
      : h('span', null, s.title || id);
  }

  /** Value + confidence badge + last verified + source, or "Not yet verified". */
  function sourcedValue(src, format) {
    if (!src) return h('span', { class: 'not-verified' }, 'Not yet verified');
    return h('span', { class: 'sourced' },
      h('span', { class: 'sourced-main' }, h('span', { class: 'fact-value' }, format(src.value)),
        src.note ? h('span', { class: 'fact-note' }, src.note) : null, badge(src.confidence)),
      h('span', { class: 'fact-meta' }, `Last verified ${formatIsoDate(src.as_of)} · `, sourceLink(src.source_id)));
  }

  function fact(label, src, format) {
    return h('div', { class: 'fact' }, h('dt', null, label), h('dd', null, sourcedValue(src, format)));
  }

  function section(title, ...body) {
    return h('section', { class: 'panel-section' }, h('h3', null, title), ...body);
  }

  function tags(list) {
    return h('ul', { class: 'tag-list' }, list.map((t) => h('li', { class: 'tag' }, humanize(t))));
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

  function tierTags(r) {
    return [
      r.tier === 'A' ? h('span', { class: 'tier tier-a' }, 'Tier A') : h('span', { class: 'tier tier-b' }, 'Tier B'),
      r.tier === 'A' && r.tier_a_status === 'draft' ? h('span', { class: 'tier tier-draft', title: 'Tier A list awaiting approval' }, 'Draft profile') : null,
    ];
  }

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
      ['Resorts', fmtInt(index.resorts.size)],
      ['Tier A', fmtInt(tierA)],
    ]));
    out.push(legend());
    out.push(section('States', h('ul', { class: 'nav-list' }, index.stateList.map((s) => h('li', null,
      navLink({ level: 'state', stateCode: s.code }, { class: 'nav-item' },
        h('span', { class: 'nav-name' }, s.name),
        h('span', { class: 'nav-meta' }, `${plural(s.cluster_ids.length, 'cluster', 'clusters')} · ${plural(s.resort_count, 'resort', 'resorts')}`)))))));
    return out;
  }

  function legend() {
    return h('ul', { class: 'legend', 'aria-label': 'Map legend' },
      h('li', null, h('span', { class: 'swatch swatch-a', 'aria-hidden': 'true' }), 'Tier A deep profile'),
      h('li', null, h('span', { class: 'swatch swatch-b', 'aria-hidden': 'true' }), 'Tier B open data profile'),
      h('li', null, h('span', { class: 'swatch swatch-cluster', 'aria-hidden': 'true' }), 'Cluster'),
      h('li', { class: 'legend-note' }, 'Larger circles mean more skiable acres.'));
  }

  function state(view) {
    const s = index.states.get(view.stateCode);
    const clusters = s.cluster_ids.map((id) => index.clusters.get(id)).sort((a, b) => a.name.localeCompare(b.name));
    return [
      breadcrumb(view),
      ...heading('State', s.name),
      h('p', { class: 'intro' }, `${plural(clusters.length, 'cluster', 'clusters')} and ${plural(s.resort_count, 'resort', 'resorts')}. Clusters are the labor markets instructors choose between, so some cross state lines.`),
      section('Clusters', h('ul', { class: 'nav-list' }, clusters.map((c) => {
        const others = c.states.filter((x) => x !== s.code).map((x) => (index.states.get(x) || { name: x }).name);
        return h('li', null, navLink({ level: 'cluster', stateCode: s.code, clusterId: c.id }, { class: 'nav-item' },
          h('span', { class: 'nav-name' }, c.name),
          h('span', { class: 'nav-meta' }, `${plural(c.resort_ids.length, 'resort', 'resorts')} · ${fmtInt(c.tier_a_count)} Tier A`),
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
    return [
      breadcrumb(view),
      ...heading('Cluster', c.name),
      stats([['States', joinAnd(stateNames)], ['Resorts', fmtInt(resorts.length)], ['Tier A', fmtInt(c.tier_a_count)]]),
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
      h('div', { class: 'tier-row' }, tierTags(r)),
      h('dl', { class: 'facts' }, fact('Owner', r.owner, fmt.text)),
    ];
    if (r.tier === 'A' && r.tier_a_criteria.length) {
      out.push(section('Why Tier A', h('ul', { class: 'criteria' }, r.tier_a_criteria.map((k) => h('li', null,
        h('span', { class: 'criterion' }, CRITERIA[k.criterion] || humanize(k.criterion)), ' ', h('span', { class: 'fact-meta' }, sourceLink(k.source_id)))))));
    }
    out.push(section('Operation size', h('dl', { class: 'facts' },
      fact('Skiable acres', r.size.acres, fmt.int),
      fact('Vertical drop', r.size.vertical_ft, fmt.ft),
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
      ['Suits these skiers and riders', r.fit.enthusiast_types],
      ['Suits these professionals', r.fit.professional_types],
      ['Typical clients', r.clients.profile_tags],
    ].filter(([, l]) => l.length);
    if (fitBits.length) {
      out.push(section('Fit and clients', fitBits.map(([label, l]) => [h('p', { class: 'sub' }, label), tags(l)])));
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
          await renderResortCharts(climateRoot, climate);
        } catch (err) {
          console.warn('[map] charts', err.message);
          climateRoot.append(h('p', { class: 'not-verified' }, 'Charts could not load. Try reloading the page.'));
        }
      });
    }
    return out;
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
        await renderCompareCharts(chartsRoot, entries);
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
