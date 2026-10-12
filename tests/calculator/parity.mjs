// JS parity for the Season Income Calculator model.
//
//   node tests/calculator/parity.mjs          every golden vector through assets/js/season-income.mjs
//   node tests/calculator/parity.mjs --dump   JSON (tables + adapter case results) for tests/calculator/test_parity.py
//
// For each vector in golden.json: every value equals the captured double exactly, whole dollar
// figures equal roundHalfUp (Math.round, ties toward positive infinity), the effective rate and
// months strings equal toFixedHalfUp (toFixed, ties away from zero), and the verdict matches.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as si from '../../assets/js/season-income.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const camel = (s) => s.replace(/_([a-z])/g, (_, c) => c.toUpperCase());

export function inputsFromGolden(inp) {
  const out = { stateRate: si.stateTaxRate(inp.state) };
  if (out.stateRate === null) throw new Error(`no rate for ${inp.state}`);
  for (const [k, v] of Object.entries(inp)) if (k !== 'state') out[camel(k)] = v;
  return out;
}

if (process.argv.includes('--dump')) {
  const cases = JSON.parse(fs.readFileSync(path.join(HERE, 'adapter_cases.json'), 'utf8')).cases;
  const adapter = cases.map((c) => {
    const args = {};
    for (const [k, v] of Object.entries(c.inputs)) args[camel(k)] = v;
    return { id: c.id, inputs: si.lucrativenessInputs(args), result: si.seasonIncome(si.lucrativenessInputs(args)) };
  });
  const golden = JSON.parse(fs.readFileSync(path.join(HERE, 'golden.json'), 'utf8')).vectors;
  const vectors = golden.map((v) => ({ id: v.id, result: si.seasonIncome(inputsFromGolden(v.inputs)) }));
  process.stdout.write(JSON.stringify({
    tables: {
      federal_brackets: si.FEDERAL_BRACKETS,
      fica_rate: si.FICA_RATE, weeks_per_month: si.WEEKS_PER_MONTH, state_tax_rates: si.STATE_TAX_RATES,
      verdict_thresholds: si.VERDICT_THRESHOLDS,
    },
    adapter, vectors,
    // non finite numbers as strings ("NaN", "Infinity", "-Infinity"); JSON has no literal for them
  }, (k, v) => (typeof v === 'number' && !Number.isFinite(v) ? String(v) : v)));
} else {
  checkGolden();
}

function checkGolden() {
  const golden = JSON.parse(fs.readFileSync(path.join(HERE, 'golden.json'), 'utf8'));
  const failures = [];
  for (const vec of golden.vectors) {
    const r = si.seasonIncome(inputsFromGolden(vec.inputs));
    for (const [k, want] of Object.entries(vec.values)) {
      const got = r[camel(k)];
      if (got !== want) failures.push(`${vec.id} ${k}: want ${want} got ${got}`);
    }
    for (const [k, want] of Object.entries(vec.dollars)) {
      const got = si.roundHalfUp(r[camel(k)]);
      if (got !== want) failures.push(`${vec.id} dollars.${k}: want ${want} got ${got}`);
    }
    const rate = si.toFixedHalfUp(r.effectiveRate * 100, 1);
    if (rate !== vec.effective_rate_pct_1dp) failures.push(`${vec.id} effective rate: want ${vec.effective_rate_pct_1dp} got ${rate}`);
    const months = si.toFixedHalfUp(r.months, 1);
    if (months !== vec.months_1dp) failures.push(`${vec.id} months: want ${vec.months_1dp} got ${months}`);
    if (r.verdict !== vec.verdict) failures.push(`${vec.id} verdict: want ${vec.verdict} got ${r.verdict}`);
  }
  if (failures.length) {
    console.error(`FAIL ${failures.length}\n  ${failures.slice(0, 30).join('\n  ')}`);
    process.exitCode = 1;
    return;
  }
  console.log(`OK ${golden.vectors.length} golden vectors match assets/js/season-income.mjs exactly`);
}
