#!/usr/bin/env node
// Convert us-atlas states-10m (unprojected lon/lat TopoJSON) to the
// /data/build/us-states.geojson outline file used by the Career Map.
//
//   npm install            (dev tooling only, node_modules is gitignored)
//   node scripts/build-states.mjs [output path]
//
// Output: FeatureCollection, properties { code, name }, coordinates rounded
// to 4 decimals. Only the output file is committed.
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { feature } from "topojson-client";

const require = createRequire(import.meta.url);
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outPath = resolve(process.argv[2] ?? `${repoRoot}/data/build/us-states.geojson`);

// Census FIPS state codes to USPS codes. Keep in sync with pipeline/states.py.
const FIPS_TO_USPS = {
  "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO",
  "09": "CT", "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI",
  "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
  "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
  "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
  "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
  "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
  "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
  "54": "WV", "55": "WI", "56": "WY", "60": "AS", "66": "GU", "69": "MP",
  "72": "PR", "78": "VI",
};

const round = (n) => Math.round(n * 1e4) / 1e4;

function roundCoords(c) {
  if (typeof c[0] === "number") return [round(c[0]), round(c[1])];
  return c.map(roundCoords);
}

// Drop consecutive duplicate points created by rounding; keep rings closed
// and with at least 4 positions.
function dedupeRing(ring) {
  const out = [];
  for (const p of ring) {
    const last = out[out.length - 1];
    if (!last || last[0] !== p[0] || last[1] !== p[1]) out.push(p);
  }
  const first = out[0];
  const last = out[out.length - 1];
  if (first[0] !== last[0] || first[1] !== last[1]) out.push(first);
  return out.length >= 4 ? out : null;
}

function cleanPolygon(rings) {
  const cleaned = rings.map(dedupeRing);
  if (!cleaned[0]) return null;
  return cleaned.filter(Boolean);
}

function cleanGeometry(geom) {
  const coords = roundCoords(geom.coordinates);
  if (geom.type === "Polygon") {
    return { type: "Polygon", coordinates: cleanPolygon(coords) };
  }
  if (geom.type === "MultiPolygon") {
    const polys = coords.map(cleanPolygon).filter(Boolean);
    return polys.length === 1
      ? { type: "Polygon", coordinates: polys[0] }
      : { type: "MultiPolygon", coordinates: polys };
  }
  throw new Error(`unexpected geometry type ${geom.type}`);
}

const topo = JSON.parse(readFileSync(require.resolve("us-atlas/states-10m.json"), "utf8"));
const collection = feature(topo, topo.objects.states);

const features = collection.features.map((f) => {
  const code = FIPS_TO_USPS[f.id];
  if (!code) throw new Error(`no USPS code for FIPS ${f.id} (${f.properties.name})`);
  return {
    type: "Feature",
    properties: { code, name: f.properties.name },
    geometry: cleanGeometry(f.geometry),
  };
});
features.sort((a, b) => a.properties.code.localeCompare(b.properties.code));

const epoch = process.env.SOURCE_DATE_EPOCH;
const generatedAt = (epoch ? new Date(Number(epoch) * 1000) : new Date())
  .toISOString().replace(/\.\d{3}Z$/, "Z");

const out = {
  type: "FeatureCollection",
  version: 1,
  generated_at: generatedAt,
  source: "us-atlas states-10m (US Census Bureau cartographic boundaries)",
  features,
};
mkdirSync(dirname(outPath), { recursive: true });
writeFileSync(outPath, JSON.stringify(out) + "\n");
console.log(`wrote ${features.length} states to ${outPath}`);
