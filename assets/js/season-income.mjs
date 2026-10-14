// Season Income Calculator model, shared by /income-calculator/ and the Career Map pipeline.
//
// Pure functions, no DOM. pipeline/season_income.py mirrors this file operation for operation,
// so both produce the same doubles for the same inputs. Keep the two in step: change one, change
// the other, then run tests/calculator (golden vectors, JS and Python parity).
//
// Rounding rules (identical in both languages):
// * roundHalfUp(x): whole numbers, ties toward positive infinity. Same as Math.round.
//   Used for private lesson days and for every whole dollar figure.
// * toFixedHalfUp(x, d): d decimals of the exact binary value, ties away from zero.
//   Same as Number.prototype.toFixed. Used for displayed rates and months.
// Every other value is an unrounded double.

// Federal income tax brackets used by the calculator (single filer, no deductions).
// tax = base + (taxable - over) x rate for the first bracket with taxable <= upTo.
export const FEDERAL_BRACKETS = Object.freeze([
  Object.freeze({ over: 0, upTo: 11600, base: 0, rate: 0.10 }),
  Object.freeze({ over: 11600, upTo: 47150, base: 1160, rate: 0.12 }),
  Object.freeze({ over: 47150, upTo: 100525, base: 5426, rate: 0.22 }),
  Object.freeze({ over: 100525, upTo: 191950, base: 17168, rate: 0.24 }),
  Object.freeze({ over: 191950, upTo: Infinity, base: 39110, rate: 0.32 }),
]);

// Social Security and Medicare, employee share, on wages.
export const FICA_RATE = 0.0765;

// Months in a season = weeks / WEEKS_PER_MONTH, unless the caller passes months.
export const WEEKS_PER_MONTH = 4.33;

// Flat state income tax rate on wages, as the calculator's state menu lists them.
export const STATE_TAX_RATES = Object.freeze([
  Object.freeze({ code: 'WA', name: 'Washington', rate: 0 }),
  Object.freeze({ code: 'WY', name: 'Wyoming', rate: 0 }),
  Object.freeze({ code: 'NV', name: 'Nevada', rate: 0 }),
  Object.freeze({ code: 'AK', name: 'Alaska', rate: 0 }),
  Object.freeze({ code: 'NH', name: 'New Hampshire', rate: 0 }),
  Object.freeze({ code: 'CO', name: 'Colorado', rate: 0.044 }),
  Object.freeze({ code: 'UT', name: 'Utah', rate: 0.0465 }),
  Object.freeze({ code: 'ID', name: 'Idaho', rate: 0.058 }),
  Object.freeze({ code: 'VT', name: 'Vermont', rate: 0.066 }),
  Object.freeze({ code: 'MT', name: 'Montana', rate: 0.0675 }),
  Object.freeze({ code: 'NY', name: 'New York', rate: 0.0685 }),
  Object.freeze({ code: 'OR', name: 'Oregon', rate: 0.0875 }),
  Object.freeze({ code: 'CA', name: 'California', rate: 0.093 }),
]);

// Verdict thresholds on net / gross (strictly greater than).
export const VERDICT_THRESHOLDS = Object.freeze({ strong: 0.35, workable: 0.1, tight: 0 });

/** Rate for a state code ("CO") or name ("Colorado"); null when the calculator has no rate for it. */
export function stateTaxRate(codeOrName) {
  const s = STATE_TAX_RATES.find((r) => r.code === codeOrName || r.name === codeOrName);
  return s ? s.rate : null;
}

/** Whole number, ties toward positive infinity (Math.round). Non finite values pass through. */
export function roundHalfUp(x) {
  return Math.round(x);
}

/** Fixed decimals string of the exact binary value, ties away from zero (Number.prototype.toFixed). */
export function toFixedHalfUp(x, digits) {
  return Number(x).toFixed(digits);
}

/** Federal income tax on taxable wages. */
export function federalTax(taxable) {
  for (const b of FEDERAL_BRACKETS) {
    if (taxable <= b.upTo) return b.over === 0 ? taxable * b.rate : b.base + (taxable - b.over) * b.rate;
  }
  return NaN; // only for NaN input
}

/** Verdict key for the share of gross kept: strong, workable, tight or negative. */
export function verdict(ratio) {
  if (ratio > VERDICT_THRESHOLDS.strong) return 'strong';
  if (ratio > VERDICT_THRESHOLDS.workable) return 'workable';
  if (ratio > VERDICT_THRESHOLDS.tight) return 'tight';
  return 'negative';
}

