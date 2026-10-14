// Layer toggles and legend for the resort detail map. Only groups with data are listed.
import { HOUSING_COLOR, ISO_COLOR, COMMUTE_DASH, transitLegend } from './detailmap.js';
import { h, clear } from './ui.js';

const LABEL = {
  lifts: 'Lifts',
  housing: 'Housing',
  commute: 'Commute',
  transit: 'Transit',
  isochrones: 'Drive time',
};
const ORDER = ['lifts', 'housing', 'commute', 'transit', 'isochrones'];

function lineSample(dash, color = '#f2f4f8') {
  const d = dash ? dash.map((x) => x * 3).join(' ') : '';
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="26" height="8" viewBox="0 0 26 8"><line x1="1" y1="4" x2="25" y2="4" stroke="${color}" stroke-width="3" stroke-linecap="round"${d ? ` stroke-dasharray="${d}"` : ''}/></svg>`;
  return h('img', { class: 'line-sample', alt: '', 'aria-hidden': 'true', src: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}` });
}

// Agency name from GTFS agency.txt, linked to the agency's site when the feed gives one.
function agencyLink(a) {
  if (!a.url) return h('span', null, a.name);
  return h('a', { href: a.url, target: '_blank', rel: 'noopener' }, a.name, h('span', { class: 'visually-hidden' }, ' (opens in a new tab)'));
}

function legendFor(group) {
  switch (group) {
    case 'housing':
      return h('ul', { class: 'detail-legend' },
        Object.entries({ resort_owned: 'Resort owned', master_lease: 'Master lease', unspecified: 'Listed by the resort', market: 'Market' }).map(([k, label]) =>
          h('li', null, h('span', { class: 'chip-dot', style: `background:${HOUSING_COLOR[k]}`, 'aria-hidden': 'true' }), label)),
        h('li', { class: 'legend-note' }, 'Dots mark listed buildings. Soft circles mark neighborhood areas.'));
    case 'isochrones':
      return h('ul', { class: 'detail-legend' }, Object.entries(ISO_COLOR).map(([m, color]) =>
        h('li', null, h('span', { class: 'chip-sq', style: `background:${color}`, 'aria-hidden': 'true' }), `${m} min`)),
      h('li', { class: 'legend-note' }, 'Drive time from the base area.'));
    case 'commute':
      return h('ul', { class: 'detail-legend' }, Object.entries(COMMUTE_DASH).map(([m, dash]) =>
        h('li', null, lineSample(dash), m.charAt(0).toUpperCase() + m.slice(1))),
      h('li', { class: 'legend-note' }, 'Shown for the selected housing site.'));
    case 'lifts':
      return h('ul', { class: 'detail-legend' }, h('li', null, lineSample(null, '#e7e9ee'), 'Hover a lift for its name'),
        h('li', null, h('span', { class: 'chip-dot base-dot', 'aria-hidden': 'true' }), 'Base area'));
    case 'transit':
      return h('ul', { class: 'detail-legend' }, h('li', null, lineSample(null, '#c98500'), 'Routes in agency colors'),
        h('li', null, h('span', { class: 'chip-dot stop-dot', 'aria-hidden': 'true' }), 'Stops'),
        transitLegend().map((f) => h('li', { class: 'legend-feed' },
          h('span', { class: 'feed-agencies' }, f.agencies.flatMap((a, i) => [i ? ', ' : null, agencyLink(a)]).filter(Boolean)),
          f.schedule ? h('span', { class: 'feed-schedule' }, f.schedule) : null,
          f.note ? h('span', { class: 'legend-note' }, f.note) : null)));
    default: return null;
  }
}

export function createDetailControls(root, { onToggle }) {
  const state = {};
  // A route stub built from an older shell may lack the container; detail layers then stay on with no toggles.
  if (!root) {
    return { show(groups) { for (const g of groups) onToggle(g, true); }, ensure(g) { onToggle(g, true); }, hide() {} };
  }
  return {
    show(groups) {
      clear(root);
      if (!groups.length) { root.hidden = true; document.body.classList.remove('detail-mode'); return; }
      const list = ORDER.filter((g) => groups.includes(g));
      // A disclosure: open on desktop, collapsed on phone so the map stays visible.
      const open = !window.matchMedia('(max-width: 759.98px)').matches;
      const toggles = h('ul', { class: 'detail-toggles' });
      root.append(h('details', { class: 'detail-box', open },
        h('summary', { class: 'detail-title' }, 'Resort detail layers'), toggles));
      toggles.append(...list.map((g) => {
          if (state[g] === undefined) state[g] = true;
          const id = `layer-${g}`;
          const box = h('input', {
            type: 'checkbox', id, checked: state[g], dataset: { group: g },
            onchange: (e) => { state[g] = e.target.checked; onToggle(g, e.target.checked); legend.hidden = !e.target.checked; },
          });
          const legend = legendFor(g);
          if (legend) legend.hidden = !state[g];
          onToggle(g, state[g]);
          return h('li', null, h('label', { for: id }, box, LABEL[g]), legend);
        }));
      root.hidden = false;
      document.body.classList.add('detail-mode');
    },
    /** Switch a group on (its checkbox too), for example housing before the map fits to the housing pins. */
    ensure(group) {
      state[group] = true;
      const box = root.querySelector(`input[data-group="${group}"]`);
      if (box && !box.checked) {
        box.checked = true;
        const legend = box.closest('li') && box.closest('li').querySelector('.detail-legend');
        if (legend) legend.hidden = false;
      }
      onToggle(group, true);
    },
    hide() {
      clear(root);
      root.hidden = true;
      document.body.classList.remove('detail-mode');
    },
  };
}
