// Zod mirrors of docs/map/ARCHITECTURE.md. Change the contract there first.
import { z } from '/map/vendor/zod/zod.mjs';

export const Confidence = z.enum(['published', 'reported', 'estimated']);
const IsoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}/, 'ISO date');
const MonthDay = z.string().regex(/^\d{2}-\d{2}$/, 'MM-DD');
const Slug = z.string().min(1);

/** Sourced<T> | null. Missing keys are treated as null. */
export const sourced = (value) => z.object({
  value,
  source_id: Slug,
  as_of: IsoDate,
  confidence: Confidence,
  note: z.string().nullish(),
}).nullish().transform((v) => v ?? null);

const Envelope = { version: z.number().int(), generated_at: z.string() };

export const FACILITY_TYPES = ['ski_area', 'indoor', 'training_center'];
/** Missing until the build emits it; treated as a ski area. */
const FacilityType = z.enum(FACILITY_TYPES).nullish().transform((v) => v ?? 'ski_area');

export const CRITERIA = ['diehard', 'luxury', 'athlete_development', 'heritage', 'operator_compensation', 'year_round_training', 'owner_selection'];

export const TierACriterion = z.object({
  // A criterion added to the contract later still renders (humanized) instead of dropping the resort.
  criterion: z.string().min(1),
  rationale: z.string().nullish(),
  source_id: Slug,
});

const strList = z.array(z.string()).nullish().transform((v) => v ?? []);
const numOrNull = z.number().nullish().transform((v) => v ?? null);

// ── Career (Phase 4 contract) ──
export const CERT_LEVELS = ['none', 'level_1', 'level_2', 'level_3', 'trainer', 'examiner', 'unspecified'];

export const PayRow = z.object({
  cert_level: z.enum(CERT_LEVELS),
  hourly_low: numOrNull,
  hourly_high: numOrNull,
  confidence: Confidence,
  source_id: Slug,
  as_of: IsoDate,
  season: z.string().nullish(),
  stale: z.boolean().nullish().transform((v) => Boolean(v)),
  aggregate_count: z.number().int().nullish().transform((v) => v ?? null),   // reported rows only
});

export const Career = z.object({
  pay: z.array(PayRow).nullish().transform((v) => v ?? []),
  private_rate: sourced(z.object({ low: numOrNull, high: numOrNull, unit: z.enum(['hour', 'half_day', 'full_day']) })),
  private_split: sourced(z.number().min(0).max(100)),
  // Status stays a string so a status added later renders as "being finalized" instead of dropping the resort.
  professionalism: z.object({
    score: z.number().min(0).max(100).nullish().transform((v) => v ?? null),
    status: z.string(),
    breakdown: z.array(z.object({
      component: z.string(),
      weight: numOrNull,
      value: numOrNull,
      points: numOrNull,
      source_id: z.string().nullish(),
    })).nullish().transform((v) => v ?? []),
  }).nullish().transform((v) => v ?? null),
  lucrativeness: z.object({
    low: numOrNull,
    mid: numOrNull,
    high: numOrNull,
    status: z.string(),
    assumptions: z.array(z.object({
      key: z.string(),
      value: z.union([z.number(), z.string(), z.boolean()]).nullish(),
      label: z.string(),
    })).nullish().transform((v) => v ?? []),
  }).nullish().transform((v) => v ?? null),
  stale_fields: strList,
});

