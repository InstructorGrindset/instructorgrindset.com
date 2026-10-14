#!/usr/bin/env node
// Convert us-atlas counties-10m (unprojected lon/lat TopoJSON) to
// data/raw/geo/us-counties.geojson, used by pipeline/rent.py to find the
// county of each resort base point (HUD FMR and ACS rent baselines).
//
//   npm install && node scripts/build-counties.mjs [output path]
//
// Properties { fips (5 digit), name, state (USPS) }. Coordinates rounded to 4 decimals.
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { feature } from "topojson-client";

const require = createRequire(import.meta.url);
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outPath = resolve(process.argv[2] ?? `${repoRoot}/data/raw/geo/us-counties.geojson`);

// Same table as scripts/build-states.mjs and pipeline/states.py.
const FIPS_TO_USPS = {
  "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE",
  "11": "DC", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA",
  "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
  "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM",
  "36": "NY", "37": "NC", "38": "ND", "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
  "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
  "54": "WV", "55": "WI", "56": "WY", "60": "AS", "66": "GU", "69": "MP", "72": "PR", "78": "VI",
};
const round = (n) => Math.round(n * 1e4) / 1e4;
const roundCoords = (c) => (typeof c[0] === "number" ? [round(c[0]), round(c[1])] : c.map(roundCoords));

const topo = JSON.parse(readFileSync(require.resolve("us-atlas/counties-10m.json"), "utf8"));
const features = feature(topo, topo.objects.counties).features
  .filter((f) => f.geometry)
  .map((f) => ({
    type: "Feature",
    properties: { fips: f.id, name: f.properties.name, state: FIPS_TO_USPS[f.id.slice(0, 2)] ?? null },
    geometry: { type: f.geometry.type, coordinates: roundCoords(f.geometry.coordinates) },
  }))
  .sort((a, b) => a.properties.fips.localeCompare(b.properties.fips));

mkdirSync(dirname(outPath), { recursive: true });
writeFileSync(outPath, JSON.stringify({
  type: "FeatureCollection",
  source: "us-atlas counties-10m (US Census Bureau cartographic boundaries)",
  features,
}) + "\n");
console.log(`wrote ${features.length} counties to ${outPath}`);
