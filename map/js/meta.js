// Page title, description and canonical URL per view.
// Keep these strings in step with pipeline/routes.py, which writes the same
// values into the static route stubs.
import { config } from './config.js';
import { joinAnd, plural } from './ui.js';

const SUFFIX = 'Snowsports Career Map';
const BRAND = 'Instructor Grindset';

function stateName(index, code) {
  const s = index.states.get(code);
  return s ? s.name : code;
}

/** Resort names shared by two records get the state appended, as in pipeline/routes.py. */
function titleName(index, resort) {
  let n = 0;
  for (const r of index.resortList) if (r.name === resort.name) n += 1;
  return n > 1 ? `${resort.name}, ${resort.state}` : resort.name;
}

export function canonicalPath(index, view) {
  const cluster = view.clusterId ? index.clusters.get(view.clusterId) : null;
  if (view.level === 'national' || !view.stateCode) return config.mapBase;
  if (view.level === 'state') return `${config.mapBase}${view.stateCode.toLowerCase()}/`;
  const first = cluster.states[0].toLowerCase();
  if (view.level === 'cluster' || view.level === 'compare') return `${config.mapBase}${first}/${cluster.id}/`;
  const resort = index.resorts.get(view.resortId);
  const st = cluster.states.includes(resort.state) ? resort.state.toLowerCase() : first;
  return `${config.mapBase}${st}/${cluster.id}/${resort.id}/`;
}

export function metaFor(index, view) {
  const cluster = view.clusterId ? index.clusters.get(view.clusterId) : null;
  const resort = view.resortId ? index.resorts.get(view.resortId) : null;
  switch (view.level) {
    case 'state': {
      const s = index.states.get(view.stateCode);
      return {
        title: `${s.name} Resorts and Clusters for Snowsports Instructors • ${BRAND}`,
        description: `Explore the snowsports resort clusters in ${s.name}. See season dates, snowfall, and operation size for ${plural(s.resort_count, 'resort', 'resorts')}, with a source on every number.`,
      };
    }
    case 'cluster':
    case 'compare': {
      const names = joinAnd(cluster.states.map((c) => stateName(index, c)));
      return {
        title: view.level === 'compare'
          ? `Compare Resorts in ${cluster.name} • ${BRAND}`
          : `Instructor pay, housing, and snow in ${cluster.name} • ${BRAND}`,
        description: `Explore the ${plural(cluster.resort_ids.length, 'resort', 'resorts')} of the ${cluster.name} cluster in ${names}. Compare season length, snowfall, powder days, and operation size, with a source on every number.`,
      };
    }
    case 'resort':
      return {
        title: `Instructor pay, housing, and snow at ${titleName(index, resort)} • ${BRAND}`,
        description: `Plan a season at ${resort.name} in the ${cluster.name} cluster, ${stateName(index, resort.state)}. See instructor pay, housing, season dates, snowfall, terrain, and operation size, with a source on every number.`,
      };
    default:
      return {
        title: `Instructor Grindset • ${SUFFIX}`,
        description: 'An interactive US map for working and aspiring snowsports instructors. See where to work next season and what that season will look like, with a source on every number.',
      };
  }
}

export function applyMeta(index, view) {
  const { title, description } = metaFor(index, view);
  document.title = title;
  const d = document.querySelector('meta[name="description"]');
  if (d) d.setAttribute('content', description);
  const c = document.querySelector('link[rel="canonical"]');
  if (c) c.setAttribute('href', config.siteOrigin + canonicalPath(index, view));
}
