// MapLibre map: state outlines, cluster polygons, resort circles.
import { config, prefersReducedMotion } from './config.js';
import { h } from './ui.js';

const C = config.basemap.colors;

/** Circle radius by size_rank (1..5), scaled with zoom. */
function radius(extra = 0) {
  const rank = ['coalesce', ['get', 'size_rank'], 1];
  const at = (base, step) => ['+', base + extra, ['*', rank, step]];
  return ['interpolate', ['linear'], ['zoom'], 3, at(1.6, 0.9), 7, at(3, 1.5), 11, at(5, 2.2)];
}

const fs = (key) => ['boolean', ['feature-state', key], false];

function buildStyle(index) {
  return {
    version: 8,
    sources: {
      states: { type: 'geojson', data: index.geo.usStates, promoteId: 'code' },
      clusters: { type: 'geojson', data: index.geo.clusters, promoteId: 'id' },
      resorts: { type: 'geojson', data: index.geo.resorts, promoteId: 'id' },
    },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': C.background } },
      {
        id: 'states-fill', type: 'fill', source: 'states',
        paint: {
          'fill-color': ['case', fs('selected'), C.stateFillSelected,
            ['all', fs('hover'), ['boolean', ['get', 'has_resorts'], false]], '#1c2533',
            ['boolean', ['get', 'has_resorts'], false], C.stateFillActive, C.stateFill],
        },
      },
      {
        id: 'states-line', type: 'line', source: 'states',
        paint: { 'line-color': C.stateLine, 'line-width': ['interpolate', ['linear'], ['zoom'], 3, 0.6, 8, 1.4] },
      },
      {
        id: 'clusters-fill', type: 'fill', source: 'clusters',
        paint: {
          'fill-color': C.cluster,
          'fill-opacity': ['case', fs('selected'), 0.1, fs('hover'), 0.16, fs('focus'), 0.12, fs('dim'), 0.03, 0.08],
        },
      },
      {
        id: 'clusters-line', type: 'line', source: 'clusters',
        paint: {
          'line-color': C.cluster,
          'line-opacity': ['case', fs('selected'), 1, fs('hover'), 0.9, fs('focus'), 0.8, fs('dim'), 0.2, 0.5],
          'line-width': ['case', fs('selected'), 2.2, fs('focus'), 1.4, 1],
        },
      },
      {
        id: 'states-line-selected', type: 'line', source: 'states', filter: ['==', ['get', 'code'], ''],
        paint: { 'line-color': C.stateLineSelected, 'line-width': 1.6, 'line-opacity': 0.8 },
      },
      {
        id: 'resorts-halo', type: 'circle', source: 'resorts', filter: ['==', ['get', 'id'], ''],
        paint: {
          'circle-radius': radius(5), 'circle-color': 'rgba(0,0,0,0)',
          'circle-stroke-color': C.ring, 'circle-stroke-width': 2,
        },
      },
      {
        id: 'resorts', type: 'circle', source: 'resorts',
        layout: { 'circle-sort-key': ['get', 'tier_a'] },
        paint: {
          'circle-radius': radius(),
          'circle-color': ['case', ['==', ['get', 'tier'], 'A'], C.tierA, C.tierB],
          'circle-stroke-color': ['case', fs('hover'), C.ring, ['==', ['get', 'tier'], 'A'], '#fff4dc', C.background],
          'circle-stroke-width': ['case', fs('hover'), 2.5, ['==', ['get', 'tier'], 'A'], 1.5, 1],
          'circle-opacity': ['case', fs('dim'), 0.3, ['==', ['get', 'tier'], 'A'], 1, 0.85],
          'circle-stroke-opacity': ['case', fs('dim'), 0.3, 1],
        },
      },
    ],
  };
}

