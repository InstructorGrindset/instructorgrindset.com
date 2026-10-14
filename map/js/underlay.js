// Satellite style underlay: Natural Earth relief (public domain) for the lower 48 and Alaska, served with the site
// from images/basemap/ (pipeline/tools/make_underlay.py), so it shows with no outside host. Rows are evenly spaced in
// Web Mercator, so an image maps by its corners: a MapLibre image source in the WebGL map (map.js) and an SVG feImage
// in the 2D picture (preview.js). The tone is baked in; both draw it as is. See docs/map/ARCHITECTURE.md.
import { config } from './config.js';

let pending = null;
let meta = null;

/**
 * { rasters: { lower48, alaska }: { url, coordinates, width, height, small: { url, width, height } } } or null when off
 * or unreadable. `small` is a quarter size copy for national zooms (same corners). Fetched once.
 */
export function loadUnderlay() {
  const url = config.basemap.underlay && config.basemap.underlay.url;
  if (!url) return Promise.resolve(null);
  if (!pending) {
    pending = fetch(url, { priority: 'low' })
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((j) => {
        const rasters = {};
        for (const r of j.rasters || []) {
          const c = r.coordinates;
          if (!r.image || !Array.isArray(c) || c.length !== 4) continue;
          const sm = r.small && r.small.image ? { url: new URL(r.small.image, url).href, width: r.small.width, height: r.small.height } : null;
          rasters[r.region] = { url: new URL(r.image, url).href, coordinates: c, width: r.width, height: r.height, small: sm };
        }
        meta = Object.keys(rasters).length ? { rasters } : null;
        return meta;
      })
      .catch((err) => { console.warn('[map] underlay unavailable:', err.message); return null; });
  }
  return pending;
}

/** The underlay once loadUnderlay has resolved, else null. */
export function underlayNow() { return meta; }

/** Download and decode images off the main thread (detached images, so none is a paint timing candidate). */
export function decodeUnderlay(urls) {
  const jobs = [...new Set(urls)].map((u) => {
    const img = new Image();
    img.decoding = 'async';
    img.fetchPriority = 'low';
    img.src = u;
    return img.decode();
  });
  return Promise.all(jobs).then(() => true, (err) => { console.warn('[map] underlay image:', err && err.message); return false; });
}
