// List view: the full state, cluster and resort hierarchy without the map.
import { clusterResorts, clusterOrder, isFiltered, matchesFilter, NO_FILTER } from './data.js';
import { hrefFor } from './router.js';
import { h, clear, plural } from './ui.js';
import { FACILITY_LABEL } from './labels.js';

export function createListView(root, index) {
  function link(view, label, cls) {
    return h('a', { href: hrefFor(view), 'data-nav': '', class: cls, dataset: { key: keyOf(view) } }, label);
  }

  let filter = NO_FILTER;
  let current = null;

  function build() {
    clear(root);
    root.append(h('h2', { class: 'list-title' }, 'All resorts by state and cluster'));
    if (!index.stateList.length) {
      root.append(h('p', { class: 'empty' }, 'Resort data is being compiled. Check back soon.'));
      return;
    }
    root.append(h('ul', { class: 'tree' }, index.stateList.map((s) => h('li', null,
      link({ level: 'state', stateCode: s.code }, s.name, 'tree-state'),
      h('span', { class: 'tree-meta' }, ` ${plural(s.resort_count, 'resort', 'resorts')}`),
      h('ul', null, s.cluster_ids.map((cid) => index.clusters.get(cid))
        .sort(clusterOrder)
        .map((c) => h('li', null,
          link({ level: 'cluster', stateCode: s.code, clusterId: c.id }, c.name, 'tree-cluster'),
          h('ul', null, clusterResorts(index, c)
            .filter((r) => r.state === s.code || !c.states.includes(r.state))
            .filter((r) => matchesFilter(r, filter))
            .map((r) => h('li', null,
              link({ level: 'resort', stateCode: s.code, clusterId: c.id, resortId: r.id }, r.name, 'tree-resort'),
              r.tier === 'A' ? h('span', { class: 'tier tier-a' }, 'Tier A') : null,
              r.facility_type !== 'ski_area' ? h('span', { class: `tier tier-facility fac-${r.facility_type}` }, FACILITY_LABEL[r.facility_type]) : null))))))))));
    if (isFiltered(filter)) {
      // Drop clusters and states left empty by the filter.
      for (const ul of root.querySelectorAll('.tree ul ul')) if (!ul.children.length) ul.parentElement.remove();
      for (const li of root.querySelectorAll('.tree > li')) if (!li.querySelector('.tree-resort')) li.remove();
      if (!root.querySelector('.tree-resort')) root.append(h('p', { class: 'empty' }, 'Nothing matches this filter yet.'));
    }
  }

  function keyOf(v) { return [v.stateCode || '', v.clusterId || '', v.resortId || ''].join('/'); }

  // Built the first time it is shown (show()), so the first paint skips about 2,000 hidden nodes.
  let built = false;
  return {
    show() { if (!built) { built = true; build(); if (current) this.highlight(current); } },
    setFilter(next) { filter = next; if (built) { build(); if (current) this.highlight(current); } },
    highlight(view) {
      current = view;
      if (!built) return;
      for (const a of root.querySelectorAll('[aria-current]')) a.removeAttribute('aria-current');
      const k = keyOf({ stateCode: view.stateCode, clusterId: view.clusterId, resortId: view.resortId });
      const el = root.querySelector(`a[data-key="${CSS.escape(k)}"]`);
      if (el) el.setAttribute('aria-current', 'page');
    },
  };
}