export function createMap(container, index, { onPick, getPadding }) {
  if (!window.maplibregl) throw new Error('MapLibre failed to load');
  const maplibregl = window.maplibregl;
  const map = new maplibregl.Map({
    container,
    style: buildStyle(index),
    bounds: config.nationalBounds,
    fitBoundsOptions: { padding: 20 },
    attributionControl: { compact: true, customAttribution: config.basemap.attribution },
    dragRotate: false,
    pitchWithRotate: false,
    touchPitch: false,
    renderWorldCopies: false,
    fadeDuration: 0,
    minZoom: 1.5,
    maxZoom: 14,
  });
  map.touchZoomRotate.disableRotation();
  map.keyboard.disableRotation();
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-left');
  map.on('error', (e) => console.warn('[map]', e && e.error ? e.error.message : e));

  const tip = h('div', { class: 'map-tip', role: 'presentation', hidden: true });
  container.append(tip);

  let loaded = false;
  let pending = null;
  let current = null;
  const stateKeys = []; // [{source, id}] with feature state set by applyView
  let hovered = null;

  function setFS(source, id, state) {
    map.setFeatureState({ source, id }, state);
    stateKeys.push({ source, id });
  }

  function clearFS() {
    for (const k of stateKeys) map.removeFeatureState(k);
    stateKeys.length = 0;
    hovered = null;
  }

  function applyView(view) {
    clearFS();
    const state = view.stateCode ? index.states.get(view.stateCode) : null;
    const cluster = view.clusterId ? index.clusters.get(view.clusterId) : null;
    if (state) setFS('states', state.code, { selected: true });
    map.setFilter('states-line-selected', ['==', ['get', 'code'], state ? state.code : '']);

    let focusResorts = null;
    if (cluster) {
      for (const c of index.clusters.values()) setFS('clusters', c.id, c.id === cluster.id ? { selected: true } : { dim: true });
      focusResorts = new Set(cluster.resort_ids);
    } else if (state) {
      const ids = new Set(state.cluster_ids);
      for (const c of index.clusters.values()) setFS('clusters', c.id, ids.has(c.id) ? { focus: true } : { dim: true });
      focusResorts = new Set();
      for (const cid of state.cluster_ids) for (const r of index.clusters.get(cid).resort_ids) focusResorts.add(r);
    }
    if (focusResorts) {
      for (const r of index.resorts.values()) if (!focusResorts.has(r.id)) setFS('resorts', r.id, { dim: true });
    }
    map.setFilter('resorts-halo', ['==', ['get', 'id'], view.resortId || '']);
  }

  function clampPadding(p) {
    const w = container.clientWidth; const hgt = container.clientHeight;
    const out = { ...p };
    const fix = (a, b, total) => {
      const over = out[a] + out[b] - (total - 60);
      if (over > 0) { const s = (total - 60) / (out[a] + out[b]); out[a] = Math.max(0, Math.floor(out[a] * s)); out[b] = Math.max(0, Math.floor(out[b] * s)); }
    };
    fix('left', 'right', w);
    fix('top', 'bottom', hgt);
    return out;
  }

  function boundsFor(view) {
    if (view.level === 'resort') {
      const r = index.resorts.get(view.resortId);
      // Frame the resort with its neighbours around it.
      const d = 0.22;
      return { b: [[r.lon - d * 1.3, r.lat - d], [r.lon + d * 1.3, r.lat + d]], maxZoom: 10 };
    }
    if (view.clusterId) return { b: index.clusters.get(view.clusterId).bbox, maxZoom: 10 };
    if (view.stateCode) return { b: index.states.get(view.stateCode).bbox, maxZoom: 7.5 };
    return { b: config.nationalBounds, maxZoom: 5 };
  }

  function fitView(view, animate) {
    const { b, maxZoom } = boundsFor(view);
    if (!b) return;
    map.fitBounds(b, {
      padding: clampPadding(getPadding(view)),
      maxZoom,
      duration: animate && !prefersReducedMotion() ? 900 : 0,
      essential: false,
    });
  }

  // Hit testing: resorts beat clusters beat states.
  function pick(point) {
    const pad = 6;
    const box = [[point.x - pad, point.y - pad], [point.x + pad, point.y + pad]];
    const r = map.queryRenderedFeatures(box, { layers: ['resorts'] });
    if (r.length) {
      // Prefer the nearest Tier A, then any.
      const f = r.find((x) => x.properties.tier === 'A') || r[0];
      return { kind: 'resort', id: f.properties.id, name: f.properties.name };
    }
    const c = map.queryRenderedFeatures(point, { layers: ['clusters-fill'] });
    if (c.length) {
      // Smallest cluster wins where polygons overlap.
      const best = c.map((f) => index.clusters.get(f.properties.id)).filter(Boolean)
        .sort((a, b) => area(a.bbox) - area(b.bbox))[0];
      if (best) return { kind: 'cluster', id: best.id, name: best.name };
    }
    const s = map.queryRenderedFeatures(point, { layers: ['states-fill'] });
    if (s.length) {
      const code = s[0].properties.code;
      if (index.states.has(code)) return { kind: 'state', id: code, name: index.states.get(code).name };
    }
    return null;
  }

  function setHover(target) {
    if (hovered && (!target || hovered.kind !== target.kind || hovered.id !== target.id)) {
      map.setFeatureState(hoverKey(hovered), { hover: false });
      hovered = null;
    }
    if (target && !hovered) {
      const key = hoverKey(target);
      map.setFeatureState(key, { hover: true });
      stateKeys.push(key);
      hovered = target;
    }
  }

  map.on('mousemove', (e) => {
    const t = pick(e.point);
    setHover(t);
    map.getCanvas().style.cursor = t ? 'pointer' : '';
    if (t) {
      tip.hidden = false;
      tip.textContent = t.kind === 'resort' ? t.name
        : t.kind === 'cluster' ? `${t.name} cluster` : t.name;
      const x = Math.min(e.point.x + 14, container.clientWidth - tip.offsetWidth - 8);
      tip.style.transform = `translate(${Math.max(4, x)}px, ${Math.max(4, e.point.y - 34)}px)`;
    } else tip.hidden = true;
  });
  map.on('mouseout', () => { setHover(null); tip.hidden = true; });

  map.on('click', (e) => {
    const t = pick(e.point);
    if (t) onPick(t, current);
  });

  map.on('load', () => {
    loaded = true;
    if (pending) { const v = pending; pending = null; applyView(v.view); fitView(v.view, false); }
  });

  return {
    map,
    show(view, { animate = true } = {}) {
      current = view;
      if (!loaded) { pending = { view }; return; }
      applyView(view);
      fitView(view, animate);
    },
    refit() { if (loaded && current) fitView(current, false); },
    resize() { map.resize(); },
  };
}

function hoverKey(t) {
  return { source: t.kind === 'resort' ? 'resorts' : t.kind === 'cluster' ? 'clusters' : 'states', id: t.id };
}

function area(b) { return b ? (b[1][0] - b[0][0]) * (b[1][1] - b[0][1]) : Infinity; }

