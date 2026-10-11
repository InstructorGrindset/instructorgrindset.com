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

export const TierACriterion = z.object({
  criterion: z.enum(['diehard', 'luxury', 'athlete_development', 'heritage']),
  rationale: z.string().nullish(),
  source_id: Slug,
});

const strList = z.array(z.string()).nullish().transform((v) => v ?? []);

export const Resort = z.object({
  id: Slug,
  name: z.string().min(1),
  tier: z.enum(['A', 'B']),
  tier_a_criteria: z.array(TierACriterion).nullish().transform((v) => v ?? []),
  tier_a_status: z.enum(['draft', 'approved']).nullish().transform((v) => v ?? null),
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
  career: z.unknown().nullish(),
  editorial: z.unknown().nullish(),
  climate_available: z.boolean(),
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
    cluster_id: z.string(),
    state: z.string(),
    acres: z.number().nullish(),
    vertical_ft: z.number().nullish(),
    lifts: z.number().nullish(),
    size_rank: z.number().int().min(1).max(5).nullish().transform((v) => v ?? 1),
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
