// User facing labels for facility types and Tier A criteria.
import { humanize, joinAnd, plural } from './ui.js';

export const FACILITY_LABEL = {
  ski_area: 'Ski area',
  indoor: 'Indoor ski facility',
  training_center: 'Year round training center',
};

const FACILITY_COUNT = {
  ski_area: ['ski area', 'ski areas'],
  indoor: ['indoor facility', 'indoor facilities'],
  training_center: ['training center', 'training centers'],
};

export const CRITERION_LABEL = {
  diehard: 'Diehard following',
  luxury: 'Luxury',
  athlete_development: 'Athlete development',
  heritage: 'Heritage',
  operator_compensation: 'Operator compensation',
  year_round_training: 'Year round training',
  owner_selection: 'Owner selection',
};

export function criterionLabel(key) { return CRITERION_LABEL[key] || humanize(key); }

/** {ski_area: 5, indoor: 1, training_center: 0} -> "5 ski areas and 1 indoor facility" */
export function facilityPhrase(counts) {
  const parts = ['ski_area', 'indoor', 'training_center']
    .filter((k) => counts[k] > 0)
    .map((k) => plural(counts[k], ...FACILITY_COUNT[k]));
  return parts.length ? joinAnd(parts) : 'no resorts yet';
}

export const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'ski_area', label: 'Ski areas' },
  { key: 'facility', label: 'Indoor and training' },
];
