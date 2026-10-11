// List view: the full state, cluster and resort hierarchy without the map.
import { clusterResorts, clusterOrder } from './data.js';
import { hrefFor } from './router.js';
import { h, clear, plural } from './ui.js';

export function createListView(root, index) {
  function link(view, label, cls) {
    return h('a', { href: hrefFor(view), 'data-nav': '', class: cls, dataset: { key: keyOf(view) } }, label);
  }

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
            .map((r) => h('li', null,
              link({ level: 'resort', stateCode: s.code, clusterId: c.id, resortId: r.id }, r.name, 'tree-resort'),
              r.tier === 'A' ? h('span', { class: 'tier tier-a' }, 'Tier A') : null))))))))));
  }

  function keyOf(v) { return [v.stateCode || '', v.clusterId || '', v.resortId || ''].join('/'); }

  build();
  return {
    highlight(view) {
      for (const a of root.querySelectorAll('[aria-current]')) a.removeAttribute('aria-current');
      const k = keyOf({ stateCode: view.stateCode, clusterId: view.clusterId, resortId: view.resortId });
      const el = root.querySelector(`a[data-key="${CSS.escape(k)}"]`);
      if (el) el.setAttribute('aria-current', 'page');
    },
  };
}
