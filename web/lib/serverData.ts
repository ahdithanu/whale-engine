import { readFile } from "fs/promises";
import path from "path";
import { Account, Facility, Meta, DEFAULT_WEIGHTS, isGovernmentEndUser } from "./types";
import { rollupAccounts } from "./scoring";

const DATA_DIR = path.join(process.cwd(), "public", "data");

// Mirrors web/app/page.tsx's hardcoded default -- see that file for why 15
// (the design's original) was replaced with 35.
const QUALIFY_THRESHOLD = 35;
const WHALE_THRESHOLD = 20_000_000;

export async function loadBootstrapData(): Promise<{ accounts: Account[]; facilities: Facility[]; meta: Meta }> {
  const [accountsRaw, facilitiesRaw, metaRaw] = await Promise.all([
    readFile(path.join(DATA_DIR, "accounts.json"), "utf-8"),
    readFile(path.join(DATA_DIR, "facilities.json"), "utf-8"),
    readFile(path.join(DATA_DIR, "meta.json"), "utf-8"),
  ]);
  return {
    accounts: JSON.parse(accountsRaw),
    facilities: JSON.parse(facilitiesRaw),
    meta: JSON.parse(metaRaw),
  };
}

export type Headline = {
  whaleCount: number;
  whaleTcv: number;
  qualifiedFacilityCount: number;
  totalFacilities: number;
  customerUntouchedTcv: number;
};

/** Same computation as web/app/page.tsx's top-level rollup math (default
 * weights, default threshold, government end users excluded from whale
 * tier) -- kept here so generateMetadata/opengraph-image can compute the
 * real current headline numbers without duplicating the logic by hand. */
export function computeHeadline(accounts: Account[], facilities: Facility[]): Headline {
  const rollups = rollupAccounts(accounts, facilities, DEFAULT_WEIGHTS, QUALIFY_THRESHOLD);
  const whaleRollups = rollups.filter((r) => r.untouchedTcv >= WHALE_THRESHOLD && !isGovernmentEndUser(r.account.legal_name));
  return {
    whaleCount: whaleRollups.length,
    whaleTcv: whaleRollups.reduce((s, r) => s + r.untouchedTcv, 0),
    qualifiedFacilityCount: rollups.reduce((s, r) => s + r.qualifiedCount, 0),
    totalFacilities: facilities.length,
    customerUntouchedTcv: rollups.filter((r) => r.account.account_is_customer).reduce((s, r) => s + r.untouchedTcv, 0),
  };
}