export const Resort = z.object({
  id: Slug,
  name: z.string().min(1),
  tier: z.enum(['A', 'B']),
  tier_a_criteria: z.array(TierACriterion).nullish().transform((v) => v ?? []),
  tier_a_status: z.enum(['draft', 'approved']).nullish().transform((v) => v ?? null),
  approval: z.object({ by: z.string().nullish(), date: z.string().nullish(), note: z.string().nullish() })
    .nullish().transform((v) => v ?? null),
  facility_type: FacilityType,
  owner: sourced(z.string()),
  lat: z.number().min(-90).max(90),
  lon: z.number().min(-180).max(180),
  location_source_id: z.string().nullish(),
  cluster_id: Slug,
  state: z.string().length(2).transform((s) => s.toUpperCase()),
  size: z.object({
    acres: sourced(z.number()),
    vertical_ft: sourced(z.number()),
    lifts: sourced(z.number()),
    ski_school_staff_est: sourced(z.number()),
  }),
  elevation: z.object({
    base_ft: sourced(z.number()),
    summit_ft: sourced(z.number()),
  }).nullish().transform((v) => v ?? { base_ft: null, summit_ft: null }),
  season: z.object({
    open_typical: sourced(MonthDay),
    close_typical: sourced(MonthDay),
    length_days: sourced(z.number()),
    training_start: sourced(MonthDay),
  }),
  terrain: z.object({
    beginner_pct: sourced(z.number()),
    intermediate_pct: sourced(z.number()),
    advanced_pct: sourced(z.number()),
    expert_pct: sourced(z.number()),
    character_tags: strList,
  }),
  fit: z.object({ enthusiast_types: strList, professional_types: strList })
    .nullish().transform((v) => v ?? { enthusiast_types: [], professional_types: [] }),
  clients: z.object({ profile_tags: strList, notes_md: z.string().nullish() })
    .nullish().transform((v) => v ?? { profile_tags: [], notes_md: null }),
  career: Career.nullish().transform((v) => v ?? null),
  editorial: z.unknown().nullish(),
  climate_available: z.boolean(),
  detail_available: z.boolean().nullish().transform((v) => Boolean(v)),
  housing_summary: z.object({
    site_count: z.number().int().nonnegative().nullish(),
    cheapest_rent_monthly_low: numOrNull,
    cheapest_site_id: z.string().nullish(),
    has_transit: z.boolean().nullish(),
  }).nullish().transform((v) => v ?? null),
  sources: z.array(z.string()),
});

export const ResortsFile = z.object({ ...Envelope, resorts: z.array(z.unknown()) });

const Position = z.tuple([z.number(), z.number()]).rest(z.number());
const Ring = z.array(Position).min(4);
const PolygonGeom = z.object({ type: z.literal('Polygon'), coordinates: z.array(Ring) });
const MultiPolygonGeom = z.object({ type: z.literal('MultiPolygon'), coordinates: z.array(z.array(Ring)) });
const AreaGeom = z.union([PolygonGeom, MultiPolygonGeom]);

/** Envelope only; each feature is validated on its own so one bad record cannot blank the map. */
export const FeatureCollectionShell = z.object({
  type: z.literal('FeatureCollection'),
  features: z.array(z.unknown()),
}).passthrough();

export const ClusterFeature = z.object({
  type: z.literal('Feature'),
  geometry: AreaGeom,
  properties: z.object({
    id: Slug,
    name: z.string().min(1),
    states: z.array(z.string().length(2)).min(1).transform((a) => a.map((s) => s.toUpperCase())),
    resort_ids: z.array(z.string()),
    tier_a_count: z.number().int().nonnegative(),
    overview_md: z.string().nullish(),
    sources: z.array(z.string()).nullish().transform((v) => v ?? []),
  }),
});

export const ResortFeature = z.object({
  type: z.literal('Feature'),
  geometry: z.object({ type: z.literal('Point'), coordinates: Position }),
  properties: z.object({
    id: Slug,
    name: z.string(),
    tier: z.enum(['A', 'B']),
    facility_type: z.enum(FACILITY_TYPES).nullish(),
    cluster_id: z.string(),
    state: z.string(),
    acres: z.number().nullish(),
    vertical_ft: z.number().nullish(),
    lifts: z.number().nullish(),
    size_rank: z.number().int().min(1).max(5).nullish().transform((v) => v ?? 1),
    // Recolor values (Phase 4); absent until the build emits them.
    season_length_days: numOrNull,
    snowfall_annual_in: numOrNull,
    professionalism_score: numOrNull,
    lucrativeness_mid: numOrNull,
  }),
});

export const StateFeature = z.object({
  type: z.literal('Feature'),
  geometry: AreaGeom,
  properties: z.object({ code: z.string().length(2), name: z.string() }),
});

export const StatesFile = z.object({
  ...Envelope,
  states: z.array(z.object({
    code: z.string().length(2).transform((s) => s.toUpperCase()),
    name: z.string().min(1),
    cluster_ids: z.array(z.string()),
    resort_count: z.number().int().nonnegative(),
  })),
});

export const SourcesFile = z.object({
  ...Envelope,
  sources: z.array(z.object({
    id: Slug,
    url: z.string(),
    title: z.string(),
    accessed_date: z.string().nullish(),
    note: z.string().nullish(),
  })),
});

const num = z.number().nullish().transform((v) => v ?? null);

export const ClimateMonth = z.object({
  resort_id: z.string(),
  month: z.number().int().min(1).max(12),
  snowfall_in: num,
  powder_days: num,
  banner_days: num,
  avg_storm_depth_in: num,
  density_pct_median: num,
  density_mix: z.object({ dry: z.number(), average: z.number(), moist: z.number() })
    .nullish().transform((v) => v ?? null),
  density_source: z.enum(['snotel', 'estimated']).nullish().transform((v) => v ?? null),
  temp_high_f: num,
  temp_low_f: num,
  wind_mph: num,
  sun_pct: num,
});

