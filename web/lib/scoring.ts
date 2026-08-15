import { Account, Facility, SignalKey, Weights } from "./types";

/** Client-side weighted rescoring — mirrors pipeline/score.py's math exactly
 * (score already 0-100 per signal; weights are arbitrary positive numbers,
 * normalized to fractions here the same way the design's sliders do it). */
export function scoreFacility(f: Facility, weights: Weights): number {
  const keys: SignalKey[] = ["voc", "trend", "dart", "dod", "size"];
  const wSum = keys.reduce((s, k) => s + weights[k], 0) || 1;
  let num = 0;
  keys.forEach((k) => {
    num += (weights[k] / wSum) * f.signals[k].score;
  });
  return Math.round(num * 10) / 10;
}

export function nSignalsPresent(f: Facility): number {
  const keys: SignalKey[] = ["voc", "trend", "dart", "dod", "size"];
  return keys.filter((k) => f.signals[k].present).length;
}

// A facility only ever counts toward TCV if the resolver's own qualification
// gate (VOC/permit/employee-count) already passed AND the live-adjustable
// composite score clears the live threshold -- moving weights can only ever
// REMOVE facilities from the qualified set the backend produced, never
// invent new TCV the backend never computed a basis for. Shared by
// rollupAccounts (per-account) and any per-region/per-facility breakdown
// that needs the exact same live-weighted qualification call.
export function facilityQualifies(f: Facility, weights: Weights, qualifyThreshold: number): boolean {
  return f.qualified_for_tcv && scoreFacility(f, weights) >= qualifyThreshold;
}

export type AccountRollup = {
  account: Account;
  facilities: Facility[];
  qualifiedCount: number;
  untouchedQualifiedCount: number;
  untouchedTcv: number;
  installedTcv: number;
  pipelineTcv: number;
};

/** Re-derives qualification (score >= threshold) and account rollups under
 * the CURRENT weights/threshold — this is what makes dragging a slider
 * re-rank accounts live, not just re-order a fixed facility list. */
export function rollupAccounts(
  accounts: Account[],
  facilities: Facility[],
  weights: Weights,
  qualifyThreshold: number
): AccountRollup[] {
  const byAccount = new Map<string, Facility[]>();
  facilities.forEach((f) => {
    if (!byAccount.has(f.account_id)) byAccount.set(f.account_id, []);
    byAccount.get(f.account_id)!.push(f);
  });

  const rollups = accounts.map((account) => {
    const facs = byAccount.get(account.account_id) ?? [];
    let qualifiedCount = 0;
    let untouchedQualifiedCount = 0;
    let untouchedTcv = 0;
    let installedTcv = 0;
    let pipelineTcv = 0;
    facs.forEach((f) => {
      if (facilityQualifies(f, weights, qualifyThreshold)) {
        qualifiedCount++;
        if (f.installed_status === "untouched") {
          untouchedQualifiedCount++;
          untouchedTcv += f.est_facility_tcv;
        } else if (f.installed_status === "in_pipeline") {
          pipelineTcv += f.est_facility_tcv;
        } else if (f.installed_status === "installed") {
          installedTcv += f.est_facility_tcv;
        }
      }
    });
    return {
      account,
      facilities: facs,
      qualifiedCount,
      untouchedQualifiedCount,
      untouchedTcv,
      installedTcv,
      pipelineTcv,
    };
  });

  rollups.sort((a, b) => b.untouchedTcv - a.untouchedTcv);
  return rollups;
}

export function money(n: number): string {
  if (n >= 1e9) return "$" + (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return "$" + Math.round(n / 1e3) + "K";
  return "$" + Math.round(n);
}