/**
 * The calculator's season model.
 *
 * inputs: { stateRate, weeks, daysPerWeek, hoursPerDay, groupRate, privateRate, privatePct (0..100),
 *           privateLengthHours (hours per private day), tipsPerDay, rentMonthly, lifeMonthly,
 *           transport (one time), gear (one time), months? (default weeks / WEEKS_PER_MONTH) }
 * Tips are untaxed. Federal tax, state tax and FICA apply to wages only.
 */
export function seasonIncome(inputs) {
  const {
    stateRate, weeks, daysPerWeek, hoursPerDay, groupRate, privateRate, privatePct, privateLengthHours,
    tipsPerDay, rentMonthly, lifeMonthly, transport, gear,
  } = inputs;

  const totalDays = weeks * daysPerWeek;
  const privateDaysRaw = totalDays * privatePct / 100;
  const privateDays = roundHalfUp(privateDaysRaw);
  const groupDays = totalDays - privateDays;
  const privateHours = privateDays * privateLengthHours;
  const groupHours = groupDays * hoursPerDay;

  const groupIncome = groupHours * groupRate;
  const privateIncome = privateHours * privateRate;
  const tipIncome = totalDays * tipsPerDay;
  const taxable = groupIncome + privateIncome;
  const gross = taxable + tipIncome;

  const fedTax = federalTax(taxable);
  const stateTax = taxable * stateRate;
  const fica = taxable * FICA_RATE;
  const totalTax = fedTax + stateTax + fica;
  const effectiveRate = taxable > 0 ? totalTax / taxable : 0;
  const afterTax = gross - totalTax;
  const afterTaxWages = taxable - totalTax;

  const months = inputs.months ?? weeks / WEEKS_PER_MONTH;
  const housingCost = rentMonthly * months;
  const lifeCost = lifeMonthly * months;
  const totalExpenses = housingCost + lifeCost + transport + gear;

  const net = afterTax - totalExpenses;
  const netBeforeTax = gross - totalExpenses;
  const weeklyNet = net / weeks;
  const ratio = gross > 0 ? net / gross : 0;

  return {
    totalDays, privateDaysRaw, privateDays, groupDays, groupHours, privateHours,
    groupIncome, privateIncome, tipIncome, taxable, gross,
    federalTax: fedTax, stateTax, fica, totalTax, effectiveRate, afterTax, afterTaxWages,
    months, housingCost, lifeCost, totalExpenses,
    net, netBeforeTax, weeklyNet, ratio, verdict: verdict(ratio),
  };
}

/**
 * Maps the Career Map lucrativeness assumptions onto seasonIncome inputs (no privates, no tips):
 *   weeks = seasonLengthDays / 7, one work block per week of hoursPerWeek hours at payHourlyMidUsd,
 *   rent = rentMonthlyUsd, life = foodMonthlyUsd + transportMonthlyUsd, months = seasonMonths,
 *   one time costs = passOffsetUsd + gearOffsetUsd (in the gear slot), transport slot 0.
 * The map (pipeline/career.py) calls the Python twin with rent, food, transport, pass and gear at 0 and no tips
 * (owner decision 2026-10-14), once per wage: the middle of the posted wage range gives the lucrativeness `mid`
 * ("Estimated net income"), the bottom `low` and the top `high`. netBeforeTax (then equal to gross) is that value.
 * stateRate only affects the after tax figures (net, totalTax, ...).
 */
export function lucrativenessInputs({
  payHourlyMidUsd, hoursPerWeek, seasonLengthDays, rentMonthlyUsd, foodMonthlyUsd, transportMonthlyUsd,
  seasonMonths, passOffsetUsd, gearOffsetUsd, stateRate = 0,
}) {
  return {
    stateRate, weeks: seasonLengthDays / 7, daysPerWeek: 1, hoursPerDay: hoursPerWeek,
    groupRate: payHourlyMidUsd, privateRate: 0, privatePct: 0, privateLengthHours: 0, tipsPerDay: 0,
    rentMonthly: rentMonthlyUsd, lifeMonthly: foodMonthlyUsd + transportMonthlyUsd,
    transport: 0, gear: passOffsetUsd + gearOffsetUsd, months: seasonMonths,
  };
}