export const ClimateFile = z.object({
  ...Envelope,
  resort_id: z.string(),
  source_ids: z.array(z.string()),
  period: z.object({ start_year: z.number().int(), end_year: z.number().int() }),
  months: z.array(ClimateMonth).min(1).max(12),
});

/** definitions.json: the keys the UI reads are checked; the rest passes through. */
export const DefinitionsFile = z.object({
  snow: z.object({
    powder_day_in: z.number().positive().nullish(),
    banner_day_in: z.number().positive().nullish(),
  }).passthrough().nullish(),
  density: z.object({
    dry_max_pct: z.number().positive().nullish(),
    moist_min_pct: z.number().positive().nullish(),
  }).passthrough().nullish(),
}).passthrough();

// ── Resort detail (Phase 5): data/build/detail/<id>.json ──
const LineGeom = z.object({ type: z.literal('LineString'), coordinates: z.array(Position).min(2) });
const MultiLineGeom = z.object({ type: z.literal('MultiLineString'), coordinates: z.array(z.array(Position).min(2)).min(1) });
const AnyLine = z.union([LineGeom, MultiLineGeom]);

export const LiftFeature = z.object({
  type: z.literal('Feature'),
  geometry: AnyLine,
  properties: z.object({ name: z.string().nullish(), lift_type: z.string().nullish(), source_id: z.string().nullish() })
    .passthrough().nullish().transform((v) => v ?? {}),
});

export const Commute = z.object({
  mode: z.enum(['drive', 'bus', 'walk', 'shuttle']),
  minutes: numOrNull,
  distance_mi: numOrNull,
  transit_route_ids: strList,
  geometry: AnyLine.nullish().transform((v) => v ?? null),
  source_id: z.string().nullish(),
});

export const HousingSite = z.object({
  id: Slug,
  resort_id: z.string().nullish(),
  name: z.string().min(1),
  type: z.enum(['resort_owned', 'master_lease', 'market']),
  lat: z.number().min(-90).max(90),
  lon: z.number().min(-180).max(180),
  location_source_id: z.string().nullish(),
  precision: z.enum(['building', 'neighborhood']),
  rent_monthly: sourced(z.object({ low: numOrNull, high: numOrNull })),
  occupancy: z.enum(['shared', 'private', 'mixed']).nullish().transform((v) => v ?? null),
  deposit: sourced(z.number()),
  eligibility_note: z.string().nullish(),
  confidence: Confidence.nullish(),
  verified_date: z.string().nullish(),
  commutes: z.array(Commute).nullish().transform((v) => v ?? []),
});

export const DetailFile = z.object({
  version: z.number().int(),
  generated_at: z.string(),
  resort_id: z.string(),
  base_area: z.object({ lat: z.number(), lon: z.number(), source_id: z.string().nullish() }).nullish().transform((v) => v ?? null),
  // Arrays are validated per item in data.js so one odd lift cannot hide the rest.
  lifts: z.object({ type: z.literal('FeatureCollection'), features: z.array(z.unknown()) }).passthrough()
    .nullish().transform((v) => v ?? { type: 'FeatureCollection', features: [] }),
  housing: z.array(z.unknown()).nullish().transform((v) => v ?? []),
  rent_baseline: z.object({
    county_fips: z.string().nullish(),
    county_name: z.string().nullish(),
    fmr: z.object({ studio: sourced(z.number()), one_br: sourced(z.number()), two_br: sourced(z.number()) })
      .nullish().transform((v) => v ?? { studio: null, one_br: null, two_br: null }),
    acs_median_gross_rent: sourced(z.number()),
  }).nullish().transform((v) => v ?? null),
  transit: z.object({
    routes: z.array(z.object({
      id: z.string(), agency: z.string().nullish(), short_name: z.string().nullish(), long_name: z.string().nullish(),
      color: z.string().nullish(), geometry: AnyLine,
    })).nullish().transform((v) => v ?? []),
    stops: z.array(z.object({ id: z.string(), name: z.string(), lat: z.number(), lon: z.number(), route_ids: strList }))
      .nullish().transform((v) => v ?? []),
    source_ids: strList,
  }).nullish().transform((v) => v ?? null),
  isochrones: z.array(z.object({
    minutes: z.number(), mode: z.string().nullish(), geometry: AreaGeom, source_id: z.string().nullish(),
  })).nullish().transform((v) => v ?? []),
});
