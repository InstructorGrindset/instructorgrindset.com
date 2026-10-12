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

/** Certification levels in display order (the Phase 4 contract). */
export const CERT_LEVELS = ['none', 'level_1', 'level_2', 'level_3', 'trainer', 'examiner', 'unspecified'];

export const CERT_LABEL = {
  none: 'No certification',
  level_1: 'Level 1',
  level_2: 'Level 2',
  level_3: 'Level 3',
  trainer: 'Trainer',
  examiner: 'Examiner',
  unspecified: 'All levels (posted range)',
};

export const COMPONENT_LABEL = {
  cert_pay_differential: 'Cert pay differential',
  paid_training_hours: 'Paid training hours',
  examiner_trainer_presence: 'Examiner and trainer presence',
  private_lesson_share: 'Private lesson share',
  year_round_staff: 'Year round staff',
  clinic_access: 'Clinic access',
};

export const RATE_UNIT = { hour: 'per hour', half_day: 'per half day', full_day: 'per full day' };

/** Client profile vocabulary (config/definitions.yaml career.vocab). Unknown tags are humanized. */
const TAG_LABEL = {
  destination_families: 'Destination families',
  local_kids_programs: 'Local kids programs',
  adaptive: 'Adaptive',
  race: 'Race',
  freestyle: 'Freestyle',
  international: 'International guests',
  uhnw_privates: 'Ultra high net worth privates',
};

export function tagLabel(tag) { return TAG_LABEL[tag] || humanize(tag); }

export const RECOLOR_MODES = [
  { key: 'default', label: 'Default' },
  { key: 'lucrativeness', label: 'Lucrativeness', prop: 'lucrativeness_mid', title: 'Estimated season savings, mid case', unit: 'USD per season', noun: 'lucrativeness' },
  { key: 'professionalism', label: 'Professionalism', prop: 'professionalism_score', title: 'Professionalism score', unit: 'Score out of 100', noun: 'professionalism' },
  { key: 'season', label: 'Season length', prop: 'season_length_days', title: 'Typical season length', unit: 'Days', noun: 'season length' },
  { key: 'snowfall', label: 'Snowfall', prop: 'snowfall_annual_in', title: 'Average snowfall per season', unit: 'Inches per season', noun: 'snowfall' },
];
