// Career Map entry point.
import { config, isPhone } from './config.js';
import { loadAll } from './data.js';
import { createRouter } from './router.js';
import { createMap } from './map.js';
import { createPanel } from './panel.js';
import { createSheet } from './sheet.js';
import { createListView } from './listview.js';
import { applyMeta } from './meta.js';
import { h } from './ui.js';

const $ = (id) => document.getElementById(id);

const els = {
  stage: $('map-stage'),
  map: $('map'),
  panel: $('panel'),
  content: $('panel-content'),
  scroll: $('panel-scroll'),
  handle: $('sheet-handle'),
  status: $('panel-status'),
  list: $('list-view'),
  listToggle: $('list-toggle'),
  devBadge: $('dev-badge'),
};

if (config.isDevData && els.devBadge) {
  els.devBadge.hidden = false;
  els.devBadge.textContent = 'Test fixture data';
}

let mapCtl = null;
const sheet = createSheet(els.panel, els.handle, els.stage, { onChange: () => mapCtl && mapCtl.refit() });

function announce(text) {
  els.status.textContent = '';
  // Next frame so repeated messages are still announced.
  requestAnimationFrame(() => { els.status.textContent = text; });
}

function setListView(on) {
  document.body.classList.toggle('list-mode', on);
  els.listToggle.setAttribute('aria-pressed', String(on));
  els.listToggle.textContent = on ? 'Map view' : 'List view';
  els.list.hidden = !on;
  els.map.hidden = on;
  if (!on && mapCtl) requestAnimationFrame(() => { mapCtl.resize(); mapCtl.refit(); });
}

function panelWidth(view) {
  return view.level === 'compare' ? config.comparePanelWidth : config.desktopPanelWidth;
}

function padding(view) {
  if (isPhone()) return { top: 24, left: 24, right: 24, bottom: sheet.visible() + 16 };
  const w = Math.min(panelWidth(view), els.stage.clientWidth * 0.6);
  return { top: 48, left: 56, right: w + 40, bottom: 40 };
}

function summary(index, view) {
  switch (view.level) {
    case 'state': return `Showing ${index.states.get(view.stateCode).name}.`;
    case 'cluster': return `Showing the ${index.clusters.get(view.clusterId).name} cluster.`;
    case 'resort': return `Showing ${index.resorts.get(view.resortId).name}.`;
    case 'compare': return `Comparing ${view.compare.length} resorts.`;
    default: return 'Showing the national view.';
  }
}

async function main() {
  let index;
  try {
    index = await loadAll();
  } catch (err) {
    console.warn('[map] data load failed:', err.message);
    const panel = createPanel(els.content, null, { onCompare: () => {} });
    panel.renderError(err.message);
    els.panel.setAttribute('aria-busy', 'false');
    return;
  }

  let router;
  const panel = createPanel(els.content, index, { onCompare: (view) => router.go(view) });
  const list = createListView(els.list, index);

  try {
    mapCtl = createMap(els.map, index, {
      getPadding: padding,
      onPick(target, current) {
        const cur = current || { level: 'national' };
        if (target.kind === 'state') {
          if (cur.stateCode === target.id && cur.level === 'state') return;
          router.go({ level: 'state', stateCode: target.id });
        } else if (target.kind === 'cluster') {
          const c = index.clusters.get(target.id);
          const st = cur.stateCode && c.states.includes(cur.stateCode) ? cur.stateCode : c.states[0];
          router.go({ level: 'cluster', stateCode: st, clusterId: c.id });
        } else {
          const r = index.resorts.get(target.id);
          const c = index.clusters.get(r.cluster_id);
          if (!c) return;
          const st = cur.stateCode && c.states.includes(cur.stateCode) ? cur.stateCode
            : (c.states.includes(r.state) ? r.state : c.states[0]);
          router.go({ level: 'resort', stateCode: st, clusterId: c.id, resortId: r.id });
        }
      },
    });
  } catch (err) {
    console.warn('[map] map unavailable, showing list view:', err.message);
    els.map.append(h('p', { class: 'map-fallback' }, 'The interactive map needs WebGL. The list view shows every resort.'));
    setListView(true);
  }

  router = createRouter(index, (view, { initial, fellBack }) => {
    els.panel.classList.toggle('is-wide', view.level === 'compare');
    els.stage.classList.toggle('is-wide', view.level === 'compare');
    const headingEl = panel.render(view);
    els.scroll.scrollTop = 0;
    applyMeta(index, view);
    list.highlight(view);
    if (mapCtl) {
      if (!isPhone()) mapCtl.resize();
      mapCtl.show(view, { animate: !initial });
    }
    let msg = summary(index, view);
    if (fellBack) {
      msg = `That link points to a page we could not find, so here is the closest match. ${msg}`;
      els.content.prepend(h('p', { class: 'notice', role: 'note' }, 'That link points to a page we could not find, so here is the closest match.'));
    }
    announce(msg);
    if (!initial) {
      sheet.reveal();
      if (headingEl) headingEl.focus({ preventScroll: true });
    }
  });

  // In-app links: History API navigation, real hrefs for new tabs and crawlers.
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('a[data-nav]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    router.goHref(a.getAttribute('href'));
  });

  els.listToggle.addEventListener('click', () => setListView(els.list.hidden));
  els.listToggle.hidden = false;

  router.start();
  els.panel.setAttribute('aria-busy', 'false');
  window.__careerMap = { index, router, ready: true };
}

main();
