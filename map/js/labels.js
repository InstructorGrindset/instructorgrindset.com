// User facing labels for facility types and Tier A criteria.
import { formatIsoDate, humanize, joinAnd, plural } from './ui.js';

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

export const ACCESS_LABEL = { convenient: 'Convenient', transit_friendly: 'Transit friendly', mixed: 'Mixed', car_centric: 'Car centric' };

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
  { key: 'season', label: 'Season length', prop: 'season_length_days', title: 'Typical season length', unit: 'Days', noun: 'season length' },
  { key: 'snowfall', label: 'Snowfall', prop: 'snowfall_annual_in', title: 'Average snowfall per season', unit: 'Inches per season', noun: 'snowfall' },
];

/** Official trail map formats as shown next to the link. */
export const TRAILMAP_FORMAT = { pdf: 'PDF', jpg: 'JPG image', png: 'PNG image', page: 'Web page' };

/** Live cams and jobs (links only). Keep in step with pipeline/routes.py. */
export const WEBCAM_KIND = { page: 'Web page', image: 'Still image', stream: 'Live stream' };
export const JOB_GROUPS = [
  ['ski_school', 'Ski school'], ['coaching', 'Coaching and clubs'], ['management', 'Management'],
  ['corporate', 'Parent company'], ['all', 'Job boards'],
];
export const DISCIPLINE_LABEL = {
  race: 'Race', freeride: 'Freeride', freestyle: 'Freestyle', park: 'Park', nordic: 'Nordic',
  adaptive: 'Adaptive', snowboard: 'Snowboard', moguls: 'Moguls',
};

// ── Transit schedules on the resort detail map ──
/** "Schedule for May 21 to Dec 6, 2026" (the year once when both dates share it); null without dates. */
export function transitSchedule(feed) {
  if (!feed || !feed.as_of) return null;
  if (!feed.valid_to) return `Schedule from ${formatIsoDate(feed.as_of)}`;
  const [a, b] = [formatIsoDate(feed.as_of), formatIsoDate(feed.valid_to)];
  const sameYear = feed.as_of.slice(0, 4) === feed.valid_to.slice(0, 4);
  return `Schedule for ${sameYear ? a.replace(/, \d{4}$/, '') : a} to ${b}`;
}

/** The one line caution under a feed: an offseason schedule, or a winter schedule that has ended. */
export function transitNote(feed) {
  if (!feed) return null;
  if (feed.covers_winter === false) return 'Offseason schedule. Winter ski service can differ; check the agency.';
  if (feed.covers_winter === true && feed.ended) return "Last season's schedule. This winter's service can differ; check the agency.";
  if (feed.covers_winter == null) return 'Winter ski service can differ; check the agency.';
  return null;
}
